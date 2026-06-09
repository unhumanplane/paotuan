import json
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import prompt_snapshot_data
from astrbot_plugin_auto_trpg_dm.core.story_forge_runtime import (
    STORY_FORGE_ARCHIVE_KEY,
    STORY_FORGE_BRIEF_KEY,
    StoryForgeRuntimeConfig,
    advance_story_forge_pressure_clock,
    apply_story_forge_turn,
    build_story_grid_render_envelope,
    normalize_convergence_action,
    record_story_forge_pressure_clock,
    record_story_forge_convergence,
    story_forge_runtime_diagnostics,
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
    diagnostics = story_forge_runtime_diagnostics(saved)
    assert diagnostics["scene_goal_card_count"] == 1
    assert diagnostics["map_seed_action_count"] == 1
    assert diagnostics["rendered_action_count"] == 1
    assert diagnostics["rendered_map_ref_count"] == 1
    assert diagnostics["map_seed_to_svg_closed"] is True
    assert diagnostics["needs_scene_goal_cards"] is False


def test_story_forge_diagnostics_flags_missing_goal_cards_and_progress():
    session = GameSession.new("group")
    session.scene[STORY_FORGE_ARCHIVE_KEY] = {
        "turns": [{"turn_id": "t1"}],
        "open_threads": [{"id": "hook", "text": "Who moved the bell?"}],
        "thread_progress": [],
        "clue_ledger": [{"id": "clue", "text": "Fresh scrape marks."}],
        "convergence_actions": [],
    }

    diagnostics = story_forge_runtime_diagnostics(session)

    assert diagnostics["enabled_archive_present"] is True
    assert diagnostics["open_thread_count"] == 1
    assert diagnostics["thread_progress_count"] == 0
    assert diagnostics["needs_scene_goal_cards"] is True
    assert diagnostics["needs_thread_progress"] is True


def test_story_forge_pressure_clock_records_advances_and_projects_brief():
    repo = _repo("story-forge-pressure-clock")
    session = GameSession.new("group")
    repo.save_session(session)

    created = record_story_forge_pressure_clock(
        repo,
        "group",
        actor={"player_id": "p1", "display_name": "Ada"},
        clock={
            "clock_id": "clock:ritual",
            "label": "Dawn ritual",
            "pressure_type": "time",
            "value": 1,
            "max": 3,
            "visibility": "public",
            "public_signal": "A bell hums under the floor.",
            "stakes": "At 3/3 the harbor mist locks the lower gate.",
            "next_risk_hint": "Delay or loud entry advances the ritual.",
            "counterplay_hint": "Disrupt the bell focus to slow it.",
            "on_complete": {
                "effect": "The mist seals the lower gate.",
                "failure_forward": "The party can still search for a mist anchor.",
                "new_scene_goal": "Find the mist anchor before it stabilizes.",
            },
        },
    )

    advanced = advance_story_forge_pressure_clock(
        repo,
        "group",
        clock_id="clock:ritual",
        delta=1,
        trigger="continued_investigation",
        cause="The party kept searching the ledger instead of disrupting the bell.",
        visible_effect="A second bell tone vibrates through the wet stone.",
    )

    saved = repo.load_session("group")
    archive = saved.scene[STORY_FORGE_ARCHIVE_KEY]
    brief = saved.scene[STORY_FORGE_BRIEF_KEY]
    diagnostics = story_forge_runtime_diagnostics(saved)

    assert created["ok"] is True
    assert advanced["ok"] is True
    assert archive["pressure_clocks"][0]["value"] == 2
    assert archive["clock_events"][0]["trigger"] == "continued_investigation"
    assert brief["visible_pressure_clocks"][0]["clock_id"] == "clock:ritual"
    assert brief["visible_pressure_clocks"][0]["value"] == 2
    assert brief["recent_clock_events"][0]["visible_effect"].startswith("A second bell")
    assert diagnostics["pressure_clock_count"] == 1
    assert diagnostics["active_pressure_clock_count"] == 1
    assert diagnostics["clock_event_count"] == 1


def test_story_forge_pressure_clock_derives_distinct_ids_from_chinese_labels():
    repo = _repo("story-forge-pressure-clock-chinese-id")
    session = GameSession.new("group")
    repo.save_session(session)

    first = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "label": "涨潮",
            "on_complete": {"failure_forward": "队伍仍可从高处绕行，但要放弃码头线索。"},
        },
    )
    second = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "label": "仪式推进",
            "on_complete": {"new_scene_goal": "在仪式稳定前切断祭坛供能。"},
        },
    )

    saved = repo.load_session("group")
    clock_ids = [clock["clock_id"] for clock in saved.scene[STORY_FORGE_ARCHIVE_KEY]["pressure_clocks"]]

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(clock_ids) == 2
    assert len(set(clock_ids)) == 2
    assert all(clock_id.startswith("clock:") for clock_id in clock_ids)


