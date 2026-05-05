from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr


PLUGIN_VERSION = "0.1.0"
DEFAULT_BRIDGE_URL = "http://192.168.123.148:8767/coder"
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_MAX_PROMPT_CHARS = 4000
DEFAULT_MAX_REPLY_CHARS = 3500


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
        logger.info(
            "Hermes coder plugin initialized: enabled=%s groups=%d bridge=%s secret_configured=%s timeout=%s",
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

    async def _handle_coder(self, event: AstrMessageEvent, content: Any):
        if not self.enabled:
            yield event.plain_result("Hermes /coder 当前未启用。")
            event.stop_event()
            return
        group_id = self._event_group_id(event)
        if group_id:
            if group_id not in self.group_whitelist:
                logger.info("Hermes coder denied for group=%s", group_id)
                yield event.plain_result("这个群没有启用 /coder。")
                event.stop_event()
                return
        elif not self.allow_private_chat:
            yield event.plain_result("/coder 只允许在白名单群聊中使用。")
            event.stop_event()
            return

        prompt = self._prompt_from_command_content(content)
        if not prompt:
            yield event.plain_result("用法：/coder <要 Hermes 处理的任务>")
            event.stop_event()
            return
        if len(prompt) > self.max_prompt_chars:
            yield event.plain_result(f"这条 /coder 太长了，最多 {self.max_prompt_chars} 字。")
            event.stop_event()
            return
        if not self.bridge_secret:
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
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self._post_bridge, payload),
                timeout=self.timeout_seconds + 5,
            )
        except asyncio.TimeoutError:
            yield event.plain_result("Hermes 处理超时了，任务可能还在后台跑。")
            event.stop_event()
            return
        except Exception as exc:
            logger.warning("Hermes coder bridge call failed: %s", exc)
            yield event.plain_result(f"Hermes bridge 调用失败：{self._safe_error(exc)}")
            event.stop_event()
            return

        text = str(response.get("reply") or response.get("error") or "").strip()
        if not text:
            text = "Hermes 没有返回文本结果。"
        if len(text) > self.max_reply_chars:
            text = text[: self.max_reply_chars].rstrip() + "\n\n[已截断]"
        yield event.plain_result(text)
        event.stop_event()

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
