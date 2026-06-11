import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.control_authority import (
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROLLER_TYPE_SYSTEM_HOST,
)
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


def test_non_owner_advance_turn_error_includes_auto_act_hint():
    repo = _repo_with_player_turn()
    tools = TurnTools(repo, "group", actor={"player_id": "intruder"})

    result = asyncio.run(tools.turn_control(action="advance_turn", summary="下一位"))

    assert result["ok"] is False
    assert result["error"] == "turn_advance_requires_owner_or_timeout"
    assert "next_tool_hint" in result
    assert "auto_act_current" in result["message"]
    assert "不要重复调用" in result["message"]


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
    assert "hosted_policy" not in saved.battle["turn"]["actions_this_round"]["pc_owner"]
    assert saved.battle["turn"]["current_entity_id"] == "pc_next"
    assert saved.battle["turn"]["deadline_at"]


def test_set_sequence_mode_enables_strict_turn_order():
    repo = _repo_with_player_turn()
    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="set_sequence_mode",
            sequence_mode="strict",
            order_source="existing_state",
            summary="Use standard initiative order.",
        )
    )

    assert result["ok"] is True
    assert result["turn"]["sequence_mode"] == "strict"
    assert result["turn"]["strict_sequence"] is True
    assert "硬性行动指针" in result["llm_instruction"]
    assert repo.load_session("group").battle["turn"]["sequence_mode"] == "strict"


def test_set_sequence_mode_requires_declared_order_source_for_strict():
    repo = _repo_with_player_turn()

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="set_sequence_mode",
            sequence_mode="strict",
            summary="Player asked for strict turns.",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "strict_sequence_requires_order_source"
    assert repo.load_session("group").battle["turn"].get("sequence_mode") != "strict"


def test_player_facing_strict_alias_does_not_enable_strict_sequence():
    repo = _repo_with_player_turn()

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="严格回合制"))

    assert result["ok"] is False
    assert result["error"] == "unsupported_turn_control_action"
    assert repo.load_session("group").battle["turn"].get("sequence_mode") != "strict"


def test_strict_start_round_rejects_player_supplied_order_without_rule_source():
    repo = _runtime_repo("strict_rejects_player_order")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="start_round",
            turn_order=["pc_player_choice", "enemy_player_choice"],
            sequence_mode="strict",
            summary="Player supplied a preferred strict order.",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "strict_sequence_requires_rule_order"
    assert (repo.load_session("group").battle.get("turn") or {}).get("sequence_mode") != "strict"


def test_strict_start_round_accepts_rule_initiative_source():
    repo = _runtime_repo("strict_rule_initiative_order")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="start_round",
            turn_order=["pc_won_initiative", "enemy_lost_initiative"],
            sequence_mode="strict",
            order_source="rule_initiative",
            summary="Initiative check established the order.",
        )
    )

    assert result["ok"] is True
    assert result["turn"]["sequence_mode"] == "strict"
    assert result["turn"]["turn_order"] == ["pc_won_initiative", "enemy_lost_initiative"]


def test_strict_sequence_blocks_actor_from_resolving_later_owned_turn():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    session.battle["turn"]["sequence_mode"] = "strict"
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "next"}).turn_control(
            action="record_action",
            current_entity_id="pc_next",
            summary="I act before the current actor.",
            advance_after=False,
        )
    )

    assert result["ok"] is False
    assert result["error"] == "wrong_turn_actor"
    assert result["sequence_mode"] == "strict"
    saved = repo.load_session("group")
    assert saved.battle["turn"]["actions_this_round"] == {}
    assert saved.battle["turn"]["current_entity_id"] == "pc_owner"


def test_flexible_sequence_still_allows_actor_owned_later_turn():
    repo = _repo_with_player_turn()

    result = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "next"}).turn_control(
            action="record_action",
            current_entity_id="pc_next",
            summary="I take my turn before the suggested anchor.",
            advance_after=False,
        )
    )

    assert result["ok"] is True
    saved = repo.load_session("group")
    assert saved.battle["turn"]["sequence_mode"] == "flexible"
    assert "pc_next" in saved.battle["turn"]["actions_this_round"]
    assert saved.battle["turn"]["current_entity_id"] == "pc_owner"


