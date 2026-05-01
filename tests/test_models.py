import json
from pathlib import Path

from astrbot_plugin_auto_trpg_dm.core.models import (
    AuditBuffer,
    CycleAction,
    CycleState,
    GameSession,
    RACycleInput,
)


def test_old_save_loads_with_cycle_defaults():
    session = GameSession.from_dict(
        {
            "session_id": "group",
            "mode": "narrative",
            "title": "Legacy",
            "scene": {"summary": "existing scene"},
            "battle": {"active": False},
        }
    )

    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.current_cycle_id == 0
    assert session.audit_buffer.actions == []
    assert session.ra_cycle_input.actions == []
    assert session.environment_summaries == []
    assert session.rule_sets == {}


def test_cycle_fields_round_trip_to_dict():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    session.current_cycle_id = 2
    session.rule_sets["combat"] = {"name": "combat"}
    session.audit_buffer = AuditBuffer(
        cycle_id=2,
        actions=[
            CycleAction(
                player_id="player-1",
                character_id="pc-1",
                player_message="raw player text",
                dm_narrative="DM narration",
                tools_called=[{"name": "execute_rule", "result": {"hp": 4}}],
                timestamp="2026-05-01T00:00:00+00:00",
            )
        ],
        started_at="2026-05-01T00:00:00+00:00",
        ended_at="",
    )
    session.ra_cycle_input = RACycleInput(
        cycle_id=2,
        actions=[
            {
                "dm_narrative": "DM narration",
                "tools_called": [{"name": "execute_rule", "result_sanitized": {"hp": 4}}],
            }
        ],
    )
    session.environment_summaries.append({"cycle_id": 1, "summary": "first cycle"})

    data = session.to_dict()
    loaded = GameSession.from_dict(json.loads(json.dumps(data, ensure_ascii=False)))

    assert data["cycle_state"] == "cycle_resolving"
    assert loaded.cycle_state == CycleState.CYCLE_RESOLVING
    assert loaded.current_cycle_id == 2
    assert loaded.audit_buffer.actions[0].player_message == "raw player text"
    assert loaded.ra_cycle_input.actions[0]["tools_called"][0]["result_sanitized"]["hp"] == 4
    assert loaded.environment_summaries == [{"cycle_id": 1, "summary": "first cycle"}]
    assert loaded.rule_sets["combat"]["name"] == "combat"


def test_invalid_cycle_state_falls_back_to_active():
    session = GameSession.from_dict({"session_id": "group", "cycle_state": "unknown"})

    assert session.cycle_state == CycleState.CYCLE_ACTIVE


def test_config_schema_defines_ra_enabled_default_off():
    schema_path = Path(__file__).parents[1] / "astrbot_plugin_auto_trpg_dm" / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["ra_enabled"]["type"] == "bool"
    assert schema["ra_enabled"]["hint"] == "false"
