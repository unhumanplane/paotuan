import pytest

from astrbot_plugin_auto_trpg_dm.rendering.overview_topology import (
    OVERVIEW_TOPOLOGY_RENDER_TYPE,
    build_overview_topology_render_input,
    layout_overview_topology,
    render_overview_topology_svg,
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


def test_build_overview_topology_input_rejects_area_with_missing_node():
    envelope = _base_envelope()
    envelope["areas"][0]["node_ids"].append("hidden-room")

    with pytest.raises(ValueError, match="overview_area_node_missing:outer-ring"):
        build_overview_topology_render_input(envelope)


def test_build_overview_topology_input_rejects_hidden_landmark_status():
    envelope = _base_envelope()
    envelope["landmarks"][0]["status"] = "hidden"

    with pytest.raises(ValueError, match="overview_landmark_status_invalid:bell"):
        build_overview_topology_render_input(envelope)


def test_layout_overview_topology_reuses_stored_positions_and_generates_missing_nodes():
    render_input = build_overview_topology_render_input(_base_envelope())

    layout = layout_overview_topology(render_input)

    assert layout.positions["gate"].x == 10
    assert layout.positions["gate"].y == 20
    assert layout.reused_node_ids == ("gate",)
    assert layout.generated_node_ids == ("market",)
    assert layout.positions["market"].x > layout.positions["gate"].x


def test_layout_overview_topology_does_not_reserve_space_for_rejected_hidden_nodes():
    visible = _base_envelope()
    with_hidden_layout = _base_envelope()
    with_hidden_layout["layout"]["positions"]["hidden-room"] = {"x": 999, "y": 999}

    visible_layout = layout_overview_topology(build_overview_topology_render_input(visible))
    with pytest.raises(ValueError, match="overview_layout_node_missing:hidden-room"):
        build_overview_topology_render_input(with_hidden_layout)

    assert visible_layout.bounds[2] < 900
    assert visible_layout.bounds[3] < 700


def test_render_overview_topology_svg_outputs_layered_visual_only_svg_without_coordinates():
    render_input = build_overview_topology_render_input(_base_envelope())

    svg = render_overview_topology_svg(render_input)

    assert '<g data-layer="edges">' in svg
    assert '<g data-layer="areas">' in svg
    assert '<g data-layer="landmarks">' in svg
    assert '<g data-layer="nodes">' in svg
    assert '<g data-layer="labels">' in svg
    assert 'stroke-dasharray="8 7"' in svg
    assert 'overview topology - visual only' in svg
    assert "10,20" not in svg
    assert "hidden-room" not in svg


def test_render_overview_topology_svg_marks_current_location():
    render_input = build_overview_topology_render_input(_base_envelope())

    svg = render_overview_topology_svg(render_input)

    assert 'data-node-id="gate"' in svg
    assert 'fill="#2f6f73"' in svg
