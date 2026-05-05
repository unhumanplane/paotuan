import pytest

from astrbot_plugin_auto_trpg_dm.rendering.overview_topology import (
    OVERVIEW_TOPOLOGY_RENDER_TYPE,
    build_overview_topology_render_input,
)


def _base_envelope():
    return {
        "render_type": OVERVIEW_TOPOLOGY_RENDER_TYPE,
        "projection": "player_view",
        "map_id": "overview-1",
        "title": "北门周边",
        "map_revision": "map-7",
        "layout_revision": "layout-3",
        "display_profile": {"width": 820, "height": 620},
        "layout": {"positions": {"gate": {"x": 10, "y": 20}}},
        "nodes": [
            {"id": "gate", "label": "北门", "visibility": "player", "order": 1},
            {"id": "market", "label": "旧集市", "visibility": "public", "order": 2},
        ],
        "edges": [
            {
                "id": "gate-market",
                "source_id": "gate",
                "target_id": "market",
                "relationship": "road",
                "direction": "east",
                "distance_band": "near",
                "status": "suspected",
                "visibility": "player",
            }
        ],
        "areas": [{"id": "outer-ring", "label": "外环", "node_ids": ["gate", "market"], "visibility": "player"}],
        "landmarks": [{"id": "bell", "label": "钟楼", "node_id": "gate", "visibility": "public"}],
        "current_node_id": "gate",
    }


def test_build_overview_topology_input_accepts_player_view_topology():
    render_input = build_overview_topology_render_input(_base_envelope())

    assert render_input.render_type == OVERVIEW_TOPOLOGY_RENDER_TYPE
    assert render_input.map_id == "overview-1"
    assert render_input.title == "北门周边"
    assert render_input.display.show_numeric_coordinates is False
    assert render_input.display.width == 820
    assert render_input.nodes[0].layout_pos is not None
    assert render_input.nodes[0].layout_pos.x == 10
    assert render_input.edges[0].status == "suspected"
    assert render_input.current_node_id == "gate"


def test_build_overview_topology_input_rejects_non_player_projection():
    envelope = _base_envelope()
    envelope["projection"] = "dm_narration_view"

    with pytest.raises(ValueError, match="overview_projection_not_player_view"):
        build_overview_topology_render_input(envelope)


def test_build_overview_topology_input_rejects_hidden_node():
    envelope = _base_envelope()
    envelope["nodes"][1]["visibility"] = "hidden"

    with pytest.raises(ValueError, match="overview_visibility_not_player_safe:market"):
        build_overview_topology_render_input(envelope)


def test_build_overview_topology_input_rejects_edge_with_missing_endpoint():
    envelope = _base_envelope()
    envelope["edges"][0]["target_id"] = "hidden-room"

    with pytest.raises(ValueError, match="overview_edge_endpoint_missing:gate-market"):
        build_overview_topology_render_input(envelope)


def test_build_overview_topology_input_rejects_blocked_raw_fields():
    envelope = _base_envelope()
    envelope["nodes"][0]["payload"] = {"path": "/local/runtime/secret.svg"}

    with pytest.raises(ValueError, match="overview_blocked_field:path"):
        build_overview_topology_render_input(envelope)


def test_build_overview_topology_input_rejects_unknown_layout_position():
    envelope = _base_envelope()
    envelope["layout"]["positions"]["hidden-room"] = {"x": 200, "y": 200}

    with pytest.raises(ValueError, match="overview_layout_node_missing:hidden-room"):
        build_overview_topology_render_input(envelope)
