import sys
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

sys.modules["astrbot"] = _astrbot
sys.modules["astrbot.core"] = _astrbot.core
sys.modules["astrbot.core.agent"] = _astrbot.core.agent
sys.modules["astrbot.core.agent.run_context"] = _astrbot.core.agent.run_context
sys.modules["astrbot.core.agent.tool"] = _astrbot.core.agent.tool
sys.modules["astrbot.core.astr_agent_context"] = _astrbot.core.astr_agent_context

# Provide placeholder classes for imports in tools/registry.py
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

# Mock astrbot.api for main.py
_astrbot.api = ModuleType("astrbot.api")
_astrbot.api.logger = MagicMock()
_astrbot.api.event = ModuleType("astrbot.api.event")
_astrbot.api.star = ModuleType("astrbot.api.star")
sys.modules["astrbot.api"] = _astrbot.api
sys.modules["astrbot.api.event"] = _astrbot.api.event
sys.modules["astrbot.api.star"] = _astrbot.api.star

# Mock astrbot.core.message components for main.py
_astrbot.core.message = ModuleType("astrbot.core.message")
_astrbot.core.message.components = ModuleType("astrbot.core.message.components")
_astrbot.core.message.message_event_result = ModuleType("astrbot.core.message.message_event_result")
_astrbot.core.utils = ModuleType("astrbot.core.utils")
_astrbot.core.utils.astrbot_path = ModuleType("astrbot.core.utils.astrbot_path")
_astrbot.core.star = ModuleType("astrbot.core.star")
_astrbot.core.star.filter = ModuleType("astrbot.core.star.filter")
_astrbot.core.star.filter.command = ModuleType("astrbot.core.star.filter.command")
sys.modules["astrbot.core.message"] = _astrbot.core.message
sys.modules["astrbot.core.message.components"] = _astrbot.core.message.components
sys.modules["astrbot.core.message.message_event_result"] = _astrbot.core.message.message_event_result
sys.modules["astrbot.core.utils"] = _astrbot.core.utils
sys.modules["astrbot.core.utils.astrbot_path"] = _astrbot.core.utils.astrbot_path
sys.modules["astrbot.core.star"] = _astrbot.core.star
sys.modules["astrbot.core.star.filter"] = _astrbot.core.star.filter
sys.modules["astrbot.core.star.filter.command"] = _astrbot.core.star.filter.command

# Provide placeholders for main.py imports
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

from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.core.router import (
    IntentRouter,
    _looks_like_stateful_player_message,
)
from astrbot_plugin_auto_trpg_dm.main import AutoTrpgDmPlugin


class FakeRepository:
    def __init__(self):
        self.sessions = {}
        self.audit = []

    def load_session(self, session_id):
        return self.sessions.get(session_id, GameSession(session_id=session_id))

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
def router(fake_repo):
    r = IntentRouter(
        astr_context=None,
        repository=fake_repo,
        tool_registry=None,
    )
    return r


@pytest.fixture
def fake_plugin(fake_repo):
    class Plugin(AutoTrpgDmPlugin):
        def __init__(self, repo):
            self.repository = repo
            self.plugin_logger = FakeLogger()

    return Plugin(fake_repo)


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


class TestMaybeAppendCycleBuffer:
    def test_skips_queries(self, router, fake_repo):
        session = GameSession(session_id="test")
        fake_repo.sessions["test"] = session
        router._maybe_append_cycle_buffer(
            session, "test", {"player_id": "p1"}, "status", "OK"
        )
        assert len(session.audit_buffer.actions) == 0

    def test_appends_stateful_actions(self, router, fake_repo):
        session = GameSession(session_id="test")
        fake_repo.sessions["test"] = session
        router._last_tool_trace = [
            {"name": "execute_rule", "args": {}, "result": {"damage": 5}}
        ]
        router._maybe_append_cycle_buffer(
            session, "test", {"player_id": "p1"}, "我攻击哥布林", "你命中了！"
        )
        assert len(session.audit_buffer.actions) == 1
        action = session.audit_buffer.actions[0]
        assert action.player_id == "p1"
        assert action.player_message == "我攻击哥布林"
        assert action.dm_narrative == "你命中了！"
        assert len(action.tools_called) == 1

    def test_appends_with_character_binding(self, router, fake_repo):
        session = GameSession(session_id="test")
        session.player_character_map["p1"] = "pc_wizard"
        fake_repo.sessions["test"] = session
        router._maybe_append_cycle_buffer(
            session, "test", {"player_id": "p1"}, "我施法", "法术生效。"
        )
        action = session.audit_buffer.actions[0]
        assert action.character_id == "pc_wizard"

    def test_preserves_cycle_id(self, router, fake_repo):
        session = GameSession(session_id="test")
        session.current_cycle_id = 3
        session.audit_buffer.cycle_id = 3
        fake_repo.sessions["test"] = session
        router._maybe_append_cycle_buffer(
            session, "test", {"player_id": "p1"}, "我移动", "你移动了。"
        )
        assert session.audit_buffer.cycle_id == 3
        assert session.current_cycle_id == 3

    def test_handles_exception_gracefully(self, router, fake_repo):
        session = GameSession(session_id="test")
        fake_repo.sessions["test"] = session
        # Passing a non-dict for actor should not crash
        router._maybe_append_cycle_buffer(
            session, "test", None, "我攻击", "命中"
        )
        # Should survive due to exception handling


