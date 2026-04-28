from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

from pydantic import BaseModel, Field

from ..core.memory import MemoryCompressor
from ..core.prompts import build_system_prompt, build_user_prompt
from ..storage.json_repository import JsonGameRepository


class EstimateTokenUsageArgs(BaseModel):
    detail_level: str = Field(
        default="summary",
        description="summary 或 detail；detail 会返回更细的组成部分估算",
    )


ToolSpecsProvider = Callable[[Any], tuple[list[str], list[dict[str, Any]]]]


class DiagnosticTools:
    def __init__(self, repository: JsonGameRepository, session_id: str):
        self.repository = repository
        self.session_id = session_id
        self.compressor = MemoryCompressor()
        self.tool_specs_provider: ToolSpecsProvider | None = None

    def set_tool_specs_provider(self, provider: ToolSpecsProvider) -> None:
        self.tool_specs_provider = provider

    async def estimate_token_usage(self, detail_level: str = "summary") -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        snapshot = session.compact_snapshot()
        snapshot_text = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        full_save_text = json.dumps(session.to_dict(), ensure_ascii=False, separators=(",", ":"))
        sections = {
            "scene": snapshot.get("scene", {}),
            "world_tags": snapshot.get("world_tags", {}),
            "characters": snapshot.get("characters", []),
            "battle": snapshot.get("battle", {}),
            "rules": snapshot.get("rules", []),
            "memory_summary": snapshot.get("memory_summary", ""),
        }
        section_chars = {
            key: len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            for key, value in sections.items()
        }
        audit_path = self.repository.audit_path(self.session_id)
        result: Dict[str, Any] = {
            "ok": True,
            "note": "token 为粗略估算；中文、JSON、英文混排会让实际值浮动。",
            "session_id": self.session_id,
            "limits": {
                "configured_context_window_tokens": 260_000,
                "compression_trigger_snapshot_chars": self.compressor.max_snapshot_chars,
                "summary_limit_chars": self.compressor.max_summary_chars,
            },
            "current": {
                "full_save_chars": len(full_save_text),
                "compact_snapshot_chars": len(snapshot_text),
                "memory_summary_chars": len(session.memory_summary),
                "audit_bytes": _path_size(audit_path),
                "characters": len(session.characters),
                "participants": len(session.participants),
                "rules": len(session.rules),
                "battle_active": bool(session.battle.get("active", False)),
            },
            "rough_token_estimate": _rough_token_estimate(len(snapshot_text)),
            "prompt_budget": self._prompt_budget(session),
            "compression": {
                "would_compress_now": self.compressor.snapshot_chars(session)
                > self.compressor.max_snapshot_chars
                or len(session.memory_summary) >= self.compressor.max_summary_chars,
                "snapshot_chars_remaining_before_compression": max(
                    0,
                    self.compressor.max_snapshot_chars - self.compressor.snapshot_chars(session),
                ),
            },
            "largest_sections_by_chars": sorted(
                section_chars.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:6],
        }
        if detail_level.strip().lower() == "detail":
            result["section_chars"] = section_chars
            result["audit_path"] = str(audit_path)
            result["save_path"] = str(self.repository.save_path(self.session_id))
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "estimate_token_usage",
                "input": {"detail_level": detail_level},
                "result": result,
            },
        )
        return result

    def _prompt_budget(self, session: Any) -> Dict[str, Any]:
        if not self.tool_specs_provider:
            return {"available": False}
        actor = {
            "player_id": "<diagnostic>",
            "display_name": "<diagnostic>",
            "platform": "",
            "session_id": self.session_id,
            "seen_at": "",
        }
        tool_names, tool_specs = self.tool_specs_provider(session.mode)
        tool_schema_text = json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":"))
        system_prompt = build_system_prompt(session, session.mode, tool_names, tool_specs, actor=actor)
        sample_user_prompt = build_user_prompt("示例玩家输入")
        total_chars = len(system_prompt) + len(sample_user_prompt) + len(tool_schema_text)
        by_mode: Dict[str, Any] = {}
        try:
            from ..core.models import GameMode

            for mode in GameMode:
                names, specs = self.tool_specs_provider(mode)
                text = json.dumps(specs, ensure_ascii=False, separators=(",", ":"))
                by_mode[mode.value] = {
                    "tool_count": len(names),
                    "tool_schema_chars": len(text),
                    "rough_tool_schema_tokens": _rough_token_estimate(len(text)),
                }
        except Exception:
            by_mode = {}
        return {
            "available": True,
            "mode": session.mode.value,
            "tool_count": len(tool_names),
            "system_prompt_chars": len(system_prompt),
            "sample_user_prompt_chars": len(sample_user_prompt),
            "tool_schema_chars": len(tool_schema_text),
            "total_request_chars_excluding_chat_history": total_chars,
            "rough_total_request_tokens": _rough_token_estimate(total_chars),
            "mode_tool_schema_costs": by_mode,
        }


def _path_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _rough_token_estimate(chars: int) -> Dict[str, int]:
    return {
        "low": max(1, chars // 4),
        "heuristic": max(1, chars // 2),
        "high": max(1, int(chars / 1.5)),
    }
