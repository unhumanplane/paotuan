import asyncio
from datetime import datetime, timedelta, timezone
import sys
import types
from pathlib import Path
from uuid import uuid4


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

from astrbot_plugin_auto_trpg_dm.core.map_delivery_cadence import MAP_RENDER_LEGACY_LLM_SVG
from astrbot_plugin_auto_trpg_dm.core.map_core import (
    DEFAULT_STRICT_LOCAL_MAP_ID,
    MAP_VIEW_DM_NARRATION,
    project_map_store,
    save_active_strict_grid,
)
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.turn_labels import fallback_turn_entity_label
from astrbot_plugin_auto_trpg_dm.main import AutoTrpgDmPlugin, _heartbeat_turn_suspend_reason
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.map_tools import MapTools
from astrbot_plugin_auto_trpg_dm.tools.memory_tools import MemoryTools, _battle_entity_is_terminal_for_rejoin


def _repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


class _FakeMapLlmResponse:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text


class _FakeMapAstrContext:
    def __init__(self):
        self.requests = []

    async def llm_generate(self, **kwargs):
        self.requests.append(kwargs)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900" viewBox="0 0 900 900">'
            '<rect x="0" y="0" width="900" height="900" fill="#f8fafc"/>'
            '<rect x="80" y="120" width="420" height="420" fill="#e2e8f0" stroke="#334155"/>'
            '<circle cx="180" cy="220" r="24" fill="#2563eb"/>'
            '<text x="180" y="224" text-anchor="middle" dominant-baseline="middle">PC</text>'
            "</svg>"
        )
        return _FakeMapLlmResponse(svg)


def _session_with_stale_battle_grid() -> GameSession:
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.world_tags["_background_ready"] = True
    session.characters["pc_owner"] = Character(id="pc_owner", name="MapStore Owner", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="MapStore Next", player_id="next")
    session.characters["ghost"] = Character(id="ghost", name="Stale Ghost", player_id="intruder")
    session.participants["owner"] = {"display_name": "Owner"}
    session.participants["intruder"] = {"display_name": "Intruder"}
    session.player_character_map["owner"] = "pc_owner"
    session.player_character_map["next"] = "pc_next"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "Stale Mirror Owner", "tags": {"player_id": "intruder"}},
                "ghost": {"id": "ghost", "name": "旧镜像幽灵", "tags": {"player_id": "intruder"}},
            },
        },
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner", "pc_next"],
            "current_index": 0,
            "current_entity_id": "pc_owner",
            "actions_this_round": {},
            "timeout_seconds": 120,
            "turn_log": [],
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "MapStore Owner", "tags": {"player_id": "owner"}},
                "pc_next": {"id": "pc_next", "name": "MapStore Next", "tags": {"player_id": "next"}},
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    return session


def test_local_turn_fast_path_reads_map_store_owner_before_stale_battle_grid():
    repo = _repo("local_turn_fast_path_map_store")
    session = _session_with_stale_battle_grid()
    repo.save_session(session)
    plugin = object.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo

    denied = asyncio.run(
        plugin._local_turn_fast_path(
            "group",
            session,
            {"player_id": "intruder", "display_name": "Intruder"},
            "我结束回合",
        )
    )
    allowed = asyncio.run(
        plugin._local_turn_fast_path(
            "group",
            repo.load_session("group"),
            {"player_id": "owner", "display_name": "Owner"},
            "我结束回合",
        )
    )

    assert "当前建议等待 MapStore Owner" in denied
    assert "MapStore Owner本回合结束" in allowed
    saved = repo.load_session("group")
    assert saved.battle["turn"]["actions_this_round"]["pc_owner"]["source"] == "player"


def test_turn_status_and_destination_ignore_stale_battle_grid_labels_and_owners():
    plugin = object.__new__(AutoTrpgDmPlugin)
    session = _session_with_stale_battle_grid()

    status = plugin._format_turn_status(session, include_order=True)
    destination = plugin._format_turn_destination(session)

    assert "MapStore Owner" in status
    assert "MapStore Next" in status
    assert "Owner" in status
    assert "Stale Mirror Owner" not in status
    assert "旧镜像幽灵" not in status
    assert destination == "建议行动：MapStore Owner；本轮未行动者也可直接行动。"


