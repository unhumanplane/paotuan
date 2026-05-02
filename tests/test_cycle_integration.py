import asyncio
import json
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock astrbot modules before importing anything that transitively needs them
# ---------------------------------------------------------------------------

_astrbot = ModuleType("astrbot")
_astrbot.core = ModuleType("astrbot.core")
_astrbot.core.agent = ModuleType("astrbot.core.agent")
_astrbot.core.agent.run_context = ModuleType("astrbot.core.agent.run_context")
_astrbot.core.agent.tool = ModuleType("astrbot.core.agent.tool")
_astrbot.core.astr_agent_context = ModuleType("astrbot.core.astr_agent_context")

import sys as _sys

_sys.modules["astrbot"] = _astrbot
_sys.modules["astrbot.core"] = _astrbot.core
_sys.modules["astrbot.core.agent"] = _astrbot.core.agent
_sys.modules["astrbot.core.agent.run_context"] = _astrbot.core.agent.run_context
_sys.modules["astrbot.core.agent.tool"] = _astrbot.core.agent.tool
_sys.modules["astrbot.core.astr_agent_context"] = _astrbot.core.astr_agent_context


class _MockGeneric:
    def __class_getitem__(cls, item):
        return cls


class _ContextWrapper(_MockGeneric):
    pass


class _FunctionTool(_MockGeneric):
    pass


class _ToolSet(_MockGeneric):
    pass


class _AstrAgentContext:
    pass


_astrbot.core.agent.run_context.ContextWrapper = _ContextWrapper
_astrbot.core.agent.tool.FunctionTool = _FunctionTool
_astrbot.core.agent.tool.ToolSet = _ToolSet
_astrbot.core.astr_agent_context.AstrAgentContext = _AstrAgentContext

_astrbot.api = ModuleType("astrbot.api")
_astrbot.api.logger = MagicMock()
_astrbot.api.event = ModuleType("astrbot.api.event")
_astrbot.api.star = ModuleType("astrbot.api.star")
_sys.modules["astrbot.api"] = _astrbot.api
_sys.modules["astrbot.api.event"] = _astrbot.api.event
_sys.modules["astrbot.api.star"] = _astrbot.api.star

_astrbot.core.message = ModuleType("astrbot.core.message")
_astrbot.core.message.components = ModuleType("astrbot.core.message.components")
_astrbot.core.message.message_event_result = ModuleType("astrbot.core.message.message_event_result")
_astrbot.core.utils = ModuleType("astrbot.core.utils")
_astrbot.core.utils.astrbot_path = ModuleType("astrbot.core.utils.astrbot_path")
_astrbot.core.star = ModuleType("astrbot.core.star")
_astrbot.core.star.filter = ModuleType("astrbot.core.star.filter")
_astrbot.core.star.filter.command = ModuleType("astrbot.core.star.filter.command")
_sys.modules["astrbot.core.message"] = _astrbot.core.message
_sys.modules["astrbot.core.message.components"] = _astrbot.core.message.components
_sys.modules["astrbot.core.message.message_event_result"] = _astrbot.core.message.message_event_result
_sys.modules["astrbot.core.utils"] = _astrbot.core.utils
_sys.modules["astrbot.core.utils.astrbot_path"] = _astrbot.core.utils.astrbot_path
_sys.modules["astrbot.core.star"] = _astrbot.core.star
_sys.modules["astrbot.core.star.filter"] = _astrbot.core.star.filter
_sys.modules["astrbot.core.star.filter.command"] = _astrbot.core.star.filter.command


class _MockContext:
    pass


class _MockStar:
    pass


_astrbot.api.event.AstrMessageEvent = MagicMock
_astrbot.api.event.filter = MagicMock()
_astrbot.api.star.Context = _MockContext
_astrbot.api.star.Star = _MockStar
_astrbot.api.star.register = lambda *a, **k: lambda c: c
_astrbot.core.message.components.Image = MagicMock
_astrbot.core.message.components.Plain = MagicMock
_astrbot.core.message.components.Reply = MagicMock
_astrbot.core.message.message_event_result.MessageChain = MagicMock
_astrbot.core.utils.astrbot_path.get_astrbot_data_path = lambda: "/tmp/astrbot_test"
_astrbot.core.star.filter.command.GreedyStr = MagicMock

# ---------------------------------------------------------------------------
# Now safe to import our modules
# ---------------------------------------------------------------------------

from astrbot_plugin_auto_trpg_dm.core.cycle_buffer import (
    append_cycle_action,
    complete_cycle_without_ra,
    cycle_end_requested,
)
from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
from astrbot_plugin_auto_trpg_dm.core.environment_agent import (
    RecorderAgent,
    complete_cycle_with_ra,
    recover_cycle_after_ra_failure,
    validate_ra_patch_candidates,
)
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.core.router import _looks_like_stateful_player_message
from astrbot_plugin_auto_trpg_dm.main import AutoTrpgDmPlugin


