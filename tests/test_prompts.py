import json

from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.core.map_core import (
    MAP_TYPE_STRICT_LOCAL,
    MAP_VISIBILITY_DM,
    MAP_VISIBILITY_HIDDEN,
    add_map_fact,
    add_render_ref,
    create_map_record,
    save_active_strict_grid,
)
from astrbot_plugin_auto_trpg_dm.core.prompts import (
    BASE_RULES,
    build_cycle_start_prompt,
    build_diagnostic_system_prompt,
    build_ra_system_prompt,
    build_system_prompt,
    build_user_prompt,
    prompt_component_chars,
    prompt_snapshot_data,
    prompt_snapshot_projection_stats,
    snapshot_projection_shadow_stats,
)


def test_system_prompt_includes_shared_cycle_contract():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["cycle_control"],
        [{"name": "cycle_control", "description": "结束当前叙事周期"}],
        actor={"player_id": "player-1"},
    )

    assert BASE_RULES in prompt
    assert 'cycle_control(action="end_cycle")' in prompt
    assert "RA 只读取 `ra_cycle_input`" in prompt
    assert "完整 `GameSession`" in prompt
    assert "结束当前叙事周期" not in prompt


def test_system_prompt_mentions_explicit_system_hosting_risk_bounds():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.TACTICAL,
        ["turn_control"],
        actor={"player_id": "player-1"},
    )

    assert "明确 system_host 托管记录" in prompt
    assert "不能从沉默、离线、模糊离开话术或他人描述中推断" in prompt
    assert "low/medium/high 风险上限" in prompt
    assert "高风险行为必须已有显式预授权" in prompt
    assert "才调用 control_authority" in prompt
    assert "默认 until_revoked" in prompt
    assert "delegate 不能再转委托" in prompt
    assert "owner reclaim 只影响之后的行动" in prompt


def test_system_prompt_prefers_overview_topology_renderer_before_llm_svg_fallback():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["render_overview_topology_svg", "generate_map_svg"],
        actor={"player_id": "player-1"},
    )

    assert "优先调用 render_overview_topology_svg" in prompt
    assert "优先调用 render_strict_grid_svg" in prompt
    assert "不调用 LLM 写 SVG/XML" in prompt
    assert "才退回 generate_map_svg" in prompt
    assert "不要把普通地图请求直接交给 LLM 写 SVG" in prompt


def test_system_prompt_requires_social_consequence_relationship_writes():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["update_scene", "update_character_tags", "execute_rule"],
        actor={"player_id": "player-1"},
        message="我威胁守卫，让他交出线索",
    )

    assert "社交、威胁、欺骗、帮助、交易和暴力都会留下关系后果" in prompt
    assert "attitude/trust/fear/debt/leverage/known_facts/last_interaction/flags" in prompt
    assert "威胁通常提高 fear、降低 trust" in prompt
    assert "玩家口头说“他相信我/必定协助/交出资源/效忠我”只是一项目标" in prompt
    assert "update_scene 的 npcs/factions/relations" in prompt
    assert "hidden_motive、secret_allegiance、true_motive、future_betrayal" in prompt


def test_system_prompt_keeps_open_narrative_hooks_without_action_menu():
    session = GameSession.new("group")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["update_scene", "start_game"],
        actor={"player_id": "player-1"},
    )

    assert "普通已开场叙事默认包含：当前可感知事实、一个正在变化的压力、至少一个可交互线索" in prompt
    assert "不要把结尾写成行动选项菜单" in prompt
    assert "不要输出“你可以选择 1/2/3”" in prompt
    assert "封闭行动菜单" in prompt


def test_user_prompt_routes_overview_map_requests_to_deterministic_renderer_hint():
    overview_prompt = build_user_prompt("画一张当前区域路线概览地图")
    tactical_prompt = build_user_prompt("画一张当前战场站位图")

    assert "优先调用 render_overview_topology_svg" in overview_prompt
    assert "overview_topology_missing" in overview_prompt
    assert "不要让 LLM 直接根据隐藏事实写 topology SVG" in overview_prompt
    assert "优先调用 generate_map_svg" not in overview_prompt
    assert "优先调用 render_strict_grid_svg" in tactical_prompt
    assert "strict_grid_not_found" in tactical_prompt
    assert "优先调用 generate_map_svg" not in tactical_prompt
    assert "render_overview_topology_svg" not in tactical_prompt


