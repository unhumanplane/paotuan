from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

from .models import GameSession, utc_now_iso


HONCHO_MEMORY_SCHEMA_VERSION = "paotuan.external_memory.v1"
HONCHO_CLOUD_BASE_URL = "https://api.honcho.dev"
HONCHO_MEMORY_KINDS = frozenset(
    {
        "key_event",
        "memory_summary",
        "player_preference",
        "character_trait",
        "relationship_note",
        "recap",
        "dm_style_feedback",
    }
)
HONCHO_DREAM_SUGGESTION_KINDS = frozenset(
    {
        "player_preference",
        "character_trait",
        "relationship_note",
        "recap",
        "dm_style_feedback",
    }
)
_MIN_DREAM_CONFIDENCE = 0.5
_MIN_DREAM_EVIDENCE_COUNT = 2
_HONCHO_KIND_CONFIDENCE = {
    "key_event": 0.85,
    "memory_summary": 0.8,
    "player_preference": 0.7,
    "character_trait": 0.7,
    "relationship_note": 0.7,
    "recap": 0.75,
    "dm_style_feedback": 0.65,
}
_REDACTED = "[redacted]"
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(
        r"(?i)\b(?:system|developer|raw)\s+prompt\s*[:：]?[^\n\r]*",
    ),
    re.compile(r"(?i)\baudit(?:\s+record)?\s*[:：]?[^\n\r]*"),
    re.compile(
        r"(?i)\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|COOKIE)[A-Z0-9_]*\b"
    ),
    re.compile(r"(?i)\b(?:sk|gh[pousr]|xox[baprs]?)-[A-Za-z0-9_\-=]{8,}\b"),
    re.compile(r"(?i)\b[A-Z]:\\[^\s,;，。]+"),
    re.compile(r"(?i)\b[A-Z]:/[^\s,;，。]+"),
    re.compile(
        r"(?i)(?:/home|/Users|/root|/var|/etc|/tmp|/mnt|/opt)/[^\s,;，。]+"
    ),
    re.compile(
        r"(?i)\b(?:https?|ssh|postgresql|postgres|mysql|redis|mongodb)://[^\s,;，。]+"
    ),
    re.compile(r"\b(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}(?::\d+)?\b"),
    re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}(?::\d+)?\b"),
    re.compile(r"\b172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}(?::\d+)?\b"),
)
_REDACTION_VERSION = "paotuan.redaction.v1"
_SYNCED_EVENT_IDS_KEY = "_honcho_synced_event_ids"
_MAX_SYNCED_EVENT_IDS = 128
_STATE_SENSITIVE_MEMORY_TERMS = (
    "hp",
    "生命值",
    "血量",
    "物品",
    "装备",
    "inventory",
    "位置",
    "坐标",
    "position",
    "回合",
    "轮次",
    "turn",
    "initiative",
    "规则",
    "豁免",
    "命中",
    "伤害",
    "骰",
    "dc",
    "ac",
    "法术位",
    "资源",
)
_CAMPAIGN_LIFECYCLE_PHASES = frozenset(
    {
        "setup",
        "active",
        "chapter_transition",
        "interlude",
        "recap",
        "finale",
        "archived",
    }
)


@dataclass(frozen=True)
class HonchoMemoryConfig:
    enabled: bool = False
    target: str = "auto"
    workspace_id: str = ""
    api_key_env: str = "HONCHO_API_KEY"
    cloud_api_key_env: str = ""
    base_url: str = ""
    self_hosted_api_key_env: str = ""
    self_hosted_auth_enabled: bool = False
    environment: str = "production"
    timeout_seconds: int = 8
    max_context_chars: int = 1600
    write_enabled: bool = True
    read_enabled: bool = True
    assistant_peer_id: str = "paotuan_dm"
    cross_campaign_personalization_enabled: bool = False


@dataclass(frozen=True)
class HonchoMemoryIds:
    honcho_session_id: str
    player_peer_id: str
    character_peer_id: str
    assistant_peer_id: str
    campaign_id: str
    chapter_id: str
    environment: str


