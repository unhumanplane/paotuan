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
from astrbot_plugin_auto_trpg_dm.core.models import Character, CycleState, GameSession
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
            "reason": "??",
            "rule_name": "skill_check",
            "version": 2,
            "rolls": [{"expression": "1d20", "total": 14, "rolls": [14]}],
            "rule_result": {"success": True, "total": 17},
        },
        {
            "type": "dice_check",
            "ok": True,
            "reason": "??",
            "rule_name": "damage_roll",
            "version": 1,
            "rolls": [{"expression": "2d6", "total": 7, "rolls": [3, 4]}],
            "rule_result": {"total": 7},
        },
    ]

    summary = plugin._format_dice_summary(items)

    assert summary.startswith("???????")
    assert summary.count("?????") == 2
    assert "??" in summary
    assert "??" in summary
    assert "skill_check v2" in summary
    assert "damage_roll v1" in summary


def test_quoted_result_can_prefix_dice_summary_before_completion():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    event = FakeEvent()

    result = plugin._quoted_result(
        event,
        "??????",
        dice_summary="???????\n1. ???????",
    )

    text = _component_text(result)
    assert text.startswith("???????")
    assert "\n\n??????" in text


def test_quoted_result_does_not_expose_local_svg_path_when_preview_fails():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.plugin_logger = FakeLogger()
    plugin._ensure_png_preview = lambda file_path, item: ""
    event = FakeEvent()

    result = plugin._quoted_result(
        event,
        "??????",
        pending_outputs=[
            {
                "type": "svg_map",
                "name": "gate.svg",
                "path": "C:/runtime/private/maps/gate.svg",
            }
        ],
    )

    text = _component_text(result)
    assert "??????gate.svg" in text
    assert "C:/runtime/private" not in text
    assert "gate.svg" in text


def test_manual_ambient_image_fast_path_schedules_independent_generation():
    session = GameSession.new("group")
    session.scene["summary"] = "?????????????"
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
            "???apikey?? ??????",
        )
    )

    assert "???? API key" in reply
    assert scheduled["session_id"] == "group"
    assert scheduled["story_moment"] == "??????"
    assert repo.session.scene["ambient_image_state"]["generation_started_at"] == "now"
    assert repo.audits[-1]["action"] == "manual_ambient_image_scheduled"


def test_manual_ambient_image_fast_path_reports_missing_independent_key():
    session = GameSession.new("group")
    session.scene["summary"] = "?????????????"
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
            "??",
        )
    )

    assert "???? API key ?????" in reply
    assert "PACKYAPI_SORA_API_KEY" in reply
    assert repo.audits[-1]["action"] == "manual_ambient_image_blocked"


def test_scene_tracking_status_fast_path_returns_visible_hooks_without_advancing():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene.update(
        {
            "_game_started": True,
            "current_objective": "?????????????",
            "clues": [
                {"id": "mud", "text": "?????????", "status": "discovered", "visibility": "player"},
                {"id": "truth", "text": "?????????", "visibility": "hidden"},
            ],
            "open_hooks": [{"id": "side-door", "text": "??????????", "status": "open"}],
            "pressure_clock": {"label": "????", "text": "????????????", "status": "active"},
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
            "????/??",
        )
    )

    assert "?????????????????" in reply
    assert "????????" in reply
    assert "?????????" in reply
    assert "????????" not in reply
    assert repo.session.cycle_state == CycleState.CYCLE_ACTIVE
    assert repo.audits[-1]["action"] == "scene_tracking_status"


def test_timeline_fact_claim_fast_path_returns_authoritative_state():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene.update(
        {
            "_game_started": True,
            "summary": "??????????????????????",
            "current_objective": "????????????????????????",
            "current_conflict": "????????????????????????",
            "open_hooks": [
                {
                    "id": "boot-inscription",
                    "text": "???????????????",
                    "status": "open",
                    "visibility": "player",
                }
            ],
        }
    )
    session.timeline = {
        "day": 1,
        "time_of_day": "morning",
        "label": "? 1 ???",
        "status": "global",
    }
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
            "???????????",
        )
    )

    assert "????" in reply
    assert "? 1 ???" in reply
    assert "?????" in reply
    assert "??????????" in reply
    assert "??????" in reply
    assert repo.audits[-1]["action"] == "authoritative_state_check"


