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
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.main import (
    AutoTrpgDmPlugin,
    _guided_background_patch_from_text,
    _looks_like_in_campaign_content_expansion_request,
    _looks_like_new_campaign_seed_request,
    _looks_like_backup_preview_request,
    _looks_like_restore_latest_backup_request,
    _looks_like_restart_latest_backup_story_request,
)


class FakeEvent:
    def __init__(self, message_id="msg-1", message_str=""):
        self.message_obj = type("MessageObj", (), {"message_id": message_id})()
        self.message_str = message_str
        self.stopped = False

    def plain_result(self, text):
        return {"kind": "plain", "text": text}

    def chain_result(self, components):
        return {"kind": "chain", "components": components}

    def stop_event(self):
        self.stopped = True


async def _collect_async_generator(generator):
    return [item async for item in generator]


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


def test_duplicate_reply_blocks_same_message_while_in_flight():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin._recent_dm_messages = {}
    plugin._inflight_dm_messages = {}

    assert plugin._duplicate_reply("group", "player", "same action") == ""
    duplicate = plugin._duplicate_reply("group", "player", "same action")

    assert "same action" not in duplicate
    assert duplicate
    assert ("group", "player", "same action") in plugin._inflight_dm_messages


def test_mark_message_finished_clears_inflight_duplicate_guard():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin._recent_dm_messages = {}
    plugin._inflight_dm_messages = {}

    assert plugin._duplicate_reply("group", "player", "same action") == ""
    plugin._mark_message_finished("group", "player", "same action")

    assert ("group", "player", "same action") not in plugin._inflight_dm_messages


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


def test_quoted_result_does_not_expose_local_svg_path_when_preview_fails():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.plugin_logger = FakeLogger()
    plugin._ensure_png_preview = lambda file_path, item: ""
    event = FakeEvent()

    result = plugin._quoted_result(
        event,
        "地图已附上。",
        pending_outputs=[
            {
                "type": "svg_map",
                "name": "gate.svg",
                "path": "C:/runtime/private/maps/gate.svg",
            }
        ],
    )

    text = _component_text(result)
    assert "地图已生成：gate.svg" in text
    assert "C:/runtime/private" not in text
    assert "gate.svg" in text


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


def test_scene_tracking_status_fast_path_returns_visible_hooks_without_advancing():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene.update(
        {
            "_game_started": True,
            "current_objective": "确认旧剧院里失踪者的去向。",
            "clues": [
                {"id": "mud", "text": "门口有新鲜泥脚印。", "status": "discovered", "visibility": "player"},
                {"id": "truth", "text": "幕后黑手就是馆长。", "visibility": "hidden"},
            ],
            "open_hooks": [{"id": "side-door", "text": "侧门锁孔有新鲜刮痕。", "status": "open"}],
            "pressure_clock": {"label": "巡警靠近", "text": "街角手电光正在转向剧院。", "status": "active"},
        }
    )
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "当前目标/线索",
        )
    )

    assert "当前目标：确认旧剧院里失踪者的去向" in reply
    assert "门口有新鲜泥脚印" in reply
    assert "侧门锁孔有新鲜刮痕" in reply
    assert "幕后黑手就是馆长" not in reply
    assert repo.session.cycle_state == CycleState.CYCLE_ACTIVE
    assert repo.audits[-1]["action"] == "scene_tracking_status"


def test_backup_story_commands_are_classified_before_background_fallback():
    assert _looks_like_restart_latest_backup_story_request("重新开上一个存档的故事") is True
    assert _looks_like_restore_latest_backup_request("重新开上一个存档的故事") is False

    assert _looks_like_backup_preview_request("查看上一个存档的故事") is True
    assert _looks_like_restart_latest_backup_story_request("查看上一个存档的故事") is False
    assert _looks_like_backup_preview_request("查看备份") is False


