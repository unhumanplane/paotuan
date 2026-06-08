import json
import subprocess
import sys

from scripts.story_forge_compare import (
    _archive_prompt_context,
    _batch_report_markdown,
    _batch_row,
    _initial_archive_from_story,
    _multi_turn_audit_prompt_context,
    _multi_turn_summary_markdown,
    _read_cases,
    _read_archive_state,
    _read_json_if_exists,
    _read_player_actions_list,
    _read_simulation_suite,
    _read_story_file,
    _render_archive_map_seeds,
    _story_payload_runtime_context,
    _repaired_simulation_payload,
    _revelation_focus_from_archive,
    _revision_story_payload,
    _suite_report_markdown,
    _suite_row,
    _simulation_summary_markdown,
    _unique_run_dir,
    build_simulation_audit_messages,
    build_simulation_messages,
    build_simulation_repair_messages,
    build_multi_turn_audit_messages,
    build_legacy_messages,
    build_story_forge_messages,
    compare_results,
    extract_json_object,
    merge_archive_patch,
    parse_json_object,
    run_multi_turn_simulation,
    score_output,
)


class Args:
    def __init__(self, **values):
        self.__dict__.update(values)


def test_story_forge_prompt_contains_architecture_requirements():
    messages = build_story_forge_messages("开一个雨夜港口调查团", "克制恐怖，调查为主")
    joined = "\n".join(item["content"] for item in messages)

    assert "编剧层和叙事 DM 分离" in joined
    assert "canon_facts" in joined
    assert "player_known_state" in joined
    assert "hidden_truth" in joined
    assert "稳定资产 ID" in joined
    assert "失败推进" in joined
    assert "interaction_triggers" in joined
    assert "map_or_topology_hint" in joined
    assert "玩家可感知信息" in joined
    assert "说明书和编号菜单感" in joined


def test_legacy_prompt_does_not_require_story_forge_architecture():
    messages = build_legacy_messages("开一个雨夜港口调查团", "")
    joined = "\n".join(item["content"] for item in messages)

    assert "旧版单体 TRPG DM" in joined
    assert "不要引入编剧/记录员/资产库等新架构" in joined
    assert "playable_scene_cards" not in joined


def test_extract_json_object_handles_markdown_fenced_json():
    text = """```json
{"title":"雨港","scene_patch":{"current_objective":"查明灯塔为何重亮"}}
```"""

    payload = extract_json_object(text)

    assert payload == {
        "title": "雨港",
        "scene_patch": {"current_objective": "查明灯塔为何重亮"},
    }


def test_parse_json_object_repairs_common_deepseek_json_slips():
    text = '{"runtime_brief":{"continuity_warnings":["不要直接说出怪物"，]},"ok":true,}'

    payload, error, repaired = parse_json_object(text)

    assert payload == {
        "runtime_brief": {"continuity_warnings": ["不要直接说出怪物"]},
        "ok": True,
    }
    assert repaired is True
    assert "repaired" in error


def test_score_output_does_not_reward_invalid_json_text_hits():
    raw_text = '{"interactables":[{"id":"PROP_x"},{"id":"PROP_y"}], "campaign_assets":'

    score = score_output(None, raw_text)

    assert score["score"] == 0
    assert score["valid_json"] is False
    assert all(check["evidence"] == "invalid_json" for check in score["checks"])


def test_score_output_rewards_playable_scene_structure():
    payload = {
        "campaign_diagnosis": {"grade": "A", "playable_state": "ready"},
        "playable_scene_cards": [
            {
                "player_objective": "进入灯塔",
                "interactables": [
                    {"id": "LOC_lighthouse_door"},
                    {"id": "PROP_wet_logbook"},
                ],
                "npc_agendas": [
                    {"npc_id": "NPC_harbor_guard", "wants": "拖延调查"}
                ],
                "clue_channels": [
                    {"clue_id": "CLUE_reverse_bell", "routes": ["倾听", "询问"]},
                    {"clue_id": "CLUE_scraped_lock", "routes": ["观察", "开锁"]},
                ],
                "pressure_clock": {"label": "涨潮", "tick": 1, "max": 4},
                "failure_forward": ["守卫发现玩家，但暴露侧门钥匙在岗亭。"],
            }
        ],
        "session_archive_plan": {
            "canon_facts_to_track": ["灯塔确实重亮"],
            "player_known_state": ["港口听见倒放钟声"],
            "hidden_truth": ["灯塔下方有人主动启动仪式"],
        },
        "campaign_assets": {
            "characters": [{"asset_id": "NPC_harbor_guard"}],
            "locations": [{"asset_id": "LOC_lighthouse"}],
            "props": [{"asset_id": "PROP_wet_logbook"}],
            "clues": [{"asset_id": "CLUE_reverse_bell"}],
        },
        "playability_selfcheck": {"score": 86, "blocking_issues": []},
        "player_facing_opening": {
            "opening_intro": "雨水把码头灯拉成长线。",
            "player_guidance": "你们能从钟声、岗亭或灯塔侧门切入。",
        },
    }

    score = score_output(payload, "")

    assert score["score"] >= 80
    assert score["valid_json"] is True


def test_score_output_accepts_lowercase_stable_asset_ids():
    payload = {
        "campaign_assets": {
            "characters": [{"asset_id": "npc_driver_01"}],
            "locations": [{"asset_id": "loc_supply_station"}],
            "props": [{"asset_id": "prop_radio"}],
            "clues": [{"asset_id": "clue_signal_anomaly"}],
        }
    }

    score = score_output(payload, "")
    assets = next(check for check in score["checks"] if check["id"] == "assets")

    assert assets["passed"] is True


def test_compare_results_reports_story_forge_added_checks():
    legacy = {
        "title": "雨港",
        "opening_intro": "雨水落下。",
        "initial_hook": "灯塔亮起。",
        "player_guidance": "你们可以调查。",
        "scene_patch": {
            "current_objective": "查明灯塔为何重亮",
            "open_hooks": [{"id": "hook_lighthouse", "text": "灯塔重亮"}],
            "pressure_clock": {"label": "涨潮", "tick": 1, "max": 4},
        },
    }
    forge = {
        "playable_scene_cards": [
            {
                "player_objective": "查明灯塔为何重亮",
                "interactables": [{"id": "LOC_pier"}, {"id": "PROP_logbook"}],
                "npc_agendas": [{"npc_id": "NPC_guard", "wants": "封锁码头"}],
                "clue_channels": [
                    {"clue_id": "CLUE_bell"},
                    {"clue_id": "CLUE_lock"},
                ],
                "pressure_clock": {"label": "涨潮", "tick": 1, "max": 4},
                "failure_forward": ["守卫逼近，但留下岗亭空档。"],
            }
        ],
        "runtime_brief": {"do_not_reveal": ["仪式主谋"]},
        "session_archive_plan": {
            "canon_facts_to_track": ["灯塔重亮"],
            "player_known_state": ["钟声异常"],
            "hidden_truth": ["有人主动启动仪式"],
        },
        "campaign_assets": {
            "characters": [{"asset_id": "NPC_guard"}],
            "locations": [{"asset_id": "LOC_pier"}],
            "props": [{"asset_id": "PROP_logbook"}],
            "clues": [{"asset_id": "CLUE_bell"}],
        },
        "playability_selfcheck": {"score": 90, "blocking_issues": []},
        "player_facing_opening": {
            "opening_intro": "雨水落下。",
            "player_guidance": "你们可以从码头、岗亭或灯塔侧门切入。",
        },
    }

    comparison = compare_results(legacy, "", forge, "")

    assert comparison["delta_score"] > 0
    assert "interactables" in comparison["story_forge_added_checks"]
    assert "assets" in comparison["story_forge_added_checks"]