def test_location_fact_query_fast_path_returns_authoritative_state():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene.update(
        {
            "_game_started": True,
            "location": "????????????????????",
            "summary": "???????????????",
            "current_objective": "?????????????????",
            "current_conflict": "???????",
            "open_hooks": [
                {
                    "id": "boot-inscription",
                    "text": "????????????",
                    "status": "open",
                    "visibility": "player",
                }
            ],
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
            "???",
        )
    )

    assert "????" in reply
    assert "?????????" in reply
    assert "?????" in reply
    assert "???????????" in reply
    assert repo.audits[-1]["action"] == "authoritative_state_check"


def test_unbound_post_start_action_is_blocked_before_llm_tools():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_chen_dahu"] = Character(id="pc_chen_dahu", name="???", player_id="bound-player")
    session.player_character_map["bound-player"] = "pc_chen_dahu"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "late-player", "display_name": "??"},
            "??",
        )
    )

    assert "?????????" in reply
    assert "??" in reply
    assert repo.audits[-1]["action"] == "unbound_actor_action"


def test_unbound_post_start_social_action_is_blocked_before_llm_tools():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_chen_dahu"] = Character(id="pc_chen_dahu", name="???", player_id="bound-player")
    session.player_character_map["bound-player"] = "pc_chen_dahu"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "late-player", "display_name": "??"},
            "?????",
        )
    )

    assert "?????????" in reply
    assert repo.audits[-1]["action"] == "unbound_actor_action"


def test_unbound_post_start_camp_chore_action_is_blocked_before_llm_tools():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_chen_dahu"] = Character(id="pc_chen_dahu", name="???", player_id="bound-player")
    session.player_character_map["bound-player"] = "pc_chen_dahu"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "late-player", "display_name": "???"},
            "??????????????????????????",
        )
    )

    assert "?????????" in reply
    assert repo.audits[-1]["action"] == "unbound_actor_action"


def test_unbound_post_start_join_request_still_reaches_character_creation():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_chen_dahu"] = Character(id="pc_chen_dahu", name="???", player_id="bound-player")
    session.player_character_map["bound-player"] = "pc_chen_dahu"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "late-player", "display_name": "?"},
            "???????????????????",
        )
    )

    assert reply == ""
    assert not any(record.get("action") == "unbound_actor_action" for record in repo.audits)


def test_resume_fast_path_accepts_merged_resume_dm_resume_text():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene["_game_started"] = True
    session.scene["_dm_paused"] = True
    session.scene["_dm_pause_reason"] = "???? 2/4??????????????????????? `/dm resume`?"
    session.scene["_dm_paused_by"] = {"player_id": "__heartbeat__", "display_name": "????"}
    session.scene["_dm_paused_at"] = "2026-05-18T02:45:03+00:00"
    repo = FakeRepository(session)
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.ambient_image_config = AmbientImageConfig(enabled=False)
    plugin.plugin_logger = FakeLogger()
    plugin._schedule_pause_resume_ambient_image = lambda *args, **kwargs: None

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "resume/dm resume",
        )
    )

    assert reply == "????????? `/dm` ???????????"
    assert repo.session.scene["_dm_paused"] is False
    assert "_dm_pause_reason" not in repo.session.scene
    assert repo.session.scene["_dm_resume_command"] == "resume/dm resume"
    assert repo.audits[-1]["action"] == "resume"


