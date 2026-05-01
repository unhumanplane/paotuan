import json

import pytest

from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
from astrbot_plugin_auto_trpg_dm.core.models import (
    AuditBuffer,
    CycleAction,
    CycleState,
    GameSession,
    RACycleInput,
)


class TestCycleStateTransitions:
    def test_active_to_resolving(self):
        session = GameSession.new("test")
        assert session.cycle_state == CycleState.CYCLE_ACTIVE

        CycleStateMachine.transition(session, CycleState.CYCLE_RESOLVING)

        assert session.cycle_state == CycleState.CYCLE_RESOLVING

    def test_resolving_to_transition(self):
        session = GameSession.new("test")
        session.cycle_state = CycleState.CYCLE_RESOLVING

        CycleStateMachine.transition(session, CycleState.CYCLE_TRANSITION)

        assert session.cycle_state == CycleState.CYCLE_TRANSITION

    def test_transition_to_active(self):
        session = GameSession.new("test")
        session.cycle_state = CycleState.CYCLE_TRANSITION

        CycleStateMachine.transition(session, CycleState.CYCLE_ACTIVE)

        assert session.cycle_state == CycleState.CYCLE_ACTIVE

    def test_invalid_transition_raises(self):
        session = GameSession.new("test")

        with pytest.raises(ValueError):
            CycleStateMachine.transition(session, CycleState.CYCLE_TRANSITION)

    def test_start_new_cycle_increments_id_and_resets_buffers(self):
        session = GameSession.new("test")
        session.current_cycle_id = 2
        session.audit_buffer.actions.append(
            CycleAction(player_id="p1", character_id="c1", player_message="hi", dm_narrative="ok", tools_called=[])
        )

        CycleStateMachine.start_new_cycle(session)

        assert session.current_cycle_id == 3
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.audit_buffer.cycle_id == 3
        assert session.audit_buffer.actions == []
        assert session.ra_cycle_input.cycle_id == 3
        assert session.ra_cycle_input.actions == []


class TestAuditBufferAppend:
    def test_append_action_adds_to_audit_buffer(self):
        session = GameSession.new("test")
        session.current_cycle_id = 1
        session.audit_buffer.cycle_id = 1

        CycleStateMachine.append_action(
            session,
            player_id="john",
            character_id="pc_john",
            player_message="attack orc",
            dm_narrative="John charges...",
            tools_called=[{"name": "execute_rule", "result": {"hp": 4}}],
        )

        assert len(session.audit_buffer.actions) == 1
        action = session.audit_buffer.actions[0]
        assert action.player_id == "john"
        assert action.player_message == "attack orc"
        assert action.tools_called[0]["name"] == "execute_rule"

    def test_append_action_generates_ra_projection_without_player_message(self):
        session = GameSession.new("test")
        session.current_cycle_id = 1
        session.audit_buffer.cycle_id = 1

        CycleStateMachine.append_action(
            session,
            player_id="john",
            character_id="pc_john",
            player_message="attack orc",
            dm_narrative="John charges...",
            tools_called=[{"name": "execute_rule", "result": {"hp": 4}}],
        )

        ra = session.ra_cycle_input
        assert len(ra.actions) == 1
        ra_action = ra.actions[0]
        assert "player_message" not in ra_action
        assert ra_action["dm_narrative"] == "John charges..."
        assert ra_action["tools_called"][0]["name"] == "execute_rule"

    def test_append_action_redacts_tool_args(self):
        session = GameSession.new("test")
        session.current_cycle_id = 1
        session.audit_buffer.cycle_id = 1

        CycleStateMachine.append_action(
            session,
            player_id="john",
            character_id="pc_john",
            player_message="attack orc",
            dm_narrative="John charges...",
            tools_called=[
                {
                    "name": "execute_rule",
                    "args": {"token": "secret", "player_id": "real_id"},
                    "result": {"hp": 4, "alive": True},
                }
            ],
        )

        ra_tool = session.ra_cycle_input.actions[0]["tools_called"][0]
        assert "args" not in ra_tool
        assert ra_tool["result"]["hp"] == 4
        assert ra_tool["result"]["alive"] is True

    def test_multiple_actions_accumulate(self):
        session = GameSession.new("test")
        session.current_cycle_id = 1

        for i in range(3):
            CycleStateMachine.append_action(
                session,
                player_id=f"p{i}",
                character_id=f"c{i}",
                player_message=f"msg{i}",
                dm_narrative=f"narrative{i}",
                tools_called=[],
            )

        assert len(session.audit_buffer.actions) == 3
        assert len(session.ra_cycle_input.actions) == 3


class TestOldSaveCompatibility:
    def test_game_session_from_dict_without_cycle_fields(self):
        """Old saves without cycle fields must load with safe defaults."""
        old_save = {
            "session_id": "legacy",
            "mode": "narrative",
            "title": "Old Game",
            "characters": {},
            "battle": {"active": False},
        }

        session = GameSession.from_dict(old_save)

        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.audit_buffer.cycle_id == 0
        assert session.audit_buffer.actions == []
        assert session.ra_cycle_input.cycle_id == 0
        assert session.ra_cycle_input.actions == []
        assert session.current_cycle_id == 0
        assert session.environment_summaries == []
        assert session.rule_sets == {}

    def test_game_session_round_trip(self):
        session = GameSession.new("round_trip")
        session.current_cycle_id = 5
        session.cycle_state = CycleState.CYCLE_RESOLVING
        session.audit_buffer.cycle_id = 5
        CycleStateMachine.append_action(
            session,
            player_id="alice",
            character_id="pc_alice",
            player_message="heal bob",
            dm_narrative="Alice casts heal...",
            tools_called=[{"name": "execute_rule", "result": {"healing": 8}}],
        )
        session.environment_summaries.append({"cycle_id": 4, "summary": "prev"})
        session.rule_sets = {"combat": "d20"}

        data = session.to_dict()
        json_str = json.dumps(data)
        restored = GameSession.from_dict(json.loads(json_str))

        assert restored.cycle_state == CycleState.CYCLE_RESOLVING
        assert restored.current_cycle_id == 5
        assert len(restored.audit_buffer.actions) == 1
        assert restored.audit_buffer.actions[0].player_message == "heal bob"
        assert restored.ra_cycle_input.cycle_id == 5
        assert len(restored.ra_cycle_input.actions) == 1
        assert "player_message" not in restored.ra_cycle_input.actions[0]
        assert restored.environment_summaries == [{"cycle_id": 4, "summary": "prev"}]
        assert restored.rule_sets == {"combat": "d20"}