class FakeRepository:
    def __init__(self):
        self.sessions = {}
        self.audit = []

    def load_session(self, session_id):
        return self.sessions.get(session_id, GameSession.new(session_id))

    def save_session(self, session):
        self.sessions[session.session_id] = session

    def append_audit(self, session_id, record):
        self.audit.append({"session_id": session_id, **record})


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


@pytest.fixture
def fake_repo():
    return FakeRepository()


@pytest.fixture
def fake_plugin(fake_repo):
    class Plugin(AutoTrpgDmPlugin):
        def __init__(self, repo):
            self.repository = repo
            self.plugin_logger = FakeLogger()

    return Plugin(fake_repo)


# ---------------------------------------------------------------------------
# _looks_like_stateful_player_message
# ---------------------------------------------------------------------------


class TestLooksLikeStatefulPlayerMessage:
    def test_action_messages_are_stateful(self):
        assert _looks_like_stateful_player_message("我攻击敌人") is True
        assert _looks_like_stateful_player_message("移动到B3") is True
        assert _looks_like_stateful_player_message("我施法") is True

    def test_query_messages_are_not_stateful(self):
        assert _looks_like_stateful_player_message("status") is False
        assert _looks_like_stateful_player_message("token") is False
        assert _looks_like_stateful_player_message("当前轮次") is False
        assert _looks_like_stateful_player_message("规则列表") is False

    def test_empty_message_is_not_stateful(self):
        assert _looks_like_stateful_player_message("") is False
        assert _looks_like_stateful_player_message("   ") is False

    def test_map_request_is_not_stateful(self):
        assert _looks_like_stateful_player_message("画个地图") is False
        assert _looks_like_stateful_player_message("生成地图") is False


# ---------------------------------------------------------------------------
# cycle_end_requested
# ---------------------------------------------------------------------------


class TestCycleEndRequested:
    def test_detects_end_cycle_tool(self):
        tool_trace = [
            {"tool": "cycle_control", "args": {"action": "end_cycle"}, "result": {"ok": True, "action": "end_cycle"}}
        ]
        assert cycle_end_requested(tool_trace) is True

    def test_ignores_other_tools(self):
        tool_trace = [
            {"tool": "execute_rule", "args": {}, "result": {"ok": True, "damage": 5}}
        ]
        assert cycle_end_requested(tool_trace) is False

    def test_ignores_failed_cycle_control(self):
        tool_trace = [
            {"tool": "cycle_control", "args": {"action": "end_cycle"}, "result": {"ok": False, "error": "not_allowed"}}
        ]
        assert cycle_end_requested(tool_trace) is False

    def test_empty_trace(self):
        assert cycle_end_requested([]) is False

    def test_detects_among_mixed_tools(self):
        tool_trace = [
            {"tool": "execute_rule", "args": {}, "result": {"ok": True}},
            {"tool": "cycle_control", "args": {"action": "end_cycle"}, "result": {"ok": True, "action": "end_cycle"}},
        ]
        assert cycle_end_requested(tool_trace) is True


# ---------------------------------------------------------------------------
# End-to-end: buffer append + cycle end + without RA
# ---------------------------------------------------------------------------


class TestEndToEndWithoutRa:
    def test_full_cycle_without_ra(self, fake_repo):
        session = GameSession.new("e2e")
        fake_repo.sessions["e2e"] = session

        # Player action
        record = append_cycle_action(
            session,
            actor={"player_id": "p1"},
            player_message="我攻击兽人",
            completion="你命中了兽人，造成 4 点伤害。",
            tool_results=[
                {"tool": "execute_rule", "args": {"rule_name": "attack"}, "result": {"ok": True, "damage": 4}}
            ],
        )
        assert record["player_id"] == "p1"
        assert len(session.audit_buffer.actions) == 1
        assert len(session.ra_cycle_input.actions) == 1

        # DM signals cycle end via tool trace
        tool_trace = [
            {"tool": "cycle_control", "args": {"action": "end_cycle"}, "result": {"ok": True, "action": "end_cycle"}}
        ]
        assert cycle_end_requested(tool_trace) is True

        # Without RA: cycle completes directly
        session.cycle_state = CycleState.CYCLE_RESOLVING
        complete_cycle_without_ra(session)

        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.current_cycle_id == 1
        assert session.audit_buffer.actions == []
        assert session.ra_cycle_input.actions == []

    def test_preserves_character_binding(self, fake_repo):
        session = GameSession.new("bind_test")
        session.player_character_map["p1"] = "pc_wizard"
        fake_repo.sessions["bind_test"] = session

        record = append_cycle_action(
            session,
            actor={"player_id": "p1"},
            player_message="我施法",
            completion="法术生效。",
            tool_results=[],
        )
        assert record["character_id"] == "pc_wizard"

    def test_cycle_id_preserved_across_actions(self, fake_repo):
        session = GameSession.new("cycle_id_test")
        session.current_cycle_id = 3
        fake_repo.sessions["cycle_id_test"] = session

        append_cycle_action(
            session,
            actor={"player_id": "p1"},
            player_message="我移动",
            completion="你移动了。",
            tool_results=[],
        )
        assert session.audit_buffer.cycle_id == 3
        assert session.ra_cycle_input.cycle_id == 3