class TestMaybeResolveCycle:
    def test_short_circuits_resolving_state(self, router, fake_repo):
        session = GameSession(session_id="test")
        session.cycle_state = CycleState.CYCLE_RESOLVING
        fake_repo.sessions["test"] = session
        result = router._maybe_resolve_cycle(session, "test")
        assert result is not None
        assert result["short_circuit"] is True
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        audit = [a for a in fake_repo.audit if a.get("type") == "cycle_resolved_short_circuit"]
        assert len(audit) == 1

    def test_noop_when_already_active(self, router, fake_repo):
        session = GameSession(session_id="test")
        session.cycle_state = CycleState.CYCLE_ACTIVE
        fake_repo.sessions["test"] = session
        result = router._maybe_resolve_cycle(session, "test")
        assert result is None
        assert session.cycle_state == CycleState.CYCLE_ACTIVE

    def test_noop_on_transition_state(self, router, fake_repo):
        session = GameSession(session_id="test")
        session.cycle_state = CycleState.CYCLE_TRANSITION
        fake_repo.sessions["test"] = session
        result = router._maybe_resolve_cycle(session, "test")
        assert result is None
        assert session.cycle_state == CycleState.CYCLE_TRANSITION


class TestCycleStateGate:
    def test_short_circuits_resolving(self, fake_plugin, fake_repo):
        session = GameSession(session_id="gate_test")
        session.cycle_state = CycleState.CYCLE_RESOLVING
        fake_repo.sessions["gate_test"] = session
        reply = fake_plugin._cycle_state_gate("gate_test", {"player_id": "p1"}, "我行动")
        assert reply == ""
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        audit = [a for a in fake_repo.audit if a.get("type") == "cycle_state_gate_short_circuit"]
        assert len(audit) == 1
        assert audit[0]["from_state"] == "CYCLE_RESOLVING"

    def test_short_circuits_transition(self, fake_plugin, fake_repo):
        session = GameSession(session_id="gate_test")
        session.cycle_state = CycleState.CYCLE_TRANSITION
        fake_repo.sessions["gate_test"] = session
        reply = fake_plugin._cycle_state_gate("gate_test", {"player_id": "p1"}, "我行动")
        assert reply == ""
        assert session.cycle_state == CycleState.CYCLE_ACTIVE

    def test_passes_through_active(self, fake_plugin, fake_repo):
        session = GameSession(session_id="gate_test")
        session.cycle_state = CycleState.CYCLE_ACTIVE
        fake_repo.sessions["gate_test"] = session
        reply = fake_plugin._cycle_state_gate("gate_test", {"player_id": "p1"}, "我行动")
        assert reply == ""
        assert session.cycle_state == CycleState.CYCLE_ACTIVE

    def test_passes_through_legacy_sessions(self, fake_plugin, fake_repo):
        session = GameSession(session_id="gate_test")
        fake_repo.sessions["gate_test"] = session
        reply = fake_plugin._cycle_state_gate("gate_test", {"player_id": "p1"}, "我行动")
        assert reply == ""


class TestEndToEndCycleFlow:
    def test_full_cycle_from_action_to_resolution(self, router, fake_repo):
        session = GameSession(session_id="e2e")
        session.cycle_state = CycleState.CYCLE_ACTIVE
        fake_repo.sessions["e2e"] = session

        # Player sends a stateful action
        router._last_tool_trace = [{"name": "execute_rule", "args": {}, "result": {"damage": 5}}]
        router._maybe_append_cycle_buffer(
            session, "e2e", {"player_id": "p1"}, "我攻击", "命中，造成5点伤害。"
        )
        assert len(session.audit_buffer.actions) == 1

        # DM signals cycle end (via cycle_control tool, simulated here)
        CycleStateMachine.transition(session, CycleState.CYCLE_RESOLVING)
        assert session.cycle_state == CycleState.CYCLE_RESOLVING

        # Router short-circuits back to ACTIVE (PR 3, no RA yet)
        result = router._maybe_resolve_cycle(session, "e2e")
        assert result is not None
        assert session.cycle_state == CycleState.CYCLE_ACTIVE

        # Next player message hits the gate
        from astrbot_plugin_auto_trpg_dm.main import AutoTrpgDmPlugin

        class Plugin(AutoTrpgDmPlugin):
            def __init__(self, repo):
                self.repository = repo
                self.plugin_logger = FakeLogger()

        plugin = Plugin(fake_repo)
        reply = plugin._cycle_state_gate("e2e", {"player_id": "p2"}, "我治疗")
        assert reply == ""
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
