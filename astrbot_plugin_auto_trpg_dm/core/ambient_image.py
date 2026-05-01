from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


AMBIENT_IMAGE_API_MODES = {"images", "chat_completions"}
DEFAULT_AMBIENT_IMAGE_BASE_URL = "https://www.packyapi.com"


@dataclass
class AmbientImageConfig:
    enabled: bool = False
    api_mode: str = "images"
    base_url: str = DEFAULT_AMBIENT_IMAGE_BASE_URL
    api_key_env: str = "PACKYAPI_SORA_API_KEY"
    model: str = "gpt-image-2"
    prompt_model: str = ""
    size: str = "3840x2160"
    quality: str = "high"
    output_format: str = "png"
    response_format: str = "url"
    timeout_seconds: int = 60
    send_to_chat: bool = True
    frequency: str = "medium"
    prompt_template: str = ""


HttpPost = Callable[[str, dict[str, str], dict[str, Any], int], tuple[int, dict[str, str], bytes]]
HttpGet = Callable[[str, dict[str, str], int], tuple[int, dict[str, str], bytes]]


class AmbientImageProvider:
    def __init__(
        self,
        config: AmbientImageConfig,
        *,
        environ: dict[str, str] | None = None,
        http_post: HttpPost | None = None,
        http_get: HttpGet | None = None,
    ):
        self.config = config
        self.environ = environ if environ is not None else os.environ
        self.http_post = http_post or _http_post_json
        self.http_get = http_get or _http_get

    async def generate(self, prompt: str) -> dict[str, Any]:
        unavailable = self._unavailable()
        if unavailable:
            return unavailable
        started = time.monotonic()
        timeout = max(1, int(self.config.timeout_seconds or 60))
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._generate_sync, prompt),
                timeout=timeout + 5,
            )
        except TimeoutError:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_timeout",
                "elapsed_ms": _elapsed_ms(started),
            }
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_call_failed",
                "reason": redact_ambient_image_text(str(exc), limit=240),
                "elapsed_ms": _elapsed_ms(started),
            }

    def _unavailable(self) -> dict[str, Any] | None:
        if not self.config.enabled:
            return {"ok": True, "available": False, "reason": "ambient_image_disabled"}
        api_mode = normalize_ambient_image_api_mode(self.config.api_mode)
        if not api_mode:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_api_mode_invalid",
                "api_mode": redact_ambient_image_text(self.config.api_mode, limit=40),
            }
        base_url = normalize_ambient_image_base_url(self.config.base_url)
        if not base_url:
            return {"ok": False, "available": False, "error": "ambient_image_base_url_missing"}
        api_key_env = str(self.config.api_key_env or "").strip()
        api_key = str(self.environ.get(api_key_env, "")).strip() if api_key_env else ""
        if not api_key:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_api_key_missing",
                "api_key_env": api_key_env,
            }
        return None

    def _generate_sync(self, prompt: str) -> dict[str, Any]:
        api_mode = normalize_ambient_image_api_mode(self.config.api_mode) or "images"
        base_url = normalize_ambient_image_base_url(self.config.base_url)
        api_key_env = str(self.config.api_key_env or "").strip()
        api_key = str(self.environ.get(api_key_env, "")).strip()
        endpoint = (
            "/v1/images/generations"
            if api_mode == "images"
            else "/v1/chat/completions"
        )
        url = f"{base_url}{endpoint}"
        payload = self._payload(api_mode, prompt)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        timeout = max(1, int(self.config.timeout_seconds or 60))
        started = time.monotonic()
        try:
            status, response_headers, response_body = self.http_post(url, headers, payload, timeout)
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            status = int(getattr(exc, "code", 0) or 0)
            response_headers = dict(getattr(exc, "headers", {}) or {})
        except urllib.error.URLError as exc:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_network_error",
                "reason": redact_ambient_image_text(str(exc.reason), limit=200),
                "elapsed_ms": _elapsed_ms(started),
            }

        parse_result = parse_ambient_image_response(
            response_body,
            api_mode=api_mode,
            output_format=self.config.output_format,
        )
        if parse_result.get("ok"):
            image_result = self._materialize_image(parse_result, timeout)
            if image_result.get("ok"):
                return {
                    **image_result,
                    "api_mode": api_mode,
                    "model": self.config.model,
                    "size": self.config.size,
                    "quality": self.config.quality,
                    "elapsed_ms": _elapsed_ms(started),
                }
            return {**image_result, "api_mode": api_mode, "elapsed_ms": _elapsed_ms(started)}

        if status < 200 or status >= 300:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_http_error",
                "status": status,
                "reason": _response_error_excerpt(response_body),
                "elapsed_ms": _elapsed_ms(started),
            }
        return {
            "ok": False,
            "available": False,
            "error": parse_result.get("error", "ambient_image_result_missing"),
            "reason": parse_result.get("reason", ""),
            "elapsed_ms": _elapsed_ms(started),
        }

    def _payload(self, api_mode: str, prompt: str) -> dict[str, Any]:
        model = str(self.config.model or "gpt-image-2").strip() or "gpt-image-2"
        if api_mode == "chat_completions":
            return {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }
        return {
            "model": model,
            "prompt": prompt,
            "size": str(self.config.size or "3840x2160").strip() or "3840x2160",
            "quality": str(self.config.quality or "high").strip() or "high",
            "output_format": str(self.config.output_format or "png").strip() or "png",
            "response_format": str(self.config.response_format or "url").strip() or "url",
            "n": 1,
        }

    def _materialize_image(self, parse_result: dict[str, Any], timeout: int) -> dict[str, Any]:
        if parse_result.get("b64_json"):
            try:
                image_bytes = base64.b64decode(str(parse_result["b64_json"]), validate=True)
            except Exception:
                return {
                    "ok": False,
                    "available": False,
                    "error": "ambient_image_base64_invalid",
                }
            if not image_bytes:
                return {"ok": False, "available": False, "error": "ambient_image_empty_bytes"}
            return {
                "ok": True,
                "available": True,
                "image_bytes": image_bytes,
                "extension": _safe_image_extension(self.config.output_format),
                "source": "b64_json",
            }

        image_url = str(parse_result.get("url") or "").strip()
        if not image_url:
            return {"ok": False, "available": False, "error": "ambient_image_result_missing"}
        try:
            status, headers, image_bytes = self.http_get(image_url, {"Accept": "image/*"}, timeout)
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_download_failed",
                "reason": redact_ambient_image_text(str(exc), limit=200),
                "url": image_url,
            }
        if status < 200 or status >= 300:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_download_failed",
                "status": status,
                "url": image_url,
            }
        if not image_bytes:
            return {"ok": False, "available": False, "error": "ambient_image_empty_bytes", "url": image_url}
        content_type = _headers_get(headers, "content-type")
        return {
            "ok": True,
            "available": True,
            "image_bytes": image_bytes,
            "extension": _extension_from_content_type(content_type) or _extension_from_url(image_url),
            "source": "url",
            "url": image_url,
            "content_type": content_type,
        }


