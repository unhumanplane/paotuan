from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from pydantic import BaseModel, Field

from ..core.external_memory import audit_safe_external_memory_result
from ..core.memory import MemoryCompressor
from ..core.prompts import (
    build_diagnostic_system_prompt,
    build_system_prompt,
    build_user_prompt,
    prompt_component_chars,
    prompt_snapshot_projection_stats,
    snapshot_projection_shadow_stats,
)
from ..storage.json_repository import JsonGameRepository


class EstimateTokenUsageArgs(BaseModel):
    detail_level: str = Field(
        default="summary",
        description="summary 或 detail；detail 会返回更细的组成部分估算",
    )


ToolSpecsProvider = Callable[..., Tuple[List[str], List[Dict[str, Any]]]]
PROMPT_BUDGET_SAMPLE_MESSAGE = "sample player action"


class DiagnosticTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        *,
        external_memory_enabled: bool = False,
        external_memory_read_enabled: bool = False,
        external_memory_max_context_chars: int = 0,
    ):
        self.repository = repository
        self.session_id = session_id
        self.compressor = MemoryCompressor()
        self.tool_specs_provider: ToolSpecsProvider | None = None
        self.external_memory_enabled = bool(external_memory_enabled)
        self.external_memory_read_enabled = bool(external_memory_read_enabled)
        self.external_memory_max_context_chars = max(0, _safe_int(external_memory_max_context_chars))

    def set_tool_specs_provider(self, provider: ToolSpecsProvider) -> None:
        self.tool_specs_provider = provider

    def _tool_specs_for(self, mode: Any, message: str = "") -> Tuple[List[str], List[Dict[str, Any]]]:
        if not self.tool_specs_provider:
            return [], []
        try:
            return self.tool_specs_provider(mode, message)
        except TypeError:
            return self.tool_specs_provider(mode)

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
            "external_memory": {
                **self._external_memory_config_summary(),
                "observability": self._external_memory_observability(),
            },
            "prompt_budget": self._prompt_budget(session),
            "prompt_snapshot_projection": prompt_snapshot_projection_stats(
                session,
                session.mode,
                PROMPT_BUDGET_SAMPLE_MESSAGE,
                actor={"player_id": "<diagnostic>"},
                snapshot_projection_enabled=True,
            ),
            "snapshot_projection_shadow": snapshot_projection_shadow_stats(
                session,
                session.mode,
                "",
                actor={"player_id": "<diagnostic>"},
            ),
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
            return {
                "available": False,
                "external_memory": self._external_memory_config_summary(),
            }
        actor = {
            "player_id": "<diagnostic>",
            "display_name": "<diagnostic>",
            "platform": "",
            "session_id": self.session_id,
            "seen_at": "",
        }
        tool_names, tool_specs = self._tool_specs_for(session.mode)
        tool_schema_text = json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":"))
        diagnostic_tool_names, diagnostic_tool_specs = self._tool_specs_for(
            session.mode,
            "token",
        )
        diagnostic_tool_schema_text = json.dumps(
            diagnostic_tool_specs,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_prompt = build_system_prompt(
            session,
            session.mode,
            tool_names,
            tool_specs,
            actor=actor,
            message=PROMPT_BUDGET_SAMPLE_MESSAGE,
            snapshot_projection_enabled=True,
        )
        diagnostic_system_prompt = build_diagnostic_system_prompt(
            session,
            session.mode,
            diagnostic_tool_names,
            actor=actor,
        )
        component_chars = prompt_component_chars(
            session,
            session.mode,
            tool_names,
            actor=actor,
            message=PROMPT_BUDGET_SAMPLE_MESSAGE,
            snapshot_projection_enabled=True,
        )
        component_chars["system_prompt_chars"] = len(system_prompt)
        component_chars["tool_schema_chars"] = len(tool_schema_text)
        attributed_prompt_chars = sum(
            value
            for key, value in component_chars.items()
            if key.endswith("_chars") and key not in {"system_prompt_chars", "tool_schema_chars"}
            if isinstance(value, int)
        )
        component_chars["static_prompt_shell_chars"] = max(
            0,
            len(system_prompt) - attributed_prompt_chars,
        )
        diagnostic_component_chars = prompt_component_chars(
            session,
            session.mode,
            diagnostic_tool_names,
            actor=actor,
            profile="diagnostic",
        )
        diagnostic_component_chars["system_prompt_chars"] = len(diagnostic_system_prompt)
        diagnostic_component_chars["tool_schema_chars"] = len(diagnostic_tool_schema_text)
        diagnostic_attributed_prompt_chars = sum(
            value
            for key, value in diagnostic_component_chars.items()
            if key.endswith("_chars") and key not in {"system_prompt_chars", "tool_schema_chars"}
            if isinstance(value, int)
        )
        diagnostic_component_chars["diagnostic_static_shell_chars"] = max(
            0,
            len(diagnostic_system_prompt) - diagnostic_attributed_prompt_chars,
        )
        external_memory_budget = self._external_memory_budget(session, tool_names, tool_specs, actor)
        sample_user_prompt = build_user_prompt("示例玩家输入")
        total_chars = len(system_prompt) + len(sample_user_prompt) + len(tool_schema_text)
        total_with_external_memory_chars = (
            total_chars + external_memory_budget["estimated_section_chars"]
        )
        by_mode: Dict[str, Any] = {}
        try:
            from ..core.models import GameMode

            for mode in GameMode:
                names, specs = self._tool_specs_for(mode)
                text = json.dumps(specs, ensure_ascii=False, separators=(",", ":"))
                diagnostic_names, diagnostic_specs = self._tool_specs_for(mode, "token")
                diagnostic_text = json.dumps(
                    diagnostic_specs,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                by_mode[mode.value] = {
                    "tool_count": len(names),
                    "tool_schema_chars": len(text),
                    "rough_tool_schema_tokens": _rough_token_estimate(len(text)),
                    "diagnostic_tool_count": len(diagnostic_names),
                    "diagnostic_tool_schema_chars": len(diagnostic_text),
                    "diagnostic_rough_tool_schema_tokens": _rough_token_estimate(
                        len(diagnostic_text)
                    ),
                }
        except Exception:
            by_mode = {}
        return {
            "available": True,
            "mode": session.mode.value,
            "tool_count": len(tool_names),
            "diagnostic_tool_count": len(diagnostic_tool_names),
            "system_prompt_chars": len(system_prompt),
            "diagnostic_system_prompt_chars": len(diagnostic_system_prompt),
            "sample_user_prompt_chars": len(sample_user_prompt),
            "tool_schema_chars": len(tool_schema_text),
            "diagnostic_tool_schema_chars": len(diagnostic_tool_schema_text),
            "total_request_chars_excluding_chat_history": total_chars,
            "total_request_chars_with_external_memory_budget": total_with_external_memory_chars,
            "rough_total_request_tokens": _rough_token_estimate(total_chars),
            "rough_total_request_tokens_with_external_memory_budget": _rough_token_estimate(
                total_with_external_memory_chars
            ),
            "external_memory": external_memory_budget,
            "system_prompt_component_chars": component_chars,
            "system_prompt_component_tokens": _component_token_estimates(component_chars),
            "diagnostic_prompt_component_chars": diagnostic_component_chars,
            "diagnostic_prompt_component_tokens": _component_token_estimates(
                diagnostic_component_chars
            ),
            "mode_tool_schema_costs": by_mode,
        }

    def _external_memory_budget(
        self,
        session: Any,
        tool_names: list[str],
        tool_specs: list[dict[str, Any]],
        actor: dict[str, str],
    ) -> dict[str, Any]:
        configured_max = self.external_memory_max_context_chars
        included = (
            self.external_memory_enabled
            and self.external_memory_read_enabled
            and configured_max > 0
        )
        if not included:
            return {
                "enabled": self.external_memory_enabled,
                "read_enabled": self.external_memory_read_enabled,
                "configured_max_context_chars": configured_max,
                "included_in_budget": False,
                "estimated_section_chars": 0,
            }
        base_prompt = build_system_prompt(
            session,
            session.mode,
            tool_names,
            tool_specs,
            actor=actor,
            message=PROMPT_BUDGET_SAMPLE_MESSAGE,
            snapshot_projection_enabled=True,
        )
        placeholder_context = "x" * configured_max
        prompt_with_external_memory = build_system_prompt(
            session,
            session.mode,
            tool_names,
            tool_specs,
            actor=actor,
            external_memory_context=placeholder_context,
            message=PROMPT_BUDGET_SAMPLE_MESSAGE,
            snapshot_projection_enabled=True,
        )
        return {
            "enabled": True,
            "read_enabled": True,
            "configured_max_context_chars": configured_max,
            "included_in_budget": True,
            "estimated_section_chars": max(0, len(prompt_with_external_memory) - len(base_prompt)),
        }

    def _external_memory_config_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.external_memory_enabled,
            "read_enabled": self.external_memory_read_enabled,
            "configured_max_context_chars": self.external_memory_max_context_chars,
            "budget_note": "configured_max_context_chars is a budget ceiling, not the actual Honcho context fetched for this turn.",
        }

    def _external_memory_observability(self) -> dict[str, Any]:
        records = self.repository.last_audit_records(self.session_id, limit=80)
        summary: dict[str, Any] = {
            "safe_fields_only": True,
            "audit_window_records": len(records),
            "read_attempts": 0,
            "read_successes": 0,
            "read_failures": 0,
            "write_attempts": 0,
            "write_successes": 0,
            "write_failures": 0,
            "skipped_duplicate_writes": 0,
            "failures_by_error": {},
            "skips_by_reason": {},
            "latest_context_chars": 0,
            "max_context_chars": 0,
            "state_sensitive_context_observed": False,
        }
        for record in records:
            record_type = str(record.get("type") or "")
            if not record_type.startswith("external_memory_"):
                continue
            raw_result = record.get("result") if isinstance(record.get("result"), dict) else {}
            result = audit_safe_external_memory_result(raw_result)
            if record_type in {"external_memory_context_observed", "external_memory_context_failed"}:
                summary["read_attempts"] += 1
                if result.get("ok", True):
                    summary["read_successes"] += 1
                else:
                    summary["read_failures"] += 1
            elif record_type in {
                "external_memory_write_key_event",
                "external_memory_write_summary",
            }:
                summary["write_attempts"] += 1
                if result.get("available"):
                    summary["write_successes"] += 1
                elif not result.get("ok", True):
                    summary["write_failures"] += 1
            error = str(result.get("error") or "")
            if error:
                failures = summary["failures_by_error"]
                failures[error] = failures.get(error, 0) + 1
            reason = str(result.get("reason") or "")
            status = str(result.get("status") or "")
            if reason and result.get("ok", True) and (
                not result.get("available") or status.startswith("skipped")
            ):
                skips = summary["skips_by_reason"]
                skips[reason] = skips.get(reason, 0) + 1
                if reason == "duplicate_external_memory_event":
                    summary["skipped_duplicate_writes"] += 1
            if "context_chars" in result:
                context_chars = max(0, _safe_int(result.get("context_chars")))
                summary["latest_context_chars"] = context_chars
                summary["max_context_chars"] = max(summary["max_context_chars"], context_chars)
            if result.get("state_sensitive_context"):
                summary["state_sensitive_context_observed"] = True
        return summary


def _path_size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _rough_token_estimate(chars: int) -> Dict[str, int]:
    return {
        "low": max(1, chars // 4),
        "heuristic": max(1, chars // 2),
        "high": max(1, int(chars / 1.5)),
    }


def _component_token_estimates(component_chars: Dict[str, Any]) -> Dict[str, Any]:
    estimates: Dict[str, Any] = {}
    for key, value in component_chars.items():
        if key.endswith("_chars") and isinstance(value, int):
            estimates[key[: -len("_chars")] + "_tokens"] = (
                _rough_token_estimate(value)["heuristic"] if value > 0 else 0
            )
        else:
            estimates[key] = value
    return estimates
