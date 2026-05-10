from astrbot_plugin_auto_trpg_dm.core.control_authority import (
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROLLER_TYPE_SYSTEM_HOST,
)
from astrbot_plugin_auto_trpg_dm.core.hosted_action_policy import (
    classify_hosted_action_risk,
    evaluate_hosted_action_policy,
)
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession


def _hosted_session(risk_ceiling: str = "low") -> GameSession:
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="Hosted PC", player_id="owner")
    session.control_authority = {
        "records": {
            "pc_owner": {
                "character_id": "pc_owner",
                "owner_player_id": "owner",
                "active_controller_id": "__system__",
                "controller_type": CONTROLLER_TYPE_SYSTEM_HOST,
                "status": CONTROL_STATUS_HOSTED_BY_SYSTEM,
                "authorized_by": "owner",
                "risk_ceiling": risk_ceiling,
                "duration_type": "until_revoked",
                "audit_ref": "host-auth-1",
            }
        }
    }
    return session


def test_hosted_action_policy_requires_explicit_system_host_record():
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")

    result = evaluate_hosted_action_policy(
        session,
        "pc_owner",
        actor={"player_id": "__system__"},
        summary="保守防御",
    )

    assert result["ok"] is False
    assert result["hosted"] is False
    assert result["reason"] == "no_active_system_hosting"


def test_hosted_action_policy_allows_low_risk_system_host_action():
    result = evaluate_hosted_action_policy(
        _hosted_session(),
        "pc_owner",
        actor={"player_id": "__system__"},
        summary="防御并跟随队伍",
        timeout=True,
    )

    assert result["ok"] is True
    assert result["hosted"] is True
    assert result["risk"] == "low"
    assert result["risk_ceiling"] == "low"
    assert result["audit_ref"] == "host-auth-1"
    assert result["reason"] == "hosted_action_within_risk_ceiling"


def test_hosted_action_policy_downgrades_high_risk_action_above_ceiling():
    result = evaluate_hosted_action_policy(
        _hosted_session("low"),
        "pc_owner",
        actor={"player_id": "__heartbeat__"},
        summary="消耗大招并签下不可逆契约",
        reason="托管期间自动推进",
    )

    assert result["ok"] is False
    assert result["hosted"] is True
    assert result["risk"] == "high"
    assert result["risk_ceiling"] == "low"
    assert result["reason"] == "hosted_action_exceeds_risk_ceiling"
    assert result["fallback_policy"] == "defend_or_follow"
    assert "不消耗稀缺资源" in result["fallback_summary"]


def test_hosted_action_policy_rejects_non_system_actor():
    result = evaluate_hosted_action_policy(
        _hosted_session(),
        "pc_owner",
        actor={"player_id": "intruder"},
        summary="防御",
    )

    assert result["ok"] is False
    assert result["hosted"] is True
    assert result["reason"] == "actor_is_not_system_host"


def test_hosted_action_risk_classifier_distinguishes_medium_and_high():
    assert classify_hosted_action_risk("普通攻击最近目标") == "medium"
    assert classify_hosted_action_risk("交出关键物品并暴露秘密") == "high"
    assert classify_hosted_action_risk("保持掩护并防御") == "low"
