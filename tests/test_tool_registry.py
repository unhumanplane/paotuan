import sys
import tempfile
import types
from pathlib import Path


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

from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.rules.python_runtime import PythonRuleRuntime
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.registry import ToolRegistry


def _registry_with_ready_session():
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    repo = JsonGameRepository(root / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)
    registry = ToolRegistry(repo, PythonRuleRuntime(root / "rules"))
    return tmp, registry


def test_tool_registry_prunes_estimate_token_usage_for_ordinary_requests():
    tmp, registry = _registry_with_ready_session()
    try:
        _toolset, names, _executor, _specs = registry.for_mode(
            GameMode.TACTICAL,
            "group",
            message="我攻击最近的敌人",
        )
    finally:
        tmp.cleanup()

    assert "estimate_token_usage" not in names
    assert "session_control" in names
    assert "cycle_control" in names


def test_tool_registry_keeps_estimate_token_usage_for_diagnostic_requests():
    tmp, registry = _registry_with_ready_session()
    try:
        _toolset, names, _executor, _specs = registry.for_mode(
            GameMode.TACTICAL,
            "group",
            message="分析当前 token 消耗",
        )
    finally:
        tmp.cleanup()

    assert "estimate_token_usage" in names
    assert "session_control" in names
    assert "cycle_control" in names

