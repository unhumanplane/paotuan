#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
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
DEFAULT_MAX_PROMPT_CHARS = 4000
DEFAULT_WORKDIR = OPS / "work" / "paotuan"
DEFAULT_SECRET_PATH = OPS / "secrets" / "coder_bridge_secret"
DEFAULT_ASTRBOT_CODER_CONFIG_PATH = Path("/volume1/docker/astrbot/data/config/astrbot_plugin_hermes_coder_config.json")
DEFAULT_ASTRBOT_API_KEY_PATH = OPS / "secrets" / "astrbot_openapi_im_key"
DEFAULT_ASTRBOT_API_URL = "http://127.0.0.1:6185/api/v1/im/message"
DEFAULT_NOTIFY_SESSION_TEMPLATE = "default:GroupMessage:{group_id}"
DEFAULT_ASTRBOT_API_TIMEOUT_SECONDS = 10
DEFAULT_MAX_NOTIFY_CHARS = 3500


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
    env.update(
        {
            "HOME": str(ROOT / "home"),
            "HERMES_HOME": str(ROOT / "data"),
            "TMPDIR": str(ROOT / "tmp"),
            "HERMES_ACCEPT_HOOKS": "1",
            "PATH": f"{ROOT}/install/hermes-agent/venv/bin:{ROOT}/home/.local/bin:{ROOT}/data/node/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
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


class CoderBridge:
    def __init__(self, args: argparse.Namespace):
        self.secret_path = Path(args.secret_path)
        self.timeout_seconds = int(args.timeout_seconds)
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
        if not (self.workdir / ".git").exists():
            return json_response({"ok": False, "error": f"workdir is not a git repo: {self.workdir}"}, status=500)
        hermes_prompt = build_prompt(payload)
        try:
            returncode, output = await asyncio.to_thread(run_hermes, hermes_prompt, self.timeout_seconds, self.workdir)
        except subprocess.TimeoutExpired:
            return json_response({"ok": False, "error": "Hermes timed out"}, status=504)
        except Exception as exc:
            return json_response({"ok": False, "error": f"Hermes execution failed: {exc}"}, status=500)
        reply = tail_text(output.strip(), self.max_output_chars)
        return json_response({"ok": returncode == 0, "returncode": returncode, "reply": reply})

    async def notify(self, request: web.Request) -> web.Response:
        payload, error_response = await self._read_signed_json(request)
        if error_response is not None:
            return error_response
        assert payload is not None
        try:
            group_id, session, text = self._resolve_notify_payload(payload)
            api_key = read_secret(self.astrbot_api_key_path)
            api_result = await asyncio.to_thread(self._post_astrbot_message, api_key, session, text)
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
