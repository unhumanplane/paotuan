import pytest

from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession


def test_cycle_state_machine_happy_path():
    session = GameSession.new("group")
    machine = CycleStateMachine()

    assert machine.begin_resolving(session) == CycleState.CYCLE_RESOLVING
    assert machine.begin_transition(session) == CycleState.CYCLE_TRANSITION
    assert machine.activate(session) == CycleState.CYCLE_ACTIVE


def test_cycle_state_machine_allows_ra_failure_skip_to_active():
    session = GameSession.new("group")
    machine = CycleStateMachine()

    machine.begin_resolving(session)

    assert machine.activate(session) == CycleState.CYCLE_ACTIVE


def test_cycle_state_machine_rejects_invalid_transition():
    session = GameSession.new("group")
    machine = CycleStateMachine()

    with pytest.raises(ValueError, match="invalid_cycle_transition"):
        machine.begin_transition(session)