def test_heartbeat_timeout_notice_hides_unmapped_enemy_internal_slug():
    plugin = object.__new__(AutoTrpgDmPlugin)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "ambusher_3",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["ambusher_2", "ambusher_3"],
            "current_index": 1,
            "current_entity_id": "ambusher_3",
            "actions_this_round": {"ambusher_2": {"source": "auto", "summary": "done"}},
        },
    }

    notice = plugin._format_heartbeat_timeout_notice(
        "伏击者 2",
        133,
        session,
        {"auto_paused": False},
        actor_kind="enemy",
    )

    assert "ambusher" not in notice
    assert "轮次推进：伏击者 2的敌方回合等待 133 秒后，已按保守策略推进。" in notice
    assert "建议行动：伏击者 3；本轮未行动者也可直接行动。" in notice
    assert "未响应" not in notice


def test_fallback_turn_label_distinguishes_nested_npc_ids():
    assert fallback_turn_entity_label("npc_b3_1") == "NPC 1"
    assert fallback_turn_entity_label("npc_b3_2") == "NPC 2"
    assert fallback_turn_entity_label("enemy_b3_2") == "敌人 2"


def test_heartbeat_notice_for_fast_npc_advance_does_not_claim_player_timeout():
    plugin = object.__new__(AutoTrpgDmPlugin)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.characters["pc_owner"] = Character(id="pc_owner", name="玩家角色", player_id="owner")
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["npc_b3_1", "pc_owner"],
            "current_index": 1,
            "current_entity_id": "pc_owner",
            "actions_this_round": {"npc_b3_1": {"source": "auto", "summary": "done"}},
        },
    }

    notice = plugin._format_heartbeat_timeout_notice(
        "NPC 1",
        7,
        session,
        {"auto_paused": False},
        actor_kind="npc",
    )

    assert "无需等待玩家超时" in notice
    assert "等待 120 秒" not in notice
    assert "建议行动：玩家角色；本轮未行动者也可直接行动。" in notice


def test_heartbeat_silences_consecutive_non_player_fallback_notices():
    plugin = object.__new__(AutoTrpgDmPlugin)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "npc_b3_2",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["npc_b3_1", "npc_b3_2", "pc_owner"],
            "current_index": 1,
            "current_entity_id": "npc_b3_2",
            "actions_this_round": {"npc_b3_1": {"source": "auto", "summary": "done"}},
        },
    }

    assert plugin._should_send_heartbeat_timeout_notice(
        session,
        {"auto_paused": False},
        actor_kind="npc",
    ) is False

    session.characters["pc_owner"] = Character(id="pc_owner", name="玩家角色", player_id="owner")
    session.battle["turn_entity_id"] = "pc_owner"
    session.battle["turn"]["current_entity_id"] = "pc_owner"
    session.battle["turn"]["current_index"] = 2

    assert plugin._should_send_heartbeat_timeout_notice(
        session,
        {"auto_paused": False},
        actor_kind="npc",
    ) is True


def test_heartbeat_advances_non_player_turn_without_waiting_for_deadline(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.characters["pc_owner"] = Character(id="pc_owner", name="玩家角色", player_id="owner")
    now = datetime.now(timezone.utc)
    session.battle = {
        "active": True,
        "turn_entity_id": "npc_b3_1",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["npc_b3_1", "npc_b3_2", "pc_owner"],
            "current_index": 0,
            "current_entity_id": "npc_b3_1",
            "actions_this_round": {},
            "timeout_seconds": 120,
            "waiting_since_at": now.isoformat(),
            "deadline_at": (now + timedelta(seconds=119)).isoformat(),
            "turn_log": [],
        },
    }
    repo.save_session(session)
    plugin = object.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.plugin_logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()

    result = asyncio.run(plugin._heartbeat_check_session("group"))

    saved = repo.load_session("group")
    assert result["advanced"] is True
    assert result["notice"] == ""
    assert saved.battle["turn"]["current_entity_id"] == "npc_b3_2"
    assert saved.battle["turn"]["actions_this_round"]["npc_b3_1"]["source"] == "auto"


