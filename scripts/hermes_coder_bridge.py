#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from aiohttp import web
except ImportError:  # pragma: no cover - some test envs do not run the HTTP server.
    web = None  # type: ignore[assignment]


ROOT = Path("/volume1/docker/hermes")
OPS = ROOT / "paotuan"
HERMES_ENV = OPS / "bin" / "hermes-env.sh"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_JOB_TIMEOUT_SECONDS = 1800
DEFAULT_MAX_PROMPT_CHARS = 4000
DEFAULT_WORKDIR = OPS / "work" / "paotuan"
DEFAULT_SECRET_PATH = OPS / "secrets" / "coder_bridge_secret"
DEFAULT_ASTRBOT_CODER_CONFIG_PATH = Path("/volume1/docker/astrbot/data/config/astrbot_plugin_hermes_coder_config.json")
DEFAULT_ASTRBOT_API_KEY_PATH = OPS / "secrets" / "astrbot_openapi_im_key"
DEFAULT_ASTRBOT_API_URL = "http://127.0.0.1:6185/api/v1/im/message"
DEFAULT_NOTIFY_SESSION_TEMPLATE = "default:GroupMessage:{group_id}"
DEFAULT_ASTRBOT_API_TIMEOUT_SECONDS = 10
DEFAULT_MAX_NOTIFY_CHARS = 3500
DEFAULT_BACKGROUND_ACCEPTED_REPLY = "Hermes 已转入后台执行，完成后会把结果发回群里。"
DEFAULT_GAME_DATA_DIR = Path("/volume1/docker/astrbot/data/plugin_data/astrbot_plugin_auto_trpg_dm")
DEFAULT_GAME_EXPORT_DIR = Path("/volume1/docker/astrbot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs")
DEFAULT_GAME_EXPORT_SEND_DIR = Path("/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/game_logs")
DEFAULT_GAME_LOG_TAIL_BYTES = 8_000
DEFAULT_GAME_AUDIT_TAIL_BYTES = 8_000
DEFAULT_GAME_REPLY_CHARS = 3_200


def require_aiohttp_web():
    if web is None:
        raise SystemExit("aiohttp is required to run hermes_coder_bridge.py")
    return web


def json_response(*args, **kwargs):
    return require_aiohttp_web().json_response(*args, **kwargs)


