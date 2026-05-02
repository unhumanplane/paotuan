import sys
import types


def _install_fake_astrbot_modules():
    if "astrbot.core.agent.tool" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    core = types.ModuleType("astrbot.core")
    agent = types.ModuleType("astrbot.core.agent")
    run_context = types.ModuleType("astrbot.core.agent.run_context")
    tool = types.ModuleType("astrbot.core.agent.tool")
    astr_agent_context = types.ModuleType("astrbot.core.astr_agent_context")

    class FakeContextWrapper:
        def __class_getitem__(cls, item):
            return cls

    class FakeFunctionTool:
        def __class_getitem__(cls, item):
            return cls

        def validate_parameters(self):
            return None

    class FakeToolSet:
        def __init__(self, tools):
            self.tools = tools

    class FakeAstrAgentContext:
        pass

    run_context.ContextWrapper = FakeContextWrapper
    tool.FunctionTool = FakeFunctionTool
    tool.ToolSet = FakeToolSet
    astr_agent_context.AstrAgentContext = FakeAstrAgentContext

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.core"] = core
    sys.modules["astrbot.core.agent"] = agent
    sys.modules["astrbot.core.agent.run_context"] = run_context
    sys.modules["astrbot.core.agent.tool"] = tool
    sys.modules["astrbot.core.astr_agent_context"] = astr_agent_context


_install_fake_astrbot_modules()

from astrbot_plugin_auto_trpg_dm.core.router import IntentRouter, _extract_llm_usage_summary


def test_extract_llm_usage_summary_reads_openai_cached_tokens():
    summary = _extract_llm_usage_summary(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 64},
            },
            "completion_text": "raw answer should not be copied",
        }
    )

    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 20
    assert summary["total_tokens"] == 120
    assert summary["cached_tokens"] == 64
    assert summary["cache_hit_ratio_pct"] == 64.0
    assert "completion_text" not in summary


def test_extract_llm_usage_summary_reads_object_usage():
    class Usage:
        prompt_tokens = "160"
        completion_tokens = 40
        prompt_tokens_details = {"cached_tokens": "120"}

    class Response:
        usage = Usage()

    summary = _extract_llm_usage_summary(Response())

    assert summary["prompt_tokens"] == 160
    assert summary["completion_tokens"] == 40
    assert summary["cached_tokens"] == 120
    assert summary["cache_hit_ratio_pct"] == 75.0


def test_extract_llm_usage_summary_reads_anthropic_cache_fields():
    summary = _extract_llm_usage_summary(
        {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 30,
                "cache_read_input_tokens": 150,
                "cache_creation_input_tokens": 25,
            }
        }
    )

    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 30
    assert summary["cache_read_input_tokens"] == 150
    assert summary["cache_creation_input_tokens"] == 25
    assert summary["cache_hit_ratio_pct"] == 75.0


def test_start_game_arg_repair_coerces_json_string_outline():
    repaired = IntentRouter._repair_tool_args(
        "start_game",
        {
            "opening_intro": "开场文字",
            "campaign_outline": '{"act_1":"导火索","act_2":"升级","act_3":"高潮"}',
            "scene_patch": "废弃枢纽站里传来爪刃声。",
        },
        "开始游戏",
    )

    assert repaired["campaign_outline"]["act_1"] == "导火索"
    assert repaired["scene_patch"]["summary"] == "废弃枢纽站里传来爪刃声。"
