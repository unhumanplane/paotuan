from astrbot_plugin_auto_trpg_dm.core.modes import GameModeStateMachine
from astrbot_plugin_auto_trpg_dm.core.map_core import save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession


def test_battle_state_forces_tactical_mode():
    session = GameSession.new("s")
    session.battle["active"] = True

    mode = GameModeStateMachine().detect(session, "我想回忆一下背景")

    assert mode == GameMode.TACTICAL

def test_active_turn_forces_tactical_mode_even_when_battle_flag_is_false():
    session = GameSession.new("s")
    session.battle = {
        "active": False,
        "turn": {
            "active": True,
            "phase": "character_turn",
            "turn_order": ["pc"],
            "current_entity_id": "pc",
        },
    }

    mode = GameModeStateMachine().detect(session, "status")

    assert mode == GameMode.TACTICAL


def test_suspended_turn_does_not_force_tactical_mode():
    session = GameSession.new("s")
    session.battle = {
        "active": False,
        "turn": {
            "active": True,
            "phase": "suspended",
            "turn_order": ["pc"],
            "current_entity_id": "",
        },
    }

    mode = GameModeStateMachine().detect(session, "old memory")

    assert mode == GameMode.NARRATIVE


def test_active_strict_exploration_map_does_not_force_tactical_mode():
    session = GameSession.new("s")
    session.mode = GameMode.TACTICAL
    save_active_strict_grid(
        session.maps,
        {"width": 4, "height": 4, "cells": [], "entities": {}},
        map_id="strict-room",
        title="Strict room",
    )

    mode = GameModeStateMachine().detect(session, "我想回忆一下旧事")

    assert mode == GameMode.NARRATIVE


def test_grid_keyword_still_enters_tactical_for_strict_exploration():
    session = GameSession.new("s")
    save_active_strict_grid(
        session.maps,
        {"width": 4, "height": 4, "cells": [], "entities": {}},
        map_id="strict-room",
        title="Strict room",
    )

    mode = GameModeStateMachine().detect(session, "地图情况怎么样？")

    assert mode == GameMode.TACTICAL


def test_character_hint_enters_character_creation():
    session = GameSession.new("s")

    mode = GameModeStateMachine().detect(session, "我是一个失忆医生")

    assert mode == GameMode.CHARACTER_CREATION


def test_retired_character_message_does_not_switch_live_campaign_to_character_creation():
    session = GameSession.new("s")
    session.scene["_game_started"] = True
    session.characters["pc_latatos"] = object()
    session.characters["pc_laofei"] = object()
    session.player_character_map = {"p1": "pc_latatos", "p2": "pc_laofei"}

    mode = GameModeStateMachine().detect(session, "去哪里不重要，算是角色退场了")

    assert mode == GameMode.NARRATIVE


def test_retirement_backstory_stays_resolution_in_live_campaign():
    session = GameSession.new("s")
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = object()
    session.player_character_map = {"p1": "pc_zhagu"}

    mode = GameModeStateMachine().detect(
        session,
        "讲述自己在海上的经历，提前“退休”后的渔夫生活，同时打听这趟探险情况",
    )

    assert mode == GameMode.RESOLUTION


def test_explicit_retirement_message_still_stays_narrative_in_live_campaign():
    session = GameSession.new("s")
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = object()
    session.player_character_map = {"p1": "pc_zhagu"}

    mode = GameModeStateMachine().detect(session, "扎古，退休")

    assert mode == GameMode.NARRATIVE

