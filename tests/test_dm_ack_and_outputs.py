import asyncio
import sys
import types


def _install_fake_astrbot_modules():
    if "astrbot.api" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    core = types.ModuleType("astrbot.core")
    core_star = types.ModuleType("astrbot.core.star")
    agent = types.ModuleType("astrbot.core.agent")
    run_context = types.ModuleType("astrbot.core.agent.run_context")
    tool = types.ModuleType("astrbot.core.agent.tool")
    astr_agent_context = types.ModuleType("astrbot.core.astr_agent_context")

    filter_pkg = types.ModuleType("astrbot.core.star.filter")
    command = types.ModuleType("astrbot.core.star.filter.command")
    message = types.ModuleType("astrbot.core.message")
    components = types.ModuleType("astrbot.core.message.components")
    message_event_result = types.ModuleType("astrbot.core.message.message_event_result")
    utils = types.ModuleType("astrbot.core.utils")
    astrbot_path = types.ModuleType("astrbot.core.utils.astrbot_path")


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

    class FakeLogger:
        def info(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def exception(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    class FakeFilter:
        EventMessageType = type("EventMessageType", (), {"ALL": "ALL"})
        @staticmethod
        def command(*args, **kwargs):
            return lambda fn: fn
        @staticmethod
        def event_message_type(*args, **kwargs):
            return lambda fn: fn

    class FakeStar:
        def __init__(self, context=None):
            self.context = context

    def register(*args, **kwargs):
        return lambda cls: cls

    class GreedyStr(str):
        pass

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class Reply:
        def __init__(self, id=None):
            self.id = id

    class Image:
        @staticmethod
        def fromFileSystem(path):
            return type("ImageComponent", (), {"path": path})()

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []


    run_context.ContextWrapper = FakeContextWrapper
    tool.FunctionTool = FakeFunctionTool
    tool.ToolSet = FakeToolSet
    astr_agent_context.AstrAgentContext = FakeAstrAgentContext

    api.logger = FakeLogger()
    event.AstrMessageEvent = object
    event.filter = FakeFilter
    star.Context = object
    star.Star = FakeStar
    star.register = register
    command.GreedyStr = GreedyStr
    components.Image = Image
    components.Plain = Plain
    components.Reply = Reply
    message_event_result.MessageChain = MessageChain
    astrbot_path.get_astrbot_data_path = lambda: "/tmp/astrbot-data"

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.core": core,
        "astrbot.core.star": core_star,

        "astrbot.core.agent": agent,
        "astrbot.core.agent.run_context": run_context,
        "astrbot.core.agent.tool": tool,
        "astrbot.core.astr_agent_context": astr_agent_context,
        "astrbot.core.star.filter": filter_pkg,
        "astrbot.core.star.filter.command": command,
        "astrbot.core.message": message,
        "astrbot.core.message.components": components,
        "astrbot.core.message.message_event_result": message_event_result,
        "astrbot.core.utils": utils,
        "astrbot.core.utils.astrbot_path": astrbot_path,
    }.items():
        sys.modules[name] = module


_install_fake_astrbot_modules()

from astrbot_plugin_auto_trpg_dm.core.ambient_image import AmbientImageConfig
from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.main import AutoTrpgDmPlugin


class FakeEvent:
    def __init__(self, message_id="msg-1"):
        self.message_obj = type("MessageObj", (), {"message_id": message_id})()

    def plain_result(self, text):
        return {"kind": "plain", "text": text}

    def chain_result(self, components):
        return {"kind": "chain", "components": components}


def _component_text(result):
    parts = []
    for item in result["components"]:
        text = getattr(item, "text", "")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def test_dm_ack_is_rate_limited_per_sender():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin._recent_dm_acks = {}

    assert plugin._should_send_dm_ack("group", "player", now=100.0) is True
    assert plugin._should_send_dm_ack("group", "player", now=105.0) is False
    assert plugin._should_send_dm_ack("group", "other", now=105.0) is True
    assert plugin._should_send_dm_ack("group", "player", now=111.0) is True


def test_format_dice_summary_combines_multiple_checks():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    items = [
        {
            "type": "dice_check",
            "ok": True,
            "reason": "潜行",
            "rule_name": "skill_check",
            "version": 2,
            "rolls": [{"expression": "1d20", "total": 14, "rolls": [14]}],
            "rule_result": {"success": True, "total": 17},
        },
        {
            "type": "dice_check",
            "ok": True,
            "reason": "伤害",
            "rule_name": "damage_roll",
            "version": 1,
            "rolls": [{"expression": "2d6", "total": 7, "rolls": [3, 4]}],
            "rule_result": {"total": 7},
        },
    ]

    summary = plugin._format_dice_summary(items)

    assert summary.startswith("本轮检定摘要：")
    assert summary.count("骰子检定：") == 2
    assert "潜行" in summary
    assert "伤害" in summary
    assert "skill_check v2" in summary
    assert "damage_roll v1" in summary


def test_quoted_result_can_prefix_dice_summary_before_completion():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    event = FakeEvent()

    result = plugin._quoted_result(
        event,
        "主叙事结果。",
        dice_summary="本轮检定摘要：\n1. 骰子检定：潜行",
    )

    text = _component_text(result)
    assert text.startswith("本轮检定摘要：")
    assert "\n\n主叙事结果。" in text


def test_manual_ambient_image_fast_path_schedules_independent_generation():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=True)
    plugin.plugin_logger = FakeLogger()
    scheduled = {}

    class Provider:
        def _unavailable(self):
            return None

    def mark_generation_started(target_session):
        state = dict(target_session.scene.get("ambient_image_state") or {})
        state["generation_started_at"] = "now"
        target_session.scene["ambient_image_state"] = state
        repo.save_session(target_session)

    plugin.router = types.SimpleNamespace(
        ambient_image_provider=Provider(),
        _mark_ambient_image_generation_started=mark_generation_started,
    )

    def schedule(event, session_id, actor, message, *, story_moment, rationale):
        scheduled.update(
            {
                "session_id": session_id,
                "actor": actor,
                "message": message,
                "story_moment": story_moment,
                "rationale": rationale,
            }
        )

    plugin._schedule_manual_ambient_image = schedule

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "用独立apikey生图 当前雾夜街道",
        )
    )

    assert "独立图片 API key" in reply
    assert scheduled["session_id"] == "group"
    assert scheduled["story_moment"] == "当前雾夜街道"
    assert repo.session.scene["ambient_image_state"]["generation_started_at"] == "now"
    assert repo.audits[-1]["action"] == "manual_ambient_image_scheduled"


def test_manual_ambient_image_fast_path_reports_missing_independent_key():
    session = GameSession.new("group")
    session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=True)
    plugin.plugin_logger = FakeLogger()

    class Provider:
        def _unavailable(self):
            return {
                "ok": False,
                "available": False,
                "error": "ambient_image_api_key_missing",
                "api_key_env": "PACKYAPI_SORA_API_KEY",
            }

    plugin.router = types.SimpleNamespace(ambient_image_provider=Provider())

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "配图",
        )
    )

    assert "独立生图 API key 没有读取到" in reply
    assert "PACKYAPI_SORA_API_KEY" in reply
    assert repo.audits[-1]["action"] == "manual_ambient_image_blocked"


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakeRepository:
    def __init__(self, session):
        self.session = session
        self.audits = []

    def load_session(self, session_id):
        assert session_id == self.session.session_id
        return self.session

    def save_session(self, session):
        self.session = session

    def append_audit(self, session_id, record):
        assert session_id == self.session.session_id
        self.audits.append(record)