def test_strict_sequence_timeout_policy_does_not_allow_out_of_order_actor_action():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    session.battle["turn"]["sequence_mode"] = "strict"
    repo.save_session(session)

    events = TurnTools(repo, "group", actor={"player_id": "next"}).apply_turn_timeout_policy(
        repo.load_session("group"),
        "我攻击最近的敌人",
    )

    assert events[0]["type"] == "turn_strict_order_waiting"
    assert events[0]["actor_entity_id"] == "pc_next"
    assert events[0]["current_entity_id"] == "pc_owner"
    saved = repo.load_session("group")
    assert saved.battle["turn"]["current_entity_id"] == "pc_owner"
    assert saved.battle["turn"]["actions_this_round"] == {}


def test_system_host_timeout_action_records_hosted_policy_metadata():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    session.control_authority = {
        "records": {
            "pc_owner": {
                "character_id": "pc_owner",
                "owner_player_id": "owner",
                "active_controller_id": "__system__",
                "controller_type": CONTROLLER_TYPE_SYSTEM_HOST,
                "status": CONTROL_STATUS_HOSTED_BY_SYSTEM,
                "authorized_by": "owner",
                "risk_ceiling": "low",
                "audit_ref": "host-auth-1",
            }
        }
    }
    now = datetime.now(timezone.utc)
    session.battle["turn"]["waiting_since_at"] = (now - timedelta(seconds=150)).isoformat()
    session.battle["turn"]["deadline_at"] = (now - timedelta(seconds=30)).isoformat()
    repo.save_session(session)

    events = TurnTools(repo, "group", actor={"player_id": "intruder"}).apply_turn_timeout_policy(
        repo.load_session("group"),
        "继续推进",
    )

    action = repo.load_session("group").battle["turn"]["actions_this_round"]["pc_owner"]
    assert events[0]["type"] == "turn_timeout_auto_action"
    assert events[0]["hosted_policy"]["audit_ref"] == "host-auth-1"
    assert action["source"] == "hosted_timeout"
    assert action["hosted_policy"]["ok"] is True
    assert action["hosted_policy"]["risk"] == "low"
    assert action["hosted_policy"]["audit_ref"] == "host-auth-1"


def test_system_host_auto_action_downgrades_high_risk_request_above_ceiling():
    repo = _repo_with_player_turn()
    session = repo.load_session("group")
    session.control_authority = {
        "records": {
            "pc_owner": {
                "character_id": "pc_owner",
                "owner_player_id": "owner",
                "active_controller_id": "__system__",
                "controller_type": CONTROLLER_TYPE_SYSTEM_HOST,
                "status": CONTROL_STATUS_HOSTED_BY_SYSTEM,
                "authorized_by": "owner",
                "risk_ceiling": "low",
                "audit_ref": "host-auth-2",
            }
        }
    }
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "__system__"}).turn_control(
            action="auto_act_current",
            current_entity_id="pc_owner",
            summary="消耗大招并签下不可逆契约",
            reason="托管期间自动推进",
            advance_after=False,
        )
    )

    action = repo.load_session("group").battle["turn"]["actions_this_round"]["pc_owner"]
    assert result["ok"] is True
    assert result["auto_action"]["hosted_policy"]["ok"] is False
    assert result["auto_action"]["hosted_policy"]["reason"] == "hosted_action_exceeds_risk_ceiling"
    assert action["source"] == "hosted_auto"
    assert action["hosted_policy"]["fallback_policy"] == "defend_or_follow"
    assert "不可逆契约" not in action["summary"]
    assert "不消耗稀缺资源" in action["summary"]


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


def test_start_round_excludes_corpse_entities_from_turn_order():
    repo = _runtime_repo("turn_order_excludes_corpse")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "pc": {"id": "pc", "name": "PC", "x": 1, "y": 1, "faction": "party", "blocks_move": True},
                "enemy_alive": {"id": "enemy_alive", "name": "Enemy", "x": 3, "y": 1, "faction": "hostile", "blocks_move": True},
                "enemy_dead": {
                    "id": "enemy_dead",
                    "name": "Dead enemy",
                    "x": 2,
                    "y": 1,
                    "faction": "hostile",
                    "blocks_move": True,
                    "tags": {"status": "dead corpse"},
                },
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    session.battle = {"active": True, "map_id": DEFAULT_STRICT_LOCAL_MAP_ID, "turn": {"active": False}}
    repo.save_session(session)

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="start_round"))

    assert result["ok"] is True
    assert result["turn"]["turn_order"] == ["pc", "enemy_alive"]
    assert "enemy_dead" not in result["turn"]["pending_entity_ids"]