def test_backup_story_commands_are_classified_before_background_fallback():
    assert _looks_like_restart_latest_backup_story_request("???????????") is True
    assert _looks_like_restore_latest_backup_request("???????????") is False

    assert _looks_like_backup_preview_request("??????????") is True
    assert _looks_like_restart_latest_backup_story_request("??????????") is False
    assert _looks_like_backup_preview_request("????") is False


def test_visual_map_requests_without_background_reach_tool_chain():
    for message in ("??????", "??????"):
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
            "?????40K???????????????????????????",
        )
    )

    assert "??" in reply
    assert "LLM" in reply
    assert "????????" in reply
    assert "_pending_campaign_preferences" in repo.session.scene
    assert repo.session.scene["_pending_campaign_preferences"]["template_key"] == "llm_generated_campaign"
    assert "_background_ready" not in repo.session.world_tags
    assert any(record.get("action") == "campaign_preference_question" for record in repo.audits)


def test_campaign_preference_answer_writes_llm_generated_background_without_template_match():
    session = GameSession.new("group")
    session.scene["_pending_campaign_preferences"] = {
        "seed": "?????40K???????????????????????????",
        "template_key": "llm_generated_campaign",
        "template_title": "LLM ????",
        "question": "??????",
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
            "????????????????????",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_generation"]["source"] == "llm_generated_campaign"
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "llm_generated_campaign"
    assert "????" not in repo.session.world_tags["campaign_background"]
    assert "??" in repo.session.world_tags["campaign_preferences"]["intensity_and_style"]
    assert "_pending_campaign_preferences" not in repo.session.scene
    assert any(record.get("action") == "campaign_preference_answered" for record in repo.audits)


def test_campaign_preference_answer_writes_template_background_and_continues_to_router():
    session = GameSession.new("group")
    session.scene["_pending_campaign_preferences"] = {
        "seed": "?????40K???????????????????????????",
        "template_key": "grimdark_underhive_purge",
        "template_title": "????????",
        "question": "??????",
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
            "????????????????????",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "grimdark_underhive_purge"
    assert "??" in repo.session.world_tags["campaign_preferences"]["intensity_and_style"]
    assert "_pending_campaign_preferences" not in repo.session.scene
    assert any(record.get("action") == "campaign_preference_answered" for record in repo.audits)


def test_structured_custom_campaign_brief_does_not_turn_into_low_magic_preset():
    custom_script = (
        "???????????????:???\n"
        "???????\n"
        "??????????????????????????????????????"
        "???????????????????????????????????????\n"
        "????????????????????????????\n"
        "??NPC??????????????????????????\n"
        "??NPC??????????????????????????????????????????\n"
        "??????????????????????????????????"
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

    assert "?????" in question
    assert "??????" not in question
    assert repo.session.scene["_pending_campaign_preferences"]["template_key"] == "custom_player_brief"
    assert "_background_ready" not in repo.session.world_tags

    reply = asyncio.run(
        plugin._local_fast_path(
            FakeEvent(),
            "group",
            {"player_id": "player-a"},
            "?????",
        )
    )

    assert reply == ""
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["genre"] == "player_custom_campaign"
    assert repo.session.world_tags["campaign_generation"]["source"] == "player_custom_brief"
    assert repo.session.world_tags["campaign_contract"]["template_key"] == "custom_player_brief"
    assert "????" in repo.session.world_tags["campaign_background"]
    assert "???" in repo.session.world_tags["campaign_background"]
    assert "???????????????" not in repo.session.world_tags["campaign_background"]
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
            "???????",
        )
    )

    assert "????" in reply
    assert "????????" in reply
    assert "? 2 ?" in reply
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
            "??????????",
        )
    )

    assert "?????????????????" in reply
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
            "? 2 ???",
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
            "??????",
        )
    )

    assert "??????????????????" in reply
    assert repo.session.world_tags["_background_ready"] is True
    assert repo.session.world_tags["campaign_preset"]["key"] == "underhive_rusted_chapel"
    assert repo.session.world_tags["campaign_preset"]["current_objective"] == "?????????????"
    assert repo.session.world_tags["campaign_preset"]["current_pressure"] == "????????????????????"
    assert "????" in repo.session.world_tags["campaign_generation"]["opening_scene"]
    assert any(record.get("action") == "campaign_preset_loaded" for record in repo.audits)


