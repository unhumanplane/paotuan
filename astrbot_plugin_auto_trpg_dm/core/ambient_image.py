from __future__ import annotations

import asyncio
import base64
from functools import partial
import ipaddress
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple


AMBIENT_IMAGE_API_MODES = {"images", "chat_completions"}
DEFAULT_AMBIENT_IMAGE_BASE_URL = "https://www.packyapi.com"
DEFAULT_AMBIENT_IMAGE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
MAX_AMBIENT_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AMBIENT_IMAGE_RESPONSE_BYTES = 30 * 1024 * 1024


@dataclass
class AmbientImageConfig:
    enabled: bool = False
    api_mode: str = "images"
    base_url: str = DEFAULT_AMBIENT_IMAGE_BASE_URL
    api_key: str = ""
    api_key_env: str = "PACKYAPI_SORA_API_KEY"
    user_agent: str = DEFAULT_AMBIENT_IMAGE_USER_AGENT
    model: str = "gpt-image-2"
    prompt_model: str = ""
    size: str = "1536x1024"
    quality: str = "medium"
    output_format: str = "png"
    response_format: str = "url"
    timeout_seconds: int = 120
    send_to_chat: bool = True
    frequency: str = "medium"
    prompt_template: str = ""
    activity_window_minutes: int = 60
    activity_min_messages: int = 10
    activity_min_players: int = 2
    similarity_recent_count: int = 3
    similarity_threshold: float = 0.82
    similarity_retry_enabled: bool = True


HttpPost = Callable[[str, Dict[str, str], Dict[str, Any], int], Tuple[int, Dict[str, str], bytes]]
HttpGet = Callable[[str, Dict[str, str], int], Tuple[int, Dict[str, str], bytes]]
DnsResolver = Callable[[str, int], List[str]]


class AmbientImageSizeLimitError(Exception):
    def __init__(
        self,
        *,
        source: str,
        max_bytes: int,
        actual_bytes: int | None = None,
        content_length: int | None = None,
    ):
        self.source = source
        self.max_bytes = max_bytes
        self.actual_bytes = actual_bytes
        self.content_length = content_length
        super().__init__(source)


class AmbientImageUrlBlockedError(Exception):
    def __init__(self, url: str, check: dict[str, Any]):
        self.url = url
        self.error = str(check.get("error") or "ambient_image_url_blocked")
        self.reason = str(check.get("reason") or "")
        super().__init__(self.reason or self.error)


