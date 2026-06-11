from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Tuple
from urllib.parse import urlparse


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEFAULT_DEEPSEEK_USER_AGENT = "paotuan-deepseek-v4-flash"
DEFAULT_DEEPSEEK_TIMEOUT_SECONDS = 120

HttpPost = Callable[[str, Dict[str, str], Dict[str, Any], int], Tuple[int, Dict[str, str], bytes]]


@dataclass(frozen=True)
class DeepSeekV4FlashConfig:
    enabled: bool = True
    api_key: str = ""
    api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    model: str = DEFAULT_DEEPSEEK_MODEL
    timeout_seconds: int = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS
    user_agent: str = DEFAULT_DEEPSEEK_USER_AGENT


class DeepSeekV4FlashClient:
    def __init__(
        self,
        config: DeepSeekV4FlashConfig,
        *,
        environ: Mapping[str, str] | None = None,
        http_post: HttpPost | None = None,
    ):
        self.config = config
        self.environ = environ if environ is not None else os.environ
        self.http_post = http_post or _http_post_json

    def call_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 0,
        temperature: float = 0.2,
        json_mode: bool = True,
        thinking: str | None = "disabled",
        extra_body: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        unavailable = self._unavailable()
        if unavailable:
            return unavailable
        if not isinstance(messages, list) or not messages:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_messages_missing",
                "reason": "messages must be a non-empty list",
            }

        base_url = normalize_deepseek_base_url(self.config.base_url)
        api_key, api_key_source, api_key_env = self._api_key()
        request_timeout = max(1, int(timeout_seconds or self.config.timeout_seconds or DEFAULT_DEEPSEEK_TIMEOUT_SECONDS))
        payload = self._build_payload(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            thinking=thinking,
            extra_body=extra_body,
        )
        url = _join_url(base_url, "/chat/completions")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent(),
        }
        started = time.monotonic()
        try:
            status, response_headers, response_body, request_fallbacks = _post_chat_with_fallbacks(
                url,
                payload,
                api_key=api_key,
                timeout=request_timeout,
                http_post=self.http_post,
                headers=headers,
            )
        except TimeoutError:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_timeout",
                "elapsed_ms": _elapsed_ms(started),
            }
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_network_error",
                "reason": _safe_text(getattr(exc, "reason", exc), 240),
                "elapsed_ms": _elapsed_ms(started),
            }
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_call_failed",
                "reason": _safe_text(exc, 240),
                "elapsed_ms": _elapsed_ms(started),
            }

        parse_result = _parse_json_response(response_body)
        if not parse_result.get("ok"):
            if status < 200 or status >= 300:
                return {
                    "ok": False,
                    "available": False,
                    "provider": "deepseek",
                    "error": "deepseek_http_error",
                    "status": status,
                    "reason": _response_error_excerpt(response_body),
                    "elapsed_ms": _elapsed_ms(started),
                    "request_fallbacks": request_fallbacks,
                }
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": parse_result.get("error", "deepseek_invalid_json"),
                "reason": parse_result.get("reason", ""),
                "elapsed_ms": _elapsed_ms(started),
                "request_fallbacks": request_fallbacks,
            }

        raw_response = parse_result["raw_response"]
        return {
            "ok": True,
            "available": True,
            "provider": "deepseek",
            "model": str(raw_response.get("model") or self.config.model or DEFAULT_DEEPSEEK_MODEL),
            "base_url": base_url,
            "text": response_text(raw_response),
            "reasoning_content": reasoning_content(raw_response),
            "usage": raw_response.get("usage") or {},
            "finish_reason": finish_reason(raw_response),
            "elapsed_ms": _elapsed_ms(started),
            "request_fallbacks": request_fallbacks,
            "raw_response": raw_response,
        }

    async def acall_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 0,
        temperature: float = 0.2,
        json_mode: bool = True,
        thinking: str | None = "disabled",
        extra_body: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout = max(1, int(timeout_seconds or self.config.timeout_seconds or DEFAULT_DEEPSEEK_TIMEOUT_SECONDS))
        return await asyncio.wait_for(
            asyncio.to_thread(
                self.call_chat_completion,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                json_mode=json_mode,
                thinking=thinking,
                extra_body=extra_body,
                timeout_seconds=timeout,
            ),
            timeout=timeout + 5,
        )

    def complete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        contexts: list[dict[str, Any] | str] | None = None,
        max_tokens: int = 0,
        temperature: float = 0.2,
        json_mode: bool = True,
        thinking: str | None = "disabled",
        extra_body: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        messages = build_messages(prompt, system_prompt=system_prompt, contexts=contexts)
        return self.call_chat_completion(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            thinking=thinking,
            extra_body=extra_body,
            timeout_seconds=timeout_seconds,
        )

    async def acomplete(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        contexts: list[dict[str, Any] | str] | None = None,
        max_tokens: int = 0,
        temperature: float = 0.2,
        json_mode: bool = True,
        thinking: str | None = "disabled",
        extra_body: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        messages = build_messages(prompt, system_prompt=system_prompt, contexts=contexts)
        return await self.acall_chat_completion(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
            thinking=thinking,
            extra_body=extra_body,
            timeout_seconds=timeout_seconds,
        )

    def _unavailable(self) -> dict[str, Any] | None:
        if not self.config.enabled:
            return {"ok": True, "available": False, "reason": "deepseek_disabled"}
        base_url = normalize_deepseek_base_url(self.config.base_url)
        if not base_url:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_base_url_missing",
            }
        api_key, api_key_source, api_key_env = self._api_key()
        if not api_key:
            return {
                "ok": False,
                "available": False,
                "provider": "deepseek",
                "error": "deepseek_api_key_missing",
                "api_key_env": api_key_env,
                "api_key_source": api_key_source,
            }
        return None

    def _api_key(self) -> tuple[str, str, str]:
        direct_key = str(self.config.api_key or "").strip()
        if direct_key:
            return direct_key, "config", ""
        api_key_env = str(self.config.api_key_env or "").strip()
        if not api_key_env:
            return "", "missing", ""
        if _looks_like_env_var_name(api_key_env):
            return str(self.environ.get(api_key_env, "")).strip(), "env", api_key_env
        return api_key_env, "config_legacy_api_key_env", ""

    def _user_agent(self) -> str:
        return str(self.config.user_agent or DEFAULT_DEEPSEEK_USER_AGENT).strip() or DEFAULT_DEEPSEEK_USER_AGENT

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float,
        json_mode: bool,
        thinking: str | None,
        extra_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": str(self.config.model or DEFAULT_DEEPSEEK_MODEL).strip() or DEFAULT_DEEPSEEK_MODEL,
            "messages": list(messages),
            "temperature": temperature,
        }
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        thinking_mode = normalize_thinking_mode(thinking)
        if thinking_mode != "auto":
            payload["thinking"] = {"type": thinking_mode}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if extra_body:
            payload.update(extra_body)
        return payload


