from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import (
    BASE_RULES,
    build_cycle_start_prompt,
    build_diagnostic_system_prompt,
    build_ra_system_prompt,
    build_system_prompt,
    prompt_component_chars,
)


def test_system_prompt_includes_shared_cycle_contract():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["cycle_control"],
        [{"name": "cycle_control", "description": "结束当前叙事周期"}],
        actor={"player_id": "player-1"},
    )

    assert BASE_RULES in prompt
    assert 'cycle_control(action="end_cycle")' in prompt
    assert "RA 只读取 `ra_cycle_input`" in prompt
    assert "完整 `GameSession`" in prompt
    assert "结束当前叙事周期" not in prompt


def test_ra_system_prompt_restricts_input_and_output_contract():
    prompt = build_ra_system_prompt()

    assert "只根据 `ra_cycle_input`" in prompt
    assert "清洗后的权威字段快照" in prompt
    assert "完整 `GameSession`" in prompt
    assert "原始玩家输入" in prompt
    assert "prompt" in prompt
    assert "诊断字段" in prompt
    assert "raw audit" in prompt
    assert "合法 JSON" in prompt
    assert "不调用工具" in prompt


def test_cycle_start_prompt_uses_validated_summary_not_raw_patch_candidates():
    prompt = build_cycle_start_prompt(
        {
            "summary": "队伍击退巡逻队并封住北门。",
            "discrepancies": ["口头叙事伤害高于工具结算。"],
            "patch_candidates": {
                "character_status": [{"id": "pc-1", "hp": 7}],
            },
        }
    )

    assert "已经验证的 RA 摘要" in prompt
    assert "discrepancies" in prompt
    assert "不要把未验证的补丁候选当成事实" in prompt
    assert "队伍击退巡逻队" in prompt
    assert "口头叙事伤害高于工具结算" in prompt


def test_system_prompt_only_includes_ra_summary_when_enabled():
    session = GameSession.new("group")
    session.environment_summaries.append({"summary": "上一周期摘要", "discrepancies": []})

    disabled_prompt = build_system_prompt(session, GameMode.NARRATIVE, [], actor={})
    enabled_prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        [],
        actor={},
        include_ra_context=True,
    )

    assert "上一周期摘要" not in disabled_prompt
    assert "上一周期摘要" in enabled_prompt


def test_system_prompt_minifies_snapshot_and_avoids_duplicate_memory_summary():
    session = GameSession.new("group")
    session.memory_summary = "长期摘要：队伍已经发现北门暗道。"
    session.scene["summary"] = "队伍在北门外等待。"

    prompt = build_system_prompt(session, GameMode.NARRATIVE, [], actor={"player_id": "player-1"})

    assert prompt.count("长期摘要：队伍已经发现北门暗道。") == 1
    assert '"session_id":"group"' in prompt
    assert '"player_id":"player-1"' in prompt
    assert '"memory_summary"' not in prompt


def test_diagnostic_system_prompt_is_lightweight_and_keeps_safety_boundary():
    session = GameSession.new("group")
    session.memory_summary = "长期摘要不应进入轻量诊断 prompt。"
    session.scene["summary"] = "很长的场景正文不应进入轻量诊断 prompt。"

    prompt = build_diagnostic_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
    )

    assert "AstrBot TRPG DM" in prompt
    assert "estimate_token_usage" in prompt
    assert "session_control" in prompt
    assert "audit" in prompt
    assert "prompt" in prompt
    assert '"session_id":"group"' in prompt
    assert "长期摘要不应进入轻量诊断 prompt" not in prompt
    assert "很长的场景正文不应进入轻量诊断 prompt" not in prompt
    assert BASE_RULES not in prompt


def test_prompt_component_chars_reports_standard_and_diagnostic_profiles():
    session = GameSession.new("group")
    session.memory_summary = "memory summary"
    session.scene["summary"] = "scene summary"

    standard = prompt_component_chars(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
        external_memory_context="external memory",
    )
    diagnostic = prompt_component_chars(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
        external_memory_context="external memory",
        profile="diagnostic",
    )

    assert standard["profile"] == "standard"
    assert standard["snapshot_chars"] > 0
    assert standard["memory_summary_chars"] == len("memory summary")
    assert standard["external_memory_chars"] == len("external memory")
    assert standard["base_rules_chars"] == len(BASE_RULES)
    assert diagnostic["profile"] == "diagnostic"
    assert diagnostic["diagnostic_snapshot_chars"] > 0
    assert diagnostic["memory_summary_chars"] == 0
    assert diagnostic["external_memory_chars"] == 0
    assert "snapshot_chars" not in diagnostic
