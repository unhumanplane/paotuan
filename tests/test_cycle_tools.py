import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import CycleAction, CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.tools.cycle_tools import CycleTools


class FakeRepository:
    def __init__(self):
        self.session = GameSession.new("group")
        self.audit_records = []
        self.saved = 0

    def load_session(self, session_id):
        assert session_id == "group"
        self.session.session_id = session_id
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


def test_cycle_control_rejects_timeline_advance_until_all_players_synced():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}
    repo.session.player_character_map = {"p1": "pc-1", "p2": "pc-2"}

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="大家休息到第二天清晨",
            timeline_patch={"day": 2, "time_of_day": "morning"},
        )
    )

    assert result["ok"] is False
    assert result["error"] == "timeline_sync_required"
    assert result["missing_player_ids"] == ["p2"]
    assert repo.session.cycle_state == CycleState.CYCLE_ACTIVE
    assert repo.session.timeline["day"] == 1


def test_cycle_control_advances_global_timeline_when_players_synced():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}
    repo.session.player_character_map = {"p1": "pc-1", "p2": "pc-2"}
    repo.session.audit_buffer.actions.append(CycleAction(player_id="p2"))

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="全员扎营到第二天清晨",
            timeline_patch={"day": 2, "time_of_day": "morning"},
        )
    )

    assert result["ok"] is True
    assert result["timeline_result"]["timeline_advanced"] is True
    assert repo.session.cycle_state == CycleState.CYCLE_RESOLVING
    assert repo.session.timeline["day"] == 2
    assert repo.session.timeline["time_of_day"] == "morning"


def test_cycle_control_rejects_per_player_timeline_patch():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="错误地分别推进时间",
            timeline_patch={"player_times": {"p1": "第二天", "p2": "前一晚"}},
        )
    )

    assert result["ok"] is False
    assert result["error"] == "per_player_timeline_forbidden"
    assert repo.session.timeline["day"] == 1


def test_cycle_control_requires_structured_timeline_patch_for_time_skip():
    repo = FakeRepository()

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="大家休息到第二天清晨",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "timeline_patch_required"
    assert repo.session.cycle_state == CycleState.CYCLE_ACTIVE
    assert repo.session.timeline["day"] == 1
