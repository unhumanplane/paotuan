import json

from scripts.render_story_grid_map import (
    build_story_grid_render_envelope,
    render_story_grid_map,
)


def test_build_story_grid_render_envelope_accepts_story_grid_shape():
    payload = {
        "map_id": "lighthouse-ground",
        "title": "Lighthouse ground floor",
        "grid": {
            "width": 4,
            "height": 3,
            "cells": [
                {"x": 0, "y": 0, "terrain": "stone"},
                {"x": 2, "y": 1, "terrain": "water", "visibility": "hidden"},
                {"x": 9, "y": 9, "terrain": "wall"},
            ],
            "entities": {
                "hero": {"name": "Hero", "x": 1, "y": 1, "faction": "ally"},
                "hidden_keeper": {"name": "Hidden Keeper", "x": 2, "y": 1, "visibility": "hidden"},
            },
            "doors": [{"id": "front", "x": 0, "y": 1, "side": "west"}],
            "labels": [{"id": "stairs", "x": 3, "y": 2, "text": "Stairs"}],
        },
        "visible_bounds": {"min_x": 0, "min_y": 0, "max_x": 3, "max_y": 2},
        "layout": {"margin": 10, "header_height": 30, "legend_height": 50, "cell_size": 32},
    }

    envelope = build_story_grid_render_envelope(payload)

    assert envelope["projection"] == "player_view"
    assert envelope["map_id"] == "lighthouse-ground"
    assert envelope["grid"]["width"] == 4
    assert envelope["grid"]["height"] == 3
    assert len(envelope["grid"]["cells"]) == 2
    assert envelope["grid"]["entities"][0]["id"] == "hero"
    assert envelope["doors"][0]["id"] == "front"
    assert envelope["labels"][0]["text"] == "Stairs"


def test_render_story_grid_map_writes_svg_and_safe_metadata(tmp_path):
    payload = {
        "map_id": "lighthouse-ground",
        "title": "Lighthouse ground floor",
        "grid": {
            "width": 3,
            "height": 3,
            "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
            "entities": [
                {"id": "hero", "name": "Hero", "x": 1, "y": 1, "faction": "ally"},
                {"id": "secret", "name": "Secret Keeper", "x": 2, "y": 2, "visibility": "hidden"},
            ],
            "labels": [{"id": "stairs", "x": 0, "y": 2, "text": "Stairs"}],
        },
    }

    result = render_story_grid_map(payload, output_dir=tmp_path)

    svg_path = tmp_path / result["file_name"]
    assert result["ok"] is True
    assert result["render_type"] == "strict_grid_svg"
    assert result["visual_only"] is True
    assert svg_path.exists()
    svg = svg_path.read_text(encoding="utf-8")
    assert "Scale: 5 ft per cell" in svg
    assert ">HER<" in svg
    assert "Stairs" in svg
    assert "Secret Keeper" not in svg
    assert "secret" not in svg

    metadata_path = next(tmp_path.glob("*.metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    safe_projection = metadata["safe_projection"]
    assert "file_path" not in safe_projection
    assert "raw_svg" not in safe_projection
    assert "grid" not in safe_projection
    assert safe_projection["name"] == result["file_name"]


def test_render_story_grid_map_accepts_convergence_action_map_seed(tmp_path):
    payload = {
        "thread_id": "THREAD_lamp",
        "action_type": "scene_entry",
        "scene_goal": "Enter the lower service room",
        "next_scene_entry": "Lower service room under the lighthouse stairs",
        "map_grid_seed": {
            "map_id": "service-room",
            "grid": {
                "width": 3,
                "height": 3,
                "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
                "entities": [
                    {"id": "hero", "name": "Hero", "x": 1, "y": 1, "faction": "ally"},
                    {"id": "secret", "name": "Secret Keeper", "x": 2, "y": 2, "visibility": "hidden"},
                ],
                "labels": [{"id": "panel", "x": 2, "y": 1, "text": "Panel"}],
            },
        },
    }

    envelope = build_story_grid_render_envelope(payload)
    assert envelope["map_id"] == "service-room"
    assert envelope["title"] == "Lower service room under the lighthouse stairs"

    result = render_story_grid_map(payload, output_dir=tmp_path)
    svg = (tmp_path / result["file_name"]).read_text(encoding="utf-8")

    assert ">HER<" in svg
    assert "Panel" in svg
    assert "Secret Keeper" not in svg
    assert "secret" not in svg

    metadata = json.loads(next(tmp_path.glob("*.metadata.json")).read_text(encoding="utf-8"))
    assert metadata["safe_projection"]["map_id"] == "service-room"
    assert "file_path" not in metadata["safe_projection"]
    assert "grid" not in metadata["safe_projection"]


def test_render_story_grid_map_accepts_nested_scene_goal_map_seed():
    payload = {
        "title": "Fallback title",
        "scene_goal": {
            "goal": "Cross the flooded archive",
            "map_grid_seed": {
                "map_id": "flooded-archive",
                "title": "Flooded archive",
                "grid": {
                    "width": 2,
                    "height": 2,
                    "cells": [[0, 0, "water"]],
                },
            },
        },
    }

    envelope = build_story_grid_render_envelope(payload)

    assert envelope["map_id"] == "flooded-archive"
    assert envelope["title"] == "Flooded archive"
    assert envelope["grid"]["cells"] == [{"x": 0, "y": 0, "terrain": "water"}]


def test_render_story_grid_map_adapts_model_type_fields(tmp_path):
    payload = {
        "map_grid_seed": {
            "map_id": "lighthouse-ground",
            "title": "Lighthouse ground",
            "grid": {
                "width": 3,
                "height": 3,
                "cells": [
                    {"x": 0, "y": 0, "type": "wall", "label": "Outer wall"},
                    {"x": 1, "y": 1, "type": "door", "label": "Iron door", "state": "locked"},
                ],
                "entities": [{"x": 2, "y": 1, "type": "NPC", "label": "Watcher"}],
            },
        }
    }

    envelope = build_story_grid_render_envelope(payload)

    wall = envelope["grid"]["cells"][0]
    assert wall["terrain"] == "wall"
    assert wall["blocks_move"] is True
    assert wall["blocks_los"] is True
    assert envelope["doors"][0]["state"] == "locked"
    assert envelope["labels"][0]["text"] == "Outer wall"
    assert envelope["labels"][1]["text"] == "Iron door"
    assert envelope["grid"]["entities"][0]["name"] == "Watcher"
    assert envelope["grid"]["entities"][0]["faction"] == "npc"

    result = render_story_grid_map(payload, output_dir=tmp_path)
    svg = (tmp_path / result["file_name"]).read_text(encoding="utf-8")
    assert "Outer wall" in svg
    assert "Iron door" in svg
    assert ">WAT<" in svg
