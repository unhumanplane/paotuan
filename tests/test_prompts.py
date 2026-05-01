from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import (
    BASE_RULES,
    _build_ra_summary_block,
    _inject_base_rules,
    _inject_ra_summary,
    build_cycle_start_prompt,
    build_ra_system_prompt,
    build_system_prompt,
)


class TestBaseRules:
    def test_base_rules_contains_meta_mechanics(self):
        assert "双 Agent 架构" in BASE_RULES
        assert "DM Agent" in BASE_RULES
        assert "RA Recorder Agent" in BASE_RULES

    def test_base_rules_contains_prohibited_behaviors(self):
        assert "禁止 OOC" in BASE_RULES
        assert "元游戏" in BASE_RULES

    def test_base_rules_contains_universal_constraints(self):
        assert "start_game 后" in BASE_RULES
        assert "锁定" in BASE_RULES

    def test_base_rules_length_under_800_chars(self):
        assert len(BASE_RULES) <= 800, f"BASE_RULES is {len(BASE_RULES)} chars"


class TestBuildRaSystemPrompt:
    def test_contains_base_rules(self):
        prompt = build_ra_system_prompt()
        assert BASE_RULES in prompt

    def test_contains_json_only_instruction(self):
        prompt = build_ra_system_prompt()
        assert "JSON only" in prompt or "output must be valid JSON" in prompt

    def test_contains_no_tool_access(self):
        prompt = build_ra_system_prompt()
        assert "不拥有工具调用权限" in prompt

    def test_contains_authority_fields_guidance(self):
        prompt = build_ra_system_prompt()
        assert "权威字段" in prompt
        assert "tool trace" in prompt

    def test_contains_discrepancy_guidance(self):
        prompt = build_ra_system_prompt()
        assert "discrepancy" in prompt or "discrepancies" in prompt


class TestBuildCycleStartPrompt:
    def test_empty_summaries_returns_fallback(self):
        session = GameSession.new("test")
        prompt = build_cycle_start_prompt(session)
        assert "暂无上一周期总结" in prompt

    def test_with_summary_includes_all_fields(self):
        session = GameSession.new("test")
        session.environment_summaries.append({
            "cycle_id": 3,
            "summary": "The party advanced.",
            "character_status": {"pc_john": {"hp": 14}},
            "enemy_status": {"orc": {"hp": 4}},
            "world_changes": {"scene_updates": {"conflict": "retreat"}},
            "rules_triggered": ["melee_attack"],
            "discrepancies": ["dm said fall but hp=4"],
        })
        prompt = build_cycle_start_prompt(session)
        assert "周期 #3" in prompt
        assert "The party advanced." in prompt
        assert "pc_john" in prompt
        assert "orc" in prompt
        assert "melee_attack" in prompt
        assert "dm said fall but hp=4" in prompt


class TestInjectBaseRules:
    def test_injects_before_hard_rules(self):
        original = "硬性规则：\n1. Foo"
        result = _inject_base_rules(original)
        assert BASE_RULES in result
        assert result.index(BASE_RULES) < result.index("硬性规则：")

    def test_no_marker_prepends_to_top(self):
        original = "Some prompt without marker"
        result = _inject_base_rules(original)
        assert result.startswith(BASE_RULES)


class TestBuildRaSummaryBlock:
    def test_empty_summaries_returns_empty(self):
        session = GameSession.new("test")
        assert _build_ra_summary_block(session) == ""

    def test_includes_summary_and_discrepancies(self):
        session = GameSession.new("test")
        session.environment_summaries.append({
            "summary": "Fight happened.",
            "discrepancies": ["hp mismatch"],
            "character_status": {"pc": {"hp": 10}},
        })
        block = _build_ra_summary_block(session)
        assert "Fight happened." in block
        assert "hp mismatch" in block
        assert "pc" in block

    def test_skips_empty_fields(self):
        session = GameSession.new("test")
        session.environment_summaries.append({"summary": "Only summary."})
        block = _build_ra_summary_block(session)
        assert "敌人状态" not in block


class TestInjectRaSummary:
    def test_no_summaries_returns_prompt_unchanged(self):
        session = GameSession.new("test")
        original = "当前会话状态快照：\n{}"
        result = _inject_ra_summary(original, session)
        assert result == original

    def test_injects_before_snapshot(self):
        session = GameSession.new("test")
        session.environment_summaries.append({"summary": "Test."})
        original = "当前会话状态快照：\n{}"
        result = _inject_ra_summary(original, session)
        assert "上一周期 RA 总结" in result
        assert result.index("上一周期 RA 总结") < result.index("当前会话状态快照：")


class TestBuildSystemPromptHooks:
    def test_system_prompt_contains_base_rules(self):
        session = GameSession.new("test")
        prompt = build_system_prompt(
            session=session,
            mode=GameMode.NARRATIVE,
            tool_names=["update_scene"],
        )
        assert BASE_RULES in prompt

    def test_system_prompt_contains_ra_summary_placeholder_when_no_summaries(self):
        session = GameSession.new("test")
        prompt = build_system_prompt(
            session=session,
            mode=GameMode.NARRATIVE,
            tool_names=["update_scene"],
        )
        # When no summaries, _inject_ra_summary should not add anything
        assert "上一周期 RA 总结" not in prompt

    def test_system_prompt_contains_ra_summary_when_summaries_exist(self):
        session = GameSession.new("test")
        session.environment_summaries.append({
            "summary": "Previous cycle.",
            "character_status": {},
        })
        prompt = build_system_prompt(
            session=session,
            mode=GameMode.NARRATIVE,
            tool_names=["update_scene"],
        )
        assert "上一周期 RA 总结" in prompt
        assert "Previous cycle." in prompt

    def test_prompt_length_estimate_under_limit(self):
        session = GameSession.new("test")
        prompt = build_system_prompt(
            session=session,
            mode=GameMode.NARRATIVE,
            tool_names=["update_scene"],
        )
        # BASE_RULES should not increase prompt by more than ~1000 chars
        # Original prompt is ~150 rules; adding BASE_RULES should be modest
        assert len(prompt) < 15000, f"Prompt is {len(prompt)} chars"
