from __future__ import annotations

from typing import Any

from .control_authority import (
    CONTROL_RISK_HIGH,
    CONTROL_RISK_LOW,
    CONTROL_RISK_MEDIUM,
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROLLER_TYPE_SYSTEM_HOST,
    control_record_for_character,
)


HOSTED_ACTION_SYSTEM_ACTORS = {"__system__", "__heartbeat__", "__dm_host__"}
HOSTED_ACTION_RISK_ORDER = {
    CONTROL_RISK_LOW: 0,
    CONTROL_RISK_MEDIUM: 1,
    CONTROL_RISK_HIGH: 2,
}
HOSTED_ACTION_FALLBACK_POLICY = "defend_or_follow"

_HIGH_RISK_TERMS = {
    "death",
    "die",
    "kill self",
    "suicide",
    "permanent",
    "irreversible",
    "pvp",
    "betray",
    "betrayal",
    "secret",
    "contract",
    "signature",
    "sign",
    "surrender",
    "key item",
    "rare",
    "scarce",
    "legendary",
    "死亡",
    "自杀",
    "永久",
    "不可逆",
    "稀缺",
    "珍贵",
    "传说",
    "背叛",
    "秘密",
    "契约",
    "签字",
    "投降",
    "交出关键",
    "消耗大招",
}
_MEDIUM_RISK_TERMS = {
    "attack",
    "cast",
    "spell",
    "skill",
    "check",
    "resource",
    "攻击",
    "施法",
    "法术",
    "检定",
    "技能",
    "普通资源",
}


def evaluate_hosted_action_policy(
    session: Any,
    character_id: str,
    *,
    actor: dict[str, Any] | None = None,
    summary: str = "",
    reason: str = "",
    timeout: bool = False,
) -> dict[str, Any]:
    character = str(character_id or "").strip()
    record = control_record_for_character(session, character)
    owner_id = str(record.get("owner_player_id") or "")
    active_controller_id = str(record.get("active_controller_id") or "")
    controller_type = str(record.get("controller_type") or "")
    status = str(record.get("status") or "")
    audit_ref = str(record.get("audit_ref") or "")

    if status != CONTROL_STATUS_HOSTED_BY_SYSTEM or controller_type != CONTROLLER_TYPE_SYSTEM_HOST:
        return {
            "ok": False,
            "hosted": False,
            "reason": "no_active_system_hosting",
            "character_id": character,
            "owner_player_id": owner_id,
            "active_controller_id": active_controller_id,
            "controller_type": controller_type,
            "status": status,
        }

    actor_id = str((actor or {}).get("player_id") or "").strip()
    if actor_id and actor_id not in HOSTED_ACTION_SYSTEM_ACTORS and actor_id != active_controller_id:
        return {
            "ok": False,
            "hosted": True,
            "reason": "actor_is_not_system_host",
            "character_id": character,
            "owner_player_id": owner_id,
            "active_controller_id": active_controller_id,
            "controller_type": controller_type,
            "status": status,
            "audit_ref": audit_ref,
        }

    risk = classify_hosted_action_risk(f"{summary}\n{reason}")
    ceiling = _safe_risk(record.get("risk_ceiling"))
    allowed = HOSTED_ACTION_RISK_ORDER[risk] <= HOSTED_ACTION_RISK_ORDER[ceiling]
    result = {
        "ok": allowed,
        "hosted": True,
        "character_id": character,
        "owner_player_id": owner_id,
        "active_controller_id": active_controller_id,
        "controller_type": controller_type,
        "status": status,
        "risk": risk,
        "risk_ceiling": ceiling,
        "duration_type": str(record.get("duration_type") or "until_revoked"),
        "expires_at": str(record.get("expires_at") or ""),
        "audit_ref": audit_ref,
        "hosted_policy": "system_host_v1",
        "timeout": bool(timeout),
    }
    if allowed:
        result["reason"] = "hosted_action_within_risk_ceiling"
        return result
    result["reason"] = "hosted_action_exceeds_risk_ceiling"
    result["fallback_policy"] = HOSTED_ACTION_FALLBACK_POLICY
    result["fallback_summary"] = conservative_hosted_action_summary(session, character)
    return result


def classify_hosted_action_risk(text: str) -> str:
    normalized = _strip_safe_negated_risk_phrases(str(text or "").lower())
    compact = normalized.replace(" ", "")
    if any(term in normalized or term.replace(" ", "") in compact for term in _HIGH_RISK_TERMS):
        return CONTROL_RISK_HIGH
    if any(term in normalized or term.replace(" ", "") in compact for term in _MEDIUM_RISK_TERMS):
        return CONTROL_RISK_MEDIUM
    return CONTROL_RISK_LOW


def conservative_hosted_action_summary(session: Any, character_id: str) -> str:
    label = str(character_id or "").strip()
    character = getattr(session, "characters", {}).get(label)
    if character and getattr(character, "name", ""):
        label = str(character.name)
    return f"{label} 托管中只采取保守行动：防御、保持掩护、跟随队伍，不消耗稀缺资源。"


def _strip_safe_negated_risk_phrases(text: str) -> str:
    safe_phrases = (
        "不消耗稀缺资源",
        "不消耗珍贵资源",
        "不使用稀缺资源",
        "不使用珍贵资源",
        "不触发新的复杂机制",
        "without spending scarce resources",
        "do not spend scarce resources",
        "no scarce resources",
    )
    cleaned = text
    for phrase in safe_phrases:
        cleaned = cleaned.replace(phrase, "")
    return cleaned


def _safe_risk(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in HOSTED_ACTION_RISK_ORDER else CONTROL_RISK_LOW
