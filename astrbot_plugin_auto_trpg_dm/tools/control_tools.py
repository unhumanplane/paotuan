from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.control_authority import CONTROL_RISK_LOW
from ..core.control_transfer import (
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RECLAIM,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
    CONTROL_ACTION_STATUS,
    CONTROL_DURATION_UNTIL_REVOKED,
    CONTROL_MUTATING_ACTIONS,
    apply_control_change,
)
from ..storage.json_repository import JsonGameRepository


class ControlAuthorityArgs(BaseModel):
    action: str = Field(
        ...,
        description=(
            "控制权动作：delegate_to_player、relinquish_to_system、reclaim 或 status。"
            "只有在角色持有人明确确认后才能调用前三类变更动作。"
        ),
    )
    character_id: str = Field(..., description="要变更或查询临时控制权的角色/实体 ID。")
    target_player_id: str = Field(
        default="",
        description="delegate_to_player 时必填的目标玩家 ID；不要用玩家昵称猜测 ID。",
    )
    risk_ceiling: str = Field(
        default=CONTROL_RISK_LOW,
        description="系统托管或代控风险上限：low、medium、high；默认 low。",
    )
    duration_type: str = Field(
        default=CONTROL_DURATION_UNTIL_REVOKED,
        description=(
            "控制持续类型：until_next_turn、until_combat_end、until_scene_end、"
            "until_time、until_revoked；默认 until_revoked。"
        ),
    )
    expires_at: str = Field(
        default="",
        description="duration_type=until_time 时填写的到期时间字符串；不会自动从含糊文本推断。",
    )
    consent_reference: str = Field(
        default="",
        description="可选确认引用 ID 或摘要标签；不要传入原始私聊、长文本或敏感同意内容。",
    )
    audit_ref: str = Field(default="", description="可选审计引用 ID；为空时由代码生成。")


class ControlTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: dict[str, str] | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def control_authority(
        self,
        action: str,
        character_id: str,
        target_player_id: str = "",
        risk_ceiling: str = CONTROL_RISK_LOW,
        duration_type: str = CONTROL_DURATION_UNTIL_REVOKED,
        expires_at: str = "",
        consent_reference: str = "",
        audit_ref: str = "",
    ) -> dict[str, Any]:
        normalized_action = _normalize_action(action)
        session = self.repository.load_session(self.session_id)
        result = apply_control_change(
            session,
            normalized_action,
            character_id,
            actor=self.actor,
            target_player_id=target_player_id,
            risk_ceiling=risk_ceiling,
            duration_type=duration_type,
            expires_at=expires_at,
            consent_reference=consent_reference,
            audit_ref=audit_ref,
        )
        if result.get("ok") and normalized_action in CONTROL_MUTATING_ACTIONS:
            self.repository.save_session(session)
        self.repository.append_audit(
            self.session_id,
            {
                "type": "tool",
                "tool": "control_authority",
                "actor": self.actor,
                "input": {
                    "action": normalized_action,
                    "character_id": character_id,
                    "target_player_id": target_player_id,
                    "risk_ceiling": risk_ceiling,
                    "duration_type": duration_type,
                    "expires_at": expires_at,
                    "consent_reference_provided": bool(str(consent_reference or "").strip()),
                    "audit_ref": audit_ref,
                },
                "result": result,
            },
        )
        return result


def _normalize_action(action: str) -> str:
    normalized = str(action or "").strip().lower().replace("-", "_")
    aliases = {
        "delegate": CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "delegate_player": CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "system_host": CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
        "host": CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
        "relinquish": CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
        "reclaim_control": CONTROL_ACTION_RECLAIM,
        "revoke": CONTROL_ACTION_RECLAIM,
        "query": CONTROL_ACTION_STATUS,
    }
    return aliases.get(normalized, normalized)
