from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from astrbot_plugin_auto_trpg_dm.rendering import (
    GridCellRender,
    GridDoorRender,
    GridEntityRender,
    GridHazardRender,
    GridLabelRender,
    GridObstacleRender,
    GridRuleScale,
    StrictGridLayout,
    StrictGridRenderInput,
    calculate_strict_grid_canvas,
    render_strict_grid_svg,
)


def _root(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _elements(root: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in root.iter() if _local_name(item.tag) == name]


def test_strict_grid_svg_has_stable_canvas_grid_lines_and_scale_legend():
    render_input = StrictGridRenderInput(
        map_id="gatehouse",
        title="Gatehouse",
        width=3,
        height=2,
        layout=StrictGridLayout(margin=10, header_height=30, legend_height=50, cell_size=40),
        rule_scale=GridRuleScale(distance_per_cell=5, unit="ft"),
    )

    first = render_strict_grid_svg(render_input)
    second = render_strict_grid_svg(render_input)
    root = _root(first)

    assert first == second
    assert root.attrib["width"] == "140"
    assert root.attrib["height"] == "180"
    assert root.attrib["viewBox"] == "0 0 140 180"
    assert calculate_strict_grid_canvas(render_input).grid_y == 40
    assert "Scale: 5 ft per cell" in first

    grid_lines = [
        item
        for item in _elements(root, "line")
        if item.attrib.get("stroke") == "#64748b" and item.attrib.get("stroke-width") == "1"
    ]
    assert len(grid_lines) == 7
    assert any(line.attrib == {"x1": "10", "y1": "40", "x2": "10", "y2": "120", "stroke": "#64748b", "stroke-width": "1"} for line in grid_lines)
    assert any(line.attrib == {"x1": "10", "y1": "40", "x2": "130", "y2": "40", "stroke": "#64748b", "stroke-width": "1"} for line in grid_lines)


def test_strict_grid_svg_renders_structured_tactical_layers():
    render_input = StrictGridRenderInput(
        map_id="room",
        title="Room",
        width=3,
        height=3,
        layout=StrictGridLayout(margin=12, header_height=32, legend_height=58, cell_size=36),
        cells=(
            GridCellRender(x=0, y=0, terrain="stone", blocks_move=True),
            GridCellRender(x=1, y=0, terrain="water", blocks_los=True, cover=2),
            GridCellRender(x=2, y=2, terrain="floor", discovered=False),
        ),
        doors=(GridDoorRender(id="north-door", x=1, y=0, side="north", state="closed", blocks_los=True),),
        hazards=(GridHazardRender(id="spikes", x=2, y=1, kind="spikes"),),
        obstacles=(GridObstacleRender(id="crate", x=0, y=2, kind="crate", blocks_los=True),),
        entities=(GridEntityRender(id="hero", name="Hero", x=1, y=1, faction="ally"),),
        labels=(GridLabelRender(id="exit", x=2, y=0, text="Exit"),),
    )

    svg = render_strict_grid_svg(render_input)
    root = _root(svg)

    assert ">HER<" in svg
    assert "HER=Hero(1,1)" in svg
    assert ">Exit<" in svg
    assert "movement block" in svg
    assert "LOS block" in svg
    assert ">0<" in svg
    assert "Entities" in svg
    assert any(item.attrib.get("fill") == "#bfdbfe" for item in _elements(root, "rect"))
    assert any(item.attrib.get("fill") == "#cbd5e1" for item in _elements(root, "rect"))
    assert any(item.attrib.get("fill") == "#f97316" for item in _elements(root, "polygon"))
    assert any(item.attrib.get("fill") == "#2563eb" for item in _elements(root, "circle"))
    assert any(item.attrib.get("stroke") == "#92400e" and item.attrib.get("stroke-width") == "5" for item in _elements(root, "line"))


def test_strict_grid_svg_escapes_text_instead_of_creating_markup():
    render_input = StrictGridRenderInput(
        map_id="escape",
        title="<script>alert(1)</script>",
        width=1,
        height=1,
        entities=(GridEntityRender(id="bad", name="<script>bad</script>", x=0, y=0),),
    )

    svg = render_strict_grid_svg(render_input)
    root = _root(svg)

    assert "<script>" not in svg
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in svg
    assert not [item for item in root.iter() if _local_name(item.tag).lower() == "script"]


def test_strict_grid_svg_rejects_invalid_coordinates_and_door_sides():
    with pytest.raises(ValueError, match="entity_coordinate_out_of_bounds"):
        render_strict_grid_svg(
            StrictGridRenderInput(
                map_id="bad",
                title="Bad",
                width=2,
                height=2,
                entities=(GridEntityRender(id="outside", name="Outside", x=2, y=0),),
            )
        )

    with pytest.raises(ValueError, match="invalid_door_side"):
        render_strict_grid_svg(
            StrictGridRenderInput(
                map_id="bad-door",
                title="Bad door",
                width=2,
                height=2,
                doors=(GridDoorRender(id="door", x=0, y=0, side="up"),),
            )
        )
