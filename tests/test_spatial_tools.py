import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, MAP_TYPE_STRICT_LOCAL, get_map_record
from astrbot_plugin_auto_trpg_dm.core.map_delivery_cadence import MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.spatial_tools import SpatialTools


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def _ready_session() -> GameSession:
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    return session


def test_create_grid_writes_strict_local_map_and_legacy_mirror():
    repo = _repo("create_grid")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")

    result = asyncio.run(tools.create_grid(width=5, height=4))

    assert result["ok"] is True
    session = repo.load_session("group")
    assert session.mode == GameMode.TACTICAL
    assert session.battle["map_id"] == result["map_id"]
    assert session.battle["grid"]["width"] == 5
    record = get_map_record(session.maps, result["map_id"])
    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert record["grid"] == session.battle["grid"]


def test_create_grid_uses_default_strict_map_authority_with_auditable_source():
    repo = _repo("create_grid_default_map")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")

    result = asyncio.run(tools.create_grid(width=5, height=4))

    assert result["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    session = repo.load_session("group")
    assert session.maps["active_strict_map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    assert session.battle["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    record = get_map_record(session.maps, DEFAULT_STRICT_LOCAL_MAP_ID)
    assert record["archive_identity"]["source"] == "spatial_tool_create_grid"
    assert record["archive_identity"]["authority_assumption"] == "spatial_tool_create_grid"


def test_create_grid_resets_map_store_authority_before_legacy_mirror():
    repo = _repo("create_grid_reset_authority")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))
    session = repo.load_session("group")
    session.battle["grid"] = {"width": 9, "height": 9, "cells": [], "entities": {"stale": {"x": 8, "y": 8}}}
    repo.save_session(session)

    result = asyncio.run(tools.create_grid(width=3, height=3))

    assert result["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    session = repo.load_session("group")
    record = get_map_record(session.maps, DEFAULT_STRICT_LOCAL_MAP_ID)
    assert record["grid"]["width"] == 3
    assert record["grid"] == session.battle["grid"]
    assert "stale" not in record["grid"]["entities"]


def test_place_entity_updates_strict_local_map_and_legacy_mirror():
    repo = _repo("place_entity")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))

    result = asyncio.run(tools.place_entity("pc", "PC", 1, 2))

    assert result["ok"] is True
    session = repo.load_session("group")
    map_id = session.battle["map_id"]
    assert session.battle["grid"]["entities"]["pc"]["x"] == 1
    assert get_map_record(session.maps, map_id)["grid"]["entities"]["pc"]["y"] == 2


def test_strict_map_revision_advances_when_spatial_facts_change():
    repo = _repo("strict_map_revision")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")

    asyncio.run(tools.create_grid(width=5, height=5))
    created = repo.load_session("group")
    map_id = created.battle["map_id"]
    assert get_map_record(created.maps, map_id)["record_version"] == 1

    asyncio.run(tools.place_entity("pc", "PC", 1, 1, move_points=6))
    placed = repo.load_session("group")
    assert get_map_record(placed.maps, map_id)["record_version"] == 2

    result = asyncio.run(tools.move_entity("pc", 2, 1))

    assert result["ok"] is True
    moved = repo.load_session("group")
    assert get_map_record(moved.maps, map_id)["record_version"] == 3


def test_move_entity_prefers_strict_local_map_over_stale_legacy_mirror():
    repo = _repo("move_entity")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("pc", "PC", 1, 1, move_points=6))

    session = repo.load_session("group")
    session.battle["grid"]["entities"]["pc"]["x"] = 4
    repo.save_session(session)
    result = asyncio.run(tools.move_entity("pc", 2, 1))

    assert result["ok"] is True
    session = repo.load_session("group")
    map_id = session.battle["map_id"]
    assert session.battle["grid"]["entities"]["pc"]["x"] == 2
    assert get_map_record(session.maps, map_id)["grid"]["entities"]["pc"]["x"] == 2


def test_check_attack_vector_prefers_strict_local_map_over_stale_legacy_mirror():
    repo = _repo("attack_vector")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=8, height=3))
    asyncio.run(tools.place_entity("pc", "PC", 0, 1, attack_range=5))
    asyncio.run(tools.place_entity("npc", "NPC", 5, 1, blocks_move=True))

    session = repo.load_session("group")
    session.battle["grid"]["entities"]["npc"]["x"] = 7
    repo.save_session(session)

    result = asyncio.run(tools.check_attack_vector("pc", "npc"))

    assert result["ok"] is True
    assert result["can_attack"] is True
    assert result["distance"] == 5
    assert "calculation" not in result