# ---------------------------------------------------------------------------
# End-to-end with RA (mocked LLM)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, completion_text):
        self.completion_text = completion_text


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.text)


class TestEndToEndWithRa:
    def test_ra_successful_cycle_resolution(self):
        session = GameSession.new("ra_e2e")
        session.cycle_state = CycleState.CYCLE_RESOLVING
        session.current_cycle_id = 0
        session.characters["pc-1"] = MagicMock()
        session.characters["pc-1"].tags = []
        session.characters["pc-1"].upsert_tags = lambda tags: session.characters["pc-1"].tags.extend(tags)

        append_cycle_action(
            session,
            actor={"player_id": "p1"},
            player_message="我攻击兽人",
            completion="你命中了兽人。",
            tool_results=[
                {"tool": "execute_rule", "args": {}, "result": {"ok": True, "damage": 4}}
            ],
        )

        fake_llm = FakeLlm(
            json.dumps({
                "cycle_id": 0,
                "summary": "击退兽人。",
                "character_status": [],
                "enemy_status": [],
                "world_changes": [],
                "rules_triggered": ["attack"],
                "dm_narrative_aligned": True,
                "discrepancies": [],
            }, ensure_ascii=False)
        )
        ra_result = asyncio.run(
            RecorderAgent(fake_llm, "provider", max_tokens=1024).run_cycle_resolution(session)
        )
        assert ra_result["ok"] is True
        assert ra_result["summary"]["summary"] == "击退兽人。"
        assert len(fake_llm.calls) == 1
        assert fake_llm.calls[0].get("max_tokens") == 1024

        completion = complete_cycle_with_ra(session, ra_result["summary"])
        assert completion["cycle_id"] == 0
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.current_cycle_id == 1
        assert session.environment_summaries[-1]["summary"] == "击退兽人。"

    def test_ra_retries_without_max_tokens_when_provider_rejects_it(self):
        session = GameSession.new("ra_max_tokens_fallback")
        session.cycle_state = CycleState.CYCLE_RESOLVING
        response = json.dumps({
            "cycle_id": 0,
            "summary": "记录完成。",
            "character_status": [],
            "enemy_status": [],
            "world_changes": [],
            "rules_triggered": [],
            "dm_narrative_aligned": True,
            "discrepancies": [],
        }, ensure_ascii=False)

        class RejectsMaxTokens(FakeLlm):
            async def __call__(self, **kwargs):
                self.calls.append(kwargs)
                if "max_tokens" in kwargs:
                    raise TypeError("unexpected keyword argument 'max_tokens'")
                return FakeResponse(self.text)

        fake_llm = RejectsMaxTokens(response)
        ra_result = asyncio.run(
            RecorderAgent(fake_llm, "provider", max_tokens=1024).run_cycle_resolution(session)
        )
        assert ra_result["ok"] is True
        assert len(fake_llm.calls) == 2
        assert fake_llm.calls[0].get("max_tokens") == 1024
        assert "max_tokens" not in fake_llm.calls[1]

    def test_ra_invalid_json_fallback(self):
        session = GameSession.new("ra_fail")
        session.cycle_state = CycleState.CYCLE_RESOLVING

        fake_llm = FakeLlm("这不是 JSON")
        ra_result = asyncio.run(
            RecorderAgent(fake_llm, "provider").run_cycle_resolution(session)
        )
        assert ra_result["ok"] is False
        assert ra_result["error"] == "invalid_ra_json"

        recover_cycle_after_ra_failure(session, ra_result)
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.scene.get("_ra_recovery_log")

    def test_unbacked_patch_rejected_in_e2e(self):
        session = GameSession.new("unbacked")
        append_cycle_action(
            session,
            actor={"player_id": "p1"},
            player_message="我观察",
            completion="你发现脚印。",
            tool_results=[],
        )
        summary = {
            "cycle_id": 0,
            "summary": "无工具支撑。",
            "character_status": [{"character_id": "pc-1", "tags": [{"key": "伤势", "value": "恢复"}]}],
            "enemy_status": [],
            "world_changes": [{"scene_patch": {"summary": "改写场景"}}],
            "rules_triggered": [],
            "dm_narrative_aligned": True,
            "discrepancies": [],
        }
        validation = validate_ra_patch_candidates(session, summary)
        assert validation["accepted"] == []
        assert {item["reason"] for item in validation["rejected"]} == {"missing_tool_backing"}
