import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import Character, CycleAction, CycleState, GameSession, TagValue
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


def test_cycle_control_strict_sync_ignores_retired_bound_character():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}
    repo.session.player_character_map = {"p1": "pc-1", "p2": "pc-retired"}
    repo.session.characters["pc-retired"] = Character(id="pc-retired", name="退场角色", player_id="p2")
    repo.session.characters["pc-retired"].tags.append(TagValue(key="退场状态", value="永久退场", layer="status"))

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="大家休息到第二天清晨",
            timeline_patch={"day": 2, "time_of_day": "morning"},
        )
    )

    assert result["ok"] is True
    assert result["timeline_result"]["active_player_ids"] == ["p1"]
    assert repo.session.timeline["day"] == 2


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


def test_cycle_control_timeout_policy_advances_with_safe_afk_player_defaulted():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}
    repo.session.player_character_map = {"p1": "pc-1", "p2": "pc-2"}
    repo.session.scene["scene_threads"] = {
        "character:pc-1": {
            "summary": "pc-1 已回房睡下。",
            "participants": ["pc-1"],
            "active_character_id": "pc-1",
            "status": "resolved",
        },
        "character:pc-2": {
            "summary": "pc-2 在酒馆安全待命，长时间无回应。",
            "participants": ["pc-2"],
            "active_character_id": "pc-2",
            "status": "open",
            "afk_default_safe": True,
        },
    }

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="大家休息到第二天清晨",
            timeline_patch={"day": 2, "time_of_day": "morning"},
            sync_policy="timeout",
        )
    )

    assert result["ok"] is True
    assert "error" not in result["timeline_result"]
    assert result["timeline_result"]["timeline_advanced"] is True
    assert result["timeline_result"]["afk_defaulted_player_ids"] == ["p2"]
    assert repo.session.timeline["day"] == 2
    assert repo.audit_records[-1]["timeline_result"]["afk_defaulted_player_ids"] == ["p2"]


def test_cycle_control_timeout_policy_rejects_unsafe_afk_player():
    repo = FakeRepository()
    repo.session.participants = {"p1": {}, "p2": {}}
    repo.session.player_character_map = {"p1": "pc-1", "p2": "pc-2"}
    repo.session.scene["scene_threads"] = {
        "character:pc-2": {
            "summary": "pc-2 正被敌人逼问，等待关键选择。",
            "participants": ["pc-2"],
            "active_character_id": "pc-2",
            "status": "open",
            "current_conflict": "敌人逼问，等待关键选择",
        },
    }

    result = asyncio.run(
        CycleTools(repo, "group", actor={"player_id": "p1"}).cycle_control(
            "end_cycle",
            reason="大家休息到第二天清晨",
            timeline_patch={"day": 2, "time_of_day": "morning"},
            sync_policy="timeout",
        )
    )

    assert result["ok"] is False
    assert result["error"] == "unsafe_afk_advance"
    assert result["unsafe_player_ids"] == ["p2"]
    assert repo.session.timeline["day"] == 1


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
