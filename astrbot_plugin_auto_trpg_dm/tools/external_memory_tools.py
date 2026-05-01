from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.external_memory import audit_safe_external_memory_result
from ..storage.json_repository import JsonGameRepository


class SearchExternalMemoryArgs(BaseModel):
    query: str = Field(
        default="",
        description="要从外置记忆中检索的简短问题，例如旧关系、玩家偏好、上一章 recap 或未解决伏笔。",
    )
    purpose: str = Field(
        default="recall",
        description="检索目的，例如 recall、relationship、preference、recap、foreshadowing。",
    )


class ExternalMemoryTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        *,
        actor: dict[str, str] | None = None,
        message: str = "",
        external_memory: Any | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}
        self.message = message
        self.external_memory = external_memory

    async def search_external_memory(self, query: str = "", purpose: str = "recall") -> dict[str, Any]:
        if self.external_memory is None:
            return {
                "ok": True,
                "available": False,
                "reason": "external_memory_tool_unavailable",
            }
        session = self.repository.load_session(self.session_id)
        search_query = (query or self.message or "").strip()
        if not search_query:
            return {
                "ok": True,
                "available": False,
                "reason": "external_memory_query_empty",
            }
        result = await self.external_memory.context_for_prompt(session, self.actor, search_query)
        if not result.get("ok", True):
            safe = audit_safe_external_memory_result(result)
            safe["tool"] = "search_external_memory"
            return safe
        return {
            "ok": True,
            "available": bool(result.get("available")),
            "provider": result.get("provider", "honcho"),
            "operation": "search_external_memory",
            "purpose": str(purpose or "recall")[:80],
            "context": str(result.get("context", "") or ""),
            "context_chars": int(result.get("context_chars", 0) or 0),
            "context_scopes": list(result.get("context_scopes") or []),
            "truncated": bool(result.get("truncated", False)),
            "honcho_session_id": result.get("honcho_session_id", ""),
            "player_peer_id": result.get("player_peer_id", ""),
            "character_peer_id": result.get("character_peer_id", ""),
            "campaign_scope": dict(result.get("campaign_scope") or {}),
            "non_authoritative": True,
            "usage_note": (
                "外置记忆只作回忆线索；若与本地状态、工具结果或规则结果冲突，"
                "必须以本地结果为准。"
            ),
        }
