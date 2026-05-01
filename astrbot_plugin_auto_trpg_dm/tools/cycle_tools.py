from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.cycle_state_machine import CycleStateMachine
from ..core.models import CycleState, utc_now_iso
from ..storage.json_repository import JsonGameRepository


class CycleControlArgs(BaseModel):
    action: str = Field(description='周期控制动作。MVP 仅支持 "end_cycle"。')
    reason: str = Field(default="", description="DM 结束当前叙事周期的简短原因。")


class CycleTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: dict[str, str] | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}
        self.state_machine = CycleStateMachine()

    async def cycle_control(self, action: str, reason: str = "") -> dict[str, Any]:
        normalized = str(action or "").strip().lower().replace("-", "_")
        if normalized not in {"end_cycle", "end"}:
            return {
                "ok": False,
                "error": "unsupported_cycle_action",
                "action": action,
                "supported_actions": ["end_cycle"],
            }

        session = self.repository.load_session(self.session_id)
        previous_state = session.cycle_state
        if previous_state != CycleState.CYCLE_RESOLVING:
            try:
                self.state_machine.begin_resolving(session)
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": "invalid_cycle_transition",
                    "from_state": previous_state.value,
                    "to_state": CycleState.CYCLE_RESOLVING.value,
                    "reason": str(exc),
                }
        session.audit_buffer.cycle_id = session.current_cycle_id
        session.audit_buffer.ended_at = utc_now_iso()
        self.repository.save_session(session)
        self.repository.append_audit(
            self.session_id,
            {
                "type": "cycle_control",
                "action": "end_cycle",
                "actor": self.actor,
                "reason": reason,
                "from_state": previous_state.value,
                "to_state": session.cycle_state.value,
                "cycle_id": session.current_cycle_id,
            },
        )
        return {
            "ok": True,
            "action": "end_cycle",
            "cycle_id": session.current_cycle_id,
            "from_state": previous_state.value,
            "to_state": session.cycle_state.value,
            "message": "当前叙事周期已标记为结束，等待周期结算。",
        }
