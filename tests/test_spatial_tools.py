import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import MAP_TYPE_STRICT_LOCAL, get_map_record
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
