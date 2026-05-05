import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_VISIBILITY_HIDDEN,
    MAP_VISIBILITY_PLAYER,
    add_map_fact,
    create_map_record,
    get_map_record,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.rendering.overview_topology import OVERVIEW_TOPOLOGY_RENDER_TYPE
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.overview_topology_render_tools import OverviewTopologyRenderTools


def _runtime_root(label):
    return Path(".pytest-runtime") / f"{label}-{uuid4().hex}"


def _repository_with_overview_topology(root):
    repository = JsonGameRepository(root / "data")
    session = GameSession.new("group")
    create_map_record(
        session.maps,
        "overview-1",
        title="北门概览",
        visibility=MAP_VISIBILITY_PLAYER,
        set_active=True,
    )
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="overview-topology",
        kind="overview_topology",
        text="北门和旧集市之间的路线。",
        visibility=MAP_VISIBILITY_PLAYER,
        payload={
            "nodes": [
                {"id": "gate", "label": "北门", "visibility": "player", "order": 1},
                {"id": "market", "label": "旧集市", "visibility": "player", "order": 2},
            ],
            "edges": [
                {
                    "id": "gate-market",
                    "source_id": "gate",
                    "target_id": "market",
                    "relationship": "road",
                    "direction": "east",
                    "rough_distance": "near",
                    "visibility": "player",
                }
            ],
            "areas": [{"id": "outer", "label": "外环", "node_ids": ["gate", "market"], "visibility": "player"}],
            "landmarks": [{"id": "bell", "label": "钟楼", "node_id": "gate", "visibility": "player"}],
            "layout": {"positions": {"gate": {"x": 60, "y": 80}}},
            "current_node_id": "gate",
        },
    )
    repository.save_session(session)
    return repository


def test_render_overview_topology_svg_writes_visual_ref_and_pending_output():
    root = _runtime_root("overview-render-tools")
    repository = _repository_with_overview_topology(root)
    tools = OverviewTopologyRenderTools(repository, "group")

    result = asyncio.run(tools.render_overview_topology_svg(send_to_chat=True))

    assert result["ok"] is True
    assert result["render_type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE
    assert result["visual_only"] is True
    assert result["pending_output"]["type"] == "svg_map"
    assert result["pending_output"]["render_type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE

    svg_path = root / "data" / "maps" / result["file_name"]
    assert svg_path.exists()
    svg = svg_path.read_text(encoding="utf-8")
    assert '<g data-layer="edges">' in svg
    assert 'data-node-id="gate"' in svg

    saved = repository.load_session("group")
    record = get_map_record(saved.maps, "overview-1")
    assert len(record["facts"]) == 1
    assert record["facts"][0]["kind"] == "overview_topology"
    assert "market" in record["facts"][0]["payload"]["layout"]["positions"]
    assert "<svg" not in str(record["facts"])
    assert record["render_refs"][-1]["type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE
    assert record["render_refs"][-1]["path"].endswith(".svg")
    assert record["render_refs"][-1]["visual_only"] is True
    assert saved.scene["_pending_outputs"][-1]["type"] == "svg_map"
    assert saved.scene["_pending_outputs"][-1]["render_type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE


def test_render_overview_topology_svg_missing_topology_fact_returns_stable_error():
    root = _runtime_root("overview-render-tools-missing")
    repository = JsonGameRepository(root / "data")
    session = GameSession.new("group")
    create_map_record(
        session.maps,
        "overview-1",
        title="北门概览",
        visibility=MAP_VISIBILITY_PLAYER,
        set_active=True,
    )
    repository.save_session(session)
    tools = OverviewTopologyRenderTools(repository, "group")

    result = asyncio.run(tools.render_overview_topology_svg())

    assert result == {
        "ok": False,
        "error": "overview_topology_missing",
        "map_id": "overview-1",
    }


def test_render_overview_topology_svg_uses_player_projection_before_rendering():
    root = _runtime_root("overview-render-tools-hidden")
    repository = JsonGameRepository(root / "data")
    session = GameSession.new("group")
    create_map_record(
        session.maps,
        "overview-1",
        title="北门概览",
        visibility=MAP_VISIBILITY_PLAYER,
        set_active=True,
    )
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="hidden-topology",
        kind="overview_topology",
        visibility=MAP_VISIBILITY_HIDDEN,
        payload={
            "nodes": [{"id": "secret", "label": "密道", "visibility": "hidden"}],
            "edges": [],
        },
    )
    repository.save_session(session)
    tools = OverviewTopologyRenderTools(repository, "group")

    result = asyncio.run(tools.render_overview_topology_svg())

    assert result["ok"] is False
    assert result["error"] == "overview_topology_missing"
    saved = repository.load_session("group")
    record = get_map_record(saved.maps, "overview-1")
    assert record["facts"][0]["id"] == "hidden-topology"
    assert "render_refs" in record
    assert record["render_refs"] == []