def test_user_prompt_investigation_actions_encourage_clue_scene_updates():
    prompt = build_user_prompt("我搜索桌上的信件，再询问门卫有没有见过失踪者")

    assert "用 update_scene 写入 clues/open_hooks/mysteries/current_objective/stakes/pressure_clock" in prompt
    assert "discovered/suspected/resolved/false_lead/blocked" in prompt
    assert "不要把未确认的幕后真相" in prompt


def test_ra_system_prompt_restricts_input_and_output_contract():
    prompt = build_ra_system_prompt()

    assert "只根据 `ra_cycle_input`" in prompt
    assert "清洗后的权威字段快照" in prompt
    assert "完整 `GameSession`" in prompt
    assert "原始玩家输入" in prompt
    assert "prompt" in prompt
    assert "诊断字段" in prompt
    assert "raw audit" in prompt
    assert "合法 JSON" in prompt
    assert "不调用工具" in prompt
    assert "relationship_changes" in prompt
    assert "真正写入必须已有 update_scene/update_character_tags/tool trace 支撑" in prompt


def test_cycle_start_prompt_uses_validated_summary_not_raw_patch_candidates():
    prompt = build_cycle_start_prompt(
        {
            "summary": "队伍击退巡逻队并封住北门。",
            "discrepancies": ["口头叙事伤害高于工具结算。"],
            "patch_candidates": {
                "character_status": [{"id": "pc-1", "hp": 7}],
            },
            "patch_validation": {
                "rejected": [
                    {
                        "category": "world_changes",
                        "reason": "missing_tool_backing",
                        "value": "隐藏伏击点",
                    }
                ],
            },
        }
    )

    assert "已经验证的 RA 摘要" in prompt
    assert "discrepancies" in prompt
    assert "不要把未验证的补丁候选当成事实" in prompt
    assert "队伍击退巡逻队" in prompt
    assert "口头叙事伤害高于工具结算" in prompt
    assert "rejected_count" in prompt
    assert "hp" not in prompt
    assert "隐藏伏击点" not in prompt


def test_system_prompt_only_includes_ra_summary_when_enabled():
    session = GameSession.new("group")
    session.environment_summaries.append({"summary": "上一周期摘要", "discrepancies": []})

    disabled_prompt = build_system_prompt(session, GameMode.NARRATIVE, [], actor={})
    enabled_prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        [],
        actor={},
        include_ra_context=True,
    )

    assert "上一周期摘要" not in disabled_prompt
    assert "上一周期摘要" in enabled_prompt


def test_system_prompt_ra_snapshot_context_is_projected_not_raw_summary_record():
    session = GameSession.new("group")
    session.environment_summaries.append(
        {
            "cycle_id": 1,
            "summary": "上一周期摘要",
            "discrepancies": ["叙事和工具结果冲突"],
            "patch_candidates": {"character_status": [{"id": "pc-1", "hp": 999}]},
            "patch_validation": {
                "rejected": [
                    {
                        "category": "world_changes",
                        "reason": "missing_tool_backing",
                        "value": {"text": "hidden map fact should not enter DM prompt"},
                    }
                ]
            },
            "raw_ra_output": '{"secret": true}',
        }
    )

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        [],
        actor={},
        include_ra_context=True,
    )

    assert "上一周期摘要" in prompt
    assert "叙事和工具结果冲突" in prompt
    assert "rejected_count" in prompt
    assert "raw_ra_output" not in prompt
    assert "patch_candidates" not in prompt
    assert '"hp":999' not in prompt
    assert "hidden map fact should not enter DM prompt" not in prompt


def test_system_prompt_minifies_snapshot_and_avoids_duplicate_memory_summary():
    session = GameSession.new("group")
    session.memory_summary = "长期摘要：队伍已经发现北门暗道。"
    session.scene["summary"] = "队伍在北门外等待。"

    prompt = build_system_prompt(session, GameMode.NARRATIVE, [], actor={"player_id": "player-1"})

    assert prompt.count("长期摘要：队伍已经发现北门暗道。") == 1
    assert '"session_id":"group"' in prompt
    assert '"player_id":"player-1"' in prompt
    assert '"memory_summary"' not in prompt


