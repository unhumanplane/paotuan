from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
try:
    from astrbot.api.event.filter import regex
except ModuleNotFoundError:  # AstrBot test/mocked environments may expose regex on astrbot.api.event
    regex = getattr(filter, "regex", None)
    if regex is None:
        def regex(*args, **kwargs):
            def decorator(fn):
                return fn
            return decorator
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import File, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


PLUGIN_VERSION = "0.1.0"
DEFAULT_BRIDGE_URL = "http://192.168.123.148:8767/coder"
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_MAX_PROMPT_CHARS = 4000
DEFAULT_MAX_REPLY_CHARS = 3500
DEFAULT_LOG_MAX_BYTES = 1_000_000
DEFAULT_LOG_BACKUP_COUNT = 3
DEFAULT_ACK_TEXT = "Hermes 已收到 /coder 请求，开始处理。长任务可能需要几分钟。"
CODER_LOGGER_NAME = "astrbot_plugin_hermes_coder.private"
DEFAULT_FILE_SEND_PATH_PREFIXES = {
    "/AstrBot/data/plugin_data/astrbot_plugin_hermes_coder/exports/",
}


def configure_coder_logging(
    log_path: Path,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    plugin_logger = logging.getLogger(CODER_LOGGER_NAME)
    plugin_logger.setLevel(logging.INFO)
    plugin_logger.propagate = False

    for handler in list(plugin_logger.handlers):
        plugin_logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max(10_000, int(max_bytes)),
        backupCount=max(1, int(backup_count)),
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    plugin_logger.addHandler(handler)
    plugin_logger.info("coder_logger_configured path=%s max_bytes=%s backups=%s", log_path, max_bytes, backup_count)
    return plugin_logger


def noop_coder_logger() -> logging.Logger:
    plugin_logger = logging.getLogger(CODER_LOGGER_NAME)
    if not plugin_logger.handlers:
        plugin_logger.addHandler(logging.NullHandler())
    return plugin_logger


async def run_blocking(func, *args):
    to_thread = getattr(asyncio, "to_thread", None)
    if to_thread is not None:
        return await to_thread(func, *args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args))