def test_visual_map_requests_without_background_reach_tool_chain():
    for message in ("显示现有地图", "画一下布局吧"):
        session = GameSession.new("group")
        repo = FakeRepository(session)
        plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
        plugin.repository = repo
        plugin.ambient_image_config = AmbientImageConfig(enabled=False)
        plugin.plugin_logger = FakeLogger()
        plugin.honcho_config = types.SimpleNamespace(
            enabled=False,
            read_enabled=False,
            max_context_chars=0,
        )

        reply = asyncio.run(
            plugin._local_fast_path(
                FakeEvent(),
                "group",
                {"player_id": "player-a"},
                message,
            )
        )

        assert reply == ""
        assert repo.session.world_tags["_background_ready"] is True
        assert repo.session.world_tags["background_source"] == "visual_map_request_bootstrap"
        assert any(record.get("action") == "visual_map_background_bootstrap" for record in repo.audits)
        assert not any(record.get("action") == "background_required" for record in repo.audits)


def test_new_campaign_seed_asks_style_preferences_before_background_write():
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "开一个战锤40K底巢清剿团，我是极限战士喷火兵，队里还有一个技术军士。",
        )
    )

    assert "烈度" in reply
    assert "LLM" in reply
    assert "不自动套预设剧本" in reply
    assert "_pending_campaign_preferences" in repo.session.scene
    assert repo.session.scene["_pending_campaign_preferences"]["template_key"] == "llm_generated_campaign"
    assert "_background_ready" not in repo.session.world_tags
    assert any(record.get("action") == "campaign_preference_question" for record in repo.audits)


def test_campaign_preference_answer_writes_llm_generated_background_without_template_match():
    session = GameSession.new("group")
    session.scene["_pending_campaign_preferences"] = {
        "seed": "开一个战锤40K底巢清剿团，我是极限战士喷火兵，队里还有一个技术军士。",
        "template_key": "llm_generated_campaign",
        "template_title": "LLM 原创剧本",
        "question": "先确认烈度。",
        "actor_id": "player-a",
        "asked_at": "2026-05-17T00:00:00+00:00",
    }
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "硬核，战术和恐怖均衡，别太多规则书细节。",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_generation"]["source"] == "llm_generated_campaign"
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "llm_generated_campaign"
    assert "模板骨架" not in repo.session.world_tags["campaign_background"]
    assert "硬核" in repo.session.world_tags["campaign_preferences"]["intensity_and_style"]
    assert "_pending_campaign_preferences" not in repo.session.scene
    assert any(record.get("action") == "campaign_preference_answered" for record in repo.audits)


def test_campaign_preference_answer_writes_template_background_and_continues_to_router():
    session = GameSession.new("group")
    session.scene["_pending_campaign_preferences"] = {
        "seed": "开一个战锤40K底巢清剿团，我是极限战士喷火兵，队里还有一个技术军士。",
        "template_key": "grimdark_underhive_purge",
        "template_title": "哥特科幻底巢清剿",
        "question": "先确认烈度。",
        "actor_id": "player-a",
        "asked_at": "2026-05-17T00:00:00+00:00",
    }
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "硬核，战术和恐怖均衡，别太多规则书细节。",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "grimdark_underhive_purge"
    assert "硬核" in repo.session.world_tags["campaign_preferences"]["intensity_and_style"]
    assert "_pending_campaign_preferences" not in repo.session.scene
    assert any(record.get("action") == "campaign_preference_answered" for record in repo.audits)