class HonchoExternalMemory:
    def __init__(
        self,
        config: HonchoMemoryConfig,
        *,
        environ: Mapping[str, str] | None = None,
        honcho_factory: Any | None = None,
    ):
        self.config = config
        self.environ = environ if environ is not None else os.environ
        self.honcho_factory = honcho_factory
        self._client: Any | None = None

    async def context_for_prompt(
        self,
        session: GameSession,
        actor: dict[str, str],
        query: str,
    ) -> dict[str, Any]:
        unavailable = self._unavailable(read=True)
        if unavailable:
            return unavailable
        ids = build_honcho_ids(self.config, session, actor)

        def run() -> dict[str, Any]:
            client = self._client_or_raise()
            honcho_session = client.session(ids.honcho_session_id)
            assistant = client.peer(ids.assistant_peer_id)
            scoped_contexts: list[str] = []
            context_scopes: list[str] = []
            scoped_targets = []
            if ids.player_peer_id:
                scoped_targets.append(("玩家偏好视角", "player", ids.player_peer_id))
            if ids.character_peer_id:
                scoped_targets.append(("角色知识视角", "character", ids.character_peer_id))
            if not scoped_targets:
                scoped_targets.append(("会话回忆视角", "session", ""))
            scoped_token_budget = max(256, self.config.max_context_chars // max(1, len(scoped_targets)))
            for label, scope, peer_target in scoped_targets:
                kwargs: dict[str, Any] = {
                    "summary": True,
                    "tokens": scoped_token_budget,
                }
                if peer_target:
                    kwargs.update(
                        {
                            "peer_target": peer_target,
                            "peer_perspective": ids.assistant_peer_id,
                            "search_query": redact_external_memory_text(query, limit=600),
                            "search_top_k": 8,
                            "max_conclusions": 20,
                        }
                    )
                context = honcho_session.context(**kwargs)
                scoped_text = _format_honcho_context(context, assistant=assistant)
                if scoped_text:
                    scoped_contexts.append(f"{label}：\n{scoped_text}")
                    context_scopes.append(scope)
            text = "\n\n".join(scoped_contexts)
            text, truncated = _limit_text(text, self.config.max_context_chars)
            return {
                "ok": True,
                "available": bool(text),
                "provider": "honcho",
                "context": text,
                "context_chars": len(text),
                "context_scopes": context_scopes,
                "truncated": truncated,
                "honcho_session_id": ids.honcho_session_id,
                "player_peer_id": ids.player_peer_id,
                "character_peer_id": ids.character_peer_id,
                "campaign_scope": build_campaign_memory_scope(self.config, session, actor, ids=ids),
            }

        return await self._run_honcho_call(run, operation="context")

    async def write_key_event(
        self,
        session: GameSession,
        actor: dict[str, str],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        unavailable = self._unavailable(write=True)
        if unavailable:
            return unavailable
        ids = build_honcho_ids(self.config, session, actor)
        messages = build_key_event_messages(session, actor, event, ids=ids)
        if not messages:
            return {"ok": True, "available": False, "reason": "empty_key_event"}
        source_event_id = _source_event_id_from_messages(messages)
        if source_event_id and _external_memory_event_synced(session, source_event_id):
            return {
                "ok": True,
                "available": False,
                "provider": "honcho",
                "operation": "write_key_event",
                "reason": "duplicate_external_memory_event",
                "source_event_id": source_event_id,
                "synced": False,
            }

        def run() -> dict[str, Any]:
            client = self._client_or_raise()
            honcho_session = client.session(ids.honcho_session_id)
            assistant = client.peer(ids.assistant_peer_id)
            player = client.peer(ids.player_peer_id) if ids.player_peer_id else None
            character = client.peer(ids.character_peer_id) if ids.character_peer_id else None
            peers = [peer for peer in (player, character, assistant) if peer is not None]
            if hasattr(honcho_session, "add_peers") and peers:
                honcho_session.add_peers(peers)
            honcho_messages = []
            for item in messages:
                role = (item.get("metadata") or {}).get("message_role")
                if item["speaker"] == "player" and player is not None:
                    peer = player
                elif role == "dm_key_event" and character is not None:
                    peer = character
                else:
                    peer = assistant
                honcho_messages.append(_peer_message(peer, item["content"], item["metadata"]))
            honcho_session.add_messages(honcho_messages)
            _mark_external_memory_event_synced(session, source_event_id)
            return {
                "ok": True,
                "available": True,
                "provider": "honcho",
                "operation": "write_key_event",
                "message_count": len(honcho_messages),
                "honcho_session_id": ids.honcho_session_id,
                "player_peer_id": ids.player_peer_id,
                "character_peer_id": ids.character_peer_id,
                "source_event_id": source_event_id,
                "synced": bool(source_event_id),
            }

        return await self._run_honcho_call(run, operation="write_key_event")

    async def write_memory_summary(
        self,
        session: GameSession,
        actor: dict[str, str],
        *,
        reason: str,
    ) -> dict[str, Any]:
        unavailable = self._unavailable(write=True)
        if unavailable:
            return unavailable
        content = build_memory_summary_message(session, reason=reason)
        if not content:
            return {"ok": True, "available": False, "reason": "empty_memory_summary"}
        ids = build_honcho_ids(self.config, session, actor)
        metadata = _base_metadata(
            session,
            actor,
            ids,
            kind="memory_summary",
            created_reason=reason,
            source_event_type="memory_compression",
            source_event_id=_stable_event_id(
                "memory_summary",
                session,
                actor,
                {"reason": reason, "memory_summary": session.memory_summary or ""},
            ),
            scope="chapter",
            subject_type="session",
            subject_id=ids.honcho_session_id,
        )
        metadata["reason"] = redact_external_memory_text(reason, limit=80)
        source_event_id = str(metadata.get("source_event_id") or "")
        if source_event_id and _external_memory_event_synced(session, source_event_id):
            return {
                "ok": True,
                "available": False,
                "provider": "honcho",
                "operation": "write_memory_summary",
                "reason": "duplicate_external_memory_event",
                "source_event_id": source_event_id,
                "synced": False,
            }

        def run() -> dict[str, Any]:
            client = self._client_or_raise()
            honcho_session = client.session(ids.honcho_session_id)
            assistant = client.peer(ids.assistant_peer_id)
            if hasattr(honcho_session, "add_peers"):
                honcho_session.add_peers([assistant])
            honcho_session.add_messages([_peer_message(assistant, content, metadata)])
            _mark_external_memory_event_synced(session, source_event_id)
            return {
                "ok": True,
                "available": True,
                "provider": "honcho",
                "operation": "write_memory_summary",
                "message_count": 1,
                "honcho_session_id": ids.honcho_session_id,
                "source_event_id": source_event_id,
                "synced": bool(source_event_id),
            }

        return await self._run_honcho_call(run, operation="write_memory_summary")

    def _unavailable(self, *, read: bool = False, write: bool = False) -> dict[str, Any] | None:
        if not self.config.enabled:
            return {"ok": True, "available": False, "reason": "honcho_disabled"}
        if read and not self.config.read_enabled:
            return {"ok": True, "available": False, "reason": "honcho_read_disabled"}
        if write and not self.config.write_enabled:
            return {"ok": True, "available": False, "reason": "honcho_write_disabled"}
        if not self.config.workspace_id.strip():
            return {"ok": False, "available": False, "error": "honcho_workspace_missing"}
        target, target_error = _resolve_honcho_target(self.config)
        if target_error:
            return {
                "ok": False,
                "available": False,
                "error": target_error,
                "target": redact_external_memory_text(self.config.target, limit=40),
            }
        if target == "self_hosted" and not self.config.base_url.strip():
            return {
                "ok": False,
                "available": False,
                "error": "honcho_base_url_missing",
                "target": target,
            }
        api_key_env = _honcho_api_key_env(self.config, target)
        api_key = str(self.environ.get(api_key_env, "")).strip() if api_key_env else ""
        if _honcho_api_key_required(self.config, target) and not api_key:
            return {
                "ok": False,
                "available": False,
                "error": "honcho_api_key_missing",
                "api_key_env": api_key_env,
                "target": target,
            }
        return None

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        factory = self.honcho_factory
        if factory is None:
            try:
                from honcho import Honcho as factory  # type: ignore[import-not-found]
            except ImportError as exc:
                raise HonchoUnavailable("honcho_sdk_missing") from exc
        kwargs: dict[str, Any] = {
            "workspace_id": self.config.workspace_id.strip(),
            "environment": self.config.environment.strip() or "production",
            "timeout": max(1, int(self.config.timeout_seconds)),
        }
        target, target_error = _resolve_honcho_target(self.config)
        if target_error:
            raise HonchoUnavailable(target_error)
        api_key_env = _honcho_api_key_env(self.config, target)
        api_key = str(self.environ.get(api_key_env, "")).strip() if api_key_env else ""
        if api_key:
            kwargs["api_key"] = api_key
        if target == "cloud":
            kwargs["base_url"] = HONCHO_CLOUD_BASE_URL
        elif base_url := _normalize_honcho_base_url(self.config.base_url):
            kwargs["base_url"] = base_url
        self._client = factory(**kwargs)
        return self._client

    async def _run_honcho_call(self, func: Any, *, operation: str) -> dict[str, Any]:
        started_at = time.monotonic()
        try:
            result = await asyncio.wait_for(
                _run_sync_in_thread(func),
                timeout=max(1, int(self.config.timeout_seconds)),
            )
            if isinstance(result, dict):
                safe_result = dict(result)
                safe_result.setdefault("operation", operation)
                safe_result["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
                return safe_result
            return {
                "ok": True,
                "available": True,
                "operation": operation,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }
        except HonchoUnavailable as exc:
            return {
                "ok": False,
                "available": False,
                "error": str(exc) or "honcho_unavailable",
                "operation": operation,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }
        except (asyncio.TimeoutError, TimeoutError):
            return {
                "ok": False,
                "available": False,
                "error": "honcho_timeout",
                "operation": operation,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "error": "honcho_call_failed",
                "operation": operation,
                "reason": redact_external_memory_text(str(exc), limit=240),
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }


class HonchoUnavailable(RuntimeError):
    pass


async def _run_sync_in_thread(func: Any) -> Any:
    to_thread = getattr(asyncio, "to_thread", None)
    if callable(to_thread):
        return await to_thread(func)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, func)


def _resolve_honcho_target(config: HonchoMemoryConfig) -> tuple[str, str]:
    raw = str(config.target or "auto").strip().lower().replace("-", "_")
    if not raw or raw == "auto":
        return ("self_hosted" if str(config.base_url or "").strip() else "cloud", "")
    if raw in {"cloud", "honcho_cloud"}:
        return "cloud", ""
    if raw in {"self_hosted", "selfhosted", "local", "docker"}:
        return "self_hosted", ""
    return "", "honcho_target_invalid"


def _honcho_api_key_required(config: HonchoMemoryConfig, target: str) -> bool:
    if target == "cloud":
        return True
    if target == "self_hosted":
        return bool(config.self_hosted_auth_enabled)
    return False


def _honcho_api_key_env(config: HonchoMemoryConfig, target: str) -> str:
    legacy = str(config.api_key_env or "").strip()
    if target == "cloud":
        return str(config.cloud_api_key_env or "").strip() or legacy or "HONCHO_API_KEY"
    if target == "self_hosted":
        if not config.self_hosted_auth_enabled:
            return ""
        return str(config.self_hosted_api_key_env or "").strip() or legacy or "HONCHO_API_KEY"
    return legacy or "HONCHO_API_KEY"


def _normalize_honcho_base_url(raw_base_url: str) -> str:
    raw = str(raw_base_url or "").strip()
    if not raw:
        return ""
    base_url = raw
    if "://" not in base_url:
        base_url = f"http://{base_url}"
    parsed = urlparse(base_url)
    # `urlparse` accepts URLs like `https://` and may keep a trailing `/`.
    # The SDK already resolves endpoint paths internally; removing only trailing
    # slashes avoids common user misconfiguration.
    normalized_path = parsed.path.rstrip("/")
    if normalized_path != parsed.path:
        parsed = parsed._replace(path=normalized_path)
        base_url = parsed.geturl()
    return base_url


def build_honcho_ids(
    config: HonchoMemoryConfig,
    session: GameSession,
    actor: dict[str, str],
) -> HonchoMemoryIds:
    local_session_id = _pseudonym_id("session", session.session_id)
    chapter_id = _safe_external_id(
        _first_text(
            (session.scene or {}).get("chapter_id"),
            (session.scene or {}).get("episode_id"),
            (session.world_tags or {}).get("chapter_id"),
            (session.world_tags or {}).get("episode_id"),
            "default",
        )
    )
    campaign_id = _safe_external_id(
        _first_text(
            (session.world_tags or {}).get("campaign_id"),
            (session.world_tags or {}).get("campaign"),
            "campaign",
        )
    )
    honcho_session_id = _join_id("paotuan", local_session_id, campaign_id, chapter_id)
    raw_player_id = str(actor.get("player_id", "") or "")
    if config.cross_campaign_personalization_enabled:
        player_peer_material = raw_player_id
    else:
        player_peer_material = f"{campaign_id}:{raw_player_id}" if raw_player_id else ""
    player_peer_id = _pseudonym_id("player", player_peer_material)
    character_id = ""
    if raw_player_id:
        character_id = _safe_external_id(
            (session.player_character_map or {}).get(actor.get("player_id", ""), "")
        )
    character_peer_id = f"pc_{character_id}" if character_id else ""
    return HonchoMemoryIds(
        honcho_session_id=honcho_session_id,
        player_peer_id=player_peer_id,
        character_peer_id=character_peer_id,
        assistant_peer_id=_safe_external_id(config.assistant_peer_id or "paotuan_dm"),
        campaign_id=campaign_id,
        chapter_id=chapter_id,
        environment=redact_external_memory_text(config.environment, limit=40) or "production",
    )


def build_key_event_messages(
    session: GameSession,
    actor: dict[str, str],
    event: dict[str, Any],
    *,
    ids: HonchoMemoryIds | None = None,
) -> list[dict[str, Any]]:
    ids = ids or build_honcho_ids(HonchoMemoryConfig(), session, actor)
    raw_character_id = str(event.get("character_id") or "")
    metadata = _base_metadata(
        session,
        actor,
        ids,
        kind="key_event",
        created_reason="narrative_trace",
        created_at=str(event.get("at") or ""),
        source_event_type="narrative_trace",
        source_event_id=_stable_event_id("key_event", session, actor, event),
        scope="character" if raw_character_id else "player",
        subject_type="character" if raw_character_id else "player",
        subject_id=raw_character_id or actor.get("player_id", ""),
    )
    action = redact_external_memory_text(event.get("message", ""), limit=220)
    outcome = redact_external_memory_text(event.get("outcome", ""), limit=320)
    if not action and not outcome:
        return []
    character_id = _safe_external_id(raw_character_id or metadata.get("character_id") or "")
    player_content = "\n".join(
        part
        for part in [
            "paotuan 玩家行动摘要",
            "玩家: "
            + redact_external_memory_text(
                actor.get("display_name") or actor.get("player_id", ""),
                limit=80,
            ),
            f"角色: {character_id}" if character_id else "",
            f"行动意图: {action}" if action else "",
        ]
        if part
    )
    dm_content = "\n".join(
        part
        for part in [
            "paotuan DM 关键事件裁定",
            f"团名: {redact_external_memory_text(session.title, limit=120)}",
            f"角色: {character_id}" if character_id else "",
            f"裁定结果: {outcome}" if outcome else "",
        ]
        if part
    )
    messages: list[dict[str, Any]] = []
    if player_content:
        messages.append(
            {
                "speaker": "player",
                "content": player_content,
                "metadata": {**metadata, "message_role": "player_action_summary"},
            }
        )
    if dm_content:
        messages.append(
            {
                "speaker": "assistant",
                "content": dm_content,
                "metadata": {**metadata, "message_role": "dm_key_event"},
            }
        )
    return messages


def build_memory_summary_message(session: GameSession, *, reason: str) -> str:
    summary = redact_external_memory_text(session.memory_summary or "", limit=1800)
    if not summary:
        return ""
    return "\n".join(
        [
            "paotuan 跑团记忆摘要",
            f"团名: {redact_external_memory_text(session.title, limit=120)}",
            f"原因: {redact_external_memory_text(reason, limit=80)}",
            summary,
        ]
    )


def build_campaign_memory_scope(
    config: HonchoMemoryConfig,
    session: GameSession,
    actor: dict[str, str],
    *,
    ids: HonchoMemoryIds | None = None,
) -> dict[str, Any]:
    ids = ids or build_honcho_ids(config, session, actor)
    return {
        "workspace_id": _safe_external_id(config.workspace_id or "default"),
        "environment": ids.environment,
        "local_session_id": _pseudonym_id("session", session.session_id),
        "honcho_session_id": ids.honcho_session_id,
        "campaign_id": ids.campaign_id,
        "chapter_id": ids.chapter_id,
        "lifecycle_phase": _campaign_lifecycle_phase(session),
        "player_peer_id": ids.player_peer_id,
        "character_peer_id": ids.character_peer_id,
        "assistant_peer_id": ids.assistant_peer_id,
        "cross_campaign_personalization_enabled": bool(
            config.cross_campaign_personalization_enabled
        ),
        "old_chapter_policy": "current prompt context uses current chapter; old chapters should be queried explicitly by recall tools.",
    }


def normalize_dream_suggestions(
    session: GameSession,
    actor: dict[str, str],
    suggestions: Any,
    *,
    ids: HonchoMemoryIds | None = None,
) -> list[dict[str, Any]]:
    ids = ids or build_honcho_ids(HonchoMemoryConfig(), session, actor)
    items = _coerce_dream_items(suggestions)
    normalized: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind", "") or "").strip()
        if kind not in HONCHO_DREAM_SUGGESTION_KINDS:
            continue
        content = redact_external_memory_text(
            item.get("content") or item.get("summary") or item.get("text") or "",
            limit=600,
        )
        if not content:
            continue
        evidence_count = _safe_int(
            item.get("evidence_count", item.get("support_count", item.get("observations", 0))),
        )
        confidence = _safe_float(item.get("confidence", _confidence_for_kind(kind)))
        if confidence < _MIN_DREAM_CONFIDENCE or evidence_count < _MIN_DREAM_EVIDENCE_COUNT:
            continue
        metadata = _base_metadata(
            session,
            actor,
            ids,
            kind=kind,
            created_reason="honcho_dream_review",
            created_at=str(item.get("created_at") or ""),
            source_event_type="honcho_dream",
            source_event_id=_stable_event_id("honcho_dream", session, actor, item),
            scope=str(item.get("scope") or "player"),
            subject_type=str(item.get("subject_type") or "player"),
            subject_id=str(item.get("subject_id") or actor.get("player_id", "")),
        )
        metadata.update(
            {
                "confidence": confidence,
                "evidence_count": evidence_count,
                "dream_review_required": True,
                "non_authoritative": True,
            }
        )
        normalized.append(
            {
                "ok": True,
                "kind": kind,
                "content": content,
                "metadata": metadata,
                "review_status": "pending",
                "non_authoritative": True,
            }
        )
    return normalized


def audit_safe_external_memory_result(result: dict[str, Any]) -> dict[str, Any]:
    safe = dict(result)
    safe.pop("context", None)
    safe.pop("content", None)
    if isinstance(safe.get("campaign_scope"), Mapping):
        safe["campaign_scope"] = _campaign_scope_observation(safe["campaign_scope"])
    if "error" in safe:
        safe["error"] = redact_external_memory_text(safe["error"], limit=120)
    if "reason" in safe:
        safe["reason"] = redact_external_memory_text(safe["reason"], limit=240)
    return safe


def external_memory_observation(result: Mapping[str, Any]) -> dict[str, Any]:
    safe = audit_safe_external_memory_result(dict(result))
    observation: dict[str, Any] = {
        "status": _external_memory_status(safe),
        "safe_fields_only": True,
    }
    for key in (
        "ok",
        "available",
        "provider",
        "operation",
        "error",
        "reason",
        "context_chars",
        "context_scopes",
        "truncated",
        "message_count",
        "source_event_id",
        "synced",
        "elapsed_ms",
        "campaign_scope",
    ):
        if key in safe:
            observation[key] = safe[key]
    if "context_chars" not in observation and result.get("context"):
        observation["context_chars"] = len(str(result.get("context") or ""))
    context = str(result.get("context") or "")
    if context:
        observation["external_context_present"] = True
        observation["state_sensitive_context"] = "状态敏感线索" in context
    return observation


def _external_memory_status(result: Mapping[str, Any]) -> str:
    if not result.get("ok", True):
        return "failed"
    if result.get("available"):
        return "success"
    if result.get("reason") == "duplicate_external_memory_event":
        return "skipped_duplicate"
    return "skipped"


def _campaign_scope_observation(scope: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lifecycle_phase": redact_external_memory_text(
            scope.get("lifecycle_phase", ""),
            limit=40,
        ),
        "old_chapter_policy": redact_external_memory_text(
            scope.get("old_chapter_policy", ""),
            limit=80,
        ),
        "cross_campaign_personalization_enabled": bool(
            scope.get("cross_campaign_personalization_enabled", False)
        ),
        "has_honcho_session": bool(scope.get("honcho_session_id")),
        "has_player_peer": bool(scope.get("player_peer_id")),
        "has_character_peer": bool(scope.get("character_peer_id")),
        "has_assistant_peer": bool(scope.get("assistant_peer_id")),
    }


def _base_metadata(
    session: GameSession,
    actor: dict[str, str],
    ids: HonchoMemoryIds,
    *,
    kind: str,
    created_reason: str = "",
    created_at: str = "",
    source_event_type: str = "",
    source_event_id: str = "",
    scope: str = "",
    subject_type: str = "",
    subject_id: str = "",
) -> dict[str, Any]:
    raw_player_id = str(actor.get("player_id", ""))
    raw_character_id = str((session.player_character_map or {}).get(raw_player_id, ""))
    player_id = ids.player_peer_id
    character_id = _safe_external_id(raw_character_id) if raw_character_id else ""
    subject_type = redact_external_memory_text(
        subject_type or ("character" if character_id else "player"),
        limit=40,
    )
    if subject_type == "player":
        subject_id = ids.player_peer_id
    elif subject_type == "session":
        subject_id = (
            _safe_external_id(subject_id)
            if subject_id
            else _pseudonym_id("session", session.session_id)
        )
    else:
        subject_id = _safe_external_id(subject_id or raw_character_id or raw_player_id)
    return {
        "schema_version": HONCHO_MEMORY_SCHEMA_VERSION,
        "source": "paotuan",
        "kind": kind,
        "environment": redact_external_memory_text(ids.environment, limit=40) or "production",
        "local_session_id": _pseudonym_id("session", session.session_id),
        "honcho_session_id": ids.honcho_session_id,
        "campaign_id": ids.campaign_id,
        "chapter_id": ids.chapter_id,
        "player_id": player_id,
        "character_id": character_id,
        "created_by": "paotuan_dm",
        "created_by_peer_id": ids.assistant_peer_id,
        "created_reason": redact_external_memory_text(created_reason or kind, limit=80),
        "created_at": redact_external_memory_text(created_at or utc_now_iso(), limit=80),
        "source_event_type": redact_external_memory_text(source_event_type or kind, limit=80),
        "source_event_id": _safe_external_id(
            source_event_id or _stable_event_id(kind, session, actor, {})
        ),
        "scope": redact_external_memory_text(scope or "player", limit=40),
        "subject_type": subject_type,
        "subject_id": subject_id,
        "player_peer_id": ids.player_peer_id,
        "character_peer_id": ids.character_peer_id,
        "assistant_peer_id": ids.assistant_peer_id,
        "confidence": _confidence_for_kind(kind),
        "redaction_version": _REDACTION_VERSION,
    }


def _peer_message(peer: Any, content: str, metadata: dict[str, Any]) -> Any:
    try:
        return peer.message(content, metadata=metadata)
    except TypeError:
        return peer.message(content)


def _format_honcho_context(context: Any, *, assistant: Any) -> str:
    memory_parts: list[str] = []
    state_sensitive_parts: list[str] = []

    def add_entry(label: str, content: Any, metadata: dict[str, Any] | None = None) -> None:
        text = redact_external_memory_text(content, limit=500)
        if not text:
            return
        entry = f"- [{label}] {text}"
        if _is_state_sensitive_memory(text, metadata or {}):
            state_sensitive_parts.append(entry)
        else:
            memory_parts.append(entry)

    peer_representation = redact_external_memory_text(
        getattr(context, "peer_representation", "") or "",
        limit=1200,
    )
    if peer_representation:
        add_entry("role=representation", peer_representation)
    peer_card = getattr(context, "peer_card", None)
    if peer_card:
        add_entry("role=peer_card", peer_card)
    if hasattr(context, "to_openai"):
        try:
            messages = context.to_openai(assistant=assistant)
        except TypeError:
            messages = context.to_openai()
        for item in list(messages)[-8:]:
            if isinstance(item, dict):
                role = str(item.get("role", "memory"))
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                label = _external_context_label(role, metadata)
                add_entry(label, item.get("content", ""), metadata)
    if not memory_parts and not state_sensitive_parts:
        text = redact_external_memory_text(context, limit=1200)
        if text and text != object.__repr__(context):
            add_entry("role=memory", text)
    if not memory_parts and not state_sensitive_parts:
        return ""
    sections = [
        (
            "使用边界：优先用于玩家偏好、角色倾向、关系、recap、伏笔和叙事风格；"
            "不得用来覆盖本地 HP、物品、位置、轮次、规则、骰子或工具结果。"
        )
    ]
    if memory_parts:
        sections.append("可用回忆线索：\n" + "\n".join(memory_parts))
    if state_sensitive_parts:
        sections.append(
            "状态敏感线索（只作历史回忆；若与本地状态、工具或规则结果冲突必须忽略）：\n"
            + "\n".join(state_sensitive_parts)
        )
    return "\n\n".join(sections).strip()


def _limit_text(text: str, limit: int) -> tuple[str, bool]:
    limit = max(0, int(limit))
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]", True