def test_diagnostic_system_prompt_is_lightweight_and_keeps_safety_boundary():
    session = GameSession.new("group")
    session.memory_summary = "长期摘要不应进入轻量诊断 prompt。"
    session.scene["summary"] = "很长的场景正文不应进入轻量诊断 prompt。"

    prompt = build_diagnostic_system_prompt(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
    )

    assert "AstrBot TRPG DM" in prompt
    assert "estimate_token_usage" in prompt
    assert "session_control" in prompt
    assert "audit" in prompt
    assert "prompt" in prompt
    assert '"session_id":"group"' in prompt
    assert "长期摘要不应进入轻量诊断 prompt" not in prompt
    assert "很长的场景正文不应进入轻量诊断 prompt" not in prompt
    assert BASE_RULES not in prompt


def test_prompt_component_chars_reports_standard_and_diagnostic_profiles():
    session = GameSession.new("group")
    session.memory_summary = "memory summary"
    session.scene["summary"] = "scene summary"

    standard = prompt_component_chars(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
        external_memory_context="external memory",
    )
    diagnostic = prompt_component_chars(
        session,
        GameMode.NARRATIVE,
        ["session_control", "estimate_token_usage"],
        actor={"player_id": "player-1"},
        external_memory_context="external memory",
        profile="diagnostic",
    )

    assert standard["profile"] == "standard"
    assert standard["snapshot_chars"] > 0
    assert standard["memory_summary_chars"] == len("memory summary")
    assert standard["external_memory_chars"] == len("external memory")
    assert standard["base_rules_chars"] == len(BASE_RULES)
    assert diagnostic["profile"] == "diagnostic"
    assert diagnostic["diagnostic_snapshot_chars"] > 0
    assert diagnostic["memory_summary_chars"] == 0
    assert diagnostic["external_memory_chars"] == 0
    assert "snapshot_chars" not in diagnostic


def test_snapshot_projection_shadow_estimates_savings_without_changing_prompt():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.participants["player-1"] = {"display_name": "Player One"}
    session.participants["player-2"] = {"display_name": "Player Two"}
    session.player_character_map["player-1"] = "pc-1"
    session.player_character_map["player-2"] = "pc-2"
    session.active_character_id = "pc-1"
    session.characters["pc-1"] = Character(
        id="pc-1",
        name="Scout",
        player_id="player-1",
        summary="frontline scout",
        tags=[TagValue(key="wounded", value="light wound", layer="status")],
    )
    session.characters["pc-2"] = Character(
        id="pc-2",
        name="Mage",
        player_id="player-2",
        summary="ally mage with a long tactical preference note" * 8,
        tags=[TagValue(key="spellbook", value="utility and control", layer="abilities")],
    )
    session.scene["_recent_narrative_events"] = [
        {
            "at": f"t-{index}",
            "player_id": "player-1",
            "character_id": "pc-1",
            "message": "search the gate" * 20,
            "outcome": "the patrol shifts near the gate" * 20,
        }
        for index in range(8)
    ]
    session.scene["last_map_svg"] = {
        "type": "svg_map",
        "title": "Gate fight",
        "name": "gate.svg",
        "path": "/internal/path/should/not/matter/in/projection",
    }
    session.battle = {
        "active": True,
        "turn": {
            "active": True,
            "round": 3,
            "phase": "character_turn",
            "turn_order": ["pc-1", "pc-2"],
            "current_entity_id": "pc-1",
            "actions_this_round": {},
            "turn_log": [f"log entry {index}: " + ("long detail " * 20) for index in range(8)],
        },
        "grid": {
            "entities": {
                "pc-1": {"id": "pc-1", "name": "Scout", "x": 1, "y": 2},
                "pc-2": {"id": "pc-2", "name": "Mage", "x": 2, "y": 2},
            }
        },
    }

    before_prompt = build_system_prompt(
        session,
        GameMode.TACTICAL,
        ["get_battle_snapshot"],
        actor={"player_id": "player-1"},
    )
    stats = snapshot_projection_shadow_stats(
        session,
        GameMode.TACTICAL,
        "attack the closest enemy",
        actor={"player_id": "player-1"},
    )
    after_prompt = build_system_prompt(
        session,
        GameMode.TACTICAL,
        ["get_battle_snapshot"],
        actor={"player_id": "player-1"},
    )

    assert before_prompt == after_prompt
    assert stats["shadow_only"] is True
    assert stats["profile"] == "tactical_action"
    assert stats["projected_snapshot_chars"] < stats["full_snapshot_chars"]
    assert stats["saved_snapshot_chars"] > 0
    assert "scene" in stats["changed_top_level_keys"]
    assert "characters" in stats["changed_top_level_keys"]
    assert "battle" in stats["changed_top_level_keys"]
    assert "participants" in stats["safety_kept_keys"]
    assert "player_character_map" in stats["safety_kept_keys"]
    assert "battle" in stats["safety_kept_keys"]


