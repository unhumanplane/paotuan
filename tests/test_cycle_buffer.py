from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession
from astrbot_plugin_auto_trpg_dm.core.cycle_buffer import append_cycle_action, complete_cycle_without_ra


def test_append_cycle_action_writes_full_audit_and_sanitized_ra_input():
    session = GameSession.new("group")
    session.player_character_map["player-1"] = "pc-1"
    tool_results = [
        {
            "tool": "execute_rule",
            "args": {
                "rule_name": "attack",
                "reason": "raw player text should not go to RA",
                "args": {"target": "orc"},
            },
            "result": {
                "ok": True,
                "damage": 4,
                "debug": "hidden",
                "player_message": "raw player text",
            },
        }
    ]

    record = append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我攻击兽人",
        completion="你击中了兽人，造成 4 点伤害。",
        tool_results=tool_results,
    )

    assert record["tool_names"] == ["execute_rule"]
    assert session.audit_buffer.actions[0].player_message == "我攻击兽人"
    assert session.audit_buffer.actions[0].tools_called[0]["args"]["reason"] == "raw player text should not go to RA"
    ra_tool = session.ra_cycle_input.actions[0]["tools_called"][0]
    assert ra_tool["name"] == "execute_rule"
    assert "reason" not in ra_tool["args_sanitized"]
    assert "debug" not in ra_tool["result_sanitized"]
    assert "player_message" not in ra_tool["result_sanitized"]
    assert ra_tool["result_sanitized"]["damage"] == 4


def test_complete_cycle_without_ra_returns_to_active_and_rotates_buffer():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    session.current_cycle_id = 3
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我观察",
        completion="你发现了脚印。",
        tool_results=[],
    )
    complete_cycle_without_ra(session)

    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.current_cycle_id == 4
    assert session.audit_buffer.cycle_id == 4
    assert session.audit_buffer.actions == []
    assert session.ra_cycle_input.cycle_id == 4
    assert session.ra_cycle_input.actions == []