def test_batch_row_extracts_judge_totals(tmp_path):
    comparison = {
        "legacy": {"score": 40, "valid_json": True},
        "story_forge": {"score": 100, "valid_json": True},
        "delta_score": 60,
        "legacy_response": {"finish_reason": "stop"},
        "story_forge_response": {"finish_reason": "stop"},
        "judge": {
            "scores": {
                "legacy": {"total": 54},
                "story_forge": {"total": 88},
            },
            "winner": "story_forge",
            "key_deltas": ["新版提供了失败推进。"],
            "story_forge_upgrade_notes": ["玩家可见开场再少一点说明书感。"],
        },
        "revision": {"valid_json": True, "valid_revised_story": True},
        "revision_structure": {
            "story_forge": {"score": 100},
        },
        "revision_judge": {
            "scores": {
                "legacy": {"total": 54},
                "story_forge": {"total": 93},
            },
            "story_forge_upgrade_notes": ["二稿增强了 NPC 触发反馈。"],
        },
        "revision_compare": {
            "winner": "revision",
            "adoption_recommendation": "adopt_revision",
            "estimated_delta": {"overall": 2},
            "short_verdict": "二稿保留强项并增强了触发反馈。",
        },
    }

    row = _batch_row(
        1,
        {"id": "harbor", "seed": "雨夜港口", "preference": "调查"},
        tmp_path,
        comparison,
    )

    assert row["legacy_judge_total"] == 54
    assert row["story_forge_judge_total"] == 88
    assert row["judge_delta"] == 34
    assert row["judge_winner"] == "story_forge"
    assert row["revision_valid_story"] is True
    assert row["revision_judge_total"] == 93
    assert row["revision_judge_delta"] == 5
    assert row["revision_adoption_recommendation"] == "adopt_revision"
    assert row["revision_compare_delta"] == 2


def test_batch_report_markdown_includes_judge_summary():
    rows = [
        {
            "index": 1,
            "id": "harbor",
            "legacy_structure_score": 40,
            "story_forge_structure_score": 100,
            "structure_delta": 60,
            "judge_valid_json": True,
            "legacy_judge_total": 54,
            "story_forge_judge_total": 88,
            "judge_delta": 34,
            "judge_winner": "story_forge",
            "judge_key_deltas": ["新版提供了失败推进。"],
            "story_forge_upgrade_notes": ["减少说明书感。"],
            "revision_judge_valid_json": True,
            "revision_judge_total": 93,
            "revision_judge_delta": 5,
            "revision_compare_valid_json": True,
            "revision_adoption_recommendation": "adopt_revision",
            "revision_compare_verdict": "二稿保留强项并增强了触发反馈。",
        }
    ]

    report = _batch_report_markdown(rows)

    assert "专业评审平均分差" in report
    assert "二稿相对一稿平均提升" in report
    assert "二稿评审" in report
    assert "采用建议" in report
    assert "adopt_revision" in report
    assert "二稿采用判断" in report
    assert "harbor" in report
    assert "新版提供了失败推进" in report


def test_revision_story_payload_extracts_nested_revised_story():
    revised = {
        "playable_scene_cards": [],
        "player_facing_opening": {"opening_intro": "雨落在港口。"},
    }
    payload = {
        "revision_strategy": {"fixed_blockers": ["增强 NPC 反馈。"]},
        "revised_story_forge": revised,
    }

    assert _revision_story_payload(payload) is revised


def test_revision_story_payload_accepts_direct_story_payload():
    payload = {
        "playable_scene_cards": [],
        "player_facing_opening": {"opening_intro": "雨落在港口。"},
    }

    assert _revision_story_payload(payload) is payload


def test_repaired_simulation_payload_extracts_nested_payload():
    repaired = {
        "player_facing_response": {"narration": "door stays shut"},
        "archive_patch": {"canon_facts_add": ["lockpick failed"]},
    }
    payload = {"repair_strategy": {}, "repaired_simulation": repaired}

    assert _repaired_simulation_payload(payload) is repaired
    assert _repaired_simulation_payload(repaired) is repaired
    assert _repaired_simulation_payload({"repair_strategy": {}}) is None


