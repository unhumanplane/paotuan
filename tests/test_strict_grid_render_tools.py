import asyncio
import json
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    DEFAULT_STRICT_LOCAL_MAP_ID,
    MAP_VIEW_PLAYER,
    MAP_TYPE_STRICT_LOCAL,
    get_map_record,
    project_active_map_record,
    save_active_strict_grid,
)
from astrbot_plugin_auto_trpg_dm.core.map_delivery_cadence import (
    MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
    MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.core.prompt_projection import project_tool_results_for_dm_prompt
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.strict_grid_render_tools import StrictGridRenderTools


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def test_render_strict_grid_svg_writes_visual_artifact_and_render_ref():
    repo = _repo("strict_render")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    save_active_strict_grid(
        session.maps,
        {
            "width": 3,
            "height": 3,
            "cells": [{"x": 1, "y": 1, "terrain": "stone", "visibility": "player"}],
            "entities": {"hero": {"name": "Hero", "x": 1, "y": 1, "visibility": "player"}},
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
        title="Battle grid",
    )
    repo.save_session(session)

    result = asyncio.run(StrictGridRenderTools(repo, "group").render_strict_grid_svg(title="Battle grid"))

    assert result["ok"] is True
    assert result["visual_only"] is True
    assert result["render_ref"] == {
        "type": "strict_grid_svg",
        "title": "Battle grid",
        "name": result["file_name"],
        "visual_only": True,
    }
    svg_path = Path(result["file_path"])
    assert svg_path.exists()
    svg = svg_path.read_text(encoding="utf-8")
    assert "Scale: 5 ft per cell" in svg
    saved = repo.load_session("group")
    record = get_map_record(saved.maps, DEFAULT_STRICT_LOCAL_MAP_ID)
    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert record["grid"]["entities"]["hero"]["x"] == 1
    assert record["render_refs"][0]["path"] == result["file_path"]
    assert saved.scene["_pending_outputs"][0]["type"] == "svg_map"
    assert saved.scene["_pending_outputs"][0]["render_type"] == "strict_grid_svg"
    assert saved.scene["_pending_outputs"][0]["delivery_trigger"] == MAP_DELIVERY_TRIGGER_PLAYER_REQUEST
    assert result["delivery"]["reason"] == "eligible"


def test_render_strict_grid_svg_applies_cadence_before_enqueueing_auto_round():
    repo = _repo("strict_render_cadence")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    save_active_strict_grid(
        session.maps,
        {
            "width": 3,
            "height": 3,
            "cells": [],
            "entities": {"hero": {"name": "Hero", "x": 1, "y": 1, "visibility": "player"}},
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
        title="Round map",
    )
    repo.save_session(session)
    tools = StrictGridRenderTools(repo, "group")

    first = asyncio.run(
        tools.render_strict_grid_svg(
            title="Round map",
            delivery_trigger=MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
            combat_id="combat-1",
            round_number=5,
        )
    )
    second = asyncio.run(
        tools.render_strict_grid_svg(
            title="Round map",
            delivery_trigger=MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
            combat_id="combat-1",
            round_number=5,
        )
    )

    saved = repo.load_session("group")
    assert first["delivery"]["should_send"] is True
    assert second["delivery"]["should_send"] is False
    assert second["delivery"]["reason"] == "duplicate_suppressed"
    assert len(saved.scene["_pending_outputs"]) == 1


def test_render_strict_grid_svg_migrates_legacy_grid_without_llm_or_generate_map_svg():
    repo = _repo("strict_render_legacy")
    session = GameSession.new("group")
    session.battle = {
        "active": True,
        "grid": {
            "width": 2,
            "height": 2,
            "cells": [],
            "entities": {"hero": {"name": "Hero", "x": 0, "y": 0, "visibility": "player"}},
        },
    }
    repo.save_session(session)

    result = asyncio.run(StrictGridRenderTools(repo, "group").render_strict_grid_svg(send_to_chat=False))

    assert result["ok"] is True
    assert result["send_to_chat"] is False
    saved = repo.load_session("group")
    assert saved.battle["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    record = get_map_record(saved.maps, DEFAULT_STRICT_LOCAL_MAP_ID)
    assert record["archive_identity"]["migration_source"] == "battle.grid"
    assert record["grid"]["entities"]["hero"]["x"] == 0
    assert "_pending_outputs" not in saved.scene


def test_render_strict_grid_svg_reports_missing_grid_without_legacy_fallback():
    repo = _repo("strict_render_missing")
    repo.save_session(GameSession.new("group"))

    result = asyncio.run(StrictGridRenderTools(repo, "group").render_strict_grid_svg())

    assert result == {"ok": False, "error": "strict_grid_not_found", "source": "none"}


def test_render_strict_grid_svg_projection_blocks_delivery_paths_and_raw_grid():
    repo = _repo("strict_render_projection")
    session = GameSession.new("group")
    save_active_strict_grid(
        session.maps,
        {
            "width": 2,
            "height": 2,
            "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
            "entities": {"hero": {"name": "Hero", "x": 0, "y": 0}},
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
        title="Projected battle grid",
    )
    repo.save_session(session)

    result = asyncio.run(StrictGridRenderTools(repo, "group").render_strict_grid_svg())
    projected = project_tool_results_for_dm_prompt(
        [{"tool": "render_strict_grid_svg", "args": {"title": "Projected battle grid"}, "result": result}]
    )
    rendered = json.dumps(projected, ensure_ascii=False)

    assert result["ok"] is True
    assert "file_path" in result
    assert "file_path" not in rendered
    assert "maps" not in rendered
    assert '"grid"' not in rendered
    assert "raw_svg" not in rendered
    assert result["file_name"] in rendered
    saved = repo.load_session("group")
    player_record = project_active_map_record(saved.maps, MAP_VIEW_PLAYER, strict=True)
    if player_record is not None:
        assert "path" not in str(player_record)
        assert "grid" not in str(player_record)


def test_render_strict_grid_svg_does_not_write_svg_back_to_map_facts_or_grid():
    repo = _repo("strict_render_no_fact_writeback")
    session = GameSession.new("group")
    grid = {
        "width": 2,
        "height": 2,
        "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
        "entities": {"hero": {"name": "Hero", "x": 0, "y": 0}},
    }
    save_active_strict_grid(
        session.maps,
        grid,
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
        title="No writeback grid",
    )
    repo.save_session(session)

    before = get_map_record(repo.load_session("group").maps, DEFAULT_STRICT_LOCAL_MAP_ID)
    result = asyncio.run(StrictGridRenderTools(repo, "group").render_strict_grid_svg())
    after = get_map_record(repo.load_session("group").maps, DEFAULT_STRICT_LOCAL_MAP_ID)

    assert result["ok"] is True
    assert after["grid"] == before["grid"]
    assert after["facts"] == before["facts"]
    assert len(after["render_refs"]) == len(before["render_refs"]) + 1
    assert "svg" not in after["grid"]
    assert "raw_svg" not in str(after)