class AmbientImageProvider:
    def __init__(
        self,
        config: AmbientImageConfig,
        *,
        environ: dict[str, str] | None = None,
        http_post: HttpPost | None = None,
        http_get: HttpGet | None = None,
        dns_resolver: DnsResolver | None = None,
    ):
        self.config = config
        self.environ = environ if environ is not None else os.environ
        self.http_post = http_post or _http_post_json
        self.http_get = http_get or _http_get
        self.dns_resolver = dns_resolver or _resolve_host_ips

    async def generate(self, prompt: str) -> dict[str, Any]:
        unavailable = self._unavailable()
        if unavailable:
            return unavailable
        started = time.monotonic()
        timeout = max(1, int(self.config.timeout_seconds or 120))
        try:
            return await asyncio.wait_for(
                _run_sync_in_thread(self._generate_sync, prompt),
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
        api_key, api_key_source, api_key_env = self._api_key()
        if not api_key:
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_api_key_missing",
                "api_key_env": api_key_env,
                "api_key_source": api_key_source,
            }
        return None

    def _generate_sync(self, prompt: str) -> dict[str, Any]:
        api_mode = normalize_ambient_image_api_mode(self.config.api_mode) or "images"
        base_url = normalize_ambient_image_base_url(self.config.base_url)
        api_key, _, _ = self._api_key()
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
            "User-Agent": self._user_agent(),
            "Authorization": f"Bearer {api_key}",
        }
        timeout = max(1, int(self.config.timeout_seconds or 120))
        started = time.monotonic()
        try:
            status, response_headers, response_body = self.http_post(url, headers, payload, timeout)
        except AmbientImageSizeLimitError as exc:
            return _too_large_result(exc, elapsed_ms=_elapsed_ms(started))
        except urllib.error.HTTPError as exc:
            try:
                response_body = _read_limited_body(
                    exc,
                    headers=dict(getattr(exc, "headers", {}) or {}),
                    max_bytes=MAX_AMBIENT_IMAGE_RESPONSE_BYTES,
                    source="provider_response",
                )
            except AmbientImageSizeLimitError as size_exc:
                return _too_large_result(size_exc, elapsed_ms=_elapsed_ms(started))
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
            "size": str(self.config.size or "1536x1024").strip() or "1536x1024",
            "quality": str(self.config.quality or "medium").strip() or "medium",
            "output_format": str(self.config.output_format or "png").strip() or "png",
            "response_format": str(self.config.response_format or "url").strip() or "url",
            "n": 1,
        }

    def _api_key(self) -> tuple[str, str, str]:
        direct_key = str(getattr(self.config, "api_key", "") or "").strip()
        if direct_key:
            return direct_key, "config", ""
        api_key_env = str(self.config.api_key_env or "").strip()
        if not api_key_env:
            return "", "missing", ""
        if _looks_like_env_var_name(api_key_env):
            return str(self.environ.get(api_key_env, "")).strip(), "env", api_key_env
        # Backward-compatible escape hatch for users who pasted the key into
        # the old env-name field from the AstrBot UI.
        return api_key_env, "config_legacy_api_key_env", ""

    def _user_agent(self) -> str:
        return (
            str(getattr(self.config, "user_agent", "") or DEFAULT_AMBIENT_IMAGE_USER_AGENT).strip()
            or DEFAULT_AMBIENT_IMAGE_USER_AGENT
        )

    def _materialize_image(self, parse_result: dict[str, Any], timeout: int) -> dict[str, Any]:
        if parse_result.get("b64_json"):
            b64_text = str(parse_result["b64_json"])
            if _estimated_base64_bytes(b64_text) > MAX_AMBIENT_IMAGE_BYTES:
                return _too_large_result(
                    AmbientImageSizeLimitError(
                        source="b64_json",
                        max_bytes=MAX_AMBIENT_IMAGE_BYTES,
                    )
                )
            try:
                image_bytes = base64.b64decode(b64_text, validate=True)
            except Exception:
                return {
                    "ok": False,
                    "available": False,
                    "error": "ambient_image_base64_invalid",
                }
            if not image_bytes:
                return {"ok": False, "available": False, "error": "ambient_image_empty_bytes"}
            if len(image_bytes) > MAX_AMBIENT_IMAGE_BYTES:
                return _too_large_result(
                    AmbientImageSizeLimitError(
                        source="b64_json",
                        max_bytes=MAX_AMBIENT_IMAGE_BYTES,
                        actual_bytes=len(image_bytes),
                    )
                )
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
        url_check = validate_ambient_image_url(image_url, resolver=self.dns_resolver)
        if not url_check.get("ok"):
            return {
                "ok": False,
                "available": False,
                "error": url_check.get("error", "ambient_image_url_blocked"),
                "reason": url_check.get("reason", ""),
                "url": image_url,
            }
        try:
            status, headers, image_bytes = self.http_get(
                image_url,
                {"Accept": "image/*", "User-Agent": self._user_agent()},
                timeout,
            )
        except AmbientImageSizeLimitError as exc:
            return {**_too_large_result(exc), "url": image_url}
        except AmbientImageUrlBlockedError as exc:
            return {
                "ok": False,
                "available": False,
                "error": exc.error,
                "reason": exc.reason,
                "url": exc.url,
            }
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
        content_length = _content_length(headers)
        if content_length is not None and content_length > MAX_AMBIENT_IMAGE_BYTES:
            return {
                **_too_large_result(
                    AmbientImageSizeLimitError(
                        source="download",
                        max_bytes=MAX_AMBIENT_IMAGE_BYTES,
                        content_length=content_length,
                    )
                ),
                "url": image_url,
            }
        if len(image_bytes) > MAX_AMBIENT_IMAGE_BYTES:
            return {
                **_too_large_result(
                    AmbientImageSizeLimitError(
                        source="download",
                        max_bytes=MAX_AMBIENT_IMAGE_BYTES,
                        actual_bytes=len(image_bytes),
                    )
                ),
                "url": image_url,
            }
        if content_type and not _is_image_content_type(content_type):
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_content_type_invalid",
                "content_type": content_type,
                "url": image_url,
            }
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


def validate_ambient_image_url(url: str, *, resolver: DnsResolver | None = None) -> dict[str, Any]:
    raw_url = str(url or "").strip()
    if not raw_url:
        return {"ok": False, "error": "ambient_image_url_invalid", "reason": "empty_url"}
    try:
        parsed = urllib.parse.urlparse(raw_url)
        port = parsed.port
    except ValueError:
        return {"ok": False, "error": "ambient_image_url_invalid", "reason": "invalid_port"}
    if parsed.scheme.lower() not in {"http", "https"}:
        return {"ok": False, "error": "ambient_image_url_invalid", "reason": "unsupported_scheme"}
    if parsed.username or parsed.password:
        return {"ok": False, "error": "ambient_image_url_invalid", "reason": "userinfo_not_allowed"}
    host = (parsed.hostname or "").strip().strip(".").lower()
    if not host:
        return {"ok": False, "error": "ambient_image_url_invalid", "reason": "missing_host"}
    if host == "localhost" or host.endswith(".localhost"):
        return {"ok": False, "error": "ambient_image_url_blocked", "reason": "localhost_blocked"}
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        resolve = resolver or _resolve_host_ips
        try:
            addresses = resolve(host, port or (443 if parsed.scheme.lower() == "https" else 80))
        except OSError:
            return {
                "ok": False,
                "error": "ambient_image_url_resolution_failed",
                "reason": "dns_resolution_failed",
            }
        except Exception:
            return {
                "ok": False,
                "error": "ambient_image_url_resolution_failed",
                "reason": "dns_resolution_failed",
            }
        if not addresses:
            return {
                "ok": False,
                "error": "ambient_image_url_resolution_failed",
                "reason": "dns_resolution_empty",
            }
        for address in addresses:
            try:
                resolved_ip = ipaddress.ip_address(str(address))
            except ValueError:
                return {
                    "ok": False,
                    "error": "ambient_image_url_resolution_failed",
                    "reason": "dns_resolution_invalid",
                }
            if _blocked_image_ip(resolved_ip):
                return {
                    "ok": False,
                    "error": "ambient_image_url_blocked",
                    "reason": "resolved_ip_blocked",
                }
        return {"ok": True}
    if _blocked_image_ip(ip):
        return {"ok": False, "error": "ambient_image_url_blocked", "reason": "ip_blocked"}
    return {"ok": True}


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


