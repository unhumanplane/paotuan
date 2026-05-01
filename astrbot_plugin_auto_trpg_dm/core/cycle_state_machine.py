from __future__ import annotations

from dataclasses import asdict
from typing import Any

from astrbot_plugin_auto_trpg_dm.core.models import (
    AuditBuffer,
    CycleAction,
    CycleState,
    GameSession,
    RACycleInput,
)


class CycleStateMachine:
    """Manages cycle state transitions and audit buffer operations."""

    @staticmethod
    def transition(session: GameSession, target: CycleState) -> None:
        """Validate and apply a state transition."""
        current = session.cycle_state
        valid = _VALID_TRANSITIONS.get(current, set())
        if target not in valid:
            raise ValueError(
                f"Invalid cycle transition: {current.value} -> {target.value}"
            )
        session.cycle_state = target

    @staticmethod
    def start_new_cycle(session: GameSession) -> None:
        """Reset buffers and increment cycle id for the next cycle."""
        session.current_cycle_id += 1
        session.audit_buffer = AuditBuffer(cycle_id=session.current_cycle_id)
        session.ra_cycle_input = RACycleInput(cycle_id=session.current_cycle_id)
        session.cycle_state = CycleState.CYCLE_ACTIVE

    @staticmethod
    def append_action(
        session: GameSession,
        player_id: str,
        character_id: str,
        player_message: str,
        dm_narrative: str,
        tools_called: list[dict[str, Any]],
    ) -> None:
        """Append a full action to audit_buffer and regenerate ra_cycle_input."""
        action = CycleAction(
            player_id=player_id,
            character_id=character_id,
            player_message=player_message,
            dm_narrative=dm_narrative,
            tools_called=tools_called,
        )
        session.audit_buffer.actions.append(action)
        # Rebuild RA projection from full audit buffer
        session.ra_cycle_input = _build_ra_cycle_input(session)

    @staticmethod
    def build_ra_cycle_input(session: GameSession) -> RACycleInput:
        """Generate a fresh RA projection from the current audit buffer."""
        return _build_ra_cycle_input(session)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_TRANSITIONS: dict[CycleState, set[CycleState]] = {
    CycleState.CYCLE_ACTIVE: {CycleState.CYCLE_RESOLVING},
    CycleState.CYCLE_RESOLVING: {CycleState.CYCLE_TRANSITION},
    CycleState.CYCLE_TRANSITION: {CycleState.CYCLE_ACTIVE},
}


def _build_ra_cycle_input(session: GameSession) -> RACycleInput:
    """Create a filtered RACycleInput from the current audit buffer.

    Excludes player_message and any PII/diagnostic fields from tools_called.
    """
    actions: list[dict[str, Any]] = []
    for action in session.audit_buffer.actions:
        ra_action = action.to_ra_dict()
        ra_action["tools_called"] = [_redact_tool_call(t) for t in action.tools_called]
        actions.append(ra_action)
    return RACycleInput(
        cycle_id=session.audit_buffer.cycle_id,
        actions=actions,
    )


def _redact_tool_call(tool: dict[str, Any]) -> dict[str, Any]:
    """Field-level redaction: keep tool name and state-change fields only."""
    name = str(tool.get("name", ""))
    result = dict(tool.get("result", {}))
    # Retain only state-change keys from result
    allowed_keys = {
        "hp", "mp", "alive", "position", "conditions", "inventory",
        "status", "scene_updates", "new_entities", "removed_entities",
        "current_conflict", "total", "success", "damage", "healing",
    }
    redacted_result = {
        k: v for k, v in result.items()
        if k in allowed_keys or k.endswith("_status") or k.endswith("_changes")
    }
    return {
        "name": name,
        "result": redacted_result,
    }