def test_structured_custom_campaign_brief_does_not_turn_into_low_magic_preset():
    custom_script = (
        "来一盘新游戏，剧情按照这个来搞:新剧本\n"
        "时代背景：明朝\n"
        "基本概括：老徐是锦衣卫百户，官方身份是三宝船队随员，真实任务是寻访建文余孽。"
        "舰队抵达伊朗沿岸后，当地长老提到十几年前有个自称史东的东方人路过，号称桃源公。\n"
        "玩家组成：明朝船队随员、西方背景雇佣兵、中东背景雇佣兵。\n"
        "友方NPC组成：锦衣卫百户老徐、本地部落猎手、通译、挑夫一队。\n"
        "敌对NPC组成：波斯山贼、桃源教普通信众、桃源教低级教徒、具备低魔超自然能力的高级祭司、史东。\n"
        "模组限定：武器严格遵守时代特征，没有通译时不同语言背景只能简单交流。"
    )
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    question = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            custom_script,
        )
    )

    assert "自定义剧本" in question
    assert "低魔边境冒险" not in question
    assert repo.session.scene["_pending_campaign_preferences"]["template_key"] == "custom_player_brief"
    assert "_background_ready" not in repo.session.world_tags

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "硬核一些吧",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["genre"] == "player_custom_campaign"
    assert repo.session.world_tags["campaign_generation"]["source"] == "player_custom_brief"
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "custom_player_brief"
    assert "三宝船队" in repo.session.world_tags["campaign_background"]
    assert "桃源教" in repo.session.world_tags["campaign_background"]
    assert "一份异常委托把玩家带到边境地点" not in repo.session.world_tags["campaign_background"]
    assert "_pending_campaign_preferences" not in repo.session.scene


def test_preset_list_request_before_background_returns_template_menu():
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "有什么预设剧本",
        )
    )

    assert "开箱即玩" in reply
    assert "《霓虹债务夜奔》" in reply
    assert "跑 2 号" in reply
    assert "_background_ready" not in repo.session.world_tags
    assert any(record.get("action") == "campaign_preset_list" for record in repo.audits)


def test_preset_selection_loads_background_without_extra_form():
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "就跑暖炉酒馆小镇奇案",
        )
    )

    assert "已载入预设剧本《暖炉酒馆小镇奇案》" in reply
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_preset"]["key"] == "cozy_tavern_mystery"
    assert repo.session.world_tags["campaign_generation"]["source"] == "preset_library"
    assert any(record.get("action") == "campaign_preset_loaded" for record in repo.audits)


def test_preset_selection_with_start_request_continues_to_router():
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "跑 2 号开始",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_preset"]["key"] == "grimdark_underhive_purge"
    assert any(record.get("action") == "campaign_preset_loaded" for record in repo.audits)


def test_rusted_chapel_preset_selection_writes_objective_and_pressure():
    session = GameSession.new("group")
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "就跑锈蚀圣堂",
        )
    )

    assert "已载入预设剧本《底巢清剿：锈蚀圣堂》" in reply
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_preset"]["key"] == "underhive_rusted_chapel"
    assert repo.session.world_tags["campaign_preset"]["current_objective"] == "找到失联侦察队的记录核心。"
    assert repo.session.world_tags["campaign_preset"]["current_pressure"] == "底巢通讯将在两小时后被轨道干扰彻底切断。"
    assert "帝国圣歌" in repo.session.world_tags["campaign_generation"]["opening_scene"]
    assert any(record.get("action") == "campaign_preset_loaded" for record in repo.audits)


def test_guided_background_preserves_full_three_act_campaign_seed():
    text = (
        "来一个现代背景的跑团： 第一幕 游艇探险旅行 半路史东房间内身亡  "
        "正在二爷 鹰酱等人寻找死因期间游艇深陷大雾迷航  "
        "导航失灵 传统六分仪定位显示船在南极。\n"
        "第二幕 扎古钓鱼发现神秘语言和遗迹地图 全员水下倒斗 老卡炸开墓室顶部 "
        "找到神秘导航仪 引导游艇前往未知小岛。\n"
        "第三幕 未知小岛探索遗迹，全员激斗邪教徒，发现事件真相，"
        "合力驱散神秘外星生物。小岛沉没，众群友爬上游艇跑路，"
        "一阵大雾之后回到南太平洋，导航恢复正常。"
    )

    patch = _guided_background_patch_from_text(text)

    assert "第一幕 游艇探险旅行" in patch["campaign_background"]
    assert "第二幕 扎古钓鱼发现神秘语言和遗迹地图" in patch["campaign_background"]
    assert "第三幕 未知小岛探索遗迹" in patch["campaign_background"]
    assert "第三幕 未知小岛探索遗迹" in patch["starting_premise"]


