import json
from pathlib import Path

from astrbot_plugin_auto_trpg_dm.core.models import (
    AuditBuffer,
    Character,
    CycleAction,
    CycleState,
    GameSession,
    RACycleInput,
    TagValue,
    infer_tag_layer,
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
    assert session.timeline["day"] == 1
    assert session.timeline["status"] == "global"


def test_cycle_fields_round_trip_to_dict():
    session = GameSession.new("group")
    session.cycle_state = CycleState.CYCLE_RESOLVING
    session.current_cycle_id = 2
    session.rule_sets["combat"] = {"name": "combat"}
    session.timeline = {"day": 2, "time_of_day": "morning", "label": "第 2 天清晨", "status": "global"}
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
    assert loaded.timeline["day"] == 2
    assert loaded.timeline["time_of_day"] == "morning"


def test_invalid_cycle_state_falls_back_to_active():
    session = GameSession.from_dict({"session_id": "group", "cycle_state": "unknown"})

    assert session.cycle_state == CycleState.CYCLE_ACTIVE


def test_config_schema_defines_ra_enabled_default_off():
    schema_path = Path(__file__).parents[1] / "astrbot_plugin_auto_trpg_dm" / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["ra_enabled"]["type"] == "bool"
    assert schema["ra_enabled"]["hint"] == "false"


def test_config_schema_defines_continuity_auditor_defaults_on():
    schema_path = Path(__file__).parents[1] / "astrbot_plugin_auto_trpg_dm" / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["continuity_auditor_enabled"]["type"] == "bool"
    assert schema["continuity_auditor_enabled"]["default"] is True
    assert schema["continuity_auditor_model_provider"]["default"] == "default"
    assert schema["continuity_auditor_max_tokens"]["default"] == 1200


def test_config_schema_defines_llm_tool_loop_max_steps_default():
    schema_path = Path(__file__).parents[1] / "astrbot_plugin_auto_trpg_dm" / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["llm_tool_loop_max_steps"]["type"] == "int"
    assert schema["llm_tool_loop_max_steps"]["default"] == 16


def test_relationship_tag_layer_inference_and_public_compaction():
    assert infer_tag_layer("attitude_to_watch_captain") == "relations"
    assert infer_tag_layer("阵营关系") == "relations"
    character = Character(
        id="npc_watch",
        name="Watch Captain",
        tags=[
            TagValue.from_dict(
                {
                    "key": "watch_captain",
                    "layer": "relations",
                    "value": {
                        "attitude": "敌对",
                        "trust": "低",
                        "fear": "高",
                        "known_facts": ["玩家救过巡逻兵"],
                        "secret_allegiance": "hidden cult",
                        "hidden_motive": "诱导队伍进埋伏",
                    },
                }
            )
        ],
    )

    compact = character.tags[0].value
    layers = GameSession(session_id="group", characters={"npc_watch": character}).compact_snapshot()["characters"][0]["tag_layers"]

    assert compact["attitude"] == "hostile"
    assert compact["trust"] == "low"
    assert compact["fear"] == "high"
    assert compact["secret_allegiance"] == "hidden cult"
    assert "secret_allegiance" not in str(layers)
    assert "hidden_motive" not in str(layers)
    assert "玩家救过巡逻兵" in str(layers)


def test_compact_tag_layers_keeps_recent_status_facts_when_layer_is_crowded():
    character = Character(
        id="pc_hunter",
        name="Hunter",
        tags=[
            TagValue(key=f"old_status_{index}", value=f"old value {index}", layer="status")
            for index in range(9)
        ]
        + [
            TagValue(key="最新收获", value="猎到山羊一头，皮和角完整，已带回酒馆后院", layer="status"),
            TagValue(key="最新猎获", value="山羊1只，肉质中等，部分损耗", layer="status"),
        ],
    )

    layers = GameSession(session_id="group", characters={"pc_hunter": character}).compact_snapshot()["characters"][0][
        "tag_layers"
    ]
    status_keys = [item["key"] for item in layers["status"]]

    assert status_keys[:2] == ["最新猎获", "最新收获"]
    assert "old_status_0" not in status_keys
    assert "old_status_1" not in status_keys


def test_character_upsert_tags_moves_updated_status_to_recent_end():
    character = Character(
        id="pc_hunter",
        name="Hunter",
        tags=[
            TagValue(key="最新收获", value="旧猎获", layer="status"),
            *[
                TagValue(key=f"old_status_{index}", value=f"old value {index}", layer="status")
                for index in range(8)
            ],
        ],
    )

    character.upsert_tags(
        [
            {
                "key": "最新收获",
                "value": "猎到山羊一头，皮和角完整，已带回酒馆后院",
                "layer": "status",
            }
        ]
    )
    layers = GameSession(session_id="group", characters={"pc_hunter": character}).compact_snapshot()["characters"][0][
        "tag_layers"
    ]
    status = layers["status"]

    assert status[0]["key"] == "最新收获"
    assert status[0]["value"] == "猎到山羊一头，皮和角完整，已带回酒馆后院"
    assert "old_status_0" not in [item["key"] for item in status]


def test_old_save_with_plain_relation_fields_still_loads():
    session = GameSession.from_dict(
        {
            "session_id": "group",
            "characters": {
                "npc_broker": {
                    "id": "npc_broker",
                    "name": "Broker",
                    "tags": [
                        {
                            "key": "broker_relation",
                            "value": "owes the party a favor",
                            "layer": "relations",
                        }
                    ],
                }
            },
            "scene": {"npcs": [{"name": "Broker", "stance": "neutral"}]},
            "world_tags": {"factions": {"guild": {"attitude": "friendly"}}},
        }
    )

    assert session.characters["npc_broker"].tags[0].value == "owes the party a favor"
    assert session.scene["npcs"][0]["stance"] == "neutral"
    assert session.world_tags["factions"]["guild"]["attitude"] == "friendly"