def build_messages(
    prompt: str,
    *,
    system_prompt: str = "",
    contexts: list[dict[str, Any] | str] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if str(system_prompt or "").strip():
        messages.append({"role": "system", "content": str(system_prompt)})
    for context in contexts or []:
        if isinstance(context, dict):
            role = str(context.get("role") or "system").strip() or "system"
            content = _content_to_text(context.get("content"))
            if content:
                messages.append({"role": role, "content": content})
            continue
        content = _content_to_text(context)
        if content:
            messages.append({"role": "system", "content": content})
    messages.append({"role": "user", "content": str(prompt)})
    return messages


def call_chat_completion(
    *,
    api_key: str = "",
    api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    messages: list[dict[str, Any]],
    max_tokens: int = 0,
    temperature: float = 0.2,
    timeout: int = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS,
    json_mode: bool = True,
    thinking: str | None = "disabled",
    user_agent: str = DEFAULT_DEEPSEEK_USER_AGENT,
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client = DeepSeekV4FlashClient(
        DeepSeekV4FlashConfig(
            enabled=True,
            api_key=api_key,
            api_key_env=api_key_env,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout,
            user_agent=user_agent,
        ),
        environ=environ,
        http_post=http_post,
    )
    return client.call_chat_completion(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        json_mode=json_mode,
        thinking=thinking,
        extra_body=extra_body,
        timeout_seconds=timeout,
    )


def call_chat_completion_or_raise(
    *,
    api_key: str = "",
    api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    messages: list[dict[str, Any]],
    max_tokens: int = 0,
    temperature: float = 0.2,
    timeout: int = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS,
    json_mode: bool = True,
    thinking: str | None = "disabled",
    user_agent: str = DEFAULT_DEEPSEEK_USER_AGENT,
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = call_chat_completion(
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=json_mode,
        thinking=thinking,
        user_agent=user_agent,
        environ=environ,
        http_post=http_post,
        extra_body=extra_body,
    )
    if result.get("ok") is not True:
        message = str(result.get("reason") or result.get("error") or "deepseek_call_failed")
        status = result.get("status")
        if status is not None:
            message = f"{message} (status={status})"
        raise RuntimeError(message)
    return result


def complete(
    prompt: str,
    *,
    system_prompt: str = "",
    contexts: list[dict[str, Any] | str] | None = None,
    api_key: str = "",
    api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    max_tokens: int = 0,
    temperature: float = 0.2,
    timeout: int = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS,
    json_mode: bool = True,
    thinking: str | None = "disabled",
    user_agent: str = DEFAULT_DEEPSEEK_USER_AGENT,
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = build_messages(prompt, system_prompt=system_prompt, contexts=contexts)
    return call_chat_completion(
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=json_mode,
        thinking=thinking,
        user_agent=user_agent,
        environ=environ,
        http_post=http_post,
        extra_body=extra_body,
    )


def complete_or_raise(
    prompt: str,
    *,
    system_prompt: str = "",
    contexts: list[dict[str, Any] | str] | None = None,
    api_key: str = "",
    api_key_env: str = DEFAULT_DEEPSEEK_API_KEY_ENV,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    max_tokens: int = 0,
    temperature: float = 0.2,
    timeout: int = DEFAULT_DEEPSEEK_TIMEOUT_SECONDS,
    json_mode: bool = True,
    thinking: str | None = "disabled",
    user_agent: str = DEFAULT_DEEPSEEK_USER_AGENT,
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = complete(
        prompt,
        system_prompt=system_prompt,
        contexts=contexts,
        api_key=api_key,
        api_key_env=api_key_env,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=json_mode,
        thinking=thinking,
        user_agent=user_agent,
        environ=environ,
        http_post=http_post,
        extra_body=extra_body,
    )
    if result.get("ok") is not True:
        message = str(result.get("reason") or result.get("error") or "deepseek_call_failed")
        status = result.get("status")
        if status is not None:
            message = f"{message} (status={status})"
        raise RuntimeError(message)
    return result


def response_text(response: Any) -> str:
    choice = _first_choice(response)
    if not choice:
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        return _content_to_text(message.get("content"))
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return _content_to_text(delta.get("content"))
    return ""


def reasoning_content(response: Any) -> str:
    choice = _first_choice(response)
    if not choice:
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        text = _first_reasoning_text(message)
        if text:
            return text
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return _first_reasoning_text(delta)
    return ""


def finish_reason(response: Any) -> str | None:
    choice = _first_choice(response)
    if not choice:
        return None
    value = choice.get("finish_reason")
    return str(value) if value is not None else None


def normalize_deepseek_base_url(raw_base_url: str) -> str:
    raw = str(raw_base_url or "").strip() or DEFAULT_DEEPSEEK_BASE_URL
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return parsed.geturl().rstrip("/")


def normalize_thinking_mode(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower().replace("_", "-")
    if normalized in {"", "auto"}:
        return "auto"
    if normalized in {"enabled", "enable", "on", "true", "yes"}:
        return "enabled"
    if normalized in {"disabled", "disable", "off", "false", "no"}:
        return "disabled"
    return "auto"


def _post_chat_with_fallbacks(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: int,
    http_post: HttpPost,
    headers: dict[str, str],
) -> tuple[int, dict[str, str], bytes, list[str]]:
    variants = _payload_variants(payload)
    seen: set[str] = set()
    last_status = 0
    last_headers: dict[str, str] = {}
    last_body = b""
    attempted_fallbacks: list[str] = []
    for label, candidate in variants:
        signature = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        status, response_headers, response_body = http_post(url, headers, candidate, timeout)
        if 200 <= status < 300:
            return status, response_headers, response_body, [] if label == "original" else [label]
        last_status = int(status or 0)
        last_headers = dict(response_headers or {})
        last_body = bytes(response_body or b"")
        if label != "original":
            attempted_fallbacks.append(label)
        if status not in {400, 422}:
            return status, response_headers, response_body, [] if label == "original" else [label]
    return last_status, last_headers, last_body, attempted_fallbacks


def _payload_variants(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    variants: list[tuple[str, dict[str, Any]]] = [("original", dict(payload))]
    if "thinking" in payload:
        without_thinking = dict(payload)
        without_thinking.pop("thinking", None)
        variants.append(("without_thinking", without_thinking))
    if "response_format" in payload:
        without_json_mode = dict(payload)
        without_json_mode.pop("response_format", None)
        variants.append(("without_json_mode", without_json_mode))
    if "thinking" in payload and "response_format" in payload:
        minimal = dict(payload)
        minimal.pop("thinking", None)
        minimal.pop("response_format", None)
        variants.append(("without_thinking_or_json_mode", minimal))
    return variants


def _http_post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            response_headers = dict(getattr(response, "headers", {}) or {})
            response_body = response.read()
            return status, response_headers, response_body
    except urllib.error.HTTPError as exc:
        body = _read_error_body(exc)
        return int(getattr(exc, "code", 0) or 0), dict(getattr(exc, "headers", {}) or {}), body


def _read_error_body(exc: urllib.error.HTTPError) -> bytes:
    try:
        return exc.read()
    except Exception:
        return b""


def _parse_json_response(body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": "deepseek_invalid_json",
            "reason": f"{exc.msg} at pos {exc.pos}",
            "raw_text": text[:500],
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": "deepseek_response_not_object",
            "reason": "response body is not a JSON object",
            "raw_text": text[:500],
        }
    return {"ok": True, "raw_response": parsed}


def _response_error_excerpt(body: bytes, *, limit: int = 240) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _first_choice(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0]
    return first if isinstance(first, dict) else {}


def _first_reasoning_text(container: dict[str, Any]) -> str:
    for key in ("reasoning_content", "thinking", "reasoning", "thought"):
        value = container.get(key)
        text = _content_to_text(value)
        if text:
            return text
    return ""


def _content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                else:
                    parts.append(str(item))
            else:
                parts.append(_content_to_text(item))
        return "".join(parts)
    return str(value)


def _looks_like_env_var_name(value: str) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    return candidate.replace("_", "").isalnum() and candidate[0].isalpha() and candidate.upper() == candidate


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _join_url(base_url: str, path: str) -> str:
    return str(base_url or DEFAULT_DEEPSEEK_BASE_URL).rstrip("/") + "/" + path.lstrip("/")
