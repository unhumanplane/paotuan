import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.tools.cycle_tools import CycleTools


class FakeRepository:
    def __init__(self):
        self.session = GameSession.new("group")
        self.audit_records = []
        self.saved = 0

    def load_session(self, session_id):
        assert session_id == "group"
        return self.session

    def save_session(self, session):
        self.session = session
        self.saved += 1

    def append_audit(self, session_id, record):
        assert session_id == "group"
        self.audit_records.append(record)


def test_cycle_control_end_cycle_moves_to_resolving():
    repo = FakeRepository()
    result = asyncio.run(CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control("end_cycle"))

    assert result["ok"] is True
    assert result["action"] == "end_cycle"
    assert repo.session.cycle_state == CycleState.CYCLE_RESOLVING
    assert repo.session.audit_buffer.ended_at
    assert repo.audit_records[-1]["type"] == "cycle_control"


def test_cycle_control_rejects_unknown_action():
    repo = FakeRepository()
    result = asyncio.run(CycleTools(repo, "group").cycle_control("start_cycle"))

    assert result["ok"] is False
    assert result["error"] == "unsupported_cycle_action"
    assert repo.session.cycle_state == CycleState.CYCLE_ACTIVE
