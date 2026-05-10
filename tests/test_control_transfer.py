import json
from copy import deepcopy

from astrbot_plugin_auto_trpg_dm.core.control_authority import (
    CONTROL_STATUS_DELEGATED_TO_PLAYER,
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROL_STATUS_OWNED,
    CONTROLLER_TYPE_OWNER,
    CONTROLLER_TYPE_PLAYER_DELEGATE,
    CONTROLLER_TYPE_SYSTEM_HOST,
    resolve_control_authority,
)
from astrbot_plugin_auto_trpg_dm.core.control_transfer import (
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RECLAIM,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
    CONTROL_ACTION_STATUS,
    CONTROL_DURATION_UNTIL_REVOKED,
    SYSTEM_HOST_CONTROLLER_ID,
    apply_control_change,
)
from astrbot_plugin_auto_trpg_dm.core.hosted_action_policy import evaluate_hosted_action_policy
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession


def _session_with_owner() -> GameSession:
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")
    session.player_character_map["owner"] = "pc_owner"
    return session


def test_owner_delegates_without_changing_character_owner():
    session = _session_with_owner()

    result = apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        risk_ceiling="medium",
        audit_ref="auth-1",
        consent_reference="raw consent private text",
    )

    record = session.control_authority["records"]["pc_owner"]
    allowed = resolve_control_authority(session, "pc_owner", {"player_id": "delegate"})
    denied_owner_action = resolve_control_authority(session, "pc_owner", {"player_id": "owner"})
    rendered_result = json.dumps(result, ensure_ascii=False)
    rendered_event = json.dumps(session.control_authority["events"][0], ensure_ascii=False)

    assert result["ok"] is True
    assert result["action"] == CONTROL_ACTION_DELEGATE_TO_PLAYER
    assert record["owner_player_id"] == "owner"
    assert record["active_controller_id"] == "delegate"
    assert record["controller_type"] == CONTROLLER_TYPE_PLAYER_DELEGATE
    assert record["status"] == CONTROL_STATUS_DELEGATED_TO_PLAYER
    assert record["risk_ceiling"] == "medium"
    assert record["duration_type"] == CONTROL_DURATION_UNTIL_REVOKED
    assert record["audit_ref"] == "auth-1"
    assert session.characters["pc_owner"].player_id == "owner"
    assert allowed["ok"] is True
    assert allowed["reason"] == "delegate_controls_character"
    assert denied_owner_action["ok"] is False
    assert denied_owner_action["reason"] == "actor_is_not_active_controller"
    assert "raw consent private text" not in rendered_result
    assert "raw consent private text" not in rendered_event


def test_delegate_cannot_redelegate_and_existing_record_remains_active():
    session = _session_with_owner()
    delegated = apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
    )

    result = apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="delegate",
        target_player_id="other_delegate",
    )

    record = session.control_authority["records"]["pc_owner"]
    assert delegated["ok"] is True
    assert result["ok"] is False
    assert result["error"] == "delegate_cannot_redelegate"
    assert record["active_controller_id"] == "delegate"
    assert record["controller_type"] == CONTROLLER_TYPE_PLAYER_DELEGATE
    assert len(session.control_authority["events"]) == 1


def test_non_owner_cannot_mutate_control_record():
    session = _session_with_owner()

    result = apply_control_change(
        session,
        CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
        "pc_owner",
        requester_player_id="intruder",
    )

    assert result["ok"] is False
    assert result["error"] == "requester_is_not_owner"
    assert session.control_authority == {}


def test_owner_relinquishes_to_system_host_record_shape():
    session = _session_with_owner()

    result = apply_control_change(
        session,
        CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
        "pc_owner",
        requester_player_id="owner",
        risk_ceiling="medium",
        duration_type="until_scene_end",
        audit_ref="host-auth-1",
    )

    record = session.control_authority["records"]["pc_owner"]
    policy = evaluate_hosted_action_policy(
        session,
        "pc_owner",
        actor={"player_id": SYSTEM_HOST_CONTROLLER_ID},
        summary="defend and follow",
    )

    assert result["ok"] is True
    assert record["owner_player_id"] == "owner"
    assert record["active_controller_id"] == SYSTEM_HOST_CONTROLLER_ID
    assert record["controller_type"] == CONTROLLER_TYPE_SYSTEM_HOST
    assert record["status"] == CONTROL_STATUS_HOSTED_BY_SYSTEM
    assert record["risk_ceiling"] == "medium"
    assert record["duration_type"] == "until_scene_end"
    assert policy["ok"] is True
    assert policy["hosted"] is True
    assert policy["controller_type"] == CONTROLLER_TYPE_SYSTEM_HOST
    assert policy["status"] == CONTROL_STATUS_HOSTED_BY_SYSTEM