def test_build_simulation_messages_requires_archive_patch():
    story = {
        "runtime_brief": {"do_not_reveal": ["灯塔下的实体"]},
        "playable_scene_cards": [],
        "player_facing_opening": {"opening_intro": "雨落在港口。"},
    }
    messages = build_simulation_messages(
        story_payload=story,
        player_actions="玩家直接绕到灯塔后门撬锁失败。",
        archive_state={"player_known_state": []},
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "archive_patch" in joined
    assert "player_known_state_add" in joined
    assert "hidden_truth_add" in joined
    assert "不得泄露" in joined
    assert "failure_forward" in joined
    assert "triggered_clues" in joined
    assert "稳定 npc_id/asset_id" in joined


def test_build_simulation_messages_secret_questions_become_playable_evidence():
    story = {
        "runtime_brief": {"do_not_reveal": ["灯塔下的实体", "守塔人的真实身份"]},
        "session_archive_plan": {"hidden_truth": ["灯塔下有不可见实体"]},
    }
    messages = build_simulation_messages(
        story_payload=story,
        player_actions="玩家问：如果里面是活人就让灯闪两下。",
        archive_state={"open_threads": ["灯塔为什么重亮"]},
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "不能回答 true/false" in joined
    assert "observable non-confirmation" in joined
    assert "concrete verification path" in joined
    assert "不能成为真相确认" in joined
    assert "准确认信号" in joined
    assert "固定周期" in joined
    assert "counter-evidence" in joined
    assert "不要把不可感知的隐藏主体拟人化" in joined
    assert "immediate_feedback 至少包含一种行动后果" in joined
    assert "observable_evidence 必须列出 1-3 条" in joined
    assert "verification_paths 必须列出 1-3 条" in joined
    assert "thread_progress_add 应记录既有线程被怎样推进" in joined
    assert "优先用 archive_state.open_threads 里的原文或稳定 ID" in joined
    assert "pressure_clock 是桌面节奏工具" in joined
    assert "公开指认/栽赃/威胁 NPC" in joined
    assert "非法黑入或监听" in joined
    assert "触碰高风险法器/封印/机关" in joined
    assert "pressure_patch.clock_id 必须沿用当前 archive_state.pressure_clock" in joined
    assert "visible_effect 必须写玩家能感知的节奏变化" in joined
    assert "每回合最多新增 1 条真正新线程" in joined
    assert "给 2-4 个自然下一步方向" in joined
    assert "至少给一张可跑的下一场目标卡" in joined


def test_revelation_focus_prefers_weak_open_thread():
    archive = {
        "open_threads": [
            {"id": "THREAD_lamp", "text": "灯塔为什么重亮", "evidence": ["灯芯有新油"]},
            {"id": "THREAD_keeper", "text": "十年前管理员为什么失踪"},
        ],
        "thread_progress": [
            {
                "thread_id": "THREAD_lamp",
                "new_evidence": ["油桶来自码头"],
                "next_verification": ["去码头查油桶来源"],
            }
        ],
    }

    focus = _revelation_focus_from_archive(archive)

    assert focus["priority_thread"]["thread_id"] == "thread_keeper"
    assert focus["priority_thread"]["title"] == "十年前管理员为什么失踪"
    assert focus["priority_thread"]["missing"] == ["evidence", "progress", "verification"]
    assert len(focus["weak_threads"]) == 1
    assert "不要强行牵引" in focus["instruction"]


def test_revelation_focus_empty_when_archive_has_no_open_threads():
    assert _revelation_focus_from_archive({}) == {}


def test_revelation_focus_identifies_convergence_thread():
    archive = {
        "open_threads": ["灯塔为何重亮"],
        "clue_ledger": [
            {
                "clue_id": "CLUE_lock",
                "parent_thread_id": "灯塔为何重亮",
                "clue_text": "铁门锁孔有新鲜划痕",
                "next_verification": "比对撬锁工具",
            },
            {
                "clue_id": "CLUE_light",
                "parent_thread_id": "灯塔为何重亮",
                "clue_text": "灯光周期出现一次异常",
                "next_verification": "连续观测灯光周期",
            },
        ],
    }

    focus = _revelation_focus_from_archive(archive)

    assert focus["priority_thread"] is None
    assert focus["convergence_thread"]["title"] == "灯塔为何重亮"
    assert focus["convergence_thread"]["evidence_count"] == 2
    assert focus["convergence_thread"]["verification_count"] == 2
    assert "core_lead" in focus["convergence_thread"]["needed"]


def test_build_simulation_messages_includes_revelation_focus_for_weak_threads():
    story = {"runtime_brief": {"do_not_reveal": ["灯塔地下的实体"]}}
    archive = {
        "open_threads": [
            {"id": "THREAD_keeper", "text": "十年前管理员为什么失踪"},
        ],
    }
    messages = build_simulation_messages(
        story_payload=story,
        player_actions="玩家查看管理员宿舍的旧照片。",
        archive_state=archive,
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "current_revelation_focus" in joined
    assert "priority_thread" in joined
    assert "THREAD_keeper" in joined or "thread_keeper" in joined
    assert "至少把 1 条 observable_evidence 或 verification_path" in joined
    assert "不得强行牵引" in joined
    assert "保持玩家主动性" in joined


def test_build_simulation_repair_messages_keeps_player_facing_stable():
    messages = build_simulation_repair_messages(
        story_payload={"runtime_brief": {"do_not_reveal": ["secret"]}},
        player_actions="inspect the broken wire",
        archive_state={"player_known_state": []},
        simulation_payload={
            "player_facing_response": {"narration": "the wire snaps"},
            "archive_patch": {"canon_facts_add": []},
        },
        audit_payload={
            "archive_gaps": ["archive_patch.canon_facts_add missing lockpick failure"],
            "thread_resolution_gaps": ["open thread should be resolved"],
        },
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "默认保留 player_facing_response" in joined
    assert "最小化修补" in joined
    assert "open_threads_resolved 只能包含本回合确实被玩家行动解决的线程" in joined
    assert "thread_resolution_gaps" in joined
    assert "repaired_simulation" in joined


def test_build_simulation_repair_messages_can_fix_safe_but_empty_response():
    messages = build_simulation_repair_messages(
        story_payload={"runtime_brief": {"do_not_reveal": ["secret"]}},
        player_actions="ask whether the target is alive",
        archive_state={"player_known_state": []},
        simulation_payload={
            "player_facing_response": {"narration": "你无法得知。"},
            "archive_patch": {"canon_facts_add": []},
        },
        audit_payload={
            "narrative_issues": ["安全但空白拒答，忽略玩家问题"],
            "missed_gameplay_opportunities": ["无可验证下一步"],
        },
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "安全但空白" in joined
    assert "忽略玩家问题" in joined
    assert "无可验证下一步" in joined
    assert "才可以最小改写 player_facing_response" in joined
    assert "不泄露 hidden_truth" in joined
    assert "verification path" in joined
    assert "不要回答 hidden_truth 的 true/false" in joined
    assert "observable_evidence 或 verification_paths 缺失" in joined
    assert "thread_progress_add" in joined
    assert "高风险行动但 tick_delta=0" in joined
    assert "tick_delta=1" in joined
    assert "不得重命名压力钟" in joined
    assert "只保留最重要的一条新行动线程" in joined
    assert "优先改写为 linked_thread 指向最相关的既有主线" in joined
    assert "不得为了增加戏剧张力而发明或坐实 do_not_reveal/hidden_truth" in joined
    assert "不能写成“某人就是反对派/主谋/隐藏实体”" in joined
    assert "不能同步升级到 canon_facts_add 或 player_known_state_add" in joined
    assert "容易被玩家理解成秘密确认的准信号" in joined
    assert "convergence_actions_add" in joined
    assert "scene_goal" in joined


def test_multi_turn_audit_prompt_requires_thread_resolution_checks():
    messages = build_multi_turn_audit_messages(
        story_payload={"session_archive_plan": {"open_threads": ["who has the key"]}},
        turns=[],
        final_archive={"open_threads": ["who has the key"], "resolved_threads": []},
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "thread_resolution_gaps" in joined
    assert "resolved_threads" in joined
    assert "连续追查同一个开放线程" in joined
    assert "thread_progress 是否持续记录旧线程的证据推进" in joined
    assert "final_archive.revelation_board" in joined
    assert "label/clock_id 是否跨回合稳定" in joined
    assert "持续新增多个 open_threads" in joined
    assert "叙事动量" in joined
    assert "narrative_or_gameplay_regressions" in joined


def test_read_story_file_extracts_revision_report(tmp_path):
    story = {
        "playable_scene_cards": [],
        "player_facing_opening": {"opening_intro": "雨落在港口。"},
    }
    path = tmp_path / "revision_report.json"
    path.write_text(
        json.dumps({"revision_strategy": {}, "revised_story_forge": story}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert _read_story_file(str(path)) == story


def test_simulation_summary_includes_audit_verdict():
    simulation = {
        "finish_reason": "stop",
        "payload": {
            "player_facing_response": {"narration": "锁簧断开，远处传来脚步声。"},
            "runtime_selfcheck": {
                "hidden_truth_leaked": False,
                "railroading_risk": "low",
                "failure_forward_used": True,
                "archive_updated": True,
            },
        },
    }
    audit = {
        "payload": {
            "scores": {"total": 86},
            "verdict": "pass",
            "short_reason": "失败推进和留档都有效。",
        }
    }

    summary = _simulation_summary_markdown(simulation, audit)

    assert "Runtime DM 模拟摘要" in summary
    assert "锁簧断开" in summary
    assert "verdict：pass" in summary
    assert "total：86" in summary


def test_simulation_audit_prompt_requires_evidence_paths():
    story = {"runtime_brief": {"do_not_reveal": ["灯塔实体"]}}
    simulation = {
        "archive_patch": {
            "player_known_state_add": ["灯塔后门锁孔有新鲜刮痕"]
        }
    }
    messages = build_simulation_audit_messages(
        story_payload=story,
        player_actions="玩家撬锁失败。",
        simulation_payload=simulation,
        simulation_text="",
    )
    joined = "\n".join(item["content"] for item in messages)

    assert "具体 JSON 路径" in joined
    assert "不得再把同一事实列为 archive_gaps" in joined
    assert "evidence.positive" in joined
    assert "安全而牺牲戏剧反馈" in joined
    assert "actionable_next_steps" in joined
    assert "observable_evidence 为空" in joined
    assert "thread_progress_add" in joined
    assert "高风险行动" in joined
    assert "pressure_patch.tick_delta=0" in joined


def test_read_cases_accepts_utf8_bom_batch_file(tmp_path):
    batch = tmp_path / "cases.json"
    batch.write_text(
        '\ufeff{"cases":[{"id":"harbor","seed":"雨夜港口","preference":"调查"}]}',
        encoding="utf-8",
    )
    args = Args(
        batch_file=str(batch),
        seed="",
        seed_file="",
        preference="",
        preference_file="",
    )

    cases = _read_cases(args)

    assert cases == [{"id": "harbor", "seed": "雨夜港口", "preference": "调查"}]


def test_unique_run_dir_adds_suffix_when_needed(tmp_path):
    first = _unique_run_dir(tmp_path, "run")
    second = _unique_run_dir(tmp_path, "run")

    assert first.name == "run"
    assert second.name == "run-2"
    assert first.exists()
    assert second.exists()


def test_read_player_actions_list_accepts_json_array(tmp_path):
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps(
            [
                "inspect the lighthouse lock",
                {"action": "circle the outer wall"},
                {"player_action": "question the harbor guard"},
            ]
        ),
        encoding="utf-8",
    )

    actions = _read_player_actions_list(str(path))

    assert actions == [
        "inspect the lighthouse lock",
        "circle the outer wall",
        "question the harbor guard",
    ]


def test_read_player_actions_list_splits_text_on_dash_separator(tmp_path):
    path = tmp_path / "actions.txt"
    path.write_text(
        "first turn\nstill first\n---\nsecond turn\n---\nthird turn",
        encoding="utf-8",
    )

    actions = _read_player_actions_list(str(path))

    assert actions == ["first turn\nstill first", "second turn", "third turn"]


def test_read_simulation_suite_resolves_relative_paths_and_inline_actions(tmp_path):
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "id": "acceptance",
                "cases": [
                    {
                        "id": "harbor",
                        "story_file": "story.json",
                        "archive_file": "archive.json",
                        "actions": ["turn one", {"action": "turn two"}],
                        "tags": ["investigation"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = _read_simulation_suite(str(suite))

    assert payload["id"] == "acceptance"
    assert payload["cases"][0]["id"] == "harbor"
    assert payload["cases"][0]["story_file"].endswith("story.json")
    assert payload["cases"][0]["archive_file"].endswith("archive.json")
    assert payload["cases"][0]["actions"] == ["turn one", {"action": "turn two"}]
    assert payload["cases"][0]["tags"] == ["investigation"]


def test_initial_archive_from_story_uses_archive_plan_and_pressure():
    story = {
        "session_archive_plan": {
            "canon_facts_to_track": ["premise happened"],
            "player_known_state": ["players know the job"],
            "hidden_truth": ["keeper is inside"],
            "open_threads": ["who lit the lamp"],
        },
        "playable_scene_cards": [
            {
                "pressure_clock": {
                    "label": "fog",
                    "tick": 1,
                    "max": 6,
                }
            }
        ],
    }

    archive = _initial_archive_from_story(story)

    assert archive["canon_facts"] == ["premise happened"]
    assert archive["player_known_state"] == ["players know the job"]
    assert archive["hidden_truth"] == ["keeper is inside"]
    assert archive["open_threads"] == ["who lit the lamp"]
    assert archive["pressure_clock"] == {"label": "fog", "tick": 1, "max": 6}


def test_read_archive_state_merges_story_safety_layers(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(
        json.dumps(
            {
                "player_known_state": ["players reached the hall"],
                "hidden_truth": ["existing private note"],
            }
        ),
        encoding="utf-8",
    )
    story = {
        "runtime_brief": {"do_not_reveal": ["反对派的具体身份"]},
        "session_archive_plan": {"hidden_truth": ["反对派是安全主管的竞争对手"]},
    }

    archive = _read_archive_state(str(path), story)

    assert archive["player_known_state"] == ["players reached the hall"]
    assert archive["hidden_truth"] == ["existing private note", "反对派是安全主管的竞争对手"]
    assert archive["do_not_reveal"] == ["反对派的具体身份"]


def test_run_multi_turn_simulation_writes_revelation_focus_in_dry_run(tmp_path):
    story_path = tmp_path / "story.json"
    actions_path = tmp_path / "actions.json"
    archive_path = tmp_path / "archive.json"
    story_path.write_text(
        json.dumps({"runtime_brief": {"do_not_reveal": ["地下实体"]}}),
        encoding="utf-8",
    )
    actions_path.write_text(json.dumps(["查看管理员宿舍的旧照片"]), encoding="utf-8")
    archive_path.write_text(
        json.dumps(
            {
                "open_threads": [
                    {"id": "THREAD_keeper", "text": "十年前管理员为什么失踪"},
                ]
            }
        ),
        encoding="utf-8",
    )
    args = Args(
        story_file=str(story_path),
        simulate_actions_list_file=str(actions_path),
        archive_file=str(archive_path),
        model="deepseek-v4-flash",
        judge_model="",
        base_url="https://api.deepseek.com",
        max_tokens=1024,
        judge_max_tokens=1024,
        temperature=0.2,
        judge_temperature=0.2,
        timeout=10,
        audit_simulation=False,
        repair_from_audit=False,
        no_json_mode=False,
        dry_run=True,
        api_key="",
        api_key_env="DEEPSEEK_API_KEY",
        thinking=None,
    )

    run_dir = run_multi_turn_simulation(args=args, api_key="", output_dir=tmp_path)

    turn_dir = next(run_dir.glob("turn_01*"))
    focus = _read_json_if_exists(turn_dir / "revelation_focus.json")
    turns = _read_json_if_exists(run_dir / "turns.json")
    messages = _read_json_if_exists(turn_dir / "simulation.messages.json")

    assert focus["priority_thread"]["thread_id"] == "thread_keeper"
    assert turns[0]["revelation_focus"]["priority_thread"]["thread_id"] == "thread_keeper"
    assert "current_revelation_focus" in "\n".join(item["content"] for item in messages)


def test_story_payload_runtime_context_keeps_runnable_scene_slice():
    story = {
        "campaign_director_pack": {"core_conflict": "harbor wants silence"},
        "runtime_brief": {"current_objective": "reach the lighthouse"},
        "session_archive_plan": {"hidden_truth": ["keeper is below"]},
        "playable_scene_cards": [
            {
                "scene_id": "S1",
                "title": "Pier",
                "dramatic_task": "make the first approach tense",
                "interactables": [{"id": "door", "verbs": ["inspect"], "long_backstory": "x" * 500}],
                "npc_agendas": [{"npc_id": "NPC_guard", "wants": "delay"}],
                "clue_channels": [{"clue_id": "CLUE_lock", "routes": ["inspect"]}],
                "unused_long_field": "y" * 500,
            },
            {"scene_id": "S2"},
            {"scene_id": "S3"},
            {"scene_id": "S4"},
            {"scene_id": "S5"},
        ],
        "campaign_assets": {
            "characters": [{"asset_id": "NPC_guard", "name": "Guard", "portrait_prompt": "z" * 500}],
        },
        "massive_unused_notes": ["noise"] * 100,
    }

    context = _story_payload_runtime_context(story)

    assert context["runtime_context_version"] == 1
    assert context["runtime_brief"] == {"current_objective": "reach the lighthouse"}
    assert len(context["playable_scene_cards"]) == 4
    assert context["playable_scene_cards"][0]["interactables"] == [{"id": "door", "verbs": ["inspect"]}]
    assert context["campaign_assets"]["characters"] == [{"asset_id": "NPC_guard", "name": "Guard"}]
    assert "massive_unused_notes" not in context
    assert "unused_long_field" not in context["playable_scene_cards"][0]


def test_build_simulation_messages_adds_convergence_contract_and_runtime_context():
    story = {
        "runtime_brief": {"current_objective": "trace the old keeper"},
        "playable_scene_cards": [{"scene_id": "S1", "title": "Lighthouse", "unused": "x" * 500}],
        "massive_unused_notes": ["noise"] * 20,
    }
    archive = {
        "open_threads": ["THREAD_keeper: old keeper disappearance"],
        "thread_progress": [
            {
                "thread_id": "THREAD_keeper",
                "new_evidence": ["fresh lock scratches", "old photo"],
                "next_verification": ["compare service records"],
            }
        ],
    }

    messages = build_simulation_messages(
        story_payload=story,
        player_actions="inspect the service records",
        archive_state=archive,
    )
    user = messages[1]["content"]

    assert "Runtime convergence contract" in user
    assert "convergence_actions_add" in user
    assert "scene_goal" in user
    assert "map_grid_seed" in user
    assert "runtime_context_version" in user
    assert "massive_unused_notes" not in user


def test_build_simulation_messages_places_variable_turn_data_after_stable_prefix():
    story = {
        "runtime_brief": {"current_objective": "trace the old keeper"},
        "playable_scene_cards": [{"scene_id": "S1", "title": "Lighthouse", "unused": "x" * 500}],
    }
    archive = {
        "open_threads": ["THREAD_keeper: old keeper disappearance"],
        "thread_progress": [{"thread_id": "THREAD_keeper", "new_evidence": ["fresh lock scratches"]}],
        "huge_unused_history": [{"noise": "x" * 2000}],
    }

    messages = build_simulation_messages(
        story_payload=story,
        player_actions="inspect the service records",
        archive_state=archive,
    )
    user = messages[1]["content"]

    assert user.index("当前 Story Forge 方案") < user.index("输出必须严格符合这个 JSON 结构")
    assert user.index("输出必须严格符合这个 JSON 结构") < user.index("当前回合输入（变量区")
    assert user.index("current_archive_state") < user.index("player_action")
    assert "huge_unused_history" not in user
    assert "prompt_context_version" in user


def test_archive_prompt_context_keeps_recent_playable_state_without_full_noise():
    archive = {
        "canon_facts": [f"fact-{index}" for index in range(20)],
        "open_threads": [{"id": "thread-key", "text": "who has the key"}],
        "thread_progress": [{"thread_id": "thread-key", "new_evidence": ["fresh scratch"]}],
        "clue_ledger": [{"clue_id": "clue-scratch", "clue_text": "fresh scratch"}],
        "full_raw_turns": [{"player": "x" * 2000}],
    }

    context = _archive_prompt_context(archive)

    assert context["prompt_context_version"] == 1
    assert len(context["canon_facts"]) == 12
    assert context["canon_facts"][0] == "fact-8"
    assert context["open_threads"][0]["id"] == "thread-key"
    assert "full_raw_turns" not in context


def test_multi_turn_audit_prompt_context_uses_repaired_summary_not_full_turn_archives():
    turns = [
        {
            "turn_index": 1,
            "player_action": "inspect the lock",
            "archive_before": {"pressure_clock": {"label": "table-clock", "tick": 1, "max": 4}, "huge": "before"},
            "simulation": {"raw_full_response": "x" * 2000},
            "repair": {
                "repaired_simulation": {
                    "player_facing_response": {"narration": "The lock clicks."},
                    "archive_patch": {"canon_facts_add": ["lock opened"]},
                    "pressure_patch": {"tick_delta": 1},
                }
            },
            "archive_after": {
                "canon_facts": ["lock opened"],
                "full_raw_turns": [{"noise": "x" * 2000}],
            },
        }
    ]

    context = _multi_turn_audit_prompt_context(turns)
    text = json.dumps(context, ensure_ascii=False)

    assert context[0]["player_facing_response"]["narration"] == "The lock clicks."
    assert context[0]["archive_before"]["pressure_clock"]["label"] == "table-clock"
    assert context[0]["archive_patch"]["canon_facts_add"] == ["lock opened"]
    assert context[0]["archive_after"]["canon_facts"] == ["lock opened"]
    assert "archive_before" in text
    assert "full_raw_turns" in text
    assert "raw_full_response" not in text


def test_merge_archive_patch_appends_resolves_and_updates_pressure():
    archive = {
        "canon_facts": ["job accepted"],
        "player_known_state": [],
        "hidden_truth": [],
        "open_threads": [{"id": "thread_lock", "text": "what is behind the door"}],
        "pressure_clock": {"label": "tide", "tick": 1, "max": 4},
    }
    archive_patch = {
        "canon_facts_add": ["lockpick failed", "job accepted"],
        "player_known_state_add": ["fresh scrape on lock"],
        "hidden_truth_add": ["keeper heard the noise"],
        "open_threads_add": [{"id": "thread_sound", "text": "source of metal sound"}],
        "open_threads_resolved": [{"id": "thread_lock"}],
        "npc_state_updates": [{"npc_id": "NPC_keeper", "state": "alerted"}],
        "asset_state_updates": [{"asset_id": "LOC_lighthouse_back_door", "state": "scratched"}],
    }
    pressure_patch = {
        "clock_id": "tide",
        "tick_delta": 1,
        "trigger": "metal noise",
        "visible_effect": "distant footstep",
    }

    merged = merge_archive_patch(archive, archive_patch, pressure_patch)

    assert merged["canon_facts"] == ["job accepted", "lockpick failed"]
    assert merged["player_known_state"] == ["fresh scrape on lock"]
    assert merged["hidden_truth"] == ["keeper heard the noise"]
    assert merged["open_threads"] == [{"id": "thread_sound", "text": "source of metal sound"}]
    assert merged["resolved_threads"] == [{"id": "thread_lock"}]
    assert merged["npc_state"] == [{"npc_id": "NPC_keeper", "state": "alerted"}]
    assert merged["asset_state"] == [{"asset_id": "LOC_lighthouse_back_door", "state": "scratched"}]
    assert merged["npc_state_history"][0]["id"] == "npc_keeper"
    assert merged["asset_state_history"][0]["id"] == "loc_lighthouse_back_door"
    assert merged["pressure_clock"]["tick"] == 2
    assert merged["pressure_history"][0]["trigger"] == "metal noise"
    assert archive["pressure_clock"]["tick"] == 1


def test_merge_archive_patch_coalesces_state_updates_by_id():
    archive = {
        "npc_state": [{"npc_id": "NPC_keeper", "state": "watching"}],
        "asset_state": [{"asset_id": "LOC_door", "state": "locked"}],
    }

    merged = merge_archive_patch(
        archive,
        {
            "npc_state_updates": [{"npc_id": "NPC_keeper", "state": "alerted", "position": "stairs"}],
            "asset_state_updates": [{"asset_id": "LOC_door", "state": "scratched"}],
        },
        {},
    )

    assert merged["npc_state"] == [
        {"npc_id": "NPC_keeper", "state": "alerted", "position": "stairs"}
    ]
    assert merged["asset_state"] == [{"asset_id": "LOC_door", "state": "scratched"}]
    assert merged["npc_state_history"][0]["before"] == {"npc_id": "NPC_keeper", "state": "watching"}
    assert merged["asset_state_history"][0]["before"] == {"asset_id": "LOC_door", "state": "locked"}


def test_merge_archive_patch_preserves_existing_pressure_clock_identity():
    archive = {"pressure_clock": {"label": "灯塔觉醒", "clock_id": "灯塔觉醒", "tick": 2, "max": 6}}

    merged = merge_archive_patch(
        archive,
        {},
        {
            "clock_id": "雾锁灯塔",
            "tick_delta": 1,
            "new_tick": 3,
            "trigger": "player calls into the rain",
            "visible_effect": "light stutters",
        },
    )

    assert merged["pressure_clock"]["label"] == "灯塔觉醒"
    assert merged["pressure_clock"]["clock_id"] == "灯塔觉醒"
    assert merged["pressure_clock"]["tick"] == 3
    assert merged["pressure_history"][0]["clock_id"] == "灯塔觉醒"
    assert merged["pressure_history"][0]["patch_clock_id"] == "雾锁灯塔"


def test_merge_archive_patch_applies_pressure_floor_for_secret_probe_reaction():
    archive = {"pressure_clock": {"label": "灯塔觉醒", "tick": 2, "max": 6}}
    simulation = {
        "player_facing_response": {
            "narration": "玩家问里面是不是活人。铁门内侧传来沉闷撞击声，灯光短暂熄灭后恢复。"
        },
        "pressure_patch": {
            "clock_id": "灯塔觉醒",
            "tick_delta": 0,
            "new_tick": 2,
            "trigger": "",
            "visible_effect": "",
        },
    }

    merged = merge_archive_patch(
        archive,
        {},
        simulation["pressure_patch"],
        simulation_payload=simulation,
    )

    assert merged["pressure_clock"]["tick"] == 3
    assert merged["pressure_history"][0]["tick_delta"] == 1
    assert merged["pressure_history"][0]["new_tick"] == 3
    assert "压力下限" in merged["pressure_history"][0]["trigger"]
    assert "撞击声" in merged["pressure_history"][0]["visible_effect"]


def test_merge_archive_patch_coalesces_duplicate_open_threads_by_subject_and_id():
    archive = {
        "open_threads": [
            "灯塔为何重亮",
            "THREAD_03: 铁门撬痕是谁留下的？与警告纸条是否同一方？",
        ]
    }

    merged = merge_archive_patch(
        archive,
        {
            "open_threads_add": [
                "THREAD_01（灯塔为何重亮）",
                "THREAD_03（铁门撬痕）",
                "THREAD_04: 窗沿油脂污渍的来源和性质？",
            ]
        },
        {},
    )

    assert merged["open_threads"] == [
        "THREAD_01（灯塔为何重亮）",
        "THREAD_03: 铁门撬痕是谁留下的？与警告纸条是否同一方？",
        "THREAD_04: 窗沿油脂污渍的来源和性质？",
    ]
    assert len(merged["open_threads"]) == 3
    assert "open_thread_overflow" not in merged
    assert "thread_hints" not in merged
    assert len(merged["open_thread_history"]) == 1


def test_merge_archive_patch_limits_new_open_threads_per_turn():
    archive = {"open_threads": ["灯塔为何重亮"]}

    merged = merge_archive_patch(
        archive,
        {
            "open_threads_add": [
                "THREAD_04: 窗沿油脂污渍的来源和性质？",
                "THREAD_05: 门缝沙土中黑色颗粒的来源？",
                "THREAD_06: 楼上传来的脚步声是谁？",
            ]
        },
        {},
    )

    assert merged["open_threads"] == ["灯塔为何重亮", "THREAD_04: 窗沿油脂污渍的来源和性质？"]
    assert merged["thread_hints"] == [
        {"source": "open_threads_add_over_budget", "text": "THREAD_05: 门缝沙土中黑色颗粒的来源？"},
        {"source": "open_threads_add_over_budget", "text": "THREAD_06: 楼上传来的脚步声是谁？"},
    ]


def test_merge_archive_patch_defines_thread_referenced_by_progress():
    archive = {"open_threads": ["灯塔为何重亮"]}

    merged = merge_archive_patch(
        archive,
        {
            "thread_progress_add": [
                {
                    "thread_id": "THREAD_3",
                    "remaining_unknown": "灯塔内部金属碰撞声和呼吸声的来源",
                    "next_verification": "贴近铁门听声源",
                }
            ]
        },
        {},
    )

    assert merged["open_threads"] == [
        "灯塔为何重亮",
        "THREAD_3: 灯塔内部金属碰撞声和呼吸声的来源",
    ]
    assert merged["thread_progress"][0]["thread_id"] == "THREAD_3"
    assert merged["revelation_board"][1]["thread_id"] == "thread_3"
    assert merged["revelation_board"][1]["progress_count"] == 1


def test_merge_archive_patch_revelation_board_keeps_structured_thread_evidence():
    archive = {
        "open_threads": [
            {
                "thread_id": "climb_marks",
                "title": "窗沿攀爬痕迹来源",
                "evidence": ["窗沿攀爬痕迹尺寸偏小"],
                "verification_paths": ["比对布料纤维"],
            }
        ]
    }

    merged = merge_archive_patch(archive, {}, {})

    board = merged["revelation_board"][0]
    assert board["thread_id"] == "climb_marks"
    assert board["evidence"] == ["窗沿攀爬痕迹尺寸偏小"]
    assert board["verification_paths"] == ["比对布料纤维"]
    assert board["evidence_count"] == 1
    assert board["verification_count"] == 1


def test_merge_archive_patch_tracks_clue_ledger_without_new_open_thread():
    archive = {"open_threads": ["灯塔为何重亮"]}

    merged = merge_archive_patch(
        archive,
        {
            "clue_ledger_add": [
                {
                    "clue_id": "CLUE_fiber",
                    "parent_thread_id": "灯塔为何重亮",
                    "clue_text": "窗沿裂缝里的深色布料纤维",
                    "status": "observed",
                    "game_use": "raises_question",
                    "next_verification": "比对港口雨衣和商会制服布料",
                }
            ]
        },
        {},
    )

    assert merged["open_threads"] == ["灯塔为何重亮"]
    assert merged["clue_ledger"] == [
        {
            "clue_id": "CLUE_fiber",
            "parent_thread_id": "灯塔为何重亮",
            "parent_thread_ids": ["灯塔为何重亮"],
            "clue_text": "窗沿裂缝里的深色布料纤维",
            "status": "observed",
            "game_use": "raises_question",
            "next_verification": "比对港口雨衣和商会制服布料",
        }
    ]
    board = merged["revelation_board"][0]
    assert board["thread_id"] == "灯塔为何重亮"
    assert board["evidence_count"] == 1
    assert board["hint_count"] == 1
    assert board["verification_count"] == 1


def test_merge_archive_patch_tracks_convergence_actions_as_progress():
    archive = {
        "open_threads": ["THREAD_keeper: old keeper disappearance"],
        "clue_ledger": [
            {
                "clue_id": "CLUE_photo",
                "parent_thread_id": "THREAD_keeper",
                "clue_text": "old keeper photo has a fresh crease",
                "status": "observed",
                "next_verification": "compare it with the service archive",
            }
        ],
    }

    merged = merge_archive_patch(
        archive,
        {
            "convergence_actions_add": [
                {
                    "thread_id": "THREAD_keeper",
                    "action_type": "scene_entry",
                    "synthesis": "the photo and records point toward the service archive",
                    "next_scene_entry": "the maintenance archive behind the stairwell is now a concrete lead",
                    "available_action": "ask the harbor clerk for the maintenance archive key",
                }
            ]
        },
        {},
    )

    assert merged["convergence_actions"] == [
        {
            "thread_id": "THREAD_keeper",
            "action_type": "scene_entry",
            "synthesis": "the photo and records point toward the service archive",
            "next_scene_entry": "the maintenance archive behind the stairwell is now a concrete lead",
            "available_action": "ask the harbor clerk for the maintenance archive key",
        }
    ]
    board = next(entry for entry in merged["revelation_board"] if entry["thread_id"] == "thread_keeper")
    assert board["progress_count"] == 1
    assert board["verification_count"] == 2
    focus = _revelation_focus_from_archive(merged)
    assert focus["convergence_thread"] is None


def test_merge_archive_patch_preserves_convergence_scene_goal_and_map_seed():
    archive = {
        "open_threads": ["THREAD_keeper: old keeper disappearance"],
        "thread_progress": [
            {
                "thread_id": "THREAD_keeper",
                "new_evidence": ["fresh lock scratches", "old photo"],
                "next_verification": ["compare service records"],
            }
        ],
    }

    merged = merge_archive_patch(
        archive,
        {
            "convergence_actions_add": [
                {
                    "thread_id": "THREAD_keeper",
                    "action_type": "scene_entry",
                    "scene_goal": "Enter the lower service room and test the old service panel.",
                    "entry_cost": "Spend one pressure tick or expose the party to the stairwell camera.",
                    "success_signal": "The panel answers with a visible maintenance route marker.",
                    "failure_forward": "The route marker flickers out, but a guard moves into view with the key ring.",
                    "map_grid_seed": {
                        "map_id": "lower-service-room",
                        "title": "Lower service room",
                        "grid": {
                            "width": 4,
                            "height": 3,
                            "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
                            "entities": [
                                {"id": "panel", "name": "Service panel", "x": 2, "y": 1}
                            ],
                        },
                    },
                }
            ]
        },
        {},
    )

    action = merged["convergence_actions"][0]
    assert action["scene_goal"] == "Enter the lower service room and test the old service panel."
    assert action["entry_cost"].startswith("Spend one pressure tick")
    assert action["success_signal"].startswith("The panel answers")
    assert action["failure_forward"].startswith("The route marker flickers")
    assert action["map_grid_seed"]["map_id"] == "lower-service-room"
    assert action["map_grid_seed"]["grid"]["width"] == 4

    board = next(entry for entry in merged["revelation_board"] if entry["thread_id"] == "thread_keeper")
    assert board["progress_count"] == 2
    focus = _revelation_focus_from_archive(merged)
    assert focus["convergence_thread"] is None


def test_merge_archive_patch_recovers_nested_scene_goal_card_from_model_slip():
    merged = merge_archive_patch(
        {"open_threads": ["THREAD_keeper: old keeper disappearance"]},
        {
            "convergence_actions_add": [
                {
                    "thread_id": "THREAD_keeper",
                    "action_type": "scene_entry",
                    "synthesis": "the clues now point to the tower door",
                    "next_scene_entry": "the lower tower door becomes the next playable entry point",
                    "scene_goal": {
                        "entry_cost": "Spend one pressure tick or draw the guard closer.",
                        "success_signal": "The door opens onto a visible service corridor.",
                        "failure_forward": "The lock jams, but the guard arrives with a key ring.",
                        "map_grid_seed": {
                            "map_id": "tower-door",
                            "grid": {"width": 2, "height": 2, "cells": []},
                        },
                    },
                }
            ]
        },
        {},
    )

    action = merged["convergence_actions"][0]
    assert action["scene_goal"] == "the lower tower door becomes the next playable entry point"
    assert action["entry_cost"] == "Spend one pressure tick or draw the guard closer."
    assert action["success_signal"] == "The door opens onto a visible service corridor."
    assert action["failure_forward"] == "The lock jams, but the guard arrives with a key ring."
    assert action["map_grid_seed"]["map_id"] == "tower-door"


def test_render_archive_map_seeds_writes_svg_manifest(tmp_path):
    archive = {
        "convergence_actions": [
            {
                "thread_id": "THREAD_keeper",
                "action_type": "scene_entry",
                "scene_goal": "Enter the lower tower room.",
                "map_grid_seed": {
                    "map_id": "tower-room",
                    "title": "Tower room",
                    "grid": {
                        "width": 3,
                        "height": 2,
                        "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
                        "entities": [{"id": "panel", "name": "Panel", "x": 1, "y": 1}],
                    },
                },
            }
        ]
    }

    manifest = _render_archive_map_seeds(archive, tmp_path)

    assert manifest[0]["ok"] is True
    assert manifest[0]["thread_id"] == "THREAD_keeper"
    assert manifest[0]["map_id"] == "tower-room"
    assert "file_path" not in manifest[0]["safe_projection"]
    assert "grid" not in manifest[0]["safe_projection"]
    assert (tmp_path / "rendered_maps_manifest.json").exists()
    assert list((tmp_path / "rendered_maps").glob("*.svg"))


def test_story_grid_map_cli_renders_seed_from_repo_root(tmp_path):
    payload = tmp_path / "story_map_seed.json"
    payload.write_text(
        json.dumps(
            {
                "map_grid_seed": {
                    "map_id": "tower-room",
                    "title": "Tower room",
                    "grid": {
                        "width": 2,
                        "height": 2,
                        "cells": [{"x": 0, "y": 0, "terrain": "stone"}],
                        "entities": [
                            {
                                "id": "panel",
                                "name": "Panel",
                                "x": 1,
                                "y": 1,
                            }
                        ],
                    },
                    "player_view": {
                        "labels": [
                            {
                                "x": 1,
                                "y": 1,
                                "text": "Panel",
                            }
                        ],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "maps"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_story_grid_map.py",
            "--grid-file",
            str(payload),
            "--output-dir",
            str(out_dir),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    rendered = json.loads(result.stdout)
    assert rendered["ok"] is True
    assert rendered["map_id"] == "tower-room"
    assert list(out_dir.glob("*.svg"))


def test_merge_archive_patch_clue_ledger_can_bridge_multiple_parent_threads():
    archive = {"open_threads": ["灯塔为何重亮", "十年前管理员失踪"]}

    merged = merge_archive_patch(
        archive,
        {
            "clue_ledger_add": [
                {
                    "clue_id": "CLUE_fiber",
                    "parent_thread_ids": ["灯塔为何重亮", "十年前管理员失踪"],
                    "clue_text": "窗沿裂缝里的深色布料纤维",
                    "status": "observed",
                    "game_use": "raises_question",
                    "next_verification": "比对港口旧制服布料",
                }
            ]
        },
        {},
    )

    board_by_title = {entry["title"]: entry for entry in merged["revelation_board"]}
    assert board_by_title["灯塔为何重亮"]["evidence_count"] == 1
    assert board_by_title["十年前管理员失踪"]["evidence_count"] == 1
    assert merged["clue_ledger"][0]["parent_thread_ids"] == ["灯塔为何重亮", "十年前管理员失踪"]


def test_merge_archive_patch_bridges_admin_related_clue_to_weak_thread():
    archive = {"open_threads": ["灯塔为何重亮", "十年前管理员失踪"]}

    merged = merge_archive_patch(
        archive,
        {
            "clue_ledger_add": [
                {
                    "clue_id": "CLUE_fiber",
                    "parent_thread_id": "灯塔为何重亮",
                    "clue_text": "窗沿攀爬痕迹末端找到的深色布料纤维",
                    "status": "sampled",
                    "game_use": "raises_question",
                }
            ]
        },
        {},
    )

    assert merged["clue_ledger"][0]["parent_thread_ids"] == ["灯塔为何重亮", "十年前管理员失踪"]
    board_by_title = {entry["title"]: entry for entry in merged["revelation_board"]}
    assert board_by_title["十年前管理员失踪"]["evidence_count"] == 1


def test_merge_archive_patch_extracts_clue_ledger_from_observable_evidence():
    archive = {"open_threads": ["灯塔为何重亮"]}
    simulation = {
        "player_facing_response": {
            "observable_evidence": [
                {
                    "evidence_id": "EVIDENCE_fiber",
                    "observation": "窗沿裂缝里卡着一小片深色布料纤维。",
                    "game_use": "raises_question",
                    "linked_thread_id": "灯塔为何重亮",
                }
            ],
            "verification_paths": [
                {
                    "path_id": "VERIFY_fiber",
                    "action": "比对布料纤维来源",
                    "target": "布料纤维样本",
                    "risk_or_cost": "需要找港口服装样本",
                    "expected_answer_type": "确认纤维属于哪类服装",
                }
            ],
        },
        "archive_patch": {"thread_progress_add": []},
    }

    merged = merge_archive_patch(
        archive,
        simulation["archive_patch"],
        {},
        simulation_payload=simulation,
    )

    assert merged["clue_ledger"][0]["clue_id"] == "EVIDENCE_fiber"
    assert merged["clue_ledger"][0]["parent_thread_id"] == "灯塔为何重亮"
    assert "布料纤维" in merged["clue_ledger"][0]["clue_text"]
    assert merged["revelation_board"][0]["evidence_count"] == 1


def test_merge_archive_patch_does_not_duplicate_explicit_clue_ledger_with_evidence():
    archive = {"open_threads": ["灯塔为何重亮"]}
    simulation = {
        "player_facing_response": {
            "observable_evidence": [
                {
                    "evidence_id": "EVIDENCE_fiber",
                    "observation": "窗沿裂缝里卡着一小片深色布料纤维。",
                    "game_use": "raises_question",
                    "linked_thread_id": "灯塔为何重亮",
                }
            ],
        },
        "archive_patch": {
            "clue_ledger_add": [
                {
                    "clue_id": "CLUE_fiber",
                    "parent_thread_id": "灯塔为何重亮",
                    "clue_text": "窗沿裂缝里卡着一小片深色布料纤维。",
                    "status": "observed",
                    "game_use": "raises_question",
                }
            ]
        },
    }

    merged = merge_archive_patch(
        archive,
        simulation["archive_patch"],
        {},
        simulation_payload=simulation,
    )

    assert len(merged["clue_ledger"]) == 1
    assert merged["clue_ledger"][0]["clue_id"] == "CLUE_fiber"


def test_merge_archive_patch_coalesces_thread_progress_by_thread_id():
    archive = {
        "open_threads": [{"id": "THREAD_lamp", "text": "灯塔为什么重亮"}],
        "thread_progress": [
            {
                "linked_thread_id": "THREAD_lamp",
                "new_evidence": ["灯光按固定节奏闪烁"],
                "remaining_unknown": ["谁在控制灯光"],
                "next_verification": ["检查二层窗户"],
            }
        ],
    }

    merged = merge_archive_patch(
        archive,
        {
            "thread_progress_add": [
                {
                    "linked_thread_id": "THREAD_lamp",
                    "new_evidence": ["锁孔有新刮痕"],
                    "remaining_unknown": ["谁在控制灯光", "是否有人从后门进入"],
                    "next_verification": ["检查二层窗户", "比对刮痕工具"],
                }
            ],
            "open_threads_add": [],
        },
        {},
    )

    assert merged["open_threads"] == [{"id": "THREAD_lamp", "text": "灯塔为什么重亮"}]
    assert len(merged["thread_progress"]) == 1
    progress = merged["thread_progress"][0]
    assert progress["linked_thread_id"] == "THREAD_lamp"
    assert progress["new_evidence"] == ["灯光按固定节奏闪烁", "锁孔有新刮痕"]
    assert progress["remaining_unknown"] == ["谁在控制灯光", "是否有人从后门进入"]
    assert progress["next_verification"] == ["检查二层窗户", "比对刮痕工具"]
    assert merged["thread_progress_history"][0]["thread_key"] == "thread_lamp"
    board = merged["revelation_board"][0]
    assert board["thread_id"] == "thread_lamp"
    assert board["evidence_count"] == 2
    assert board["verification_count"] == 2
    assert board["progress_count"] == 1


def test_merge_archive_patch_blocks_hidden_truth_promotion_to_player_layers():
    archive = {
        "do_not_reveal": ["反对派的具体身份"],
        "hidden_truth": ["反对派是安全主管的竞争对手"],
    }

    merged = merge_archive_patch(
        archive,
        {
            "canon_facts_add": [
                "目标技术人员就是反对派特工。",
                "目标技术人员在指控后手指停顿半秒。",
            ],
            "player_known_state_add": [
                "玩家知道目标技术人员就是反对派。",
                "玩家观察到目标技术人员疑似在规避安保视线。",
            ],
            "thread_progress_add": [
                {
                    "thread_id": "THREAD_opposition_activity",
                    "progress": "目标技术人员可能与反对派有关，仍待验证。",
                    "remaining_unknown": "其具体身份和动机",
                    "next_verification": "检查317房间或跟踪目标",
                }
            ],
            "npc_state_updates": [
                {"npc_id": "npc_target", "state": "目标技术人员就是反对派特工"},
                {"npc_id": "npc_guard", "state": "疑似注意到玩家散布的谣言"},
            ],
        },
        {},
    )

    assert "目标技术人员就是反对派特工。" not in merged["canon_facts"]
    assert "目标技术人员在指控后手指停顿半秒。" in merged["canon_facts"]
    assert "玩家知道目标技术人员就是反对派。" not in merged["player_known_state"]
    assert "玩家观察到目标技术人员疑似在规避安保视线。" in merged["player_known_state"]
    assert merged["thread_progress"] == [
        {
            "thread_id": "THREAD_opposition_activity",
            "progress": "目标技术人员可能与反对派有关，仍待验证。",
            "remaining_unknown": "其具体身份和动机",
            "next_verification": "检查317房间或跟踪目标",
        }
    ]
    assert merged["npc_state"] == [{"npc_id": "npc_guard", "state": "疑似注意到玩家散布的谣言"}]
    rejected_values = [item["value"] for item in merged["archive_guard_rejections"]]
    assert "目标技术人员就是反对派特工。" in rejected_values
    assert "玩家知道目标技术人员就是反对派。" in rejected_values


def test_merge_archive_patch_resolves_threads_by_explained_subject_text():
    archive = {
        "open_threads": [
            "谁拥有灯塔的复制钥匙？",
            "灯塔为何重亮",
        ]
    }

    merged = merge_archive_patch(
        archive,
        {
            "open_threads_resolved": [
                "谁拥有灯塔的复制钥匙？——已解决：亨利·斯托克拥有复制钥匙。"
            ]
        },
        {},
    )

    assert merged["open_threads"] == ["灯塔为何重亮"]
    assert merged["resolved_threads"] == [
        "谁拥有灯塔的复制钥匙？——已解决：亨利·斯托克拥有复制钥匙。"
    ]


def test_merge_archive_patch_keeps_uncertain_resolution_open():
    archive = {"open_threads": ["谁在幕后操纵丑闻？"]}

    merged = merge_archive_patch(
        archive,
        {
            "open_threads_resolved": [
                "谁在幕后操纵丑闻？——玩家尚未锁定具体身份。"
            ]
        },
        {},
    )

    assert "resolved_threads" not in merged
    assert "谁在幕后操纵丑闻？" in merged["open_threads"]
    assert "谁在幕后操纵丑闻？——玩家尚未锁定具体身份。" in merged["open_threads"]


def test_read_json_if_exists_returns_none_for_missing_file(tmp_path):
    path = tmp_path / "missing.json"

    assert _read_json_if_exists(path) is None

    path.write_text('{"ok": true}', encoding="utf-8")

    assert _read_json_if_exists(path) == {"ok": True}


def test_multi_turn_summary_markdown_includes_turn_count_pressure_and_verdict():
    turns = [
        {
            "turn_index": 1,
            "player_action": "inspect the back door",
            "simulation": {"player_facing_response": {"narration": "the lock clicks"}},
            "audit": {"verdict": "pass", "scores": {"total": 84}},
            "archive_after": {"pressure_clock": {"label": "tide", "tick": 2, "max": 4}},
        }
    ]
    final_archive = {
        "canon_facts": ["door inspected"],
        "pressure_clock": {"label": "tide", "tick": 2, "max": 4},
    }
    multi_audit = {
        "payload": {
            "verdict": "pass",
            "scores": {"total": 88},
            "short_reason": "continuity held",
        }
    }

    summary = _multi_turn_summary_markdown(turns, final_archive, multi_audit)

    assert "turn_count: 1" in summary
    assert "final_pressure: tide 2/4" in summary
    assert "audit=pass/84" in summary
    assert "verdict: pass" in summary
    assert "total: 88" in summary


def test_suite_row_and_report_extract_runtime_acceptance_metrics(tmp_path):
    run_dir = tmp_path / "run"
    turn_dir = run_dir / "turn_01"
    turn_dir.mkdir(parents=True)
    (run_dir / "multi_turn_audit_report.json").write_text(
        json.dumps(
            {
                "scores": {
                    "continuity": 9,
                    "player_agency_over_time": 10,
                    "archive_consistency": 9,
                    "hidden_truth_safety": 10,
                    "pressure_pacing": 8,
                    "thread_resolution": 7,
                    "narrative_momentum": 8,
                    "playability_over_time": 9,
                    "total": 54,
                },
                "verdict": "pass",
                "archive_conflicts": [],
                "unresolved_threads": ["who has the key"],
                "resolved_threads": ["why the door was locked"],
                "thread_resolution_gaps": [],
                "narrative_or_gameplay_regressions": [],
                "recommended_system_changes": [],
                "short_reason": "continuity held",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "final_archive.json").write_text(
        json.dumps(
            {
                "canon_facts": ["lockpick failed"],
                "player_known_state": ["door is locked"],
                "hidden_truth": ["keeper is inside"],
                "open_threads": ["who has the key"],
                "resolved_threads": ["why the door was locked"],
                "npc_state": [{"npc_id": "NPC_keeper", "state": "alert"}],
                "archive_guard_rejections": [{"reason": "blocked", "value": "secret"}],
                "revelation_board": [
                    {
                        "thread_id": "thread_key",
                        "title": "who has the key",
                        "status": "open",
                        "progress": [{"progress": "lock inspected"}],
                        "evidence": ["fresh scratch"],
                        "verification_paths": [],
                        "evidence_count": 1,
                        "verification_count": 0,
                        "progress_count": 1,
                        "hint_count": 0,
                    },
                    {
                        "thread_id": "weak_thread",
                        "title": "weak",
                        "status": "open",
                        "progress": [],
                        "evidence": [],
                        "verification_paths": [],
                        "evidence_count": 0,
                        "verification_count": 0,
                        "progress_count": 0,
                        "hint_count": 0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "archive.initial.json").write_text(
        json.dumps(
            {
                "revelation_board": [
                    {
                        "thread_id": "thread_key",
                        "title": "who has the key",
                        "status": "open",
                        "progress": [],
                        "evidence": [],
                        "verification_paths": [],
                        "evidence_count": 0,
                        "verification_count": 0,
                        "progress_count": 0,
                    },
                    {
                        "thread_id": "weak_thread",
                        "title": "weak",
                        "status": "open",
                        "progress": [],
                        "evidence": [],
                        "verification_paths": [],
                        "evidence_count": 0,
                        "verification_count": 0,
                        "progress_count": 0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "turns.json").write_text(
        json.dumps(
            [
                {
                    "turn_index": 1,
                    "archive_source": "repair",
                    "repair": {"repair_strategy": {}},
                }
            ]
        ),
        encoding="utf-8",
    )
    (turn_dir / "simulation.response.json").write_text(
        json.dumps(
            {
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "prompt_cache_hit_tokens": 6,
                    "prompt_cache_miss_tokens": 4,
                }
            }
        ),
        encoding="utf-8",
    )
    (turn_dir / "simulation_repaired_report.json").write_text(
        json.dumps(
            {
                "player_facing_response": {
                    "observable_evidence": [
                        {"evidence_id": "E1", "observation": "fresh scratch"}
                    ],
                    "verification_paths": [
                        {"path_id": "V1", "action": "compare scratch"}
                    ],
                },
                "archive_patch": {
                    "thread_progress_add": [
                        {"linked_thread_id": "thread_key", "new_evidence": ["fresh scratch"]}
                    ],
                    "convergence_actions_add": [
                        {
                            "thread_id": "thread_key",
                            "action_type": "scene_entry",
                            "scene_goal": "Enter the service room.",
                            "entry_cost": "Spend a pressure tick.",
                            "success_signal": "The panel lights.",
                            "failure_forward": "The guard arrives with a key.",
                            "map_grid_seed": {
                                "map_id": "service-room",
                                "grid": {"width": 2, "height": 2, "cells": []},
                            },
                        }
                    ],
                },
                "pressure_patch": {"tick_delta": 1},
            }
        ),
        encoding="utf-8",
    )

    row = _suite_row(1, {"id": "harbor", "description": "storm test"}, run_dir)
    report = _suite_report_markdown([row])

    assert row["verdict"] == "pass"
    assert row["total_score"] == 54
    assert row["repair_count"] == 1
    assert row["usage"]["total_tokens"] == 15
    assert row["usage"]["prompt_cache_hit_tokens"] == 6
    assert row["usage"]["prompt_cache_miss_tokens"] == 4
    assert row["usage"]["prompt_cache_hit_ratio"] == 0.6
    assert row["archive_counts"]["open_threads"] == 1
    assert row["archive_counts"]["archive_guard_rejections"] == 1
    assert row["archive_counts"]["revelation_board"] == 2
    assert row["revelation_board_metrics"] == {
        "thread_count": 2,
        "resolved_count": 0,
        "weak_thread_count": 1,
    }
    assert row["initial_revelation_board_metrics"] == {
        "thread_count": 2,
        "resolved_count": 0,
        "weak_thread_count": 2,
    }
    assert row["weak_thread_delta"] == -1
    assert row["thread_resolution"] == 7
    assert row["narrative_momentum"] == 8
    assert row["playability_over_time"] == 9
    assert row["runtime_structure_counts"] == {
        "observable_evidence": 1,
        "verification_paths": 1,
        "thread_progress_add": 1,
        "clue_ledger_add": 0,
        "convergence_actions_add": 1,
        "scene_goal_cards": 1,
        "map_grid_seeds": 1,
        "pressure_ticks": 1,
    }
    assert row["resolved_thread_count"] == 1
    assert "prompt cache：hit 6 / miss 4 / hit_ratio 60.00%" in report
    assert "| 1 | harbor | pass" in report
    assert "| 6 | 4 | 60.00% |" in report
    assert row["thread_resolution_gap_count"] == 0
    assert "Runtime DM 多回合验收套件报告" in report
    assert "harbor" in report
    assert "evidence" in report
    assert "verify" in report
    assert "progress" in report
    assert "goals" in report
    assert "maps" in report
    assert "board" in report
    assert "weak" in report
    assert "weak_delta" in report
    assert "initial_revelation_board_metrics" in report
    assert "guard" in report
    assert "hints" in report
    assert "continuity held" in report
    assert "15" in report
