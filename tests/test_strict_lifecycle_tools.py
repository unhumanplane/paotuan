import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED,
    MAP_LIFECYCLE_ACTIVE_EXPLORATION,
    get_map_record,
    get_strict_map_lifecycle,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.strict_lifecycle_tools import StrictLifecycleTools


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def _ready_session() -> GameSession:
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    return session


def test_create_strict_map_does_not_start_combat():
    repo = _repo("create_strict_map")
    repo.save_session(_ready_session())
    tools = StrictLifecycleTools(repo, "group")

    result = asyncio.run(tools.create_strict_map(width=6, height=4, title="Courtyard"))

    assert result["ok"] is True
    assert result["battle_active"] is False
    assert result["lifecycle"] == MAP_LIFECYCLE_ACTIVE_EXPLORATION
    session = repo.load_session("group")
    assert session.mode == GameMode.NARRATIVE
    assert session.battle["active"] is False
    assert session.battle.get("map_id", "") == ""
    assert get_strict_map_lifecycle(session.maps, result["map_id"])["combat_linked"] is False
    assert get_map_record(session.maps, result["map_id"])["grid"]["width"] == 6


def test_start_combat_on_map_links_battle_to_existing_strict_map():
    repo = _repo("start_combat_on_map")
    repo.save_session(_ready_session())
    tools = StrictLifecycleTools(repo, "group")
    created = asyncio.run(tools.create_strict_map(width=7, height=5))

    result = asyncio.run(tools.start_combat_on_map(map_id=created["map_id"], summary="Ambush"))

    assert result["ok"] is True
    assert result["battle_active"] is True
    assert result["lifecycle"] == MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED
    session = repo.load_session("group")
    assert session.mode == GameMode.TACTICAL
    assert session.battle["active"] is True
    assert session.battle["map_id"] == created["map_id"]
    assert session.battle["grid"] == get_map_record(session.maps, created["map_id"])["grid"]


def test_end_combat_preserves_strict_map_as_active_exploration():
    repo = _repo("end_combat")
    repo.save_session(_ready_session())
    tools = StrictLifecycleTools(repo, "group")
    created = asyncio.run(tools.create_strict_map(width=5, height=5))
    asyncio.run(tools.start_combat_on_map(map_id=created["map_id"]))

    result = asyncio.run(tools.end_combat(summary="Enemies routed", reason="No hostile actors remain"))

    assert result["ok"] is True
    assert result["battle_active"] is False
    assert result["lifecycle"] == MAP_LIFECYCLE_ACTIVE_EXPLORATION
    session = repo.load_session("group")
    assert session.mode == GameMode.NARRATIVE
    assert session.battle["active"] is False
    assert session.battle["map_id"] == created["map_id"]
    assert session.battle["grid"]["width"] == 5
    assert session.battle["turn"]["active"] is False
    assert session.battle["turn"]["phase"] == "ended"
    lifecycle = get_strict_map_lifecycle(session.maps, created["map_id"])
    assert lifecycle["active"] is True
    assert lifecycle["combat_linked"] is False
