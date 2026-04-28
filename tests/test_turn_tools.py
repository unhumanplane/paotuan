import asyncio
from datetime import datetime, timedelta, timezone

from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.turn_tools import TurnTools


def _repo_with_player_turn(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
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


def test_non_owner_cannot_skip_current_player_turn(tmp_path):
    repo = _repo_with_player_turn(tmp_path)
    tools = TurnTools(repo, "group", actor={"player_id": "intruder"})

    result = asyncio.run(tools.turn_control(action="skip_current", summary="替他防御跳过"))

    assert result["ok"] is False
    assert result["error"] == "character_control_denied"
    session = repo.load_session("group")
    assert session.battle["turn"]["current_entity_id"] == "pc_owner"
    assert session.battle["turn"]["actions_this_round"] == {}


def test_non_owner_push_after_timeout_auto_acts_conservatively(tmp_path):
    repo = _repo_with_player_turn(tmp_path)
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
