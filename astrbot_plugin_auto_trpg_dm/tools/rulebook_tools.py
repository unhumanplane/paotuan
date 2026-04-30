from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..rulebook import RulebookStore
from ..storage.json_repository import JsonGameRepository


class QueryCoreRulesArgs(BaseModel):
    query: str = Field(..., description="要查询的核心规则问题或玩家行动，例如：倒地状态下远程攻击如何判定")
    purpose: str = Field(default="adjudication", description="查询目的：adjudication/rules_question/tool_hint")
    categories: List[str] = Field(default_factory=list, description="可选分类过滤，例如 combat、conditions")
    limit: int = Field(default=4, ge=1, le=8, description="最多返回多少条规则卡")
    max_chars: int = Field(default=1600, ge=400, le=3000, description="返回内容最大字符数")


class RulebookTools:
    _store_cache: dict[str, RulebookStore] = {}

    def __init__(self, repository: JsonGameRepository, session_id: str):
        self.repository = repository
        self.session_id = session_id

    async def query_core_rules(
        self,
        query: str,
        purpose: str = "adjudication",
        categories: List[str] | None = None,
        limit: int = 4,
        max_chars: int = 1600,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        store = self._store()
        result = store.query(
            query=query,
            categories=categories or [],
            limit=limit,
            max_chars=max_chars,
            mode_hint=str(session.mode.value),
        )
        result["purpose"] = purpose
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "query_core_rules",
                "input": {
                    "query": query,
                    "purpose": purpose,
                    "categories": categories or [],
                    "limit": limit,
                    "max_chars": max_chars,
                },
                "result": _audit_safe_result(result),
            },
        )
        return result

    def _store(self) -> RulebookStore:
        runtime_dir = self.repository.data_dir / "rulebooks" / "dnd2024_core"
        packaged_dir = Path(__file__).resolve().parents[1] / "rulebook" / "seed" / "dnd2024_core"
        cache_key = f"{runtime_dir}|{packaged_dir}"
        store = self._store_cache.get(cache_key)
        if store is None:
            store = RulebookStore(runtime_dir, fallback_dirs=[packaged_dir])
            self._store_cache[cache_key] = store
        return store


def _audit_safe_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": bool(result.get("ok")),
        "available": bool(result.get("available")),
        "error": str(result.get("error") or ""),
        "query": str(result.get("query") or ""),
        "match_ids": [str(item.get("id") or "") for item in result.get("matches") or []],
        "rulebook": result.get("rulebook") or {},
    }