def test_move_entity_blocked_by_door_enqueues_spatial_judgment_map_once():
    repo = _repo("move_entity_blocked_map")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=4, height=3, cells=[{"x": 2, "y": 1, "terrain": "door", "blocks_move": True}]))
    asyncio.run(tools.place_entity("pc", "PC", 1, 1, move_points=4))

    result = asyncio.run(tools.move_entity("pc", 2, 1))
    repeat = asyncio.run(tools.move_entity("pc", 2, 1))

    saved = repo.load_session("group")
    pending_maps = [item for item in saved.scene.get("_pending_outputs", []) if item.get("type") == "svg_map"]
    assert result["ok"] is False
    assert result["error_code"] == "terrain_blocks_move:door"
    assert result["requires_interaction"] == "open_unlock_force_or_destroy_door"
    assert result["auto_map"]["queued"] is True
    assert repeat["auto_map"]["queued"] is False
    assert repeat["auto_map"]["delivery_reason"] == "duplicate_suppressed"
    assert len(pending_maps) == 1
    assert pending_maps[0]["delivery_trigger"] == MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION
    assert pending_maps[0]["render_type"] == "strict_grid_svg"
    assert Path(pending_maps[0]["path"]).exists()


def test_check_attack_vector_enqueues_spatial_judgment_map():
    repo = _repo("attack_vector_map")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(
        tools.create_grid(
            width=6,
            height=3,
            cells=[{"x": 2, "y": 1, "terrain": "stone_wall", "blocks_los": True}],
        )
    )
    asyncio.run(tools.place_entity("pc", "PC", 0, 1, attack_range=10))
    asyncio.run(tools.place_entity("npc", "NPC", 5, 1))

    result = asyncio.run(tools.check_attack_vector("pc", "npc"))

    saved = repo.load_session("group")
    pending_maps = [item for item in saved.scene.get("_pending_outputs", []) if item.get("type") == "svg_map"]
    assert result["ok"] is True
    assert result["can_attack"] is False
    assert result["reason"] == "line_of_sight_blocked"
    assert result["auto_map"]["queued"] is True
    assert len(pending_maps) == 1
    assert pending_maps[0]["delivery_trigger"] == MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION


def test_legacy_battle_grid_is_migrated_on_spatial_tool_load():
    repo = _repo("legacy_migration")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "grid": {"width": 5, "height": 5, "cells": [], "entities": {"pc": {"id": "pc", "name": "PC", "x": 1, "y": 1}}},
        "turn_entity_id": "",
    }
    repo.save_session(session)

    result = asyncio.run(SpatialTools(repo, "group").get_battle_snapshot())

    assert result["ok"] is True
    assert "battle" not in result
    assert "grid" not in result
    assert result["battle_status"]["active"] is True
    assert result["tactical_map"]["map_id"]
    assert result["tactical_map"]["width"] == 5
    assert result["tactical_map"]["height"] == 5
    assert result["tactical_map"]["entities"][0]["id"] == "pc"
    session = repo.load_session("group")
    assert session.battle["map_id"]
    record = get_map_record(session.maps, session.battle["map_id"])
    assert record["archive_identity"]["migration_source"] == "battle.grid"
    assert record["grid"]["entities"]["pc"]["x"] == 1


