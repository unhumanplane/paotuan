import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, MAP_TYPE_STRICT_LOCAL, get_map_record
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
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
    session = repo.load_session("group")
    assert session.battle["map_id"]
    record = get_map_record(session.maps, session.battle["map_id"])
    assert record["archive_identity"]["migration_source"] == "battle.grid"
    assert record["grid"]["entities"]["pc"]["x"] == 1
