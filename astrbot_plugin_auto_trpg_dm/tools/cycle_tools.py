from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..core.cycle_state_machine import CycleStateMachine
from ..core.timeline import (
    apply_timeline_patch,
    timeline_advance_requires_sync,
    timeline_view,
    validate_global_timeline_advance,
)
from ..core.models import CycleState, utc_now_iso
from ..storage.json_repository import JsonGameRepository


class CycleControlArgs(BaseModel):
    action: str = Field(description='周期控制动作。MVP 仅支持 "end_cycle"。')
    reason: str = Field(default="", description="DM 结束当前叙事周期的简短原因。")
    timeline_patch: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "可选的全团时间线推进补丁，只允许全局同步时间，例如 "
            '{"day":2,"time_of_day":"morning","label":"第 2 天清晨"}。'
            "不能按玩家或角色分叉。"
        ),
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
        self.state_machine = CycleStateMachine()

    async def cycle_control(
        self,
        action: str,
        reason: str = "",
        timeline_patch: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        normalized = str(action or "").strip().lower().replace("-", "_")
        if normalized not in {"end_cycle", "end"}:
            return {
                "ok": False,
                "error": "unsupported_cycle_action",
                "action": action,
                "supported_actions": ["end_cycle"],
            }

        session = self.repository.load_session(self.session_id)
        requested_timeline_patch = dict(timeline_patch or {})
        timeline_result: dict[str, Any] = {"timeline_advanced": False}
        if requested_timeline_patch or timeline_advance_requires_sync(reason):
            if not requested_timeline_patch:
                result = {
                    "ok": False,
                    "error": "timeline_patch_required",
                    "message": "这次周期结束理由包含跨日、入夜、天亮或长时间跳转；必须提供全局 timeline_patch，不能只靠叙事文本推进时间。",
                }
                self.repository.append_audit(
                    self.session_id,
                    {
                        "type": "cycle_control",
                        "action": "end_cycle",
                        "actor": self.actor,
                        "reason": reason,
                        "result": result,
                    },
                )
                return {
                    **result,
                    "action": "end_cycle",
                    "cycle_id": session.current_cycle_id,
                    "timeline": timeline_view(session.timeline),
                }
            actor_player_id = str(self.actor.get("player_id") or "").strip()
            additional_player_ids = {actor_player_id} if actor_player_id else set()
            validation = validate_global_timeline_advance(
                session,
                requested_timeline_patch,
                additional_player_ids=additional_player_ids,
            )
            if not validation.get("ok"):
                self.repository.append_audit(
                    self.session_id,
                    {
                        "type": "cycle_control",
                        "action": "end_cycle",
                        "actor": self.actor,
                        "reason": reason,
                        "timeline_patch": requested_timeline_patch,
                        "result": validation,
                    },
                )
                return {
                    **validation,
                    "action": "end_cycle",
                    "cycle_id": session.current_cycle_id,
                    "timeline": timeline_view(session.timeline),
                }
            if requested_timeline_patch:
                previous_timeline = timeline_view(session.timeline)
                session.timeline = apply_timeline_patch(
                    session.timeline,
                    requested_timeline_patch,
                    reason=reason,
                    cycle_id=session.current_cycle_id,
                )
                timeline_result = {
                    "timeline_advanced": True,
                    "previous_timeline": previous_timeline,
                    "timeline": timeline_view(session.timeline),
                    **validation,
                }
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
                "timeline": timeline_view(session.timeline),
                "timeline_result": timeline_result,
            },
        )
        return {
            "ok": True,
            "action": "end_cycle",
            "cycle_id": session.current_cycle_id,
            "from_state": previous_state.value,
            "to_state": session.cycle_state.value,
            "timeline": timeline_view(session.timeline),
            "timeline_result": timeline_result,
            "message": "当前叙事周期已标记为结束，等待周期结算。",
        }