def redact_external_memory_text(value: Any, *, limit: int = 0) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    for pattern in _SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    if limit > 0:
        return _short(text, limit)
    return text


def _contains_sensitive_text(value: Any) -> bool:
    text = str(value or "")
    return any(pattern.search(text) for pattern in _SENSITIVE_TEXT_PATTERNS)


def _pseudonym_id(prefix: str, value: Any, max_chars: int = 80) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    safe_prefix = _safe_id(prefix, max_chars=24)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{safe_prefix}_{digest}"[:max_chars]


def _safe_external_id(value: Any, max_chars: int = 80) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if _contains_sensitive_text(raw):
        return _pseudonym_id("id", raw, max_chars=max_chars)
    safe = _safe_id(redact_external_memory_text(raw), max_chars=max_chars)
    if safe == _safe_id(_REDACTED, max_chars=max_chars):
        return _pseudonym_id("id", raw, max_chars=max_chars)
    return safe


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


def _confidence_for_kind(kind: str) -> float:
    return _HONCHO_KIND_CONFIDENCE.get(kind, 0.5)


def _stable_event_id(
    kind: str,
    session: GameSession,
    actor: dict[str, str],
    event: dict[str, Any],
) -> str:
    event_items = sorted((str(key), str(value)) for key, value in event.items())
    material = repr(
        {
            "kind": kind,
            "session": session.session_id,
            "player": actor.get("player_id", ""),
            "event": event_items,
        }
    )
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
    return f"{_safe_external_id(kind, max_chars=32)}_{digest}"


