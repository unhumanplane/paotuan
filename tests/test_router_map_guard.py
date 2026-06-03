import sys
import types

import pytest


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

from astrbot_plugin_auto_trpg_dm.core.router import IntentRouter


class _Response:
    def __init__(self, completion_text="", tool_calls=None):
        self.completion_text = completion_text
        self.tool_calls = tool_calls or []


class _Repository:
    def __init__(self):
        self.audit_records = []

    def append_audit(self, session_id, record):
        self.audit_records.append((session_id, record))

    def load_session(self, session_id):
        return None


class _ToolSet:
    def __init__(self, tools):
        self.tools = tools


class _Executor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool_name, args):
        self.calls.append((tool_name, args))
        if tool_name == "update_scene":
            return {"ok": True, "scene": {"clues": [{"id": "clue_route_map", "status": "discovered"}]}}
        if tool_name == "render_overview_topology_svg":
            return {"ok": False, "error": "overview_map_not_found", "map_id": ""}
        return {"ok": True}


class _Router(IntentRouter):
    def __init__(self, responses):
        self.repository = _Repository()
        self.max_steps = 8
        self.responses = list(responses)
        self.requests = []

    async def _llm_generate(self, **kwargs):
        self.requests.append(kwargs)
        if not self.responses:
            return _Response("fallback")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_map_missing_data_does_not_hide_prior_scene_tool_result_when_final_response_available():
    router = _Router(
        [
            _Response(
                tool_calls=[
                    {"name": "update_scene", "args": {"patch": {"clues": [{"id": "clue_route_map"}]}}},
                    {"name": "render_overview_topology_svg", "args": {"title": "午夜车厢 · 车厢布局"}},
                ]
            ),
            _Response(
                tool_calls=[
                    {
                        "name": "final_response",
                        "args": {
                            "reply": "你找到了两张路线图；不过当前缺少结构化地图数据，暂时不能生成可视化地图。"
                        },
                    }
                ]
            ),
        ]
    )

    result = await router._run_llm_tool_loop(
        chat_provider_id="test",
        system_prompt="system",
        initial_prompt="initial",
        toolset=_ToolSet(
            [
                {"name": "update_scene"},
                {"name": "render_overview_topology_svg"},
                {"name": "final_response"},
            ]
        ),
        tool_executor=_Executor(),
        session_id="default:GroupMessage:test",
        raw_player_message="在车厢里寻找路线图",
        available_tool_names=["update_scene", "render_overview_topology_svg", "final_response"],
        actor={"player_id": "u1"},
    )

    assert "找到了两张路线图" in result.completion_text
    assert "结构化地图数据" in result.completion_text
    assert result.completion_text != "现在还不能生成可靠的可视化地图：当前缺少可渲染的结构化地图数据。请先建立地图、放置关键实体或补齐区域拓扑后再请求生成地图。"
