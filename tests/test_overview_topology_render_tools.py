import asyncio
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_TYPE_STRICT_LOCAL,
    MAP_VISIBILITY_HIDDEN,
    MAP_VISIBILITY_PLAYER,
    add_map_fact,
    create_map_record,
    get_map_record,
    save_active_strict_grid,
)
from astrbot_plugin_auto_trpg_dm.core.map_delivery_cadence import (
    MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
    MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
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
    assert result["map_revision"] == "1"
    assert result["width"] == 900
    assert result["height"] == 700
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
    assert "market" not in record["facts"][0]["payload"]["layout"]["positions"]
    assert "market" in record["archive_identity"]["overview_topology_layout"]["positions"]
    assert result["layout_updates"]["cached"] is True
    assert result["layout_updates"]["generated_node_ids"] == ["market"]
    assert result["layout_revision"] == result["layout_updates"]["layout_revision"]
    assert "<svg" not in str(record["facts"])
    assert record["render_refs"][-1]["type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE
    assert record["render_refs"][-1]["path"].endswith(".svg")
    assert record["render_refs"][-1]["visual_only"] is True
    assert saved.scene["_pending_outputs"][-1]["type"] == "svg_map"
    assert saved.scene["_pending_outputs"][-1]["render_type"] == OVERVIEW_TOPOLOGY_RENDER_TYPE
    assert saved.scene["_pending_outputs"][-1]["delivery_trigger"] == MAP_DELIVERY_TRIGGER_PLAYER_REQUEST
    assert result["delivery"]["reason"] == "eligible"


def test_render_overview_topology_svg_does_not_fallback_to_active_strict_map():
    repository = JsonGameRepository(_runtime_root("overview-no-strict-fallback") / "data")
    session = GameSession.new("group")
    create_map_record(
        session.maps,
        "strict-room",
        title="Player-visible strict room",
        map_type=MAP_TYPE_STRICT_LOCAL,
        visibility=MAP_VISIBILITY_PLAYER,
        set_active=True,
    )
    save_active_strict_grid(
        session.maps,
        {
            "width": 3,
            "height": 3,
            "cells": [],
            "entities": {"pc": {"name": "PC", "x": 1, "y": 1}},
        },
        map_id="strict-room",
        title="Player-visible strict room",
    )
    add_map_fact(
        session.maps,
        "strict-room",
        fact_id="strict-topology-bait",
        kind="overview_topology",
        text="This topology-shaped fact must not make a strict map render as an overview map.",
        visibility=MAP_VISIBILITY_PLAYER,
        payload={
            "nodes": [{"id": "strict-node", "label": "Strict Node", "visibility": "player"}],
            "edges": [],
            "areas": [],
            "landmarks": [],
            "current_node_id": "strict-node",
        },
    )
    repository.save_session(session)

    result = asyncio.run(OverviewTopologyRenderTools(repository, "group").render_overview_topology_svg())

    assert result == {"ok": False, "error": "overview_map_not_found", "map_id": ""}
    saved = repository.load_session("group")
    assert "_pending_outputs" not in saved.scene
    assert saved.maps["records"]["strict-room"]["render_refs"] == []


def test_render_overview_topology_svg_applies_cadence_before_enqueueing_transition():
    root = _runtime_root("overview-render-tools-cadence")
    repository = _repository_with_overview_topology(root)
    tools = OverviewTopologyRenderTools(repository, "group")

    first = asyncio.run(
        tools.render_overview_topology_svg(
            send_to_chat=True,
            delivery_trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
            trigger_id="scene:gate-to-market",
        )
    )
    second = asyncio.run(
        tools.render_overview_topology_svg(
            send_to_chat=True,
            delivery_trigger=MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
            trigger_id="scene:gate-to-market",
        )
    )

    saved = repository.load_session("group")
    assert first["delivery"]["should_send"] is True
    assert second["delivery"]["should_send"] is False
    assert second["delivery"]["reason"] == "duplicate_suppressed"
    assert len(saved.scene["_pending_outputs"]) == 1


def test_render_overview_topology_svg_reuses_layout_cache_without_rewriting_facts():
    root = _runtime_root("overview-render-tools-layout-cache")
    repository = _repository_with_overview_topology(root)
    tools = OverviewTopologyRenderTools(repository, "group")

    first = asyncio.run(tools.render_overview_topology_svg(send_to_chat=False))
    second = asyncio.run(tools.render_overview_topology_svg(send_to_chat=False))

    assert first["ok"] is True
    assert first["layout_updates"]["generated_node_ids"] == ["market"]
    assert second["ok"] is True
    assert second["layout_updates"] == {}
    saved = repository.load_session("group")
    record = get_map_record(saved.maps, "overview-1")
    assert len(record["facts"]) == 1
    assert "market" not in record["facts"][0]["payload"]["layout"]["positions"]
    assert "market" in record["archive_identity"]["overview_topology_layout"]["positions"]


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
