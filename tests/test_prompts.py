from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import (
    BASE_RULES,
    build_cycle_start_prompt,
    build_ra_system_prompt,
    build_system_prompt,
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