def test_start_round_without_map_requires_explicit_turn_order():
    repo = _runtime_repo("turn_order_requires_context")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_home"] = Character(id="pc_home", name="Offscreen sleeper", player_id="home")
    session.characters["pc_scene"] = Character(id="pc_scene", name="Scene actor", player_id="scene")
    session.player_character_map["home"] = "pc_home"
    session.player_character_map["scene"] = "pc_scene"
    repo.save_session(session)

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="start_round"))

    assert result["ok"] is False
    assert result["error"] == "empty_turn_order"
    assert result["requires_explicit_turn_order"] is True
    saved = repo.load_session("group")
    assert not (saved.battle.get("turn") or {}).get("active")


def test_start_scene_resolution_without_map_requires_explicit_turn_order():
    repo = _runtime_repo("scene_resolution_requires_context")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_home"] = Character(id="pc_home", name="Offscreen sleeper", player_id="home")
    session.characters["pc_scene"] = Character(id="pc_scene", name="Scene actor", player_id="scene")
    session.player_character_map["home"] = "pc_home"
    session.player_character_map["scene"] = "pc_scene"
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="start_scene_resolution",
            summary="Only the scene actor is present, but no explicit order was supplied.",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "empty_turn_order"
    assert result["requires_explicit_turn_order"] is True
    saved = repo.load_session("group")
    assert not (saved.battle.get("turn") or {}).get("active")
    assert (saved.battle.get("turn") or {}).get("turn_order", []) == []


def test_start_scene_resolution_accepts_explicit_turn_order_without_map():
    repo = _runtime_repo("scene_resolution_explicit_context")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_home"] = Character(id="pc_home", name="Offscreen sleeper", player_id="home")
    session.characters["pc_scene"] = Character(id="pc_scene", name="Scene actor", player_id="scene")
    session.player_character_map["home"] = "pc_home"
    session.player_character_map["scene"] = "pc_scene"
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="start_scene_resolution",
            turn_order=["pc_scene"],
            summary="Scene actor resolves the quarry skirmish.",
        )
    )

    assert result["ok"] is True
    assert result["turn"]["active"] is True
    assert result["turn"]["phase"] == "scene_resolution"
    assert result["turn"]["turn_order"] == ["pc_scene"]
    assert repo.load_session("group").battle["active"] is True


def test_start_round_marks_battle_active_when_only_turn_order_exists():
    repo = _runtime_repo("turn_order_activates_battle")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="start_round",
            turn_order=["ambusher_1", "pc_kade"],
        )
    )

    assert result["ok"] is True
    saved = repo.load_session("group")
    assert saved.battle["active"] is True
    assert saved.battle["turn"]["active"] is True


def test_end_encounter_accepts_routed_enemy_terminal_evidence():
    repo = _runtime_repo("routed_enemy_end_encounter")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "ambusher_3",
        "turn": {
            "active": True,
            "round": 2,
            "phase": "character_turn",
            "turn_order": ["ambusher_1", "ambusher_2", "ambusher_3", "pc_kade"],
            "current_index": 2,
            "current_entity_id": "ambusher_3",
            "output_limit_chars": 1440,
            "actions_this_round": {},
            "turn_log": [
                {
                    "type": "scene_resolution_start",
                    "summary": "伏击者士气检定溃散，所有残余敌方单位开始朝东北山沟方向溃退。",
                    "reason": "enemy_morale_check returned routed",
                }
            ],
        },
    }
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="end_encounter",
            summary="伏击者全线溃散，残敌向东北山沟逃窜，战斗结束。",
            reason="士气检定溃散，所有残敌已溃退。",
        )
    )

    assert result["ok"] is True
    saved = repo.load_session("group")
    assert saved.battle["active"] is False
    assert saved.battle["turn"]["active"] is False
    assert saved.battle["turn"]["phase"] == "ended"
    assert saved.mode == GameMode.NARRATIVE


