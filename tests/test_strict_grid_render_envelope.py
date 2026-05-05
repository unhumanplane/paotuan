from astrbot_plugin_auto_trpg_dm.rendering import (
    build_strict_grid_render_input,
    render_strict_grid_svg,
)


def test_build_strict_grid_render_input_crops_to_visible_bounds():
    render_input = build_strict_grid_render_input(
        {
            "projection": "player_view",
            "map_id": "strict-room",
            "title": "Visible room",
            "grid": {
                "width": 6,
                "height": 6,
                "cells": [
                    {"x": 1, "y": 1, "terrain": "stone", "visibility": "player"},
                    {"x": 5, "y": 5, "terrain": "wall", "visibility": "hidden"},
                ],
                "entities": {
                    "hero": {"name": "Hero", "x": 2, "y": 2, "faction": "ally", "visibility": "player"},
                    "hidden-assassin": {"name": "Hidden Assassin", "x": 5, "y": 5, "visibility": "hidden"},
                },
            },
            "visible_bounds": {"min_x": 1, "min_y": 1, "max_x": 2, "max_y": 2},
            "layout": {"margin": 10, "header_height": 30, "legend_height": 50, "cell_size": 40},
            "rule_scale": {"distance_per_cell": 10, "unit": "ft"},
        }
    )

    svg = render_strict_grid_svg(render_input)

    assert render_input.width == 2
    assert render_input.height == 2
    assert render_input.cells[0].x == 0
    assert render_input.cells[0].y == 0
    assert render_input.entities[0].x == 1
    assert render_input.entities[0].y == 1
    assert 'width="100"' in svg
    assert 'height="180"' in svg
    assert "Scale: 10 ft per cell" in svg
    assert "Hidden Assassin" not in svg
    assert "hidden-assassin" not in svg
    assert "wall" not in svg


def test_build_strict_grid_render_input_filters_hidden_overlay_layers():
    render_input = build_strict_grid_render_input(
        {
            "projection": "player_view",
            "map_id": "strict-room",
            "title": "Overlay room",
            "grid": {"width": 3, "height": 3, "cells": [], "entities": {}},
            "doors": [
                {"id": "visible-door", "x": 0, "y": 0, "side": "east", "visibility": "public"},
                {"id": "secret-door", "x": 1, "y": 0, "side": "south", "visibility": "dm"},
            ],
            "hazards": [
                {"id": "visible-spikes", "kind": "spikes", "x": 1, "y": 1, "visibility": "player"},
                {"id": "secret-acid", "kind": "acid", "x": 2, "y": 2, "visible": False},
            ],
            "obstacles": [
                {"id": "crate", "kind": "crate", "x": 0, "y": 2},
                {"id": "hidden-wall", "kind": "wall", "x": 2, "y": 0, "tags": {"visibility": "hidden"}},
            ],
            "labels": [
                {"id": "exit", "text": "Exit", "x": 0, "y": 1},
                {"id": "secret-label", "text": "Secret tunnel", "x": 2, "y": 1, "visibility": "hidden"},
            ],
        }
    )

    svg = render_strict_grid_svg(render_input)

    assert [door.id for door in render_input.doors] == ["visible-door"]
    assert [hazard.id for hazard in render_input.hazards] == ["visible-spikes"]
    assert [obstacle.id for obstacle in render_input.obstacles] == ["crate"]
    assert [label.id for label in render_input.labels] == ["exit"]
    assert "Exit" in svg
    assert "secret-door" not in svg
    assert "secret-acid" not in svg
    assert "hidden-wall" not in svg
    assert "Secret tunnel" not in svg


def test_build_strict_grid_render_input_uses_discovered_areas_for_cell_masking():
    render_input = build_strict_grid_render_input(
        {
            "projection": "player_view",
            "map_id": "strict-room",
            "grid": {
                "width": 3,
                "height": 2,
                "cells": [
                    {"x": 0, "y": 0, "terrain": "stone"},
                    {"x": 2, "y": 1, "terrain": "water"},
                ],
                "entities": {},
            },
            "discovered_areas": [{"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1}],
        }
    )

    undiscovered = [cell for cell in render_input.cells if not cell.discovered]
    svg = render_strict_grid_svg(render_input)

    assert len(render_input.cells) == 6
    assert {(cell.x, cell.y) for cell in undiscovered} == {(2, 0), (2, 1)}
    assert "#cbd5e1" in svg


def test_build_strict_grid_render_input_rejects_non_player_projection():
    try:
        build_strict_grid_render_input(
            {
                "projection": "dm_narration_view",
                "map_id": "strict-room",
                "grid": {"width": 2, "height": 2},
            }
        )
    except ValueError as exc:
        assert str(exc) == "strict_grid_projection_not_player_view"
    else:
        raise AssertionError("expected non-player projection rejection")