def test_story_forge_hidden_pressure_clock_event_stays_out_of_player_brief():
    repo = _repo("story-forge-pressure-clock-hidden-event")
    session = GameSession.new("group")
    repo.save_session(session)

    created = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "clock_id": "clock:hidden",
            "label": "Hidden pursuer",
            "visibility": "hidden",
            "public_signal": "Footsteps stop whenever you turn.",
            "on_complete": {
                "effect": "The pursuer changes position.",
                "failure_forward": "The party can still discover a false trail.",
            },
        },
    )
    advanced = advance_story_forge_pressure_clock(
        repo,
        "group",
        clock_id="clock:hidden",
        trigger="delay",
        cause="The party waited in the alley.",
        visible_effect="The alley grows quiet.",
    )

    saved = repo.load_session("group")
    archive = saved.scene[STORY_FORGE_ARCHIVE_KEY]
    brief = saved.scene[STORY_FORGE_BRIEF_KEY]

    assert created["ok"] is True
    assert advanced["ok"] is True
    assert archive["clock_events"][0]["player_visible"] is False
    assert brief["visible_pressure_clocks"] == []
    assert brief["recent_clock_events"] == []


def test_story_forge_diagnostics_counts_turns_since_last_clock_event():
    repo = _repo("story-forge-pressure-clock-turn-count")
    session = GameSession.new("group")
    repo.save_session(session)

    record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "clock_id": "clock:patrol",
            "label": "Patrol closes in",
            "on_complete": {"failure_forward": "The patrol blocks the easy exit, but the roof remains reachable."},
        },
    )
    apply_story_forge_turn(repo, "group", player_message="I inspect the courtyard.", dm_response="You spot lanterns.")
    advance_story_forge_pressure_clock(
        repo,
        "group",
        clock_id="clock:patrol",
        trigger="delay",
        cause="The courtyard inspection took a full exchange.",
        visible_effect="Lantern light sweeps across the far arch.",
    )
    apply_story_forge_turn(repo, "group", player_message="I check the roofline.", dm_response="The roof is reachable.")
    apply_story_forge_turn(repo, "group", player_message="I wait for a gap.", dm_response="A gap opens briefly.")

    diagnostics = story_forge_runtime_diagnostics(repo.load_session("group"))

    assert diagnostics["turns_since_last_clock_event"] == 2


def test_story_forge_pressure_clock_completion_requires_failure_forward():
    repo = _repo("story-forge-pressure-clock-guard")
    session = GameSession.new("group")
    repo.save_session(session)

    result = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "clock_id": "clock:bad",
            "label": "Bad clock",
            "max": 3,
            "on_complete": {"effect": "The party fails."},
        },
    )

    assert result["ok"] is False
    assert result["error"] == "story_forge_pressure_clock_rejected"
    assert "failure_forward" in result["reason"]


def test_story_forge_hidden_pressure_clock_stays_out_of_player_brief():
    repo = _repo("story-forge-pressure-clock-hidden")
    session = GameSession.new("group")
    repo.save_session(session)

    result = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "clock_id": "clock:hidden",
            "label": "Hidden pursuer",
            "visibility": "hidden",
            "public_signal": "Footsteps stop whenever you turn.",
            "on_complete": {
                "effect": "The pursuer changes position.",
                "failure_forward": "The party can still discover a false trail.",
            },
        },
    )

    saved = repo.load_session("group")
    brief = saved.scene[STORY_FORGE_BRIEF_KEY]

    assert result["ok"] is True
    assert saved.scene[STORY_FORGE_ARCHIVE_KEY]["pressure_clocks"][0]["clock_id"] == "clock:hidden"
    assert brief["visible_pressure_clocks"] == []


def test_story_forge_pressure_clock_rejects_hidden_truth_fields():
    repo = _repo("story-forge-pressure-clock-hidden-truth")
    session = GameSession.new("group")
    repo.save_session(session)

    result = record_story_forge_pressure_clock(
        repo,
        "group",
        clock={
            "label": "Secret culprit",
            "hidden_truth": "The keeper staged it.",
            "on_complete": {"failure_forward": "The party can recover through another lead."},
        },
    )

    assert result["ok"] is False
    assert "hidden_pressure_clock_fields_not_allowed" in result["reason"]


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