def test_new_campaign_detector_ignores_in_campaign_npc_roster_expansion():
    text = "请补充设法，110尺探险游艇通常有15-20名船员，包含船长、大副、水手和服务员、厨师等职业，请补充船上的NPC，符合剧本要求。"

    assert _looks_like_new_campaign_seed_request(text) is True
    assert _looks_like_in_campaign_content_expansion_request(text) is True


def test_in_campaign_npc_roster_expansion_does_not_trigger_reset_fast_path():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene["_game_started"] = True
    session.scene["summary"] = "音速号已经起航前准备中。"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin.honcho_config = types.SimpleNamespace(
        enabled=False,
        read_enabled=False,
        max_context_chars=0,
    )

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "请补充设法，110尺探险游艇通常有15-20名船员，包含船长、大副、水手和服务员、厨师等职业，请补充船上的NPC，符合剧本要求。",
        )
    )

    assert reply == ""
    assert not any(record.get("action") == "new_campaign_requires_reset" for record in repo.audits)


def test_empty_dm_greedystr_sentinel_is_not_routed_to_llm():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)

    class GreedyStrSentinel:
        def __str__(self):
            return "GreedyStr"

    routed_message = plugin._routed_message_from_command_content(GreedyStrSentinel())

    assert routed_message == ""


def test_literal_greedystr_text_is_preserved():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)

    routed_message = plugin._routed_message_from_command_content("GreedyStr")

    assert routed_message == "GreedyStr"


def test_empty_dm_string_greedystr_sentinel_is_not_routed_to_llm():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)

    routed_message = plugin._routed_message_from_command_content(
        "GreedyStr",
        event=FakeEvent(message_str="/dm"),
    )

    assert routed_message == ""


def test_greedystr_command_sentinel_without_raw_command_is_not_routed_to_llm():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)

    routed_message = plugin._routed_message_from_command_content(
        "GreedyStr",
        event=FakeEvent(message_str=""),
    )

    assert routed_message == ""


def test_empty_dm_command_returns_guidance_without_entering_router():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.plugin_logger = FakeLogger()
    plugin.router = types.SimpleNamespace(handle_message=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("router should not run")))

    event = FakeEvent(message_str="/dm")
    results = asyncio.run(_collect_async_generator(plugin._handle_dm_command_content(event, "GreedyStr")))

    assert len(results) == 1
    assert "请输入 `/dm` 后面的具体行动" in _component_text(results[0])
    assert event.stopped is True


def test_explicit_greedystr_argument_is_preserved_from_event_message():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)

    routed_message = plugin._routed_message_from_command_content(
        "GreedyStr",
        event=FakeEvent(message_str="/dm GreedyStr"),
    )

    assert routed_message == "GreedyStr"


def test_dm_command_prefers_full_multiline_event_argument_when_greedystr_is_truncated():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    full = (
        "/dm 来一个现代背景的跑团： 第一幕 游艇探险旅行 半路史东房间内身亡\n"
        "第二幕 扎古钓鱼发现神秘语言和遗迹地图\n"
        "第三幕 未知小岛探索遗迹"
    )

    routed_message = plugin._routed_message_from_command_content(
        "来一个现代背景的跑团：",
        event=FakeEvent(message_str=full),
    )

    assert "第一幕 游艇探险旅行" in routed_message
    assert "第二幕 扎古钓鱼发现神秘语言和遗迹地图" in routed_message
    assert "第三幕 未知小岛探索遗迹" in routed_message