def load_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(load_dotenv(ROOT / "data" / ".env"))
    path_entries = [
        "/opt/hermes/.venv/bin",
        str(ROOT / "install" / "hermes-agent" / "venv" / "bin"),
        str(ROOT / "home" / ".local" / "bin"),
        str(ROOT / "data" / "node" / "bin"),
        env.get("PATH", ""),
        "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    ]
    env.update(
        {
            "HOME": str(ROOT / "home"),
            "HERMES_HOME": str(ROOT / "data"),
            "TMPDIR": str(ROOT / "tmp"),
            "HERMES_ACCEPT_HOOKS": "1",
            "PATH": ":".join(item for item in path_entries if item),
        }
    )
    return env


def read_secret(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not value:
        raise RuntimeError(f"bridge secret missing: {path}")
    return value


def parse_str_set(value: Any) -> set[str]:
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.replace("，", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(part).strip() for part in value]
    else:
        raw_items = []
    return {item for item in raw_items if item}


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def tail_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[-limit:]


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[已截断]"


def is_game_log_request(prompt: str) -> bool:
    text = (prompt or "").strip().lower()
    if not text:
        return False
    # A review/fix/deploy prompt should still go to Hermes instead of this local fast path.
    model_task_markers = ("复盘", "审查", "修复", "优化", "部署", "review", "fix", "deploy")
    if any(marker in text for marker in model_task_markers):
        return False
    log_markers = (
        "最新日志",
        "游戏日志",
        "跑团日志",
        "插件日志",
        "独立日志",
        "日志文件",
        "日志记录",
        "记录文件",
        "审计记录",
        "auto_trpg_dm.log",
        "audit",
        "log file",
        "latest log",
    )
    request_markers = (
        "获取",
        "查看",
        "看看",
        "拿",
        "取",
        "发",
        "发送",
        "给我",
        "导出",
        "show",
        "tail",
        "get",
        "latest",
    )
    return any(marker in text for marker in log_markers) and (
        "最新" in text or any(marker in text for marker in request_markers)
    )


def _safe_session_name(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id.strip())
    return cleaned.strip("_") or "unknown"


def _format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_mtime(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _file_summary(path: Path | None, label: str) -> str:
    if path is None:
        return f"- {label}: 未找到"
    if not path.exists():
        return f"- {label}: 未找到 ({path})"
    stat = path.stat()
    return f"- {label}: {path}\n  size={_format_bytes(stat.st_size)} mtime={_format_mtime(stat.st_mtime)}"


def _read_tail_bytes(path: Path | None, limit: int) -> str:
    if path is None or not path.exists() or not path.is_file():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > limit:
            handle.seek(size - limit)
        data = handle.read(limit)
    text = data.decode("utf-8", errors="replace")
    if size > limit and "\n" in text:
        text = text.split("\n", 1)[1]
    return text.strip()


def _latest_file(directory: Path, patterns: tuple[str, ...]) -> Path | None:
    candidates: list[Path] = []
    if not directory.exists():
        return None
    for pattern in patterns:
        candidates.extend(path for path in directory.glob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _extract_group_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for match in re.findall(r"(?<!\d)(\d{6,12})(?!\d)", text or ""):
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def build_prompt(payload: dict[str, Any]) -> str:
    prompt = str(payload.get("prompt") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    sender_id = str(payload.get("sender_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    return (
        "你是 Paotuan 仓库的 Hermes 维护代理。用户通过 AstrBot /coder 命令向你发起请求。\n"
        "请用中文简洁回复。能安全执行的仓库维护、排障、日志复盘、部署检查任务可以直接处理；"
        "不要打印密钥，不要读取 AstrBot 主日志，除非用户本次明确授权；不要重启 Docker 或容器，除非用户明确要求。\n\n"
        f"来源群号: {group_id or '(private)'}\n"
        f"发送者: {sender_id or '(unknown)'}\n"
        f"会话: {session_id or '(unknown)'}\n\n"
        f"用户请求:\n{prompt}"
    )


def run_hermes(prompt: str, timeout: int, workdir: Path) -> tuple[int, str]:
    cmd = ["hermes", "--accept-hooks", "--worktree", "-z", prompt]
    proc = subprocess.run(
        cmd,
        cwd=str(workdir),
        env=command_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout or ""


async def run_blocking(func, *args):
    to_thread = getattr(asyncio, "to_thread", None)
    if to_thread is not None:
        return await to_thread(func, *args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args))

class CoderBridge:
    def __init__(self, args: argparse.Namespace):
        self.secret_path = Path(args.secret_path)
        self.timeout_seconds = int(args.timeout_seconds)
        self.job_timeout_seconds = int(args.job_timeout_seconds)
        self.max_prompt_chars = int(args.max_prompt_chars)
        self.max_output_chars = int(args.max_output_chars)
        self.workdir = Path(args.workdir)
        self.astrbot_api_url = str(args.astrbot_api_url).strip()
        self.astrbot_api_key_path = Path(args.astrbot_api_key_path)
        self.astrbot_api_timeout_seconds = int(args.astrbot_api_timeout_seconds)
        self.astrbot_coder_config_path = Path(args.astrbot_coder_config_path)
        self.notify_group_whitelist = parse_str_set(args.notify_group_whitelist)
        self.notify_session_template = str(args.notify_session_template).strip() or DEFAULT_NOTIFY_SESSION_TEMPLATE
        self.max_notify_chars = int(args.max_notify_chars)
        self.game_data_dir = Path(args.game_data_dir)
        self.game_export_dir = Path(args.game_export_dir)
        self.game_export_send_dir = Path(args.game_export_send_dir)
        self.game_log_tail_bytes = int(args.game_log_tail_bytes)
        self.game_audit_tail_bytes = int(args.game_audit_tail_bytes)
        self.game_reply_chars = int(args.game_reply_chars)
        self.started_at = time.time()

    async def health(self, request: web.Request) -> web.Response:
        del request
        return json_response({"ok": True, "service": "hermes-coder-bridge", "uptime_seconds": int(time.time() - self.started_at)})

    async def _read_signed_json(self, request: web.Request) -> tuple[dict[str, Any] | None, web.Response | None]:
        if request.content_length and request.content_length > 64_000:
            return None, json_response({"ok": False, "error": "payload too large"}, status=413)
        body = await request.read()
        try:
            secret = read_secret(self.secret_path)
        except Exception as exc:
            return None, json_response({"ok": False, "error": str(exc)}, status=500)
        signature = request.headers.get("X-Hermes-Coder-Signature", "")
        if not verify_signature(secret, body, signature):
            return None, json_response({"ok": False, "error": "invalid signature"}, status=401)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return None, json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return None, json_response({"ok": False, "error": "invalid payload"}, status=400)
        return payload, None

    async def coder(self, request: web.Request) -> web.Response:
        payload, error_response = await self._read_signed_json(request)
        if error_response is not None:
            return error_response
        assert payload is not None
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return json_response({"ok": False, "error": "empty prompt"}, status=400)
        if len(prompt) > self.max_prompt_chars:
            return json_response({"ok": False, "error": "prompt too long"}, status=400)
        if is_game_log_request(prompt):
            group_id = str(payload.get("group_id") or "").strip()
            try:
                self._session_for_group(group_id)
                game_log_result = self._build_game_log_result(payload)
            except ValueError as exc:
                return json_response({"ok": False, "error": str(exc)}, status=400)
            except Exception as exc:
                return json_response({"ok": False, "error": f"game log export failed: {exc}"}, status=500)
            reply = str(game_log_result.get("reply") or "")
            print(
                f"coder_game_log_served group={group_id} "
                f"message={payload.get('message_id') or ''} reply_chars={len(reply)}",
                flush=True,
            )
            return json_response(
                {
                    "ok": True,
                    "accepted": False,
                    "reply": reply,
                    "files": game_log_result.get("files") or [],
                }
            )
        if not (self.workdir / ".git").exists():
            return json_response({"ok": False, "error": f"workdir is not a git repo: {self.workdir}"}, status=500)
        hermes_prompt = build_prompt(payload)
        print(
            f"coder_request_accepted group={payload.get('group_id') or ''} "
            f"message={payload.get('message_id') or ''} prompt_chars={len(prompt)} "
            f"job_timeout={self.job_timeout_seconds}",
            flush=True,
        )
        asyncio.create_task(self._run_coder_job(payload, hermes_prompt))
        return json_response({"ok": True, "accepted": True, "reply": DEFAULT_BACKGROUND_ACCEPTED_REPLY})

    async def _run_coder_job(self, payload: dict[str, Any], hermes_prompt: str) -> None:
        group_id = str(payload.get("group_id") or "").strip()
        message_id = str(payload.get("message_id") or "").strip()
        try:
            session = self._session_for_group(group_id)
            print(f"coder_job_started group={group_id} message={message_id}", flush=True)
            returncode, output = await run_blocking(run_hermes, hermes_prompt, self.job_timeout_seconds, self.workdir)
            text = self._format_coder_job_reply(returncode, output)
            print(
                f"coder_job_completed group={group_id} message={message_id} "
                f"returncode={returncode} output_chars={len(output or '')} notify_chars={len(text)}",
                flush=True,
            )
        except subprocess.TimeoutExpired:
            text = f"【Hermes /coder 超时】任务超过 {self.job_timeout_seconds} 秒仍未完成，已停止等待。"
        except Exception as exc:
            text = f"【Hermes /coder 执行失败】{str(exc)[:300]}"
        try:
            api_key = read_secret(self.astrbot_api_key_path)
            api_result = await run_blocking(self._post_astrbot_message, api_key, session, text)
            if not api_result.get("ok"):
                print(f"coder_job_notify_failed group={group_id} message={message_id} error={api_result.get('error')}", flush=True)
            else:
                print(f"coder_job_notify_sent group={group_id} message={message_id} session={session}", flush=True)
        except Exception as exc:
            print(f"coder_job_notify_failed group={group_id} message={message_id} error={str(exc)[:200]}", flush=True)

    def _session_for_group(self, group_id: str) -> str:
        if not group_id:
            raise ValueError("missing group_id")
        allowed_groups = self._allowed_notify_groups()
        if group_id not in allowed_groups:
            raise ValueError("group is not in notify whitelist")
        return self.notify_session_template.format(group_id=group_id)

    def _format_coder_job_reply(self, returncode: int, output: str) -> str:
        text = tail_text((output or "").strip(), self.max_output_chars)
        if not text:
            text = "Hermes /coder 任务完成，但没有返回文本结果。"
        if returncode != 0:
            text = f"【Hermes /coder 异常退出：{returncode}】\n{text}"
        return truncate_text(text, self.max_notify_chars)

    def _build_game_log_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        group_id = str(payload.get("group_id") or "").strip()
        plugin_log = self.game_data_dir / "logs" / "auto_trpg_dm.log"
        audit_file = self._select_game_audit_file(payload)
        save_file = self._select_game_save_file(payload, audit_file)

        generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        summary_lines = [
            "【Hermes 游戏日志】",
            f"生成时间: {generated_at}",
            "范围: 只读取 Auto TRPG DM 插件独立日志、audit、save 文件；没有读取 AstrBot 主日志。",
            "",
            "文件:",
            _file_summary(plugin_log, "插件独立日志"),
            _file_summary(audit_file, "最新/指定 audit"),
            _file_summary(save_file, "最新/指定 save"),
        ]

        plugin_tail = _read_tail_bytes(plugin_log, self.game_log_tail_bytes)
        audit_tail = _read_tail_bytes(audit_file, self.game_audit_tail_bytes)
        export_body = "\n".join(
            summary_lines
            + [
                "",
                "插件日志尾部:",
                plugin_tail or "(无可读内容)",
                "",
                "audit 尾部:",
                audit_tail or "(无可读内容)",
            ]
        )
        send_file: dict[str, str] | None = None
        try:
            export_path, send_path = self._write_game_log_export(export_body, group_id)
            send_file = {"path": str(send_path), "name": export_path.name}
            summary_lines.append(_file_summary(export_path, "导出文件"))
        except Exception as exc:
            summary_lines.append(f"- 导出文件: 写入失败 ({type(exc).__name__}: {str(exc)[:160]})")

        reply = "\n".join(
            summary_lines
            + [
                "",
                "插件日志尾部:",
                plugin_tail[-900:] if plugin_tail else "(无可读内容)",
                "",
                "audit 尾部:",
                audit_tail[-1200:] if audit_tail else "(无可读内容)",
            ]
        )
        return {
            "reply": truncate_text(reply, self.game_reply_chars),
            "files": [send_file] if send_file else [],
        }

    def _build_game_log_reply(self, payload: dict[str, Any]) -> str:
        return str(self._build_game_log_result(payload).get("reply") or "")

    def _select_game_audit_file(self, payload: dict[str, Any]) -> Path | None:
        audit_dir = self.game_data_dir / "audit"
        prompt = str(payload.get("prompt") or "")
        group_id = str(payload.get("group_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        candidates: list[Path] = []
        seen_sessions: set[str] = set()

        for requested_group_id in _extract_group_ids(prompt) + ([group_id] if group_id else []):
            session_name = _safe_session_name(f"default:GroupMessage:{requested_group_id}")
            if session_name not in seen_sessions:
                seen_sessions.add(session_name)
                candidates.append(audit_dir / f"{session_name}.jsonl")

        if session_id:
            session_name = _safe_session_name(session_id)
            if session_name not in seen_sessions:
                candidates.append(audit_dir / f"{session_name}.jsonl")

        for path in candidates:
            if path.exists():
                return path
        return _latest_file(audit_dir, ("*.jsonl", "*.jsonl.*"))

    def _select_game_save_file(self, payload: dict[str, Any], audit_file: Path | None) -> Path | None:
        save_dir = self.game_data_dir / "saves"
        prompt = str(payload.get("prompt") or "")
        group_id = str(payload.get("group_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        candidates: list[Path] = []
        seen_sessions: set[str] = set()

        if audit_file is not None:
            name = audit_file.name
            if ".jsonl" in name:
                session_name = name.split(".jsonl", 1)[0]
                seen_sessions.add(session_name)
                candidates.append(save_dir / f"{session_name}.json")

        for requested_group_id in _extract_group_ids(prompt) + ([group_id] if group_id else []):
            session_name = _safe_session_name(f"default:GroupMessage:{requested_group_id}")
            if session_name not in seen_sessions:
                seen_sessions.add(session_name)
                candidates.append(save_dir / f"{session_name}.json")

        if session_id:
            session_name = _safe_session_name(session_id)
            if session_name not in seen_sessions:
                candidates.append(save_dir / f"{session_name}.json")

        for path in candidates:
            if path.exists():
                return path
        return _latest_file(save_dir, ("*.json",))

    def _write_game_log_export(self, body: str, group_id: str) -> tuple[Path, Path]:
        self.game_export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        suffix = _safe_session_name(group_id or "private")
        path = self.game_export_dir / f"game_logs_{timestamp}_{suffix}.txt"
        path.write_text(body, encoding="utf-8")
        try:
            relative = path.relative_to(self.game_export_dir)
            send_path = self.game_export_send_dir / relative
        except ValueError:
            send_path = path
        return path, send_path

    async def notify(self, request: web.Request) -> web.Response:
        payload, error_response = await self._read_signed_json(request)
        if error_response is not None:
            return error_response
        assert payload is not None
        try:
            group_id, session, text = self._resolve_notify_payload(payload)
            api_key = read_secret(self.astrbot_api_key_path)
            api_result = await run_blocking(self._post_astrbot_message, api_key, session, text)
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, status=400)
        except RuntimeError as exc:
            return json_response({"ok": False, "error": str(exc)}, status=503)
        except Exception as exc:
            return json_response({"ok": False, "error": f"AstrBot notify failed: {exc}"}, status=502)
        if not api_result.get("ok"):
            return json_response({"ok": False, "error": api_result.get("error") or "AstrBot rejected message"}, status=502)
        return json_response({"ok": True, "group_id": group_id, "session": session})

    def _allowed_notify_groups(self) -> set[str]:
        if self.notify_group_whitelist:
            return set(self.notify_group_whitelist)
        config = load_json_file(self.astrbot_coder_config_path)
        return parse_str_set(config.get("group_whitelist"))

    def _resolve_notify_payload(self, payload: dict[str, Any]) -> tuple[str, str, str]:
        session_hint = str(payload.get("session") or payload.get("umo") or "").strip()
        group_id = str(payload.get("group_id") or "").strip()
        if not group_id and session_hint:
            parts = session_hint.split(":")
            if len(parts) == 3 and parts[1] == "GroupMessage":
                group_id = parts[2].strip()
        if not group_id:
            raise ValueError("missing group_id")
        allowed_groups = self._allowed_notify_groups()
        if group_id not in allowed_groups:
            raise ValueError("group is not in notify whitelist")
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            raise ValueError("empty text")
        session = self.notify_session_template.format(group_id=group_id)
        return group_id, session, truncate_text(text, self.max_notify_chars)

    def _post_astrbot_message(self, api_key: str, session: str, text: str) -> dict[str, Any]:
        body = json.dumps(
            {"umo": session, "message": text},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            self.astrbot_api_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-API-Key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.astrbot_api_timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status_code = exc.code
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"message": raw[:500]}
        ok = 200 <= status_code < 300 and parsed.get("status") == "ok"
        return {
            "ok": ok,
            "status_code": status_code,
            "error": parsed.get("message") or parsed.get("error") or parsed.get("status"),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HERMES_CODER_BRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HERMES_CODER_BRIDGE_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--secret-path", default=os.environ.get("HERMES_CODER_BRIDGE_SECRET_PATH", str(DEFAULT_SECRET_PATH)))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("HERMES_CODER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))))
    parser.add_argument("--job-timeout-seconds", type=int, default=int(os.environ.get("HERMES_CODER_JOB_TIMEOUT_SECONDS", str(DEFAULT_JOB_TIMEOUT_SECONDS))))
    parser.add_argument("--max-prompt-chars", type=int, default=int(os.environ.get("HERMES_CODER_MAX_PROMPT_CHARS", str(DEFAULT_MAX_PROMPT_CHARS))))
    parser.add_argument("--max-output-chars", type=int, default=int(os.environ.get("HERMES_CODER_MAX_OUTPUT_CHARS", "12000")))
    parser.add_argument("--workdir", default=os.environ.get("HERMES_CODER_WORKDIR", str(DEFAULT_WORKDIR)))
    parser.add_argument("--astrbot-api-url", default=os.environ.get("HERMES_ASTRBOT_API_URL", DEFAULT_ASTRBOT_API_URL))
    parser.add_argument("--astrbot-api-key-path", default=os.environ.get("HERMES_ASTRBOT_API_KEY_PATH", str(DEFAULT_ASTRBOT_API_KEY_PATH)))
    parser.add_argument("--astrbot-api-timeout-seconds", type=int, default=int(os.environ.get("HERMES_ASTRBOT_API_TIMEOUT_SECONDS", str(DEFAULT_ASTRBOT_API_TIMEOUT_SECONDS))))
    parser.add_argument("--astrbot-coder-config-path", default=os.environ.get("HERMES_ASTRBOT_CODER_CONFIG_PATH", str(DEFAULT_ASTRBOT_CODER_CONFIG_PATH)))
    parser.add_argument("--notify-group-whitelist", default=os.environ.get("HERMES_CODER_NOTIFY_GROUPS", ""))
    parser.add_argument("--notify-session-template", default=os.environ.get("HERMES_NOTIFY_SESSION_TEMPLATE", DEFAULT_NOTIFY_SESSION_TEMPLATE))
    parser.add_argument("--max-notify-chars", type=int, default=int(os.environ.get("HERMES_MAX_NOTIFY_CHARS", str(DEFAULT_MAX_NOTIFY_CHARS))))
    parser.add_argument("--game-data-dir", default=os.environ.get("HERMES_GAME_DATA_DIR", str(DEFAULT_GAME_DATA_DIR)))
    parser.add_argument("--game-export-dir", default=os.environ.get("HERMES_GAME_EXPORT_DIR", str(DEFAULT_GAME_EXPORT_DIR)))
    parser.add_argument("--game-export-send-dir", default=os.environ.get("HERMES_GAME_EXPORT_SEND_DIR", str(DEFAULT_GAME_EXPORT_SEND_DIR)))
    parser.add_argument("--game-log-tail-bytes", type=int, default=int(os.environ.get("HERMES_GAME_LOG_TAIL_BYTES", str(DEFAULT_GAME_LOG_TAIL_BYTES))))
    parser.add_argument("--game-audit-tail-bytes", type=int, default=int(os.environ.get("HERMES_GAME_AUDIT_TAIL_BYTES", str(DEFAULT_GAME_AUDIT_TAIL_BYTES))))
    parser.add_argument("--game-reply-chars", type=int, default=int(os.environ.get("HERMES_GAME_REPLY_CHARS", str(DEFAULT_GAME_REPLY_CHARS))))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bridge = CoderBridge(args)
    web_mod = require_aiohttp_web()
    app = web_mod.Application()
    app.router.add_get("/health", bridge.health)
    app.router.add_post("/coder", bridge.coder)
    app.router.add_post("/notify", bridge.notify)
    web_mod.run_app(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