def test_get_battle_snapshot_returns_safe_tactical_summary_not_raw_grid():
    repo = _repo("battle_snapshot_safe")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=6, height=4, cells=[{"x": 2, "y": 1, "terrain": "rubble", "cover": 2}]))
    asyncio.run(tools.place_entity("pc", "Scout", 1, 2, faction="party", tags={"secret": "do-not-project"}))

    result = asyncio.run(tools.get_battle_snapshot())

    assert result["ok"] is True
    assert "battle" not in result
    assert "grid" not in result
    assert result["battle_status"]["active"] is True
    assert result["battle_status"]["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    assert result["battle_status"]["turn_entity_id"] == ""
    assert result["battle_status"]["turn"]["phase"] == "idle"
    assert result["tactical_map"]["source"] == "map_store"
    assert result["tactical_map"]["width"] == 6
    assert result["tactical_map"]["height"] == 4
    assert result["tactical_map"]["entity_count"] == 1
    assert result["tactical_map"]["entities"] == [
        {
            "id": "pc",
            "name": "Scout",
            "x": 1,
            "y": 2,
            "faction": "party",
            "move_points": 6,
            "attack_range": 1,
            "blocks_move": True,
            "life_state": "active",
        }
    ]
    assert result["tactical_map"]["terrain_feature_count"] == 1
    assert result["tactical_map"]["terrain_features"] == [
        {
            "x": 2,
            "y": 1,
            "terrain": "rubble",
            "cost": 1,
            "blocks_move": False,
            "blocks_los": False,
            "cover": 2,
        }
    ]
    assert result["compatibility"]["legacy_mirror_present"] is True
    assert result["compatibility"]["legacy_mirror_authoritative"] is False


def test_update_entity_state_marks_corpse_nonblocking_and_advances_map_revision():
    repo = _repo("update_entity_state_corpse")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("pc", "PC", 0, 1, move_points=4))
    asyncio.run(tools.place_entity("guard", "Guard", 1, 1, blocks_move=True))
    session = repo.load_session("group")
    map_id = session.battle["map_id"]
    before_version = get_map_record(session.maps, map_id)["record_version"]

    result = asyncio.run(
        tools.update_entity_state(
            "guard",
            life_state="corpse",
            status="shot dead",
            tags={"cause": "rifle"},
        )
    )
    move_result = asyncio.run(tools.move_entity("pc", 2, 1))

    assert result["ok"] is True
    assert result["entity"]["life_state"] == "corpse"
    assert result["entity"]["blocks_move"] is False
    assert move_result["ok"] is True
    session = repo.load_session("group")
    entity = session.battle["grid"]["entities"]["guard"]
    assert entity["life_state"] == "corpse"
    assert entity["blocks_move"] is False
    assert entity["tags"]["status"] == "shot dead"
    assert get_map_record(session.maps, map_id)["record_version"] == before_version + 2


def test_update_entity_state_enqueues_spatial_judgment_map():
    repo = _repo("update_entity_state_map")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("guard", "Guard", 1, 1, blocks_move=True))

    result = asyncio.run(tools.update_entity_state("guard", life_state="incapacitated", status="down"))

    saved = repo.load_session("group")
    pending_maps = [item for item in saved.scene.get("_pending_outputs", []) if item.get("type") == "svg_map"]
    assert result["ok"] is True
    assert result["auto_map"]["queued"] is True
    assert len(pending_maps) == 1
    assert pending_maps[0]["delivery_trigger"] == MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION


def test_update_entity_state_can_restore_active_over_old_status_text():
    repo = _repo("update_entity_state_active")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("guard", "Guard", 1, 1, blocks_move=True, tags={"status": "dead corpse"}))

    result = asyncio.run(tools.update_entity_state("guard", life_state="active", status="back on feet"))
    snapshot = asyncio.run(tools.get_battle_snapshot())

    assert result["ok"] is True
    assert result["entity"]["life_state"] == "active"
    assert snapshot["tactical_map"]["entities"][0]["life_state"] == "active"


def test_update_entity_state_returns_not_found_without_creating_entity():
    repo = _repo("update_entity_state_missing")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group")
    asyncio.run(tools.create_grid(width=5, height=5))

    result = asyncio.run(tools.update_entity_state("missing", life_state="corpse"))

    assert result["ok"] is False
    assert result["error_code"] == "entity_not_found"
    assert "missing" not in repo.load_session("group").battle["grid"]["entities"]


def test_get_battle_snapshot_marks_active_turn_as_active_combat_status():
    repo = _repo("battle_snapshot_active_turn")
    session = GameSession.new("group")
    session.battle = {
        "active": False,
        "turn_entity_id": "pc",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc"],
            "current_index": 0,
            "current_entity_id": "pc",
        },
    }
    repo.save_session(session)

    result = asyncio.run(SpatialTools(repo, "group").get_battle_snapshot())

    assert result["ok"] is True
    assert result["battle_status"]["active"] is True
    assert result["battle_status"]["turn"]["active"] is True
    assert result["battle_status"]["turn"]["phase"] == "character_turn"


