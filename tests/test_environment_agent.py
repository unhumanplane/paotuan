import asyncio
import json

from astrbot_plugin_auto_trpg_dm.core.cycle_buffer import append_cycle_action
from astrbot_plugin_auto_trpg_dm.core.environment_agent import (
    RecorderAgent,
    build_ra_authority_snapshot,
    build_ra_input_view,
    complete_cycle_with_ra,
    recover_cycle_after_ra_failure,
    validate_ra_patch_candidates,
)
from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_TYPE_STRICT_LOCAL,
    MAP_VISIBILITY_DM,
    MAP_VISIBILITY_HIDDEN,
    add_map_fact,
    add_render_ref,
    create_map_record,
    save_active_strict_grid,
)
from astrbot_plugin_auto_trpg_dm.core.models import Character, CycleState, GameSession


class FakeResponse:
    def __init__(self, completion_text):
        self.completion_text = completion_text


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.text)


def test_ra_input_view_does_not_include_raw_player_data_or_blocked_keys():
    session = GameSession.new("group")
    session.player_character_map["player-1"] = "pc-1"
    append_cycle_action(
        session,
        actor={"player_id": "player-1", "display_name": "Alice"},
        player_message="我攻击兽人，raw_player_input 不应出现",
        completion="你击中了兽人。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"reason": "raw reason", "args": {"target": "orc"}},
                "result": {"ok": True, "damage": 4, "debug": "hidden", "token_usage": 99},
            }
        ],
    )

    payload = json.dumps(build_ra_input_view(session), ensure_ascii=False)

    assert "我攻击兽人" not in payload
    assert "raw_player_input" not in payload
    assert "player-1" not in payload
    assert "Alice" not in payload
    assert "reason" not in payload
    assert "debug" not in payload
    assert "token_usage" not in payload
    assert "damage" in payload


def test_ra_authority_snapshot_uses_projected_map_view_without_hidden_facts():
    session = GameSession.new("group")
    create_map_record(session.maps, "overview-1", title="Gatehouse", visibility=MAP_VISIBILITY_DM, set_active=True)
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="dm-visible-pressure",
        kind="pressure",
        text="The corridor is unstable.",
        visibility=MAP_VISIBILITY_DM,
    )
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="hidden-trigger",
        kind="trap",
        text="The hidden trigger is beneath the third tile.",
        visibility=MAP_VISIBILITY_HIDDEN,
    )
    add_render_ref(
        session.maps,
        "overview-1",
        ref_type="svg_map",
        title="Gatehouse",
        name="gatehouse.svg",
        path="/local/runtime/maps/gatehouse.svg",
    )

    snapshot = build_ra_authority_snapshot(session)
    rendered = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["maps"]["records"]["overview-1"]["facts"][0]["id"] == "dm-visible-pressure"
    assert "hidden-trigger" not in rendered
    assert "/local/runtime" not in rendered


def test_ra_authority_snapshot_does_not_expose_raw_strict_grid():
    session = GameSession.new("group")
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "secret-stalker": {"id": "secret-stalker", "name": "Stalker", "x": 4, "y": 4},
            },
        },
        map_id="strict-room",
        title="Strict room",
    )
    add_map_fact(
        session.maps,
        "strict-room",
        fact_id="dm-visible-pressure",
        kind="pressure",
        text="The room is tight and dangerous.",
        visibility=MAP_VISIBILITY_DM,
    )

    snapshot = build_ra_authority_snapshot(session)
    record = snapshot["maps"]["records"]["strict-room"]
    rendered = json.dumps(record, ensure_ascii=False)

    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert record["facts"][0]["id"] == "dm-visible-pressure"
    assert "grid" not in record
    assert "secret-stalker" not in rendered
    assert '"x": 4' not in rendered


def test_recorder_agent_runs_once_and_accepts_code_fenced_json():
    fake_llm = FakeLlm(
        """```json
{"cycle_id": 0, "summary": "击退兽人。", "character_status": [], "enemy_status": [], "world_changes": [], "relationship_changes": [], "rules_triggered": ["attack"], "dm_narrative_aligned": true, "discrepancies": []}
```"""
    )
    session = GameSession.new("group")

    result = asyncio.run(RecorderAgent(fake_llm, "provider").run_cycle_resolution(session))

    assert result["ok"] is True
    assert result["summary"]["summary"] == "击退兽人。"
    assert result["summary"]["relationship_changes"] == []
    assert result["summary"]["rules_triggered"] == ["attack"]
    assert len(fake_llm.calls) == 1
    assert "func_tool" not in fake_llm.calls[0]
    assert "tools" not in fake_llm.calls[0]