def test_guided_background_preserves_full_three_act_campaign_seed():
    text = (
        "??????????? ??? ?????? ?????????  "
        "???? ??????????????????  "
        "???? ??????????????\n"
        "??? ??????????????? ?????? ???????? "
        "??????? ???????????\n"
        "??? ????????????????????????"
        "??????????????????????????"
        "????????????????????"
    )

    patch = _guided_background_patch_from_text(text)

    assert "??? ??????" in patch["campaign_background"]
    assert "??? ???????????????" in patch["campaign_background"]
    assert "??? ????????" in patch["campaign_background"]
    assert "??? ????????" in patch["starting_premise"]


def test_new_campaign_detector_ignores_in_campaign_npc_roster_expansion():
    text = "??????110????????15-20???????????????????????????????NPC????????"

    assert _looks_like_new_campaign_seed_request(text) is True
    assert _looks_like_in_campaign_content_expansion_request(text) is True


def test_in_campaign_npc_roster_expansion_does_not_trigger_reset_fast_path():
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene["_game_started"] = True
    session.scene["summary"] = "????????????"
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
            "??????110????????15-20???????????????????????????????NPC????????",
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
    assert "??? `/dm` ???????" in _component_text(results[0])
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
        "/dm ??????????? ??? ?????? ?????????\n"
        "??? ???????????????\n"
        "??? ????????"
    )

    routed_message = plugin._routed_message_from_command_content(
        "???????????",
        event=FakeEvent(message_str=full),
    )

    assert "??? ??????" in routed_message
    assert "??? ???????????????" in routed_message
    assert "??? ????????" in routed_message


def test_dm_command_recovers_multiline_argument_from_message_obj_string():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    full = (
        "/dm ???????????????:???\n"
        "???????\n"
        "??????????????????????????\n"
        "????????????????????????????\n"
        "??NPC?????????????????????\n"
        "??NPC???????????????????\n"
        "????????????????"
    )
    event = FakeEvent(message_str="/dm ???????????????:???")
    event.message_obj.message_str = full

    routed_message = plugin._routed_message_from_command_content(
        "???????????????:???",
        event=event,
    )

    assert "???????" in routed_message
    assert "???????????" in routed_message
    assert "??NPC???????" in routed_message
    assert "???????????????" in routed_message


def test_dm_command_recovers_multiline_argument_from_message_chain_plain_text():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    full = (
        "/dm ???????????????:???\n"
        "???????\n"
        "??????????????????????????\n"
        "????????????????????????????\n"
        "??NPC?????????????????????\n"
        "??NPC???????????????????\n"
        "????????????????"
    )
    event = FakeEvent(message_str="/dm ???????????????:???")
    event.message_obj.message = [types.SimpleNamespace(text=full)]

    routed_message = plugin._routed_message_from_command_content(
        "???????????????:???",
        event=event,
    )

    assert "???????" in routed_message
    assert "??NPC??????????" in routed_message
    assert "???????????????" in routed_message


def test_any_message_extracts_multiline_dm_from_message_chain_when_message_str_is_truncated():
    plugin = AutoTrpgDmPlugin.__new__(AutoTrpgDmPlugin)
    plugin.trigger_prefixes = ["/dm"]
    event = FakeEvent(message_str="/dm ???????????????:???")
    event.message_obj.message = [
        {"type": "text", "data": {"text": "/dm ???????????????:???\n"}},
        {"type": "text", "data": {"text": "???????\n"}},
        {"type": "text", "data": {"text": "??????????????????????????"}},
    ]

    routed_message = plugin._extract_best_routed_message(event, event.message_str)

    assert "???????" in routed_message
    assert "?????????????" in routed_message


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