def test_turn_destination_hides_unknown_internal_slug():
    plugin = object.__new__(AutoTrpgDmPlugin)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": True,
        "turn_entity_id": "shadow_assassin_alpha",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["shadow_assassin_alpha"],
            "current_index": 0,
            "current_entity_id": "shadow_assassin_alpha",
            "actions_this_round": {},
        },
    }

    destination = plugin._format_turn_destination(session)

    assert destination == "建议行动：行动者；本轮未行动者也可直接行动。"
    assert "shadow_assassin_alpha" not in destination


def test_heartbeat_suspends_turn_after_enemy_rout_log():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {
        "active": False,
        "turn_entity_id": "pc_yaka",
        "turn": {
            "active": True,
            "round": 3,
            "phase": "character_turn",
            "turn_order": ["ambusher_1", "pc_yaka"],
            "current_index": 1,
            "current_entity_id": "pc_yaka",
            "actions_this_round": {"ambusher_1": {"source": "auto"}},
            "turn_log": [
                {
                    "type": "scene_resolution_start",
                    "summary": "伏击者士气检定溃散，所有残敌朝东北山沟逃窜，战斗结束。",
                    "reason": "enemy_morale_check returned routed",
                }
            ],
        },
    }

    reason = _heartbeat_turn_suspend_reason(
        session,
        session.battle["turn"],
        "character_turn",
    )

    assert reason == "terminal_turn_log"


def test_heartbeat_terminal_suspend_clears_stale_timeout_pause(tmp_path):
    repo = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.scene["_dm_paused"] = True
    session.scene["_dm_pause_reason"] = "本轮已有 2/4名玩家超时，达到半数，流程已自动暂停。恢复时发 `/dm resume`。"
    session.scene["_dm_paused_by"] = {"player_id": "__heartbeat__", "display_name": "本地心跳"}
    session.scene["_dm_paused_at"] = "2026-05-18T02:45:03+00:00"
    session.battle = {
        "active": False,
        "turn_entity_id": "pc_yaka",
        "turn": {
            "active": True,
            "round": 3,
            "phase": "character_turn",
            "turn_order": ["ambusher_1", "pc_yaka"],
            "current_index": 1,
            "current_entity_id": "pc_yaka",
            "actions_this_round": {},
            "turn_log": [
                {
                    "type": "scene_resolution_start",
                    "summary": "伏击者士气检定溃散，所有残敌朝东北山沟逃窜，战斗结束。",
                    "reason": "enemy_morale_check returned routed",
                }
            ],
        },
    }
    repo.save_session(session)
    plugin = object.__new__(AutoTrpgDmPlugin)
    plugin.repository = repo
    plugin.plugin_logger = type("Logger", (), {"info": lambda *args, **kwargs: None})()

    result = plugin._suspend_heartbeat_turn_if_needed(
        "group",
        session,
        session.battle,
        session.battle["turn"],
        "character_turn",
    )

    saved = repo.load_session("group")
    assert result["suspended"] is True
    assert saved.scene["_dm_paused"] is False
    assert "_dm_pause_reason" not in saved.scene
    assert saved.scene["_dm_pause_cleared_by"] == "encounter_end"
    assert saved.battle["turn"]["active"] is False


def test_memory_battle_character_helpers_read_map_store_before_stale_battle_grid():
    session = _session_with_stale_battle_grid()

    assert MemoryTools._battle_has_player_unit(session, "pc_owner") is True
    assert MemoryTools._battle_character_label(session, "pc_owner") == "MapStore Owner"
    assert MemoryTools._battle_has_player_unit(session, "ghost") is False
    assert _battle_entity_is_terminal_for_rejoin(session, "ghost") is False

    map_store_grid = session.maps["records"][DEFAULT_STRICT_LOCAL_MAP_ID]["grid"]
    map_store_grid["entities"]["pc_owner"]["tags"]["dead"] = True
    assert _battle_entity_is_terminal_for_rejoin(session, "pc_owner") is True


