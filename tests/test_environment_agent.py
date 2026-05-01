import asyncio
import json

from astrbot_plugin_auto_trpg_dm.core.cycle_buffer import append_cycle_action
from astrbot_plugin_auto_trpg_dm.core.environment_agent import (
    RecorderAgent,
    build_ra_input_view,
    complete_cycle_with_ra,
    recover_cycle_after_ra_failure,
    validate_ra_patch_candidates,
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


def test_recorder_agent_runs_once_and_accepts_code_fenced_json():
    fake_llm = FakeLlm(
        """```json
{"cycle_id": 0, "summary": "击退兽人。", "character_status": [], "enemy_status": [], "world_changes": [], "rules_triggered": ["attack"], "dm_narrative_aligned": true, "discrepancies": []}
```"""
    )
    session = GameSession.new("group")

    result = asyncio.run(RecorderAgent(fake_llm, "provider").run_cycle_resolution(session))

    assert result["ok"] is True
    assert result["summary"]["summary"] == "击退兽人。"
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
    assert session.scene["summary"] == "北门暂时安全。"
    assert "plot" not in session.scene
    layers = {(tag.layer, tag.key, tag.value) for tag in session.characters["pc-1"].tags}
    assert ("status", "伤势", "已止血") in layers
    assert not any(tag.key == "职业" for tag in session.characters["pc-1"].tags)


def test_unbacked_ra_patch_candidate_is_rejected():
    session = GameSession.new("group")
    summary = {
        "cycle_id": 0,
        "summary": "无工具支撑。",
        "character_status": [{"character_id": "pc-1", "tags": [{"key": "伤势", "value": "恢复"}]}],
        "enemy_status": [],
        "world_changes": [{"scene_patch": {"summary": "改写场景"}}],
        "rules_triggered": [],
        "dm_narrative_aligned": True,
        "discrepancies": [],
    }

    validation = validate_ra_patch_candidates(session, summary)

    assert validation["accepted"] == []
    assert {item["reason"] for item in validation["rejected"]} == {"missing_tool_backing"}
    assert session.scene.get("summary") == "尚未开局。等待玩家用自然语言描述世界、角色或当前行动。"


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