def parse_ambient_image_response(
    body: bytes | str,
    *,
    api_mode: str,
    output_format: str = "png",
) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body or "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        image_url = extract_image_url(text)
        if image_url:
            return {"ok": True, "url": image_url}
        return {"ok": False, "error": "ambient_image_invalid_json"}

    if api_mode == "images":
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list) and data:
            first = data[0] if isinstance(data[0], dict) else {}
            if first.get("url"):
                return {"ok": True, "url": str(first["url"])}
            if first.get("b64_json"):
                return {"ok": True, "b64_json": str(first["b64_json"]), "extension": _safe_image_extension(output_format)}
    if api_mode == "chat_completions":
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else {}
            content = message.get("content", "") if isinstance(message, dict) else ""
            image_url = extract_image_url(str(content or ""))
            if image_url:
                return {"ok": True, "url": image_url}
        image_url = extract_image_url(text)
        if image_url:
            return {"ok": True, "url": image_url}
    return {"ok": False, "error": "ambient_image_result_missing"}


def extract_image_url(text: str) -> str:
    markdown = re.search(r"!\[[^\]]*\]\((https?://[^)\s]+)\)", text or "", flags=re.IGNORECASE)
    if markdown:
        return markdown.group(1).strip()
    generic = re.search(r"https?://[^\s)\"'<>]+", text or "", flags=re.IGNORECASE)
    return generic.group(0).strip() if generic else ""


def normalize_ambient_image_api_mode(value: str) -> str:
    normalized = str(value or "images").strip().lower().replace("-", "_")
    return normalized if normalized in AMBIENT_IMAGE_API_MODES else ""


def normalize_ambient_image_base_url(raw_base_url: str) -> str:
    raw = str(raw_base_url or "").strip() or DEFAULT_AMBIENT_IMAGE_BASE_URL
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return parsed.geturl().rstrip("/")


def redact_ambient_image_text(text: object, *, limit: int = 200) -> str:
    value = str(text or "")
    value = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)(api[_-]?key|token|authorization)\s*[:=]\s*['\"]?[^'\"\s]+", r"\1=[redacted]", value)
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[redacted]", value)
    value = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\){1,}[^\\\s]*", "[redacted-path]", value)
    value = re.sub(r"/(?:home|var|Users)/[^\s]+", "[redacted-path]", value)
    if len(value) > limit:
        return value[:limit] + "...[truncated]"
    return value


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), dict(response.headers.items()), response.read()


def _http_get(url: str, headers: dict[str, str], timeout: int) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return int(response.status), dict(response.headers.items()), response.read()


def _response_error_excerpt(body: bytes) -> str:
    return redact_ambient_image_text(body.decode("utf-8", errors="replace"), limit=300)


def _headers_get(headers: dict[str, str], key: str) -> str:
    lowered = key.lower()
    for item_key, value in headers.items():
        if str(item_key).lower() == lowered:
            return str(value)
    return ""


def _extension_from_content_type(content_type: str) -> str:
    lowered = (content_type or "").split(";")[0].strip().lower()
    if lowered == "image/png":
        return "png"
    if lowered in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if lowered == "image/webp":
        return "webp"
    return ""


def _extension_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    for extension in ("png", "jpg", "jpeg", "webp"):
        if path.endswith(f".{extension}"):
            return "jpg" if extension == "jpeg" else extension
    return "png"


def _safe_image_extension(value: str) -> str:
    normalized = str(value or "png").strip().lower().lstrip(".")
    return normalized if normalized in {"png", "jpg", "jpeg", "webp"} else "png"


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