@register(
    "astrbot_plugin_hermes_coder",
    "codex",
    "Expose trusted-group /coder requests to the local Hermes maintainer.",
    PLUGIN_VERSION,
    "https://github.com/unhumanplane/paotuan",
)
class HermesCoderPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self.enabled = self._config_bool("enabled", True)
        self.group_whitelist = self._config_str_set("group_whitelist")
        self.allow_private_chat = self._config_bool("allow_private_chat", False)
        self.bridge_url = self._config_str("bridge_url", DEFAULT_BRIDGE_URL)
        self.bridge_secret = self._config_str("bridge_secret", "")
        self.timeout_seconds = max(5, self._config_int("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.max_prompt_chars = max(100, self._config_int("max_prompt_chars", DEFAULT_MAX_PROMPT_CHARS))
        self.max_reply_chars = max(500, self._config_int("max_reply_chars", DEFAULT_MAX_REPLY_CHARS))
        self.ack_enabled = self._config_bool("ack_enabled", True)
        self.ack_text = self._config_str("ack_text", DEFAULT_ACK_TEXT)
        self.file_send_enabled = self._config_bool("file_send_enabled", True)
        self.file_send_path_prefixes = self._config_str_set("file_send_path_prefixes") or set(DEFAULT_FILE_SEND_PATH_PREFIXES)
        self.log_enabled = self._config_bool("log_enabled", True)
        self.log_max_bytes = max(10_000, self._config_int("log_max_bytes", DEFAULT_LOG_MAX_BYTES))
        self.log_backup_count = max(1, self._config_int("log_backup_count", DEFAULT_LOG_BACKUP_COUNT))
        self.coder_logger = self._init_coder_logger()
        logger.info(
            "Hermes coder plugin initialized: enabled=%s groups=%d bridge=%s secret_configured=%s timeout=%s",
            self.enabled,
            len(self.group_whitelist),
            self.bridge_url,
            bool(self.bridge_secret),
            self.timeout_seconds,
        )
        self.coder_logger.info(
            "plugin_initialized version=%s enabled=%s groups=%d bridge=%s secret_configured=%s timeout=%s",
            PLUGIN_VERSION,
            self.enabled,
            len(self.group_whitelist),
            self.bridge_url,
            bool(self.bridge_secret),
            self.timeout_seconds,
        )

    @filter.command("coder")
    async def on_coder_command(self, event: AstrMessageEvent, content: GreedyStr):
        async for result in self._handle_coder(event, content):
            yield result

    @filter.command("Coder")
    async def on_coder_command_title(self, event: AstrMessageEvent, content: GreedyStr):
        async for result in self._handle_coder(event, content):
            yield result

    @regex(r"^[\\/／]coder(?=\S)")
    async def on_coder_regex_fallback(self, event: AstrMessageEvent):
        content = self._prompt_from_raw_message(event.get_message_str())
        self.coder_logger.info(
            "regex_fallback_matched group=%s sender=%s message=%s raw_chars=%s prompt_chars=%s",
            self._event_group_id(event),
            self._safe_call(event, "get_sender_id"),
            str(getattr(getattr(event, "message_obj", None), "message_id", "") or ""),
            len(event.get_message_str() or ""),
            len(content),
        )
        async for result in self._handle_coder(event, content):
            yield result

    async def _handle_coder(self, event: AstrMessageEvent, content: Any):
        if not self.enabled:
            self.coder_logger.info("request_denied reason=disabled")
            yield event.plain_result("Hermes /coder 当前未启用。")
            event.stop_event()
            return
        group_id = self._event_group_id(event)
        if group_id:
            if group_id not in self.group_whitelist:
                logger.info("Hermes coder denied for group=%s", group_id)
                self.coder_logger.info("request_denied reason=group_not_whitelisted group=%s", group_id)
                yield event.plain_result("这个群没有启用 /coder。")
                event.stop_event()
                return
        elif not self.allow_private_chat:
            self.coder_logger.info("request_denied reason=private_not_allowed")
            yield event.plain_result("/coder 只允许在白名单群聊中使用。")
            event.stop_event()
            return

        prompt = self._prompt_from_command_content(content)
        sender_id = self._safe_call(event, "get_sender_id")
        message_id = str(getattr(getattr(event, "message_obj", None), "message_id", "") or "")
        if not prompt:
            self.coder_logger.info("request_denied reason=empty_prompt group=%s sender=%s message=%s", group_id or "", sender_id, message_id)
            yield event.plain_result("用法：/coder <要 Hermes 处理的任务>")
            event.stop_event()
            return
        if len(prompt) > self.max_prompt_chars:
            self.coder_logger.info(
                "request_denied reason=prompt_too_long group=%s sender=%s message=%s prompt_chars=%s max=%s",
                group_id or "",
                sender_id,
                message_id,
                len(prompt),
                self.max_prompt_chars,
            )
            yield event.plain_result(f"这条 /coder 太长了，最多 {self.max_prompt_chars} 字。")
            event.stop_event()
            return
        if not self.bridge_secret:
            self.coder_logger.warning("request_denied reason=missing_bridge_secret group=%s sender=%s message=%s", group_id or "", sender_id, message_id)
            yield event.plain_result("Hermes bridge secret 还没配置。")
            event.stop_event()
            return

        payload = self._build_payload(event, group_id, prompt)
        logger.info(
            "Hermes coder request accepted group=%s sender=%s prompt_chars=%s",
            group_id or "(private)",
            payload.get("sender_id") or "",
            len(prompt),
        )
        started_at = time.monotonic()
        self.coder_logger.info(
            "request_started group=%s sender=%s session=%s message=%s prompt_chars=%s",
            group_id or "(private)",
            payload.get("sender_id") or "",
            payload.get("session_id") or "",
            payload.get("message_id") or "",
            len(prompt),
        )
        await self._send_immediate_ack(event, payload)
        try:
            response = await asyncio.wait_for(
                run_blocking(self._post_bridge, payload),
                timeout=self.timeout_seconds + 5,
            )
        except asyncio.TimeoutError:
            self.coder_logger.warning(
                "request_timeout group=%s sender=%s message=%s timeout=%s elapsed_ms=%s",
                group_id or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
                self.timeout_seconds,
                int((time.monotonic() - started_at) * 1000),
            )
            yield event.plain_result("Hermes 处理超时了，任务可能还在后台跑。")
            event.stop_event()
            return
        except Exception as exc:
            logger.warning("Hermes coder bridge call failed: %s", exc)
            self.coder_logger.warning(
                "request_failed group=%s sender=%s message=%s elapsed_ms=%s error=%s",
                group_id or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
                int((time.monotonic() - started_at) * 1000),
                self._safe_error(exc),
            )
            yield event.plain_result(f"Hermes bridge 调用失败：{self._safe_error(exc)}")
            event.stop_event()
            return

        text = str(response.get("reply") or response.get("error") or "").strip()
        if not text:
            text = "Hermes 没有返回文本结果。"
        if len(text) > self.max_reply_chars:
            text = text[: self.max_reply_chars].rstrip() + "\n\n[已截断]"
        file_components = self._response_file_components(response)
        self.coder_logger.info(
            "request_completed group=%s sender=%s message=%s status_code=%s ok=%s reply_chars=%s files=%s elapsed_ms=%s",
            group_id or "(private)",
            payload.get("sender_id") or "",
            payload.get("message_id") or "",
            response.get("status_code") or "",
            response.get("ok") if "ok" in response else "",
            len(text),
            len(file_components),
            int((time.monotonic() - started_at) * 1000),
        )
        if file_components and callable(getattr(event, "chain_result", None)):
            try:
                yield event.chain_result([Plain(text), *file_components])
            except Exception as exc:
                self.coder_logger.warning(
                    "request_file_result_failed group=%s sender=%s message=%s error=%s",
                    group_id or "(private)",
                    payload.get("sender_id") or "",
                    payload.get("message_id") or "",
                    self._safe_error(exc),
                )
                yield event.plain_result(text)
        else:
            yield event.plain_result(text)
        event.stop_event()

    def _response_file_components(self, response: dict[str, Any]) -> list[Any]:
        if not self.file_send_enabled:
            return []
        raw_files = response.get("files")
        if not isinstance(raw_files, list):
            return []

        components: list[Any] = []
        for raw_file in raw_files[:3]:
            if not isinstance(raw_file, dict):
                continue
            file_path = str(raw_file.get("path") or raw_file.get("file") or "").strip()
            if not file_path or not self._is_allowed_file_path(file_path):
                continue
            file_name = str(raw_file.get("name") or Path(file_path).name or "hermes-coder-export.txt").strip()
            file_name = Path(file_name).name or "hermes-coder-export.txt"
            try:
                components.append(File(name=file_name, file=file_path))
            except Exception as exc:
                self.coder_logger.warning("response_file_component_failed path=%s error=%s", file_path, self._safe_error(exc))
        return components

    def _is_allowed_file_path(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/")
        for prefix in self.file_send_path_prefixes:
            normalized_prefix = str(prefix).replace("\\", "/").rstrip("/") + "/"
            if normalized.startswith(normalized_prefix):
                return True
        return False

    async def _send_immediate_ack(self, event: AstrMessageEvent, payload: dict[str, Any]) -> None:
        if not self.ack_enabled:
            self.coder_logger.info(
                "request_ack_skipped reason=disabled group=%s sender=%s message=%s",
                payload.get("group_id") or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
            )
            return
        session_id = str(payload.get("session_id") or getattr(event, "unified_msg_origin", "") or "")
        if not session_id:
            self.coder_logger.info(
                "request_ack_skipped reason=missing_session group=%s sender=%s message=%s",
                payload.get("group_id") or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
            )
            return
        send_message = getattr(self.context, "send_message", None)
        if not callable(send_message):
            self.coder_logger.info(
                "request_ack_skipped reason=missing_send_message group=%s sender=%s message=%s",
                payload.get("group_id") or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
            )
            return
        text = self.ack_text.strip() or DEFAULT_ACK_TEXT
        try:
            plain = Plain(text)
            chain = MessageChain([plain])
            chain_items = getattr(chain, "chain", None)
            if not chain_items or not any(str(getattr(item, "text", "") or "") for item in chain_items):
                # Some AstrBot test/runtime shims expose Plain/MessageChain as
                # factories or mocks. Preserve the minimal MessageChain shape so
                # context.send_message still receives the intended text.
                fallback_plain = type("SimplePlain", (), {"text": text})()
                chain = type("SimpleMessageChain", (), {"chain": [fallback_plain]})()
            await send_message(session_id, chain)
        except Exception as exc:
            self.coder_logger.warning(
                "request_ack_failed group=%s sender=%s message=%s error=%s",
                payload.get("group_id") or "(private)",
                payload.get("sender_id") or "",
                payload.get("message_id") or "",
                self._safe_error(exc),
            )
            return
        self.coder_logger.info(
            "request_ack_sent group=%s sender=%s session=%s message=%s text_chars=%s",
            payload.get("group_id") or "(private)",
            payload.get("sender_id") or "",
            session_id,
            payload.get("message_id") or "",
            len(text),
        )

    def _init_coder_logger(self) -> logging.Logger:
        if not self.log_enabled:
            return noop_coder_logger()
        try:
            data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_hermes_coder"
            return configure_coder_logging(
                data_dir / "logs" / "hermes_coder.log",
                max_bytes=self.log_max_bytes,
                backup_count=self.log_backup_count,
            )
        except Exception as exc:
            logger.warning("Hermes coder independent logger setup failed: %s", exc)
            return noop_coder_logger()

    def _post_bridge(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.bridge_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            self.bridge_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Hermes-Coder-Signature": signature,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"error": raw[:500]}
            parsed.setdefault("status_code", exc.code)
            return parsed
        parsed = json.loads(raw) if raw else {}
        parsed.setdefault("status_code", status)
        return parsed

    def _build_payload(self, event: AstrMessageEvent, group_id: str, prompt: str) -> dict[str, Any]:
        return {
            "prompt": prompt,
            "group_id": group_id,
            "sender_id": self._safe_call(event, "get_sender_id"),
            "session_id": str(getattr(event, "unified_msg_origin", "") or ""),
            "message_id": str(getattr(getattr(event, "message_obj", None), "message_id", "") or ""),
            "timestamp": int(time.time()),
        }

    @staticmethod
    def _prompt_from_command_content(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if content is None:
            return ""
        text = str(content or "").strip()
        if text == "GreedyStr":
            return ""
        return text

    @staticmethod
    def _prompt_from_raw_message(message: Any) -> str:
        text = str(message or "").strip()
        for prefix in ("/coder", "／coder", "\\coder"):
            if text.lower().startswith(prefix):
                return text[len(prefix) :].strip()
        return ""

    def _event_group_id(self, event: AstrMessageEvent) -> str:
        group_id = self._safe_call(event, "get_group_id")
        if group_id:
            return str(group_id)
        return str(getattr(event, "group_id", "") or getattr(getattr(event, "message_obj", None), "group_id", "") or "")

    @staticmethod
    def _safe_call(obj: Any, method: str) -> str:
        fn = getattr(obj, method, None)
        if not callable(fn):
            return ""
        try:
            value = fn()
        except Exception:
            return ""
        return str(value or "")

    def _config_str(self, key: str, default: str) -> str:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = default
        return str(value if value is not None else default).strip()

    def _config_int(self, key: str, default: int) -> int:
        try:
            value = self.config.get(key, default)
            return int(value)
        except Exception:
            return default

    def _config_bool(self, key: str, default: bool) -> bool:
        try:
            value = self.config.get(key, default)
        except AttributeError:
            return default
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}

    def _config_str_set(self, key: str) -> set[str]:
        try:
            value = self.config.get(key, [])
        except AttributeError:
            value = []
        if isinstance(value, str):
            raw_items = [part.strip() for part in value.replace("，", ",").split(",")]
        elif isinstance(value, (list, tuple, set)):
            raw_items = [str(part).strip() for part in value]
        else:
            raw_items = []
        return {item for item in raw_items if item}

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = str(exc).strip() or type(exc).__name__
        return text[:200]