def test_dm_command_recovers_multiline_argument_from_message_obj_string():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    full = (
        "/dm 来一盘新游戏，剧情按照这个来搞:新剧本\n"
        "时代背景：明朝\n"
        "基本概括：老徐是锦衣卫百户，真实任务是寻访建文余孽。\n"
        "玩家组成：明朝船队随员、西方背景雇佣兵、中东背景雇佣兵。\n"
        "友方NPC组成：锦衣卫百户老徐、本地部落猎手、通译。\n"
        "敌对NPC组成：波斯山贼、桃源教低级教徒、史东。\n"
        "模组限定：武器严格遵守时代特征。"
    )
    event = FakeEvent(message_str="/dm 来一盘新游戏，剧情按照这个来搞:新剧本")
    event.message_obj.message_str = full

    routed_message = plugin._routed_message_from_command_content(
        "来一盘新游戏，剧情按照这个来搞:新剧本",
        event=event,
    )

    assert "时代背景：明朝" in routed_message
    assert "玩家组成：明朝船队随员" in routed_message
    assert "敌对NPC组成：波斯山贼" in routed_message
    assert "模组限定：武器严格遵守时代特征" in routed_message


def test_dm_command_recovers_multiline_argument_from_message_chain_plain_text():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    full = (
        "/dm 来一盘新游戏，剧情按照这个来搞:新剧本\n"
        "时代背景：明朝\n"
        "基本概括：老徐是锦衣卫百户，真实任务是寻访建文余孽。\n"
        "玩家组成：明朝船队随员、西方背景雇佣兵、中东背景雇佣兵。\n"
        "友方NPC组成：锦衣卫百户老徐、本地部落猎手、通译。\n"
        "敌对NPC组成：波斯山贼、桃源教低级教徒、史东。\n"
        "模组限定：武器严格遵守时代特征。"
    )
    event = FakeEvent(message_str="/dm 来一盘新游戏，剧情按照这个来搞:新剧本")
    event.message_obj.message = [types.SimpleNamespace(text=full)]

    routed_message = plugin._routed_message_from_command_content(
        "来一盘新游戏，剧情按照这个来搞:新剧本",
        event=event,
    )

    assert "时代背景：明朝" in routed_message
    assert "友方NPC组成：锦衣卫百户老徐" in routed_message
    assert "模组限定：武器严格遵守时代特征" in routed_message


def test_any_message_extracts_multiline_dm_from_message_chain_when_message_str_is_truncated():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.trigger_prefixes = ["/dm"]
    event = FakeEvent(message_str="/dm 来一盘新游戏，剧情按照这个来搞:新剧本")
    event.message_obj.message = [
        {"type": "text", "data": {"text": "/dm 来一盘新游戏，剧情按照这个来搞:新剧本\n"}},
        {"type": "text", "data": {"text": "时代背景：明朝\n"}},
        {"type": "text", "data": {"text": "基本概括：老徐是锦衣卫百户，真实任务是寻访建文余孽。"}},
    ]

    routed_message = plugin._extract_best_routed_message(event, event.message_str)

    assert "时代背景：明朝" in routed_message
    assert "基本概括：老徐是锦衣卫百户" in routed_message


def test_command_and_any_message_share_same_event_route_claim():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.trigger_prefixes = ["/dm"]
    plugin._recent_dm_route_claims = {}
    plugin.plugin_logger = FakeLogger()
    event = FakeEvent(message_id="same-message", message_str="/dm status")

    assert plugin._claim_dm_event_route(event, "command") is True
    assert plugin._claim_dm_event_route(event, "event_message_type") is False


def test_any_message_skips_when_command_handler_already_claimed_event():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.trigger_prefixes = ["/dm"]
    plugin._recent_dm_route_claims = {}
    plugin.plugin_logger = FakeLogger()
    event = FakeEvent(message_id="same-message", message_str="/dm status")

    async def fail_handle(*args, **kwargs):
        raise AssertionError("on_any_message should not handle an already claimed /dm event")
        yield None

    plugin._handle_dm_event = fail_handle

    assert plugin._claim_dm_event_route(event, "command") is True
    results = asyncio.run(_collect_async_generator(plugin.on_any_message(event)))

    assert results == []


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