def test_get_battle_snapshot_does_not_mark_suspended_turn_active():
    repo = _repo("battle_snapshot_suspended_turn")
    session = GameSession.new("group")
    session.battle = {
        "active": False,
        "turn_entity_id": "",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "suspended",
            "turn_order": ["pc"],
            "current_index": -1,
            "current_entity_id": "",
        },
    }
    repo.save_session(session)

    result = asyncio.run(SpatialTools(repo, "group").get_battle_snapshot())

    assert result["ok"] is True
    assert result["battle_status"]["active"] is False
    assert result["battle_status"]["turn"]["active"] is True
    assert result["battle_status"]["turn"]["phase"] == "suspended"


def test_spatial_turn_guard_reads_map_store_owner_before_stale_battle_grid():
    repo = _repo("spatial_turn_guard_map_store")
    repo.save_session(_ready_session())
    tools = SpatialTools(repo, "group", actor={"player_id": "owner"})
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("pc", "MapStore PC", 1, 1, faction="party", tags={"player_id": "owner"}))
    session = repo.load_session("group")
    session.battle["turn"] = {
        "active": True,
        "round": 1,
        "phase": "character_turn",
        "turn_order": ["pc"],
        "current_index": 0,
        "current_entity_id": "pc",
        "actions_this_round": {},
    }
    session.battle["turn_entity_id"] = "pc"
    session.battle["grid"]["entities"]["pc"]["tags"] = {"player_id": "intruder"}
    repo.save_session(session)

    result = asyncio.run(tools.move_entity("pc", 2, 1))

    assert result["ok"] is True
    session = repo.load_session("group")
    assert session.battle["grid"]["entities"]["pc"]["x"] == 2


def test_delegated_controller_can_move_turn_actor_without_becoming_owner():
    repo = _repo("spatial_delegated_controller")
    session = _ready_session()
    session.characters["pc"] = Character(id="pc", name="Delegated PC", player_id="owner")
    session.player_character_map["owner"] = "pc"
    repo.save_session(session)
    tools = SpatialTools(repo, "group", actor={"player_id": "owner"})
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("pc", "Delegated PC", 1, 1, faction="party"))
    session = repo.load_session("group")
    session.control_authority = {
        "records": {
            "pc": {
                "character_id": "pc",
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": "player_delegate",
                "status": "delegated_to_player",
                "authorized_by": "owner",
            }
        }
    }
    session.battle["turn"] = {
        "active": True,
        "round": 1,
        "phase": "character_turn",
        "turn_order": ["pc"],
        "current_index": 0,
        "current_entity_id": "pc",
        "actions_this_round": {},
    }
    session.battle["turn_entity_id"] = "pc"
    repo.save_session(session)

    denied_owner = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "owner"}).move_entity("pc", 2, 1)
    )
    allowed_delegate = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "delegate"}).move_entity("pc", 2, 1)
    )

    assert denied_owner["ok"] is False
    assert denied_owner["error_code"] == "character_control_denied"
    assert denied_owner["owner_player_id"] == "owner"
    assert denied_owner["active_controller_id"] == "delegate"
    assert allowed_delegate["ok"] is True


def test_strict_sequence_blocks_spatial_action_for_later_owned_actor():
    repo = _repo("spatial_strict_sequence")
    session = _ready_session()
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="Next PC", player_id="next")
    session.player_character_map["owner"] = "pc_owner"
    session.player_character_map["next"] = "pc_next"
    repo.save_session(session)
    tools = SpatialTools(repo, "group", actor={"player_id": "owner"})
    asyncio.run(tools.create_grid(width=5, height=5))
    asyncio.run(tools.place_entity("pc_owner", "Owner PC", 1, 1, faction="party"))
    asyncio.run(tools.place_entity("pc_next", "Next PC", 2, 1, faction="party"))
    session = repo.load_session("group")
    session.battle["turn"] = {
        "active": True,
        "round": 1,
        "phase": "character_turn",
        "turn_order": ["pc_owner", "pc_next"],
        "current_index": 0,
        "current_entity_id": "pc_owner",
        "sequence_mode": "strict",
        "actions_this_round": {},
    }
    session.battle["turn_entity_id"] = "pc_owner"
    repo.save_session(session)

    result = asyncio.run(
        SpatialTools(repo, "group", actor={"player_id": "next"}).move_entity("pc_next", 3, 1)
    )

    assert result["ok"] is False
    assert result["error_code"] == "wrong_turn_actor"
    assert result["sequence_mode"] == "strict"
    saved = repo.load_session("group")
    assert saved.battle["grid"]["entities"]["pc_next"]["x"] == 2