def test_prompt_snapshot_projection_applies_without_mutating_session():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.participants["player-1"] = {"display_name": "Player One"}
    session.participants["player-2"] = {"display_name": "Player Two"}
    session.player_character_map["player-1"] = "pc-1"
    session.player_character_map["player-2"] = "pc-2"
    session.active_character_id = "pc-1"
    session.characters["pc-1"] = Character(
        id="pc-1",
        name="Scout",
        player_id="player-1",
        summary="frontline scout",
        tags=[TagValue(key="wounded", value="light wound", layer="status")],
    )
    session.characters["pc-2"] = Character(
        id="pc-2",
        name="Mage",
        player_id="player-2",
        summary="ally mage with a long tactical preference note" * 8,
        tags=[TagValue(key="spellbook", value="utility and control", layer="abilities")],
    )
    session.scene["summary"] = "The gatehouse fight has spilled into the courtyard." * 20
    session.scene["location"] = {"name": "Gatehouse courtyard", "zones": ["gate", "stairs"]}
    session.scene["npcs"] = [{"name": "Watch captain", "stance": "hostile but wounded"}]
    session.scene["clues"] = ["A fresh boot print points toward the cistern."]
    session.scene["last_map_svg"] = {
        "type": "svg_map",
        "title": "Gate fight",
        "name": "gate.svg",
        "path": "/internal/path/should/not/matter/in/projection",
    }
    session.scene["ambient_image_state"] = {"large_internal_counter": "x" * 200}
    session.scene["_map_delivery_cadence"] = {
        "schema_version": 1,
        "sent": {
            "internal": {
                "render_type": "strict_grid_svg",
                "map_id": "strict-local-map",
                "trigger": "combat_round",
            }
        },
    }
    session.scene["_recent_narrative_events"] = [
        {
            "at": f"t-{index}",
            "player_id": "player-1",
            "character_id": "pc-1",
            "message": "search the gate" * 20,
            "outcome": "the patrol shifts near the gate" * 20,
        }
        for index in range(8)
    ]
    session.battle = {
        "active": True,
        "turn": {
            "active": True,
            "round": 3,
            "phase": "character_turn",
            "turn_order": ["pc-1", "pc-2"],
            "current_entity_id": "pc-1",
            "actions_this_round": {},
            "turn_log": [f"log entry {index}: " + ("long detail " * 20) for index in range(8)],
        },
        "grid": {
            "entities": {
                "pc-1": {"id": "pc-1", "name": "Scout", "x": 1, "y": 2},
                "pc-2": {"id": "pc-2", "name": "Mage", "x": 2, "y": 2},
            }
        },
    }
    original_snapshot = session.compact_snapshot()

    full_prompt = build_system_prompt(
        session,
        GameMode.TACTICAL,
        ["get_battle_snapshot"],
        actor={"player_id": "player-1"},
        message="attack the closest enemy",
        snapshot_projection_enabled=False,
    )
    projected_prompt = build_system_prompt(
        session,
        GameMode.TACTICAL,
        ["get_battle_snapshot"],
        actor={"player_id": "player-1"},
        message="attack the closest enemy",
        snapshot_projection_enabled=True,
    )
    projected_snapshot, stats = prompt_snapshot_data(
        session,
        GameMode.TACTICAL,
        "attack the closest enemy",
        actor={"player_id": "player-1"},
        snapshot_projection_enabled=True,
    )

    assert len(projected_prompt) < len(full_prompt)
    assert stats["applied"] is True
    assert stats["shadow_only"] is False
    assert stats["saved_snapshot_chars"] > 0
    assert projected_snapshot["participants"][0]["player_id"] == "player-1"
    assert projected_snapshot["player_character_map"]["player-1"] == "pc-1"
    assert projected_snapshot["battle"]["active"] is True
    assert "grid" not in projected_snapshot["battle"]
    assert projected_snapshot["characters"]["relevant"][0]["id"] == "pc-1"
    assert projected_snapshot["scene"]["location"]["name"] == "Gatehouse courtyard"
    assert projected_snapshot["scene"]["npcs"][0]["name"] == "Watch captain"
    assert "ambient_image_state" not in projected_snapshot["scene"]
    assert "_map_delivery_cadence" not in projected_snapshot["scene"]
    assert projected_snapshot["scene"]["last_map_svg"] == {
        "type": "svg_map",
        "title": "Gate fight",
        "name": "gate.svg",
    }
    assert "/internal/path" not in str(projected_snapshot["scene"])
    assert session.compact_snapshot() == original_snapshot


