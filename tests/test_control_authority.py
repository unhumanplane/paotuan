from astrbot_plugin_auto_trpg_dm.core.control_authority import (
    CONTROL_STATUS_DELEGATED_TO_PLAYER,
    CONTROLLER_TYPE_PLAYER_DELEGATE,
    control_record_for_character,
    owner_player_id_for_entity,
    project_control_authority,
    resolve_control_authority,
)
from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession


def _session_with_owner() -> GameSession:
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")
    session.player_character_map["owner"] = "pc_owner"
    return session


def test_owner_defaults_to_active_controller_without_control_record():
    session = _session_with_owner()

    result = resolve_control_authority(session, "pc_owner", {"player_id": "owner"})

    assert result["ok"] is True
    assert result["owner_player_id"] == "owner"
    assert result["active_controller_id"] == "owner"
    assert result["controller_type"] == "owner"
    assert result["status"] == "owned"
    assert result["reason"] == "owner_controls_character"


def test_non_owner_speaker_is_not_controller_by_default():
    session = _session_with_owner()

    result = resolve_control_authority(session, "pc_owner", {"player_id": "speaker"})

    assert result["ok"] is False
    assert result["error"] == "character_control_denied"
    assert result["reason"] == "actor_is_not_active_controller"
    assert result["owner_player_id"] == "owner"
    assert result["active_controller_id"] == "owner"
    assert result["requester_player_id"] == "speaker"


def test_no_character_participant_has_no_implicit_control():
    session = _session_with_owner()

    result = resolve_control_authority(session, "missing_pc", {"player_id": "observer"})

    assert result["ok"] is False
    assert result["reason"] == "character_has_no_owner_or_controller"
    assert result["owner_player_id"] == ""
    assert result["active_controller_id"] == ""


def test_explicit_delegate_controls_character_without_changing_owner():
    session = _session_with_owner()
    session.control_authority = {
        "schema_version": 1,
        "records": {
            "pc_owner": {
                "character_id": "pc_owner",
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": CONTROLLER_TYPE_PLAYER_DELEGATE,
                "status": CONTROL_STATUS_DELEGATED_TO_PLAYER,
                "authorized_by": "owner",
                "risk_ceiling": "medium",
                "duration_type": "until_revoked",
                "audit_ref": "auth-1",
            }
        },
    }

    allowed = resolve_control_authority(session, "pc_owner", {"player_id": "delegate"})
    denied_owner_action = resolve_control_authority(session, "pc_owner", {"player_id": "owner"})
    record = control_record_for_character(session, "pc_owner")

    assert allowed["ok"] is True
    assert allowed["owner_player_id"] == "owner"
    assert allowed["active_controller_id"] == "delegate"
    assert allowed["controller_type"] == CONTROLLER_TYPE_PLAYER_DELEGATE
    assert allowed["reason"] == "delegate_controls_character"
    assert allowed["risk_ceiling"] == "medium"
    assert denied_owner_action["ok"] is False
    assert denied_owner_action["reason"] == "actor_is_not_active_controller"
    assert record["owner_player_id"] == "owner"


def test_owner_lookup_prefers_map_store_before_stale_battle_grid():
    session = _session_with_owner()
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "Owner PC",
                    "x": 1,
                    "y": 1,
                    "tags": {"player_id": "owner"},
                }
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    session.battle = {
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "grid": {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "Stale PC",
                    "x": 4,
                    "y": 4,
                    "tags": {"player_id": "intruder"},
                }
            },
        },
    }

    assert owner_player_id_for_entity(session, "pc_owner") == "owner"


def test_entity_character_tag_resolves_canonical_control_record():
    session = _session_with_owner()
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "token-1": {
                    "id": "token-1",
                    "name": "Owner PC Token",
                    "x": 1,
                    "y": 1,
                    "tags": {"character_id": "pc_owner", "player_id": "owner"},
                }
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    session.control_authority = {
        "records": {
            "pc_owner": {
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": CONTROLLER_TYPE_PLAYER_DELEGATE,
                "status": CONTROL_STATUS_DELEGATED_TO_PLAYER,
            }
        }
    }

    allowed = resolve_control_authority(session, "token-1", {"player_id": "delegate"})
    denied_owner = resolve_control_authority(session, "token-1", {"player_id": "owner"})

    assert allowed["ok"] is True
    assert allowed["character_id"] == "pc_owner"
    assert allowed["owner_player_id"] == "owner"
    assert allowed["active_controller_id"] == "delegate"
    assert denied_owner["ok"] is False
    assert denied_owner["reason"] == "actor_is_not_active_controller"


def test_control_projection_is_minimal_for_dm_and_structured_for_ra():
    session = _session_with_owner()
    session.control_authority = {
        "records": {
            "pc_owner": {
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": CONTROLLER_TYPE_PLAYER_DELEGATE,
                "status": CONTROL_STATUS_DELEGATED_TO_PLAYER,
                "authorized_by": "owner",
                "consent_reference": "private text",
                "audit_ref": "audit-1",
            }
        }
    }

    dm_view = project_control_authority(session, "dm_narration_view")
    ra_view = project_control_authority(session, "ra_authority_view")

    assert dm_view["records"][0]["active_controller_id"] == "delegate"
    assert "consent_reference" not in dm_view["records"][0]
    assert "audit_ref" not in dm_view["records"][0]
    assert ra_view["records"][0]["audit_ref"] == "audit-1"
    assert "consent_reference" not in ra_view["records"][0]