def test_end_encounter_clears_stale_timeout_pause():
    repo = _runtime_repo("end_encounter_clears_timeout_pause")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.scene["_dm_paused"] = True
    session.scene["_dm_pause_reason"] = "本轮已有 2/4名玩家超时，达到半数，流程已自动暂停。恢复时发 `/dm resume`。"
    session.scene["_dm_paused_by"] = {"player_id": "__heartbeat__", "display_name": "本地心跳"}
    session.scene["_dm_paused_at"] = "2026-05-18T02:45:03+00:00"
    session.scene["_dm_pause_source"] = "turn_timeout"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_yaka",
        "turn": {
            "active": True,
            "round": 3,
            "phase": "character_turn",
            "turn_order": ["ambusher_1", "pc_yaka"],
            "current_index": 1,
            "current_entity_id": "pc_yaka",
            "actions_this_round": {},
            "turn_log": [
                {
                    "type": "scene_resolution_start",
                    "summary": "伏击者士气溃散，所有残敌逃窜，战斗结束。",
                    "reason": "routed",
                }
            ],
        },
    }
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group").turn_control(
            action="end_encounter",
            summary="敌方全线溃退，战斗结束。",
            reason="所有残敌逃离。",
        )
    )

    saved = repo.load_session("group")
    assert result["ok"] is True
    assert saved.scene["_dm_paused"] is False
    assert "_dm_pause_reason" not in saved.scene
    assert saved.scene["_dm_pause_cleared_by"] == "encounter_end"
    assert saved.battle["turn"]["active"] is False


def test_turn_status_uses_public_label_for_unmapped_numbered_enemy_slug():
    repo = _runtime_repo("turn_public_enemy_label")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "ambusher_2",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["ambusher_1", "ambusher_2"],
            "current_index": 1,
            "current_entity_id": "ambusher_2",
            "output_limit_chars": 1440,
            "actions_this_round": {},
        },
    }
    repo.save_session(session)

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="status"))

    assert result["turn"]["current_label"] == "伏击者 2"
    assert result["turn"]["current_entity_id"] == "ambusher_2"


def test_unmapped_enemy_auto_action_does_not_store_raw_slug_or_player_timeout_wording():
    repo = _runtime_repo("turn_enemy_auto_public_label")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "ambusher_2",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["ambusher_2", "ambusher_3"],
            "current_index": 0,
            "current_entity_id": "ambusher_2",
            "output_limit_chars": 1440,
            "actions_this_round": {},
            "turn_log": [],
        },
    }
    repo.save_session(session)

    result = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "__heartbeat__"}).turn_control(
            action="auto_act_current",
            current_entity_id="ambusher_2",
            summary="ambusher_2超过 120 秒未响应，本地心跳采取保守行动。",
            reason="heartbeat",
            advance_after=True,
        )
    )

    saved = repo.load_session("group")
    summary = saved.battle["turn"]["actions_this_round"]["ambusher_2"]["summary"]
    log_summary = saved.battle["turn"]["turn_log"][-1]["summary"]
    assert result["turn"]["current_label"] == "伏击者 3"
    assert "ambusher_2" not in summary
    assert "ambusher_2" not in log_summary
    assert "未响应" not in summary
    assert "伏击者 2" in summary
    assert "保守推进" in summary


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


def test_advance_turn_skips_current_entity_that_became_corpse():
    repo = _runtime_repo("turn_skip_corpse_current")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "grid": {"width": 5, "height": 5, "cells": [], "entities": {}},
        "turn_entity_id": "dead_guard",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["dead_guard", "live_guard"],
            "current_index": 0,
            "current_entity_id": "dead_guard",
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
                "dead_guard": {
                    "id": "dead_guard",
                    "name": "Dead Guard",
                    "x": 1,
                    "y": 1,
                    "faction": "hostile",
                    "life_state": "corpse",
                    "blocks_move": False,
                    "tags": {"status": "dead corpse"},
                },
                "live_guard": {
                    "id": "live_guard",
                    "name": "Live Guard",
                    "x": 2,
                    "y": 1,
                    "faction": "hostile",
                    "blocks_move": True,
                    "tags": {},
                },
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    repo.save_session(session)

    result = asyncio.run(TurnTools(repo, "group").turn_control(action="advance_turn"))

    assert result["ok"] is True
    assert result["turn"]["current_entity_id"] == "live_guard"
    assert repo.load_session("group").battle["turn_entity_id"] == "live_guard"
