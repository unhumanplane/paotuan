import json
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import prompt_snapshot_data
from astrbot_plugin_auto_trpg_dm.core.story_forge_runtime import (
    STORY_FORGE_ARCHIVE_KEY,
    STORY_FORGE_BRIEF_KEY,
    StoryForgeRuntimeConfig,
    apply_story_forge_turn,
    build_story_grid_render_envelope,
    normalize_convergence_action,
    record_story_forge_convergence,
)
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def test_story_forge_turn_archives_raw_turn_but_projects_only_safe_brief():
    repo = _repo("story-forge-archive")
    session = GameSession.new("group")
    session.scene.update(
        {
            "current_objective": "Find the missing lighthouse keeper.",
            "open_hooks": [{"id": "hook-1", "text": "The lamp is still warm.", "status": "open"}],
            "clues": [{"id": "clue-1", "text": "Saltwater stains the ledger.", "status": "discovered"}],
            "hidden_truth": "The captain staged the disappearance.",
        }
    )
    repo.save_session(session)

    result = apply_story_forge_turn(
        repo,
        "group",
        actor={"player_id": "p1", "display_name": "Ada"},
        player_message="I check the ledger.",
        dm_response="You find saltwater in the binding.",
        config=StoryForgeRuntimeConfig(),
    )

    assert result["ok"] is True
    saved = repo.load_session("group")
    archive = saved.scene[STORY_FORGE_ARCHIVE_KEY]
    brief = saved.scene[STORY_FORGE_BRIEF_KEY]
    assert archive["turns"][0]["player_message"] == "I check the ledger."
    assert brief["open_threads"][0]["id"] == "hook-1"
    assert brief["clue_ledger"][0]["id"] == "clue-1"

    snapshot, _stats = prompt_snapshot_data(saved, saved.mode)
    rendered = json.dumps(snapshot, ensure_ascii=False)
    assert STORY_FORGE_ARCHIVE_KEY not in rendered
    assert "I check the ledger." not in rendered
    assert "captain staged" not in rendered
    assert STORY_FORGE_BRIEF_KEY in rendered
    assert "Saltwater stains" in rendered


def test_normalize_convergence_action_recovers_nested_scene_goal_map_seed():
    action = normalize_convergence_action(
        {
            "thread_id": "hook-1",
            "scene_goal": {
                "objective": "Enter the lower lighthouse.",
                "entry_cost": "Spend ten minutes forcing the swollen door.",
                "success_signal": "The lamp mechanism can be inspected.",
                "failure_forward": "Noise advances the patrol clock.",
                "map_grid_seed": {
                    "map_id": "lower-lighthouse",
                    "width": 2,
                    "height": 2,
                    "cells": [{"x": 0, "y": 0, "type": "floor"}],
                },
            },
        }
    )

    assert action["scene_goal"] == "Enter the lower lighthouse."
    assert action["entry_cost"] == "Spend ten minutes forcing the swollen door."
    assert action["success_signal"] == "The lamp mechanism can be inspected."
    assert action["failure_forward"] == "Noise advances the patrol clock."
    assert action["map_grid_seed"]["map_id"] == "lower-lighthouse"


def test_story_forge_convergence_rejects_hidden_truth_fields():
    repo = _repo("story-forge-hidden")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)

    result = record_story_forge_convergence(
        repo,
        "group",
        payload={
            "scene_goal": "Question the dockhand.",
            "entry_cost": "Offer a favor.",
            "success_signal": "He points to the tide marker.",
            "failure_forward": "He asks for proof first.",
            "hidden_truth": "The dockhand is the murderer.",
        },
    )

    assert result["ok"] is False
    assert result["error"] == "story_forge_convergence_rejected"


def test_story_forge_convergence_renders_map_seed_and_enqueues_pending_output():
    repo = _repo("story-forge-render")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)

    result = record_story_forge_convergence(
        repo,
        "group",
        payload={
            "thread_id": "hook-1",
            "scene_goal": "Cross the breakwater to inspect the bell.",
            "entry_cost": "Move in single file over slick stone.",
            "success_signal": "The bell rope and fresh scrape marks are visible.",
            "failure_forward": "A fall costs time and raises the tide clock.",
            "evidence": ["The bell rang after midnight."],
            "map_grid_seed": {
                "map_id": "breakwater",
                "title": "Breakwater",
                "width": 3,
                "height": 2,
                "cells": [
                    {"x": 0, "y": 0, "type": "floor", "label": "shore"},
                    {"x": 1, "y": 0, "type": "door", "state": "open"},
                    {"x": 2, "y": 0, "type": "wall"},
                ],
                "entities": [{"id": "pc", "name": "Scout", "x": 0, "y": 0, "faction": "ally"}],
            },
        },
    )

    assert result["ok"] is True
    assert result["rendered_map"]["file_name"].endswith(".svg")
    saved = repo.load_session("group")
    pending = saved.scene["_pending_outputs"]
    assert pending[0]["type"] == "svg_map"
    assert pending[0]["source"] == "story_forge_runtime"
    assert Path(pending[0]["path"]).exists()
    archive_action = saved.scene[STORY_FORGE_ARCHIVE_KEY]["convergence_actions"][0]
    assert archive_action["rendered_map_ref"]["file_name"] == result["rendered_map"]["file_name"]


def test_story_grid_render_envelope_adapts_model_type_fields_without_hidden_entities():
    envelope = build_story_grid_render_envelope(
        {
            "map_grid_seed": {
                "map_id": "typed-grid",
                "width": 3,
                "height": 2,
                "cells": [
                    {"x": 0, "y": 0, "type": "wall"},
                    {"x": 1, "y": 0, "type": "door", "label": "Gate"},
                ],
                "entities": [
                    {"id": "visible", "type": "NPC", "x": 1, "y": 1, "label": "Guard"},
                    {"id": "hidden", "type": "NPC", "x": 2, "y": 1, "visibility": "hidden"},
                ],
            }
        }
    )

    assert envelope["grid"]["cells"][0]["terrain"] == "wall"
    assert envelope["grid"]["cells"][0]["blocks_move"] is True
    assert envelope["doors"][0]["id"] == "door-1-0"
    assert envelope["labels"][0]["text"] == "Gate"
    assert [item["id"] for item in envelope["grid"]["entities"]] == ["visible"]