def _looks_like_env_var_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", str(value or "").strip()))


def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_headers = dict(response.headers.items())
        response_body = _read_limited_body(
            response,
            headers=response_headers,
            max_bytes=MAX_AMBIENT_IMAGE_RESPONSE_BYTES,
            source="provider_response",
        )
        return int(response.status), response_headers, response_body


def _http_get(url: str, headers: dict[str, str], timeout: int) -> tuple[int, dict[str, str], bytes]:
    current_url = url
    redirects = 0
    while True:
        request = urllib.request.Request(current_url, headers=headers, method="GET")
        try:
            with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
                response_headers = dict(response.headers.items())
                response_body = _read_limited_body(
                    response,
                    headers=response_headers,
                    max_bytes=MAX_AMBIENT_IMAGE_BYTES,
                    source="download",
                )
                return int(response.status), response_headers, response_body
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            response_headers = _headers_dict(getattr(exc, "headers", None))
            location = _headers_get(response_headers, "location")
            if status not in {301, 302, 303, 307, 308} or not location:
                raise
            redirects += 1
            if redirects > 3:
                raise AmbientImageUrlBlockedError(
                    current_url,
                    {"error": "ambient_image_redirect_blocked", "reason": "too_many_redirects"},
                ) from exc
            next_url = urllib.parse.urljoin(current_url, location)
            url_check = validate_ambient_image_url(next_url)
            if not url_check.get("ok"):
                raise AmbientImageUrlBlockedError(next_url, url_check) from exc
            current_url = next_url


def _response_error_excerpt(body: bytes) -> str:
    return redact_ambient_image_text(body.decode("utf-8", errors="replace"), limit=300)


def _headers_get(headers: dict[str, str], key: str) -> str:
    lowered = key.lower()
    for item_key, value in headers.items():
        if str(item_key).lower() == lowered:
            return str(value)
    return ""


def _headers_dict(headers: Any) -> dict[str, str]:
    if not headers or not hasattr(headers, "items"):
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def _content_length(headers: dict[str, str]) -> int | None:
    raw_value = _headers_get(headers, "content-length")
    if not raw_value:
        return None
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _read_limited_body(response: Any, *, headers: dict[str, str], max_bytes: int, source: str) -> bytes:
    content_length = _content_length(headers)
    if content_length is not None and content_length > max_bytes:
        raise AmbientImageSizeLimitError(
            source=source,
            max_bytes=max_bytes,
            content_length=content_length,
        )
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise AmbientImageSizeLimitError(
            source=source,
            max_bytes=max_bytes,
            actual_bytes=len(body),
            content_length=content_length,
        )
    return body


def _too_large_result(exc: AmbientImageSizeLimitError, *, elapsed_ms: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "available": False,
        "error": "ambient_image_too_large",
        "source": exc.source,
        "max_bytes": exc.max_bytes,
    }
    if exc.actual_bytes is not None:
        result["actual_bytes"] = exc.actual_bytes
    if exc.content_length is not None:
        result["content_length"] = exc.content_length
    if elapsed_ms is not None:
        result["elapsed_ms"] = elapsed_ms
    return result


def _estimated_base64_bytes(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    padding = len(text) - len(text.rstrip("="))
    return max(0, (len(text) * 3) // 4 - padding)


def _resolve_host_ips(host: str, port: int) -> list[str]:
    addresses: list[str] = []
    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sockaddr = item[4]
        if sockaddr:
            addresses.append(str(sockaddr[0]))
    return addresses


def _blocked_image_ip(address: ipaddress._BaseAddress) -> bool:
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_image_content_type(content_type: str) -> bool:
    return (content_type or "").split(";")[0].strip().lower().startswith("image/")


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


async def _run_sync_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    to_thread = getattr(asyncio, "to_thread", None)
    if callable(to_thread):
        return await to_thread(func, *args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler)