def test_prompt_snapshot_projection_filters_hidden_scene_clues_and_truths():
    session = GameSession.new("group")
    session.scene["current_objective"] = "确认旧剧院里失踪者的去向。"
    session.scene["clues"] = [
        {"id": "mud", "text": "门口有新鲜泥脚印。", "status": "discovered", "visibility": "player"},
        {"id": "killer", "text": "幕后黑手就是馆长。", "status": "hidden", "visibility": "hidden"},
    ]
    session.scene["mysteries"] = [
        {"id": "missing", "text": "失踪者为什么都去过午夜场？", "status": "open", "visibility": "player"},
        {"id": "secret-room", "text": "地下秘密房间在舞台下。", "visibility": "dm_only"},
    ]
    session.scene["hidden_truth"] = "馆长已经和镜中实体交易。"

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "我观察剧院入口",
        snapshot_projection_enabled=True,
    )
    rendered = json.dumps(projected_snapshot["scene"], ensure_ascii=False)

    assert "门口有新鲜泥脚印" in rendered
    assert "失踪者为什么都去过午夜场" in rendered
    assert "幕后黑手就是馆长" not in rendered
    assert "地下秘密房间" not in rendered
    assert "镜中实体" not in rendered


def test_prompt_snapshot_projection_filters_dm_only_scene_objects():
    session = GameSession.new("group")
    session.scene["current_objective"] = "找到能进入钟楼的公开路径。"
    session.scene["npcs"] = [
        {"id": "porter", "name": "门房", "visibility": "player", "text": "门房握着一串铜钥匙。"},
        {"id": "hidden-scout", "name": "屋顶窥探者", "visibility": "dm", "text": "他知道暗门位置。"},
    ]
    session.scene["locations"] = {
        "square": {"name": "钟楼广场", "visibility": "player"},
        "secret_attic": {"name": "未发现阁楼", "visibility": "dm"},
    }

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "我看看广场上有什么",
        snapshot_projection_enabled=True,
    )
    rendered = json.dumps(projected_snapshot["scene"], ensure_ascii=False)

    assert "门房" in rendered
    assert "钟楼广场" in rendered
    assert "屋顶窥探者" not in rendered
    assert "暗门位置" not in rendered
    assert "未发现阁楼" not in rendered


def test_prompt_snapshot_projects_relationships_without_hidden_motives():
    session = GameSession.new("group")
    session.scene["npcs"] = [
        {
            "id": "npc_captain",
            "name": "Watch Captain",
            "relations": {
                "attitude": "suspicious",
                "trust": "low",
                "fear": "moderate",
                "known_facts": ["玩家威胁过守卫"],
                "last_interaction": "谈判失败后保持警惕。",
                "secret_allegiance": "cult",
                "hidden_motive": "lead party into ambush",
                "future_betrayal": "betrays at midnight",
            },
        }
    ]
    session.world_tags["factions"] = {
        "merchant_guild": {
            "name": "Merchant Guild",
            "attitude": "friendly",
            "debt": "moderate",
            "known_facts": ["队伍归还了货物"],
            "true_motive": "raise prices later",
            "secret_allegiance": "smugglers",
        }
    }
    session.characters["pc_face"] = Character(
        id="pc_face",
        name="Face",
        player_id="player-1",
        tags=[
            TagValue(
                key="broker_relation",
                layer="relations",
                value={
                    "target_id": "npc_broker",
                    "attitude": "friendly",
                    "known_facts": ["玩家支付了报酬"],
                    "hidden_motive": "sell them out",
                },
            )
        ],
    )

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "守卫和商会现在怎么看我？",
        actor={"player_id": "player-1"},
        snapshot_projection_enabled=True,
    )
    rendered = str(projected_snapshot)

    assert "suspicious" in rendered
    assert "friendly" in rendered
    assert "玩家威胁过守卫" in rendered
    assert "队伍归还了货物" in rendered
    assert "玩家支付了报酬" in rendered
    assert "secret_allegiance" not in rendered
    assert "hidden_motive" not in rendered
    assert "future_betrayal" not in rendered
    assert "true_motive" not in rendered
    assert "ambush" not in rendered
    assert "smugglers" not in rendered
    assert "sell them out" not in rendered


