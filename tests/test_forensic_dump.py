import json
from pathlib import Path

import pytest

from astrbot_plugin_auto_trpg_dm.core.forensic_collector import ForensicCollector
from astrbot_plugin_auto_trpg_dm.core.forensic_diff import compute_session_diff
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository


def test_collector_builds_envelope_with_minimal_data():
    collector = ForensicCollector()
    collector.start_turn(
        session_id="sess_1",
        cycle_id=3,
        turn_sequence=3,
        actor={"player_id": "u1", "name": "Alice"},
        player_message="I attack the goblin",
        security_notes=["note"],
    )
    envelope = collector.build_envelope()

    assert envelope["envelope_version"] == "1.0"
    assert envelope["session_id"] == "sess_1"
    assert envelope["cycle_id"] == 3
    assert envelope["turn_sequence"] == 3
    assert envelope["actor"]["player_id"] == "u1"
    assert envelope["player_message"] == "I attack the goblin"
    assert envelope["timings"]["start"] != ""
    assert envelope["timings"]["end"] != ""
    assert envelope["metadata"]["envelope_size_bytes"] > 0


def test_collector_records_state_and_diff():
    collector = ForensicCollector()
    collector.start_turn(
        session_id="s",
        cycle_id=1,
        turn_sequence=1,
        actor={"player_id": "u1"},
        player_message="msg",
    )
    before = {"characters": {}, "scene": {"summary": "old"}}
    after = {"characters": {"c1": {"name": "Bob"}}, "scene": {"summary": "new"}}
    collector.record_state_before(before)
    collector.record_state_after(after)
    envelope = collector.build_envelope()

    assert envelope["state"]["before"] == before
    assert envelope["state"]["after"] == after
    diff = envelope["state"]["diff"]
    assert "characters" in diff
    assert "scene" in diff
    assert diff["characters"]["c1"]["created"] is True


def test_collector_records_prompts_conditionally():
    collector = ForensicCollector(include_prompts=True, include_raw_response=True)
    collector.start_turn("s", 1, 1, {"player_id": "u1"}, "msg")
    collector.record_prompts(
        system_prompt="sys",
        user_prompt="user",
        tool_names=["t1"],
        tool_specs=[{"name": "t1"}],
        projection_stats={"chars": 100},
        component_chars={"system": 50},
    )
    envelope = collector.build_envelope()
    assert envelope["prompts"]["system_prompt"] == "sys"
    assert envelope["prompts"]["user_prompt"] == "user"
    assert envelope["metadata"]["prompt_hashes"]["system"] != ""


def test_collector_omits_prompts_when_disabled():
    collector = ForensicCollector(include_prompts=False, include_raw_response=False)
    collector.start_turn("s", 1, 1, {"player_id": "u1"}, "msg")
    collector.record_prompts(
        system_prompt="sys",
        user_prompt="user",
        tool_names=[],
        tool_specs=[],
        projection_stats={},
        component_chars={},
    )
    envelope = collector.build_envelope()
    assert envelope["prompts"]["included"] is False
    assert "system_prompt" not in envelope["prompts"]


def test_collector_records_llm_interaction():
    collector = ForensicCollector()
    collector.start_turn("s", 1, 1, {"player_id": "u1"}, "msg")
    collector.record_llm_request(
        step=1,
        prompt="prompt text",
        contexts=[{"role": "user", "content": "hi"}],
        system_prompt="sys",
    )
    collector.record_llm_response(
        step=1,
        completion_text="hello",
        tool_calls=[],
        raw_response_safe={"id": "r1"},
        usage={"prompt_tokens": 10},
        finish_reason="stop",
    )
    envelope = collector.build_envelope()
    assert len(envelope["llm_interactions"]) == 1
    interaction = envelope["llm_interactions"][0]
    assert interaction["step"] == 1
    assert interaction["request"]["prompt"] == "prompt text"
    assert interaction["response"]["completion_text"] == "hello"
    assert interaction["response"]["raw_response_safe"]["id"] == "r1"


def test_collector_records_guards_and_final_output():
    collector = ForensicCollector()
    collector.start_turn("s", 1, 1, {"player_id": "u1"}, "msg")
    collector.record_guard("action_reasonableness", "pass", {"detail": "ok"})
    collector.record_final_output(
        completion_text="done",
        dice_summary="1d20=15",
        pending_outputs=[],
        sent_to_player=True,
    )
    envelope = collector.build_envelope()
    assert len(envelope["guards_fired"]) == 1
    assert envelope["guards_fired"][0]["name"] == "action_reasonableness"
    assert envelope["final_output"]["completion_text"] == "done"


def test_compute_session_diff_detects_memory_summary_change():
    before = {"memory_summary": "old"}
    after = {"memory_summary": "new"}
    diff = compute_session_diff(before, after)
    assert diff["memory_summary_changed"] is True


def test_compute_session_diff_detects_no_memory_summary_change():
    before = {"memory_summary": "same"}
    after = {"memory_summary": "same"}
    diff = compute_session_diff(before, after)
    assert diff["memory_summary_changed"] is False


def test_compute_session_diff_detects_character_added():
    before = {"characters": {}}
    after = {"characters": {"c1": {"name": "Bob", "hp": 10}}}
    diff = compute_session_diff(before, after)
    char_diff = diff["characters"]
    assert char_diff["c1"]["created"] is True
    assert char_diff["c1"]["name"] == "Bob"


def test_compute_session_diff_detects_scene_change():
    before = {"scene": {"summary": "dark", "current_conflict": ""}}
    after = {"scene": {"summary": "bright", "current_conflict": "fight"}}
    diff = compute_session_diff(before, after)
    scene_diff = diff["scene"]
    assert "summary" in scene_diff["keys_changed"]
    assert scene_diff["keys_changed"]["summary"]["old"] == "dark"
    assert scene_diff["keys_changed"]["summary"]["new"] == "bright"
    assert "current_conflict" in scene_diff["keys_changed"]


def test_compute_session_diff_empty_scenes():
    before = {"scene": {}}
    after = {"scene": {}}
    diff = compute_session_diff(before, after)
    assert diff["scene"] == {}


def test_repository_write_turn_dump(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
    envelope = {
        "turn_id": "t1",
        "turn_sequence": 5,
        "session_id": "group",
        "envelope_version": "1.0",
    }
    path = repo.write_turn_dump("group", envelope)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["turn_id"] == "t1"


def test_repository_list_turn_dumps(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
    repo.write_turn_dump("group", {"turn_id": "t1", "turn_sequence": 1, "session_id": "group", "envelope_version": "1.0"})
    repo.write_turn_dump("group", {"turn_id": "t2", "turn_sequence": 2, "session_id": "group", "envelope_version": "1.0"})
    dumps = repo.list_turn_dumps("group")
    assert len(dumps) == 2
    assert all("name" in d for d in dumps)


def test_repository_turn_dump_rotation_by_count(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
    for i in range(5):
        repo.write_turn_dump(
            "group",
            {"turn_id": f"t{i}", "turn_sequence": i, "session_id": "group", "envelope_version": "1.0"},
            max_turns=3,
        )
    dumps = repo.list_turn_dumps("group")
    assert len(dumps) == 3