def _source_event_id_from_messages(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    metadata = messages[0].get("metadata") or {}
    return str(metadata.get("source_event_id") or "")


def _external_memory_event_synced(session: GameSession, source_event_id: str) -> bool:
    synced_ids = (session.scene or {}).get(_SYNCED_EVENT_IDS_KEY) or []
    return source_event_id in set(str(item) for item in synced_ids)


def _mark_external_memory_event_synced(session: GameSession, source_event_id: str) -> None:
    if not source_event_id:
        return
    synced_ids = [str(item) for item in (session.scene or {}).get(_SYNCED_EVENT_IDS_KEY) or []]
    if source_event_id not in synced_ids:
        synced_ids.append(source_event_id)
    session.scene[_SYNCED_EVENT_IDS_KEY] = synced_ids[-_MAX_SYNCED_EVENT_IDS:]


def _coerce_dream_items(suggestions: Any) -> list[dict[str, Any]]:
    if isinstance(suggestions, dict):
        for key in ("suggestions", "items", "dreams", "insights"):
            value = suggestions.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        return [suggestions]
    if isinstance(suggestions, list):
        return [dict(item) for item in suggestions if isinstance(item, dict)]
    return []


def _campaign_lifecycle_phase(session: GameSession) -> str:
    raw_phase = _first_text(
        (session.scene or {}).get("lifecycle_phase"),
        (session.scene or {}).get("campaign_phase"),
        (session.world_tags or {}).get("lifecycle_phase"),
        (session.world_tags or {}).get("campaign_phase"),
        "active",
    ).lower()
    phase = _safe_external_id(raw_phase, max_chars=40)
    return phase if phase in _CAMPAIGN_LIFECYCLE_PHASES else "active"


def _external_context_label(role: str, metadata: dict[str, Any]) -> str:
    fields = [f"role={_safe_external_id(role, max_chars=40) or 'memory'}"]
    for key in ("kind", "scope", "source", "created_at"):
        value = redact_external_memory_text(metadata.get(key, ""), limit=80)
        if value:
            fields.append(f"{key}={value}")
    return ", ".join(fields)


def _is_state_sensitive_memory(text: str, metadata: dict[str, Any]) -> bool:
    combined = " ".join(
        [
            str(text or ""),
            str(metadata.get("kind", "")),
            str(metadata.get("scope", "")),
            str(metadata.get("subject_type", "")),
        ]
    ).lower()
    return any(term.lower() in combined for term in _STATE_SENSITIVE_MEMORY_TERMS)


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _join_id(*parts: str) -> str:
    return "_".join(part for part in parts if part)


def _safe_id(value: str, max_chars: int = 80) -> str:
    raw = str(value or "").strip()
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("._")
    if not safe and raw:
        safe = "id_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return (safe or "default")[:max_chars]


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
