import json

from astrbot_plugin_auto_trpg_dm.core.cycle_buffer import append_cycle_action, complete_cycle_without_ra
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession


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


def test_complete_cycle_without_ra_does_not_infer_timeline_from_cycle_text():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我守夜到天亮",
        completion="营火熄成灰，队伍来到第二天清晨。",
        tool_results=[],
    )

    result = complete_cycle_without_ra(session)

    assert result["ok"] is True
    assert result["timeline_result"]["timeline_advanced"] is False
    assert session.timeline["day"] == 1


def test_complete_cycle_without_ra_ignores_unsynced_timeline_wording_without_patch():
    session = GameSession.new("group")
    session.participants = {"player-1": {}, "player-2": {}}
    session.player_character_map = {"player-1": "pc-1", "player-2": "pc-2"}
    session.cycle_state = CycleState.CYCLE_RESOLVING
    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="我守夜到天亮",
        completion="你准备把时间推到第二天清晨。",
        tool_results=[],
    )

    result = complete_cycle_without_ra(session)

    assert result["ok"] is True
    assert result["timeline_result"]["timeline_advanced"] is False
    assert session.cycle_state == CycleState.CYCLE_ACTIVE
    assert session.current_cycle_id == 1
    assert session.audit_buffer.actions == []
    assert session.timeline["day"] == 1


def test_complete_cycle_without_ra_does_not_advance_timeline_twice_after_cycle_control():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    session.current_cycle_id = 2
    session.timeline.update(
        {
            "day": 2,
            "time_of_day": "morning",
            "label": "第 2 天清晨",
            "last_advance_reason": "全员休息到第二天清晨",
            "last_advanced_cycle_id": 2,
        }
    )

    result = complete_cycle_without_ra(session)

    assert result["ok"] is True
    assert result["timeline_result"]["timeline_advanced"] is False
    assert result["timeline_result"]["already_advanced"] is True
    assert result["timeline_result"]["timeline"]["day"] == 2
    assert session.current_cycle_id == 3
    assert session.timeline["day"] == 2
    assert session.timeline["last_advanced_cycle_id"] == 2


def test_append_cycle_action_keeps_latest_50_actions_without_leaking_overflow_to_ra_summary():
    session = GameSession.new("group")

    latest_record = {}
    for index in range(55):
        latest_record = append_cycle_action(
            session,
            actor={"player_id": "player-1"},
            player_message=f"raw-player-message-{index}",
            completion=f"DM narrative {index}",
            tool_results=[
                {
                    "tool": "update_scene",
                    "args": {
                        "summary": f"visible scene {index}",
                        "player_message": f"raw-player-message-{index}",
                    },
                    "result": {"ok": True, "summary": f"visible scene {index}"},
                }
            ],
        )

    assert len(session.audit_buffer.actions) == 50
    assert len(session.ra_cycle_input.actions) == 50
    assert session.audit_buffer.actions[0].player_message == "raw-player-message-5"
    assert session.audit_buffer.actions[-1].player_message == "raw-player-message-54"
    rendered_ra_input = json.dumps(session.ra_cycle_input.actions, ensure_ascii=False)
    assert "raw-player-message" not in rendered_ra_input
    assert "DM narrative 5" in rendered_ra_input
    assert "DM narrative 54" in rendered_ra_input
    assert session.environment_summaries == []
    assert latest_record["cycle_action_buffer_overflow"] == {
        "dropped_actions": 1,
        "max_actions": 50,
        "retained_actions": 50,
    }


def test_append_cycle_action_sanitizes_raw_grid_from_ra_tool_input():
    session = GameSession.new("group")
    tool_results = [
        {
            "tool": "get_battle_snapshot",
            "args": {},
            "result": {
                "ok": True,
                "battle": {"active": True, "grid": {"entities": {"hidden": {"x": 9, "y": 9}}}},
                "grid": {"width": 12, "height": 12, "entities": {"hidden": {"x": 9, "y": 9}}},
                "battle_status": {"active": True, "map_id": "strict-local-map"},
            },
        }
    ]

    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="查看战场",
        completion="你快速确认战场态势。",
        tool_results=tool_results,
    )

    ra_tool = session.ra_cycle_input.actions[0]["tools_called"][0]
    rendered = json.dumps(ra_tool, ensure_ascii=False)

    assert ra_tool["name"] == "get_battle_snapshot"
    assert "battle_status" in ra_tool["result_sanitized"]
    assert '"battle"' not in rendered
    assert '"grid"' not in rendered
    assert "hidden" not in rendered
    assert '"x": 9' not in rendered


def test_append_cycle_action_sanitizes_visual_backend_metadata_from_ra_input():
    session = GameSession.new("group")
    tool_results = [
        {
            "tool": "render_overview_topology_svg",
            "args": {
                "title": "North Gate",
                "metadata_path": "D:/runtime/maps/overview.svg.json",
            },
            "result": {
                "ok": True,
                "render_type": "overview_topology_svg",
                "file_path": "D:/runtime/maps/overview.svg",
                "url": "https://example.invalid/overview.svg",
                "svg": "<svg>secret coordinates</svg>",
                "raw_svg": "<svg>raw hidden</svg>",
                "pending_output": {
                    "type": "svg_map",
                    "name": "overview.svg",
                    "path": "D:/runtime/maps/overview.svg",
                    "cadence_key": "internal-cadence-key",
                    "layout_revision": "internal-layout-revision",
                },
                "layout": {
                    "positions": {
                        "hidden-room": {"x": 9, "y": 9, "visibility": "hidden"},
                    }
                },
            },
        }
    ]

    append_cycle_action(
        session,
        actor={"player_id": "player-1"},
        player_message="draw a map",
        completion="Map rendered.",
        tool_results=tool_results,
    )

    rendered = json.dumps(session.ra_cycle_input.actions[0]["tools_called"][0], ensure_ascii=False)

    assert session.ra_cycle_input.actions[0]["tools_called"][0]["name"] == "render_overview_topology_svg"
    assert "D:/runtime" not in rendered
    assert "example.invalid" not in rendered
    assert "<svg" not in rendered
    assert "secret coordinates" not in rendered
    assert "raw hidden" not in rendered
    assert "internal-cadence-key" not in rendered
    assert "internal-layout-revision" not in rendered
    assert "render_type" not in rendered
    assert "visual_only" not in rendered
    assert "hidden-room" not in rendered
    assert '"x": 9' not in rendered
