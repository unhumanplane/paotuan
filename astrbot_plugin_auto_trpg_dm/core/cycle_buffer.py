from __future__ import annotations

import json
from typing import Any

from .cycle_state_machine import CycleStateMachine
from .models import AuditBuffer, CycleAction, CycleState, RACycleInput, utc_now_iso


def append_cycle_action(
    session: Any,
    actor: dict[str, str],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    player_id = str(actor.get("player_id") or "").strip()
    character_id = str((session.player_character_map or {}).get(player_id, "") or "")
    if session.audit_buffer.cycle_id != session.current_cycle_id:
        session.audit_buffer = AuditBuffer(cycle_id=session.current_cycle_id)
    if session.ra_cycle_input.cycle_id != session.current_cycle_id:
        session.ra_cycle_input = RACycleInput(cycle_id=session.current_cycle_id)

    raw_tools = _normalise_tool_results(tool_results)
    sanitized_tools = _sanitize_tools_for_ra(raw_tools)
    action = CycleAction(
        player_id=player_id,
        character_id=character_id,
        player_message=player_message,
        dm_narrative=completion,
        tools_called=raw_tools,
        timestamp=utc_now_iso(),
    )
    session.audit_buffer.actions.append(action)
    session.ra_cycle_input.actions.append(
        {
            "dm_narrative": _compact_text(completion, 1200),
            "tools_called": sanitized_tools,
        }
    )
    return {
        "cycle_id": session.current_cycle_id,
        "player_id": player_id,
        "character_id": character_id,
        "tool_names": [item.get("name", "") for item in sanitized_tools],
    }


def cycle_end_requested(tool_results: list[dict[str, Any]]) -> bool:
    for item in tool_results:
        if item.get("tool") != "cycle_control":
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") and result.get("action") == "end_cycle":
            return True
    return False


def complete_cycle_without_ra(session: Any) -> None:
    if session.cycle_state != CycleState.CYCLE_RESOLVING:
        return
    completed_cycle_id = session.current_cycle_id
    session.current_cycle_id = completed_cycle_id + 1
    session.audit_buffer = AuditBuffer(cycle_id=session.current_cycle_id)
    session.ra_cycle_input = RACycleInput(cycle_id=session.current_cycle_id)
    CycleStateMachine().activate(session)


def sanitize_ra_payload(value: Any) -> Any:
    return _sanitize_ra_payload(value)


def _normalise_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        normalised.append(
            {
                "name": str(item.get("tool") or item.get("name") or ""),
                "args": _json_safe_value(item.get("args", {})),
                "result": _json_safe_value(item.get("result", {})),
            }
        )
    return normalised


def _sanitize_tools_for_ra(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in tool_results:
        name = str(item.get("name") or "")
        sanitized.append(
            {
                "name": name,
                "args_sanitized": _sanitize_ra_payload(item.get("args", {})),
                "result_sanitized": _sanitize_ra_payload(item.get("result", {})),
            }
        )
    return sanitized


RA_BLOCKED_PAYLOAD_KEYS = {
    "actor",
    "audit",
    "debug",
    "diagnostic",
    "diagnostics",
    "display_name",
    "message",
    "player_message",
    "player_id",
    "prompt",
    "raw_audit",
    "raw_player_input",
    "raw_text",
    "reason",
    "system_prompt",
    "text",
    "token_usage",
}


def _sanitize_ra_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in RA_BLOCKED_PAYLOAD_KEYS:
                continue
            if any(token in key_lower for token in ("prompt", "diagnostic", "debug", "token_usage", "raw_audit")):
                continue
            sanitized[key_text] = _sanitize_ra_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_ra_payload(item) for item in value[:24]]
    if isinstance(value, str):
        return _compact_text(value, 400)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _compact_text(value, 200)


def _json_safe_value(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
