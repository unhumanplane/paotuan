import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.turn_tools import TurnTools


def _repo_with_player_turn():
    repo = _runtime_repo("player_turn")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.characters["pc_owner"] = Character(id="pc_owner", name="当前玩家", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="下一位", player_id="next")
    session.player_character_map["owner"] = "pc_owner"
    session.player_character_map["next"] = "pc_next"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "当前玩家",
                    "x": 1,
                    "y": 1,
                    "move_points": 6,
                    "attack_range": 1,
                    "faction": "party",
                    "blocks_move": True,
                    "tags": {},
                },
                "pc_next": {
                    "id": "pc_next",
                    "name": "下一位",
                    "x": 2,
                    "y": 1,
                    "move_points": 6,
                    "attack_range": 1,
                    "faction": "party",
                    "blocks_move": True,
                    "tags": {},
                },
            },
        },
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner", "pc_next"],
            "current_index": 0,
            "current_entity_id": "pc_owner",
            "output_limit_chars": 180,
            "timeout_seconds": 120,
            "actions_this_round": {},
            "turn_log": [],
        },
    }
    repo.save_session(session)
    return repo


def _runtime_repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def test_non_owner_cannot_skip_current_player_turn():
    repo = _repo_with_player_turn()
    tools = TurnTools(repo, "group", actor={"player_id": "intruder"})

    result = asyncio.run(tools.turn_control(action="skip_current", summary="替他防御跳过"))

    assert result["ok"] is False
    assert result["error"] == "character_control_denied"
    session = repo.load_session("group")
    assert session.battle["turn"]["current_entity_id"] == "pc_owner"
    assert session.battle["turn"]["actions_this_round"] == {}


def test_non_owner_push_after_timeout_auto_acts_conservatively():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    now = datetime.now(timezone.utc)
    session.battle["turn"]["waiting_since_at"] = (now - timedelta(seconds=150)).isoformat()
    session.battle["turn"]["deadline_at"] = (now - timedelta(seconds=30)).isoformat()
    repo.save_session(session)

    loaded = repo.load_session("group")
    events = TurnTools(repo, "group", actor={"player_id": "intruder"}).apply_turn_timeout_policy(
        loaded,
        "继续推进",
    )

    assert events[0]["type"] == "turn_timeout_auto_action"
    saved = repo.load_session("group")
    assert saved.battle["turn"]["actions_this_round"]["pc_owner"]["source"] == "auto_timeout"
    assert saved.battle["turn"]["current_entity_id"] == "pc_next"
    assert saved.battle["turn"]["deadline_at"]


def test_start_round_derives_order_from_map_store_not_stale_battle_grid():
    repo = _runtime_repo("turn_order_map_store")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_owner"] = Character(id="pc_owner", name="当前玩家", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="下一位", player_id="next")
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "当前玩家", "x": 1, "y": 1, "faction": "party"},
                "pc_next": {"id": "pc_next", "name": "下一位", "x": 2, "y": 1, "faction": "party"},
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    session.battle = {
        "active": True,
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {"stale_enemy": {"id": "stale_enemy", "name": "旧镜像敌人", "x": 5, "y": 5}},
        },
    }
    repo.save_session(session)

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="start_round"))

    assert result["ok"] is True
    assert result["turn"]["turn_order"] == ["pc_next", "pc_owner"]
    assert "stale_enemy" not in result["turn"]["turn_order"]


def test_turn_owner_guard_reads_map_store_before_stale_battle_grid():
    repo = _runtime_repo("turn_owner_map_store")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.characters["pc_owner"] = Character(id="pc_owner", name="当前玩家", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="下一位", player_id="next")
    session.player_character_map["owner"] = "pc_owner"
    session.player_character_map["next"] = "pc_next"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "当前玩家", "x": 1, "y": 1, "tags": {}},
                "pc_next": {"id": "pc_next", "name": "下一位", "x": 2, "y": 1, "tags": {}},
            },
        },
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner", "pc_next"],
            "current_index": 0,
            "current_entity_id": "pc_owner",
            "output_limit_chars": 180,
            "timeout_seconds": 120,
            "actions_this_round": {},
            "turn_log": [],
        },
    }
    repo.save_session(session)
    session = repo.load_session("group")
    map_store_grid = dict(session.battle["grid"])
    map_store_grid["entities"] = {
        "pc_owner": {
            "id": "pc_owner",
            "name": "MapStore Owner",
            "x": 1,
            "y": 1,
            "faction": "party",
            "tags": {"player_id": "owner"},
        },
        "pc_next": {
            "id": "pc_next",
            "name": "下一位",
            "x": 2,
            "y": 1,
            "faction": "party",
            "tags": {"player_id": "next"},
        },
    }
    save_active_strict_grid(session.maps, map_store_grid, map_id=DEFAULT_STRICT_LOCAL_MAP_ID)
    session.battle["map_id"] = DEFAULT_STRICT_LOCAL_MAP_ID
    session.battle["grid"]["entities"]["pc_owner"]["name"] = "Stale Mirror Owner"
    session.battle["grid"]["entities"]["pc_owner"]["tags"] = {"player_id": "intruder"}
    repo.save_session(session)

    denied = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "intruder"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="旧 mirror 不能授权",
        )
    )
    allowed = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "owner"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="MapStore 授权",
            advance_after=False,
        )
    )

    assert denied["ok"] is False
    assert denied["error"] == "character_control_denied"
    assert denied["owner_player_id"] == "owner"
    assert allowed["ok"] is True
    assert allowed["turn"]["current_label"] == "MapStore Owner"


def test_delegated_controller_can_record_turn_action_without_becoming_owner():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    session.control_authority = {
        "records": {
            "pc_owner": {
                "character_id": "pc_owner",
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": "player_delegate",
                "status": "delegated_to_player",
                "authorized_by": "owner",
                "risk_ceiling": "low",
                "duration_type": "until_revoked",
            }
        }
    }
    repo.save_session(session)

    denied_owner = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "owner"}).turn_control(
            action="skip_current",
            current_entity_id="pc_owner",
            summary="owner 不再是当前 controller",
        )
    )
    allowed = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "delegate"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="代控角色采取防御姿态",
            advance_after=False,
        )
    )

    assert allowed["ok"] is True
    assert denied_owner["ok"] is False
    assert denied_owner["error"] == "character_control_denied"
    assert denied_owner["owner_player_id"] == "owner"
    assert denied_owner["active_controller_id"] == "delegate"