def test_prompt_snapshot_projection_can_be_disabled_for_full_snapshot():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.scene["_recent_narrative_events"] = [
        {"message": "long event" * 20, "outcome": "long outcome" * 20}
        for _ in range(6)
    ]
    session.battle = {"active": True}

    full_snapshot, stats = prompt_snapshot_data(
        session,
        GameMode.TACTICAL,
        "attack",
        snapshot_projection_enabled=False,
    )
    projection_stats = prompt_snapshot_projection_stats(
        session,
        GameMode.TACTICAL,
        "attack",
        snapshot_projection_enabled=False,
    )

    assert "memory_summary" not in full_snapshot
    assert "environment_summaries" not in full_snapshot
    assert full_snapshot["scene"]["_recent_narrative_events"] == session.compact_snapshot()["scene"]["_recent_narrative_events"]
    assert full_snapshot["battle"] == session.compact_snapshot()["battle"]
    assert stats["enabled"] is False
    assert stats["applied"] is False
    assert stats["saved_snapshot_chars"] == 0
    assert projection_stats["enabled"] is False
    assert projection_stats["projected_snapshot_chars"] == projection_stats["full_snapshot_chars"]


def test_snapshot_projection_shadow_classifies_state_query_without_actions():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {"active": True}

    stats = snapshot_projection_shadow_stats(
        session,
        GameMode.TACTICAL,
        "当前情况怎么样？我现在的位置和敌人位置？",
    )

    assert stats["profile"] == "state_query"


def test_snapshot_projection_keeps_strict_exploration_as_narrative_profile():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    save_active_strict_grid(
        session.maps,
        {"width": 5, "height": 5, "cells": [], "entities": {}},
        map_id="strict-room",
        title="Strict room",
    )

    stats = snapshot_projection_shadow_stats(
        session,
        GameMode.TACTICAL,
        "我想回忆一下背景",
    )

    assert stats["profile"] == "narrative"


def test_snapshot_projection_shadow_keeps_mixed_query_action_as_tactical():
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.battle = {"active": True}

    stats = snapshot_projection_shadow_stats(
        session,
        GameMode.TACTICAL,
        "看到敌人了吗？我上去攻击最近的敌人",
    )

    assert stats["profile"] == "tactical_action"


def test_prompt_snapshot_projection_uses_safe_dm_map_view():
    session = GameSession.new("group")
    create_map_record(session.maps, "overview-1", title="Gatehouse", visibility=MAP_VISIBILITY_DM, set_active=True)
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="visible-pressure",
        kind="pressure",
        text="The gate hinges are cracked.",
        visibility=MAP_VISIBILITY_DM,
    )
    add_map_fact(
        session.maps,
        "overview-1",
        fact_id="hidden-trigger",
        kind="trap",
        text="The hidden trigger is under the third tile.",
        visibility=MAP_VISIBILITY_HIDDEN,
    )
    add_render_ref(
        session.maps,
        "overview-1",
        ref_type="svg_map",
        title="Gatehouse",
        name="gatehouse.svg",
        path="/local/runtime/maps/gatehouse.svg",
        url="https://example.invalid/gatehouse.svg",
    )

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "look around",
        snapshot_projection_enabled=True,
    )
    full_snapshot, _ = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "look around",
        snapshot_projection_enabled=False,
    )

    assert "maps" not in full_snapshot
    assert projected_snapshot["maps"]["records"]["overview-1"]["facts"][0]["id"] == "visible-pressure"
    rendered = str(projected_snapshot["maps"])
    assert "hidden-trigger" not in rendered
    assert "/local/runtime" not in rendered
    assert "example.invalid" not in rendered