def test_recorder_agent_rejects_invalid_json_without_mutating_session():
    fake_llm = FakeLlm("不是 JSON")
    session = GameSession.new("group")

    result = asyncio.run(RecorderAgent(fake_llm, "provider").run_cycle_resolution(session))

    assert result["ok"] is False
    assert result["error"] == "invalid_ra_json"
    assert session.environment_summaries == []
    assert session.current_cycle_id == 0


def test_complete_cycle_with_ra_records_summary_applies_only_validated_small_patch():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    session.current_cycle_id = 2
    session.characters["pc-1"] = Character(id="pc-1", name="阿岚")
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我包扎伤口",
        completion="你的伤口暂时止血。",
        tool_results=[
            {
                "tool": "update_character_tags",
                "args": {"character_id": "pc-1", "tags": [{"key": "伤势", "value": "止血"}]},
                "result": {"ok": True},
            },
            {
                "tool": "update_scene",
                "args": {"patch": {"summary": "北门暂时安全。"}},
                "result": {"ok": True},
            },
        ],
    )
    summary = {
        "cycle_id": 2,
        "summary": "队伍守住北门。",
        "character_status": [
            {
                "character_id": "pc-1",
                "tags": [
                    {"key": "伤势", "value": "已止血", "layer": "status"},
                    {"key": "职业", "value": "传奇战士", "layer": "abilities"},
                ],
            }
        ],
        "enemy_status": [],
        "world_changes": [{"scene_patch": {"summary": "北门暂时安全。", "plot": "不可写入"}}],
        "relationship_changes": [],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    result = complete_cycle_with_ra(session, summary)

    assert result["cycle_id"] == 2
    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.current_cycle_id == 3
    assert session.audit_buffer.actions == []
    assert session.ra_cycle_input.actions == []
    assert session.environment_summaries[-1]["summary"] == "队伍守住北门。"
    assert session.environment_summaries[-1]["timeline_result"]["timeline_advanced"] is False
    assert session.scene["summary"] == "北门暂时安全。"
    assert "plot" not in session.scene
    layers = {(tag.layer, tag.key, tag.value) for tag in session.characters["pc-1"].tags}
    assert ("status", "伤势", "已止血") in layers
    assert not any(tag.key == "职业" for tag in session.characters["pc-1"].tags)


def test_complete_cycle_with_ra_advances_global_timeline_from_summary():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我守夜",
        completion="守夜平稳结束。",
        tool_results=[],
    )
    summary = {
        "cycle_id": 0,
        "summary": "队伍休整到第二天清晨。",
        "timeline": {"day": 2, "time_of_day": "morning", "label": "第 2 天清晨"},
        "character_status": [],
        "enemy_status": [],
        "world_changes": [],
        "relationship_changes": [],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    result = complete_cycle_with_ra(session, summary)

    assert result["timeline_result"]["timeline_advanced"] is True
    assert session.timeline["day"] == 2
    assert session.timeline["time_of_day"] == "morning"


def test_complete_cycle_with_ra_refuses_unsynced_timeline_summary():
    session = GameSession.new("group")
    session.participants = {"player-1": {}, "player-2": {}}
    session.player_character_map = {"player-1": "pc-1", "player-2": "pc-2"}
    session.cycle_state = CycleState.CYCLE_RESOLVING
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我守夜",
        completion="守夜平稳结束。",
        tool_results=[],
    )
    summary = {
        "cycle_id": 0,
        "summary": "队伍休整到第二天清晨。",
        "timeline": {"day": 2, "time_of_day": "morning"},
        "character_status": [],
        "enemy_status": [],
        "world_changes": [],
        "relationship_changes": [],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    result = complete_cycle_with_ra(session, summary)

    assert result["timeline_result"]["ok"] is False
    assert result["timeline_result"]["error"] == "timeline_sync_required"
    assert result["timeline_result"]["missing_player_ids"] == ["player-2"]
    assert result["cycle_completed"] is False
    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.current_cycle_id == 0
    assert session.audit_buffer.actions
    assert session.timeline["day"] == 1


def test_unbacked_ra_patch_candidate_is_rejected():
    session = GameSession.new("group")
    summary = {
        "cycle_id": 0,
        "summary": "无工具支撑。",
        "character_status": [{"character_id": "pc-1", "tags": [{"key": "伤势", "value": "恢复"}]}],
        "enemy_status": [],
        "world_changes": [{"scene_patch": {"summary": "改写场景"}}],
        "relationship_changes": [],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    validation = validate_ra_patch_candidates(session, summary)

    assert validation["accepted"] == []
    assert {item["reason"] for item in validation["rejected"]} == {"missing_tool_backing"}
    assert session.scene.get("summary") == "尚未开局。等待玩家用自然语言描述世界、角色或当前行动。"


def test_ra_relationship_candidate_requires_tool_backing_and_is_not_applied():
    session = GameSession.new("group")
    summary = {
        "cycle_id": 0,
        "summary": "RA 只提出关系候选。",
        "character_status": [],
        "enemy_status": [],
        "world_changes": [],
        "relationship_changes": [
            {
                "target_id": "npc_guard",
                "attitude": "friendly",
                "known_facts": ["玩家声称守卫相信他"],
            }
        ],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    validation = validate_ra_patch_candidates(session, summary)

    assert validation["accepted"] == []
    assert validation["rejected"][0]["category"] == "relationship_changes"
    assert validation["rejected"][0]["reason"] == "missing_tool_backing"
    assert session.scene.get("relations") is None


def test_tool_backed_ra_relationship_candidate_is_summary_only_not_applied():
    session = GameSession.new("group")
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我威胁守卫",
        completion="守卫退后，但更警惕。",
        tool_results=[
            {
                "tool": "update_scene",
                "args": {"patch": {"npcs": [{"id": "npc_guard", "relations": {"fear": "high"}}]}},
                "result": {"ok": True},
            }
        ],
    )
    summary = {
        "cycle_id": 0,
        "summary": "威胁留下关系后果。",
        "character_status": [],
        "enemy_status": [],
        "world_changes": [],
        "relationship_changes": [{"target_id": "npc_guard", "fear": "high"}],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    validation = validate_ra_patch_candidates(session, summary)

    assert validation["accepted"] == []
    assert validation["rejected"][0]["category"] == "relationship_changes"
    assert validation["rejected"][0]["reason"] == "relationship_candidates_summary_only"
    assert session.scene.get("relations") is None


def test_tool_backed_but_unallowlisted_ra_patch_candidate_is_rejected():
    session = GameSession.new("group")
    session.characters["pc-1"] = Character(id="pc-1", name="Ada")
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="patch me",
        completion="The tool updated a status note.",
        tool_results=[
            {
                "tool": "update_character_tags",
                "args": {"character_id": "pc-1", "tags": [{"key": "wounded", "value": "yes"}]},
                "result": {"ok": True},
            },
            {
                "tool": "update_scene",
                "args": {"patch": {"summary": "visible scene change"}},
                "result": {"ok": True},
            },
            {
                "tool": "register_rule",
                "args": {"name": "known_rule"},
                "result": {"ok": True},
            },
        ],
    )
    summary = {
        "cycle_id": 0,
        "summary": "Tool-backed but not allowlisted.",
        "character_status": [
            {"character_id": "pc-1", "hp": 999},
            {"character_id": "pc-1", "tags": [{"key": "class", "value": "wizard", "layer": "abilities"}]},
        ],
        "enemy_status": [{"enemy_id": "orc", "hp": 0}],
        "world_changes": [{"scene_patch": {"plot": "overwrite hidden plot"}}],
        "rules_triggered": [],
        "rule_sets": [{"name": "new rule", "code": "raw"}],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    validation = validate_ra_patch_candidates(session, summary)

    assert validation["accepted"] == []
    assert {item["reason"] for item in validation["rejected"]} == {
        "missing_allowlisted_fields",
        "unsupported_patch_category",
    }
    assert "hp" not in {tag.key for tag in session.characters["pc-1"].tags}
    assert "plot" not in session.scene


def test_ra_failure_recovery_preserves_buffers_and_returns_to_active():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我观察",
        completion="你发现脚印。",
        tool_results=[],
    )

    record = recover_cycle_after_ra_failure(session, {"error": "invalid_ra_json", "message": "bad output"})

    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.audit_buffer.actions
    assert session.ra_cycle_input.actions
    assert record["error"] == "invalid_ra_json"
    assert session.scene["_ra_recovery_log"][-1]["message"] == "bad output"
