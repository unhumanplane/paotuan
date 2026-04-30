from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .models import GameSession


@dataclass(frozen=True)
class HonchoMemoryConfig:
    enabled: bool = False
    workspace_id: str = ""
    api_key_env: str = "HONCHO_API_KEY"
    base_url: str = ""
    environment: str = "production"
    timeout_seconds: int = 8
    max_context_chars: int = 1600
    write_enabled: bool = True
    read_enabled: bool = True
    assistant_peer_id: str = "paotuan_dm"


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
            kwargs: dict[str, Any] = {
                "summary": True,
                "tokens": max(256, self.config.max_context_chars // 2),
            }
            if ids.player_peer_id:
                kwargs.update(
                    {
                        "peer_target": ids.player_peer_id,
                        "peer_perspective": ids.assistant_peer_id,
                        "search_query": _short(query, 600),
                        "search_top_k": 8,
                        "max_conclusions": 20,
                    }
                )
            context = honcho_session.context(**kwargs)
            text = _format_honcho_context(context, assistant=assistant)
            text, truncated = _limit_text(text, self.config.max_context_chars)
            return {
                "ok": True,
                "available": bool(text),
                "provider": "honcho",
                "context": text,
                "context_chars": len(text),
                "truncated": truncated,
                "honcho_session_id": ids.honcho_session_id,
                "player_peer_id": ids.player_peer_id,
                "character_peer_id": ids.character_peer_id,
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

        def run() -> dict[str, Any]:
            client = self._client_or_raise()
            honcho_session = client.session(ids.honcho_session_id)
            assistant = client.peer(ids.assistant_peer_id)
            player = client.peer(ids.player_peer_id) if ids.player_peer_id else None
            peers = [peer for peer in (player, assistant) if peer is not None]
            if hasattr(honcho_session, "add_peers") and peers:
                honcho_session.add_peers(peers)
            honcho_messages = []
            for item in messages:
                peer = player if item["speaker"] == "player" and player is not None else assistant
                honcho_messages.append(_peer_message(peer, item["content"], item["metadata"]))
            honcho_session.add_messages(honcho_messages)
            return {
                "ok": True,
                "available": True,
                "provider": "honcho",
                "operation": "write_key_event",
                "message_count": len(honcho_messages),
                "honcho_session_id": ids.honcho_session_id,
                "player_peer_id": ids.player_peer_id,
                "character_peer_id": ids.character_peer_id,
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
        metadata = _base_metadata(session, actor, ids, kind="memory_summary")
        metadata["reason"] = reason

        def run() -> dict[str, Any]:
            client = self._client_or_raise()
            honcho_session = client.session(ids.honcho_session_id)
            assistant = client.peer(ids.assistant_peer_id)
            if hasattr(honcho_session, "add_peers"):
                honcho_session.add_peers([assistant])
            honcho_session.add_messages([_peer_message(assistant, content, metadata)])
            return {
                "ok": True,
                "available": True,
                "provider": "honcho",
                "operation": "write_memory_summary",
                "message_count": 1,
                "honcho_session_id": ids.honcho_session_id,
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
        if self.honcho_factory is None:
            api_key_env = self.config.api_key_env.strip() or "HONCHO_API_KEY"
            if not str(self.environ.get(api_key_env, "")).strip():
                return {
                    "ok": False,
                    "available": False,
                    "error": "honcho_api_key_missing",
                    "api_key_env": api_key_env,
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
        api_key_env = self.config.api_key_env.strip() or "HONCHO_API_KEY"
        api_key = str(self.environ.get(api_key_env, "")).strip()
        if api_key:
            kwargs["api_key"] = api_key
        if self.config.base_url.strip():
            kwargs["base_url"] = self.config.base_url.strip()
        self._client = factory(**kwargs)
        return self._client

    async def _run_honcho_call(self, func: Any, *, operation: str) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func),
                timeout=max(1, int(self.config.timeout_seconds)),
            )
        except HonchoUnavailable as exc:
            return {
                "ok": False,
                "available": False,
                "error": str(exc) or "honcho_unavailable",
                "operation": operation,
            }
        except (asyncio.TimeoutError, TimeoutError):
            return {
                "ok": False,
                "available": False,
                "error": "honcho_timeout",
                "operation": operation,
            }
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "error": "honcho_call_failed",
                "operation": operation,
                "reason": _short(str(exc), 240),
            }


class HonchoUnavailable(RuntimeError):
    pass


def build_honcho_ids(
    config: HonchoMemoryConfig,
    session: GameSession,
    actor: dict[str, str],
) -> HonchoMemoryIds:
    local_session_id = _safe_id(session.session_id)
    chapter_id = _safe_id(
        _first_text(
            (session.scene or {}).get("chapter_id"),
            (session.scene or {}).get("episode_id"),
            (session.world_tags or {}).get("chapter_id"),
            (session.world_tags or {}).get("episode_id"),
            "default",
        )
    )
    campaign_id = _safe_id(
        _first_text(
            (session.world_tags or {}).get("campaign_id"),
            (session.world_tags or {}).get("campaign"),
            "campaign",
        )
    )
    honcho_session_id = _join_id("paotuan", local_session_id, campaign_id, chapter_id)
    player_id = _safe_id(actor.get("player_id", ""))
    player_peer_id = f"player_{player_id}" if player_id else ""
    character_id = ""
    if player_id:
        character_id = _safe_id((session.player_character_map or {}).get(actor.get("player_id", ""), ""))
    character_peer_id = f"pc_{character_id}" if character_id else ""
    return HonchoMemoryIds(
        honcho_session_id=honcho_session_id,
        player_peer_id=player_peer_id,
        character_peer_id=character_peer_id,
        assistant_peer_id=_safe_id(config.assistant_peer_id or "paotuan_dm"),
        campaign_id=campaign_id,
        chapter_id=chapter_id,
        environment=config.environment.strip() or "production",
    )


def build_key_event_messages(
    session: GameSession,
    actor: dict[str, str],
    event: dict[str, Any],
    *,
    ids: HonchoMemoryIds | None = None,
) -> list[dict[str, Any]]:
    ids = ids or build_honcho_ids(HonchoMemoryConfig(), session, actor)
    metadata = _base_metadata(session, actor, ids, kind="key_event")
    action = _short(str(event.get("message", "")), 220)
    outcome = _short(str(event.get("outcome", "")), 320)
    if not action and not outcome:
        return []
    character_id = str(event.get("character_id") or metadata.get("character_id") or "")
    player_content = "\n".join(
        part
        for part in [
            "paotuan 玩家行动摘要",
            f"玩家: {_short(actor.get('display_name') or actor.get('player_id', ''), 80)}",
            f"角色: {character_id}" if character_id else "",
            f"行动意图: {action}" if action else "",
        ]
        if part
    )
    dm_content = "\n".join(
        part
        for part in [
            "paotuan DM 关键事件裁定",
            f"团名: {_short(session.title, 120)}",
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
    summary = _short(str(session.memory_summary or ""), 1800)
    if not summary:
        return ""
    return "\n".join(
        [
            "paotuan 跑团记忆摘要",
            f"团名: {_short(session.title, 120)}",
            f"原因: {_short(reason, 80)}",
            summary,
        ]
    )


def audit_safe_external_memory_result(result: dict[str, Any]) -> dict[str, Any]:
    safe = dict(result)
    safe.pop("context", None)
    safe.pop("content", None)
    if "reason" in safe:
        safe["reason"] = _short(str(safe["reason"]), 240)
    return safe


def _base_metadata(
    session: GameSession,
    actor: dict[str, str],
    ids: HonchoMemoryIds,
    *,
    kind: str,
) -> dict[str, Any]:
    player_id = str(actor.get("player_id", ""))
    character_id = str((session.player_character_map or {}).get(player_id, ""))
    return {
        "source": "paotuan",
        "kind": kind,
        "environment": ids.environment,
        "local_session_id": session.session_id,
        "honcho_session_id": ids.honcho_session_id,
        "campaign_id": ids.campaign_id,
        "chapter_id": ids.chapter_id,
        "player_id": player_id,
        "character_id": character_id,
        "created_by": "paotuan_dm",
    }


def _peer_message(peer: Any, content: str, metadata: dict[str, Any]) -> Any:
    try:
        return peer.message(content, metadata=metadata)
    except TypeError:
        return peer.message(content)


def _format_honcho_context(context: Any, *, assistant: Any) -> str:
    parts: list[str] = []
    peer_representation = str(getattr(context, "peer_representation", "") or "").strip()
    if peer_representation:
        parts.append("Peer representation:\n" + peer_representation)
    peer_card = getattr(context, "peer_card", None)
    if peer_card:
        parts.append("Peer card:\n" + _short(str(peer_card), 800))
    if hasattr(context, "to_openai"):
        try:
            messages = context.to_openai(assistant=assistant)
        except TypeError:
            messages = context.to_openai()
        for item in list(messages)[-8:]:
            if isinstance(item, dict):
                role = str(item.get("role", "memory"))
                content = _short(str(item.get("content", "")), 500)
                if content:
                    parts.append(f"{role}: {content}")
    if not parts:
        text = str(context).strip()
        if text and text != object.__repr__(context):
            parts.append(text)
    return "\n\n".join(parts).strip()


def _limit_text(text: str, limit: int) -> tuple[str, bool]:
    limit = max(0, int(limit))
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]", True


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