def test_prompt_snapshot_projection_uses_safe_control_authority_view():
    session = GameSession.new("group")
    session.characters["pc-1"] = Character(id="pc-1", name="Scout", player_id="owner")
    session.player_character_map["owner"] = "pc-1"
    session.control_authority = {
        "records": {
            "pc-1": {
                "owner_player_id": "owner",
                "active_controller_id": "delegate",
                "controller_type": "player_delegate",
                "status": "delegated_to_player",
                "authorized_by": "owner",
                "consent_reference": "private handoff text",
                "audit_ref": "audit-private-1",
                "effective_at": "2026-05-10T00:00:00+00:00",
            }
        }
    }

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.NARRATIVE,
        "继续描述当前局势",
        actor={"player_id": "delegate"},
        snapshot_projection_enabled=True,
    )

    record = projected_snapshot["control_authority"]["records"][0]
    rendered = str(projected_snapshot["control_authority"])
    assert record["owner_player_id"] == "owner"
    assert record["active_controller_id"] == "delegate"
    assert record["controller_type"] == "player_delegate"
    assert record["status"] == "delegated_to_player"
    assert "consent_reference" not in record
    assert "audit_ref" not in record
    assert "effective_at" not in record
    assert "private handoff text" not in rendered
    assert "audit-private-1" not in rendered


def test_prompt_snapshot_projection_does_not_expose_raw_strict_grid():
    session = GameSession.new("group")
    session.battle = {
        "active": True,
        "turn_entity_id": "pc-1",
        "grid": {
            "width": 5,
            "height": 5,
            "entities": {
                "secret-stalker": {"id": "secret-stalker", "name": "Stalker", "x": 4, "y": 4},
            },
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 5,
            "height": 5,
            "cells": [],
            "entities": {
                "secret-stalker": {"id": "secret-stalker", "name": "Stalker", "x": 4, "y": 4},
            },
        },
        map_id="strict-room",
        title="Strict room",
    )
    add_map_fact(
        session.maps,
        "strict-room",
        fact_id="visible-pressure",
        kind="pressure",
        text="The room is tight and dangerous.",
        visibility=MAP_VISIBILITY_DM,
    )

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.TACTICAL,
        "where is everyone",
        snapshot_projection_enabled=True,
    )

    record = projected_snapshot["maps"]["records"]["strict-room"]
    rendered = str(record)
    assert record["type"] == MAP_TYPE_STRICT_LOCAL
    assert record["facts"][0]["id"] == "visible-pressure"
    assert "grid" not in record
    assert "grid" not in projected_snapshot["battle"]
    assert "secret-stalker" not in rendered
    assert "secret-stalker" not in str(projected_snapshot["battle"])
    assert "'x': 4" not in rendered


def test_compact_snapshot_uses_map_store_entities_before_stale_battle_grid():
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="MapStore Owner", player_id="owner")
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner"],
            "current_entity_id": "pc_owner",
            "actions_this_round": {},
        },
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "Stale Mirror Owner", "tags": {"player_id": "intruder"}},
                "ghost": {"id": "ghost", "name": "旧镜像幽灵", "tags": {"player_id": "intruder"}},
            },
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {"id": "pc_owner", "name": "MapStore Owner", "tags": {"player_id": "owner"}},
            },
        },
        map_id="strict-room",
    )

    compact = session.compact_snapshot()["battle"]
    rendered = str(compact)

    assert compact["turn"]["current_label"] == "MapStore Owner"
    assert compact["turn"]["current_owner_player_id"] == "owner"
    assert compact["grid"]["entities"] == [
        {"id": "pc_owner", "name": "MapStore Owner", "tags": {"player_id": "owner"}}
    ]
    assert "Stale Mirror Owner" not in rendered
    assert "旧镜像幽灵" not in rendered


def test_relevant_character_projection_uses_map_store_owner_tags_before_stale_battle_grid():
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="MapStore Owner", player_id="owner")
    session.characters["pc_hidden"] = Character(id="pc_hidden", name="Hidden Mirror", player_id="intruder")
    session.player_character_map["owner"] = "pc_owner"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "turn": {
            "active": True,
            "phase": "character_turn",
            "turn_order": ["pc_owner"],
            "current_entity_id": "pc_owner",
            "actions_this_round": {},
        },
        "grid": {
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "Stale Mirror Owner",
                    "tags": {"player_id": "intruder", "character_id": "pc_hidden"},
                },
            }
        },
    }
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "MapStore Owner",
                    "tags": {"player_id": "owner", "character_id": "pc_owner"},
                },
            },
        },
        map_id="strict-room",
    )

    projected_snapshot, _stats = prompt_snapshot_data(
        session,
        GameMode.TACTICAL,
        "我结束回合",
        actor={"player_id": "owner"},
        snapshot_projection_enabled=True,
    )
    relevant_ids = [item["id"] for item in projected_snapshot["characters"]["relevant"]]

    assert "pc_owner" in relevant_ids
    assert "pc_hidden" not in relevant_ids
