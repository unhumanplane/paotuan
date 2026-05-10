import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.control_transfer import (
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RECLAIM,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
    SYSTEM_HOST_CONTROLLER_ID,
)
from astrbot_plugin_auto_trpg_dm.core.hosted_action_policy import evaluate_hosted_action_policy
from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.map_request_guard import build_map_request_guard
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.control_tools import ControlTools
from astrbot_plugin_auto_trpg_dm.tools.spatial_tools import SpatialTools


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"stage-3-4-forward-scan-{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def _repo_with_strict_turn(label: str) -> JsonGameRepository:
    repo = _repo(label)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.world_tags["_background_ready"] = True
    session.characters["pc"] = Character(id="pc", name="Owner PC", player_id="owner")
    session.player_character_map["owner"] = "pc"
    session.participants["owner"] = {"display_name": "Owner"}
    session.participants["observer"] = {"display_name": "Observer"}
    session.battle = {
        "active": True,
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "turn_entity_id": "pc",
        "grid": {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "pc": {
                    "id": "pc",
                    "name": "Stale Mirror PC",
                    "x": 4,
                    "y": 4,
                    "move_points": 6,
                    "tags": {"player_id": "observer"},
                }
            },
        },
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc"],
            "current_index": 0,
            "current_entity_id": "pc",
            "actions_this_round": {},
            "turn_log": [],
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "pc": {
                    "id": "pc",
                    "name": "Owner PC",
                    "x": 1,
                    "y": 1,
                    "move_points": 6,
                    "faction": "party",
                    "blocks_move": True,
                    "tags": {"character_id": "pc", "player_id": "owner"},
                }
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    repo.save_session(session)
    return repo


def test_no_character_participant_cannot_gain_map_action_authority_from_focus_or_stale_mirror():
    repo = _repo_with_strict_turn("no_character")

    result = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "observer"}).move_entity("pc", 2, 1)
    )

    saved = repo.load_session("group")
    assert result["ok"] is False
    assert result["error_code"] == "character_control_denied"
    assert result["owner_player_id"] == "owner"
    assert result["active_controller_id"] == "owner"
    assert saved.maps["records"][DEFAULT_STRICT_LOCAL_MAP_ID]["grid"]["entities"]["pc"]["x"] == 1
    assert saved.battle["grid"]["entities"]["pc"]["x"] == 4


def test_owner_delegate_and_reclaim_drive_map_affecting_authority_forward_only():
    repo = _repo_with_strict_turn("delegate_reclaim")

    owner_move = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "owner"}).move_entity("pc", 2, 1)
    )
    delegated = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action=CONTROL_ACTION_DELEGATE_TO_PLAYER,
            character_id="pc",
            target_player_id="delegate",
            audit_ref="delegate-forward-scan",
        )
    )
    denied_owner = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "owner"}).move_entity("pc", 3, 1)
    )
    delegate_move = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "delegate"}).move_entity("pc", 3, 1)
    )
    reclaimed = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action=CONTROL_ACTION_RECLAIM,
            character_id="pc",
            audit_ref="reclaim-forward-scan",
        )
    )
    denied_delegate = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "delegate"}).move_entity("pc", 4, 1)
    )

    saved = repo.load_session("group")
    events = saved.control_authority["events"]
    assert owner_move["ok"] is True
    assert delegated["ok"] is True
    assert denied_owner["ok"] is False
    assert denied_owner["error_code"] == "character_control_denied"
    assert denied_owner["active_controller_id"] == "delegate"
    assert delegate_move["ok"] is True
    assert reclaimed["ok"] is True
    assert denied_delegate["ok"] is False
    assert denied_delegate["active_controller_id"] == "owner"
    assert saved.maps["records"][DEFAULT_STRICT_LOCAL_MAP_ID]["grid"]["entities"]["pc"]["x"] == 3
    assert [event["action"] for event in events] == [CONTROL_ACTION_DELEGATE_TO_PLAYER, CONTROL_ACTION_RECLAIM]


def test_system_host_map_action_still_respects_hosted_policy_and_strict_grid_rules():
    repo = _repo_with_strict_turn("system_host")
    hosted = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action=CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
            character_id="pc",
            risk_ceiling="low",
            audit_ref="host-forward-scan",
        )
    )

    policy = evaluate_hosted_action_policy(
        repo.load_session("group"),
        "pc",
        actor={"player_id": SYSTEM_HOST_CONTROLLER_ID},
        summary="保持掩护并跟随队伍",
    )
    invalid_move = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": SYSTEM_HOST_CONTROLLER_ID}).move_entity("pc", 99, 99)
    )
    valid_move = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": SYSTEM_HOST_CONTROLLER_ID}).move_entity("pc", 2, 1)
    )

    saved = repo.load_session("group")
    assert hosted["ok"] is True
    assert policy["ok"] is True
    assert policy["hosted"] is True
    assert invalid_move["ok"] is False
    assert invalid_move["error_code"] in {"out_of_bounds", "no_path", "target_blocked"}
    assert valid_move["ok"] is True
    assert saved.maps["records"][DEFAULT_STRICT_LOCAL_MAP_ID]["grid"]["entities"]["pc"]["x"] == 2


def test_visual_map_request_guard_is_actor_neutral_and_keeps_text_only_override():
    representative_actors = ("no_character", "owner", "delegate", "system_host")

    for _actor_label in representative_actors:
        guard = build_map_request_guard(
            "画一张当前战场站位图",
            available_tool_names=["render_strict_grid_svg", "move_entity", "control_authority"],
        )
        text_only = build_map_request_guard(
            "用 ASCII 文字地图画一下战场格子，不要生成图片",
            available_tool_names=["render_strict_grid_svg", "move_entity", "control_authority"],
        )

        assert guard.visual_map_request is True
        assert guard.text_only_map_request is False
        assert guard.renderer_attempt_required is True
        assert guard.preferred_renderer_tools == ("render_strict_grid_svg",)
        assert text_only.visual_map_request is True
        assert text_only.text_only_map_request is True
        assert text_only.renderer_attempt_required is False
