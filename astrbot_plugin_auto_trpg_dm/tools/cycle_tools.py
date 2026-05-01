from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.cycle_state_machine import CycleStateMachine
from ..core.models import CycleState
from ..storage.json_repository import JsonGameRepository


class CycleControlArgs(BaseModel):
    action: str = Field(
        ...,
        description='"end_cycle" 结束当前叙事周期并触发结算；"start_cycle" 开始新周期并重置 buffer。',
    )


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

    def cycle_control(self, action: str) -> dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        if action == "end_cycle":
            CycleStateMachine.transition(session, CycleState.CYCLE_RESOLVING)
            self.repository.save_session(session)
            return {
                "ok": True,
                "cycle_state": session.cycle_state.value,
                "message": "周期已结束，进入结算阶段。",
            }
        if action == "start_cycle":
            CycleStateMachine.start_new_cycle(session)
            self.repository.save_session(session)
            return {
                "ok": True,
                "cycle_state": session.cycle_state.value,
                "current_cycle_id": session.current_cycle_id,
            }
        return {
            "ok": False,
            "error": "invalid_action",
            "reason": f'不支持的动作: {action}。请使用 "end_cycle" 或 "start_cycle"。',
        }