def test_owner_reclaims_forward_only_without_rewriting_turn_data():
    session = _session_with_owner()
    session.battle = {
        "active": True,
        "turn": {
            "actions_this_round": {"pc_owner": {"summary": "already resolved"}},
            "turn_log": [{"entity_id": "pc_owner", "summary": "old action"}],
        },
    }
    apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        audit_ref="delegate-auth-1",
    )
    previous_battle = deepcopy(session.battle)

    result = apply_control_change(
        session,
        CONTROL_ACTION_RECLAIM,
        "pc_owner",
        requester_player_id="owner",
        audit_ref="reclaim-auth-1",
    )

    record = session.control_authority["records"]["pc_owner"]
    owner_allowed = resolve_control_authority(session, "pc_owner", {"player_id": "owner"})
    delegate_denied = resolve_control_authority(session, "pc_owner", {"player_id": "delegate"})
    reclaim_event = session.control_authority["events"][1]

    assert result["ok"] is True
    assert record["active_controller_id"] == "owner"
    assert record["controller_type"] == CONTROLLER_TYPE_OWNER
    assert record["status"] == CONTROL_STATUS_OWNED
    assert session.battle == previous_battle
    assert owner_allowed["ok"] is True
    assert delegate_denied["ok"] is False
    assert delegate_denied["reason"] == "actor_is_not_active_controller"
    assert len(session.control_authority["events"]) == 2
    assert reclaim_event["action"] == CONTROL_ACTION_RECLAIM
    assert reclaim_event["previous_active_controller_id"] == "delegate"
    assert reclaim_event["previous_controller_type"] == CONTROLLER_TYPE_PLAYER_DELEGATE


def test_status_is_read_only_and_omits_private_consent_reference():
    session = _session_with_owner()
    apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        consent_reference="private consent should stay out",
    )
    previous_store = deepcopy(session.control_authority)

    result = apply_control_change(session, CONTROL_ACTION_STATUS, "pc_owner")
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["event_count"] == 1
    assert result["active_controller_id"] == "delegate"
    assert result["controller_type"] == CONTROLLER_TYPE_PLAYER_DELEGATE
    assert "private consent should stay out" not in rendered
    assert "consent_reference" not in rendered
    assert session.control_authority == previous_store


def test_control_events_survive_session_round_trip_without_consent_leak():
    session = _session_with_owner()
    apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        consent_reference="private consent should not be in events",
    )

    reloaded = GameSession.from_dict(session.to_dict())
    events = reloaded.control_authority["events"]
    rendered_events = json.dumps(events, ensure_ascii=False)

    assert len(events) == 1
    assert events[0]["action"] == CONTROL_ACTION_DELEGATE_TO_PLAYER
    assert events[0]["active_controller_id"] == "delegate"
    assert "private consent should not be in events" not in rendered_events
    assert "consent_reference" not in rendered_events


def test_invalid_risk_and_duration_return_stable_errors_without_mutation():
    session = _session_with_owner()

    bad_risk = apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        risk_ceiling="extreme",
    )
    bad_duration = apply_control_change(
        session,
        CONTROL_ACTION_DELEGATE_TO_PLAYER,
        "pc_owner",
        requester_player_id="owner",
        target_player_id="delegate",
        duration_type="forever",
    )

    assert bad_risk["ok"] is False
    assert bad_risk["error"] == "invalid_risk_ceiling"
    assert bad_duration["ok"] is False
    assert bad_duration["error"] == "invalid_duration_type"
    assert session.control_authority == {}