def test_legacy_generate_map_svg_writes_render_ref_not_last_map_svg():
    repo = _repo("legacy_svg_render_ref")
    session = GameSession.new("group")
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "MapStore Owner", "tags": {"player_id": "owner"}},
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    repo.save_session(session)

    result = asyncio.run(
        MapTools(repo, "group", astr_context=_FakeMapAstrContext()).generate_map_svg(
            title="Legacy fallback",
            prompt="只做旧版 fallback 视觉草图",
            send_to_chat=True,
        )
    )

    assert result["ok"] is True
    assert result["render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert result["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    assert result["render_ref"] == {
        "type": MAP_RENDER_LEGACY_LLM_SVG,
        "title": "Legacy fallback",
        "name": result["file_name"],
        "visual_only": True,
    }
    saved = repo.load_session("group")
    assert "last_map_svg" not in saved.scene
    pending = saved.scene["_pending_outputs"][0]
    assert pending["render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert pending["preferred_render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert pending["visual_only"] is True
    assert pending["legacy_fallback"] is True
    assert pending["delivery_enqueued"] is True
    assert pending["map_id"] == DEFAULT_STRICT_LOCAL_MAP_ID
    record = saved.maps["records"][DEFAULT_STRICT_LOCAL_MAP_ID]
    assert record["render_refs"][-1]["type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert record["render_refs"][-1]["path"] == result["file_path"]

    projected = project_map_store(saved.maps, MAP_VIEW_DM_NARRATION)
    projected_ref = projected["records"][DEFAULT_STRICT_LOCAL_MAP_ID]["render_refs"][-1]
    assert projected_ref == {
        "type": MAP_RENDER_LEGACY_LLM_SVG,
        "title": "Legacy fallback",
        "name": result["file_name"],
        "visual_only": True,
    }
    assert result["file_path"] not in str(projected)


def test_legacy_generate_map_svg_without_active_map_stays_visual_delivery_only():
    repo = _repo("legacy_svg_without_active_map")
    repo.save_session(GameSession.new("group"))

    result = asyncio.run(
        MapTools(repo, "group", astr_context=_FakeMapAstrContext()).generate_map_svg(
            title="Loose legacy fallback",
            send_to_chat=True,
        )
    )

    assert result["ok"] is True
    assert result["render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert result["map_id"] == ""
    assert result["render_ref"] == {}
    saved = repo.load_session("group")
    assert saved.maps["records"] == {}
    assert "last_map_svg" not in saved.scene
    assert saved.scene["_pending_outputs"][0]["render_type"] == MAP_RENDER_LEGACY_LLM_SVG
    assert saved.scene["_pending_outputs"][0]["visual_only"] is True


def test_legacy_generate_map_svg_prompt_uses_safe_summary_not_raw_grid():
    repo = _repo("legacy_svg_safe_prompt")
    session = GameSession.new("group")
    session.battle = {
        "active": True,
        "grid": {
            "width": 8,
            "height": 8,
            "cells": [{"x": 4, "y": 4, "blocks_los": True}],
            "entities": {
                "hidden-assassin": {
                    "id": "hidden-assassin",
                    "name": "Hidden Assassin",
                    "x": 7,
                    "y": 7,
                    "tags": {"player_id": "secret-owner"},
                }
            },
        },
        "turn": {
            "active": True,
            "round": 2,
            "phase": "character_turn",
            "turn_order": ["hidden-assassin"],
            "actions_this_round": {},
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 8,
            "height": 8,
            "cells": [],
            "entities": {
                "mapstore-pc": {"id": "mapstore-pc", "name": "MapStore PC", "x": 1, "y": 1},
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    repo.save_session(session)
    context = _FakeMapAstrContext()

    result = asyncio.run(
        MapTools(repo, "group", astr_context=context).generate_map_svg(
            title="Safe legacy prompt",
            prompt="显式 legacy fallback 草图",
            send_to_chat=False,
        )
    )

    prompt = context.requests[0]["prompt"]
    assert result["ok"] is True
    assert "当前战棋安全摘要" in prompt
    assert '"grid_width":8' in prompt
    assert "Hidden Assassin" not in prompt
    assert "hidden-assassin" not in prompt
    assert "secret-owner" not in prompt
    assert '"x":7' not in prompt
    assert '"y":7' not in prompt
    assert "MapStore PC" not in prompt
    assert "mapstore-pc" not in prompt
