import sys
import types
from pathlib import Path
from uuid import uuid4


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
    root = Path(".pytest-runtime") / f"tool-registry-{uuid4().hex}"
    repo = JsonGameRepository(root / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repo.save_session(session)
    registry = ToolRegistry(repo, PythonRuleRuntime(root / "rules"))
    return registry


def _registry_without_background():
    root = Path(".pytest-runtime") / f"tool-registry-nobg-{uuid4().hex}"
    repo = JsonGameRepository(root / "data")
    repo.save_session(GameSession.new("group"))
    registry = ToolRegistry(repo, PythonRuleRuntime(root / "rules"))
    return registry


def test_tool_registry_prunes_estimate_token_usage_for_ordinary_requests():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, _specs = registry.for_mode(
        GameMode.TACTICAL,
        "group",
        message="我攻击最近的敌人",
    )

    assert "estimate_token_usage" not in names
    assert "session_control" in names
    assert "cycle_control" in names


def test_tool_registry_keeps_estimate_token_usage_for_diagnostic_requests():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, _specs = registry.for_mode(
        GameMode.TACTICAL,
        "group",
        message="分析当前 token 消耗",
    )

    assert "estimate_token_usage" in names
    assert "session_control" in names
    assert "cycle_control" in names


def test_tool_registry_exposes_strict_lifecycle_tools_for_map_setup():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.TACTICAL,
        "group",
        message="布置地图，但先不要开战",
    )

    assert "create_strict_map" in names
    assert "start_combat_on_map" in names
    assert "create_grid" in names
    assert "render_strict_grid_svg" in names
    assert "generate_map_svg" in names
    assert any(spec["name"] == "create_strict_map" for spec in specs)
    assert any(spec["name"] == "render_strict_grid_svg" for spec in specs)
    assert any(spec["name"] == "generate_map_svg" for spec in specs)


def test_tool_registry_exposes_overview_topology_renderer_for_overview_map_requests():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="画一张当前区域路线概览地图",
    )

    assert "render_overview_topology_svg" in names
    assert "generate_map_svg" in names
    assert any(spec["name"] == "render_overview_topology_svg" for spec in specs)
    assert any(spec["name"] == "generate_map_svg" for spec in specs)


def test_tool_registry_routes_strict_map_requests_to_strict_renderer():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="画一张当前战场站位图",
    )

    assert "render_strict_grid_svg" in names
    assert "render_overview_topology_svg" not in names
    assert "generate_map_svg" in names
    assert any(spec["name"] == "render_strict_grid_svg" for spec in specs)


def test_tool_registry_routes_layout_request_to_strict_renderer_with_svg_fallback():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="画一下布局吧",
    )

    assert "render_strict_grid_svg" in names
    assert "generate_map_svg" in names
    assert any(spec["name"] == "render_strict_grid_svg" for spec in specs)
    assert any(spec["name"] == "generate_map_svg" for spec in specs)


def test_tool_registry_hides_map_renderers_for_explicit_text_only_map_request():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="先用 ASCII 文字地图画一下战场格子，不要生成图片",
    )

    assert "render_strict_grid_svg" not in names
    assert "render_overview_topology_svg" not in names
    assert "generate_map_svg" not in names
    assert not any(spec["name"] in {"render_strict_grid_svg", "render_overview_topology_svg"} for spec in specs)


def test_tool_registry_keeps_visual_map_fallback_without_background():
    registry = _registry_without_background()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="画一张当前战场站位图",
    )

    assert "render_strict_grid_svg" in names
    assert "generate_map_svg" in names
    assert any(spec["name"] == "render_strict_grid_svg" for spec in specs)
    assert any(spec["name"] == "generate_map_svg" for spec in specs)


def test_tool_registry_keeps_legacy_svg_hidden_until_explicit_fallback_request():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.NARRATIVE,
        "group",
        message="请用 legacy generate_map_svg 做一张 fallback 地图草图",
    )

    assert "generate_map_svg" in names
    assert any(spec["name"] == "generate_map_svg" for spec in specs)


def test_tool_registry_exposes_end_combat_for_battle_resolution():
    registry = _registry_with_ready_session()
    _toolset, names, _executor, specs = registry.for_mode(
        GameMode.TACTICAL,
        "group",
        message="结束战斗，但保留地图",
    )

    assert "end_combat" in names
    assert "turn_control" in names
    assert any(spec["name"] == "end_combat" for spec in specs)

