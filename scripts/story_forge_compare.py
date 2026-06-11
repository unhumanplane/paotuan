#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from astrbot_plugin_auto_trpg_dm.core.deepseek_v4_flash import (
    DEFAULT_DEEPSEEK_API_KEY_ENV as DEFAULT_API_KEY_ENV,
    DEFAULT_DEEPSEEK_BASE_URL as DEFAULT_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL as DEFAULT_MODEL,
    call_chat_completion_or_raise as _deepseek_call_chat_completion,
)

DEFAULT_OUTPUT_DIR = ".story-forge-runs"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_JUDGE_MAX_TOKENS = 4096
REPAIR_POLICY_ALWAYS = "always"
REPAIR_POLICY_HIGH_RISK = "high_risk"
REPAIR_POLICY_NEVER = "never"
REPAIR_POLICY_VALUES = (REPAIR_POLICY_HIGH_RISK, REPAIR_POLICY_ALWAYS, REPAIR_POLICY_NEVER)


LEGACY_SCHEMA = {
    "title": "短团名",
    "opening_intro": "玩家可见开场，120-400 字",
    "initial_hook": "第一个可行动钩子",
    "player_guidance": "自然语言行动引导，不要编号菜单",
    "campaign_outline": {
        "act_1": "导火索",
        "act_2": "升级或反转",
        "act_3": "高潮或重大抉择",
    },
    "scene_patch": {
        "summary": "当前场景摘要",
        "current_objective": "玩家当前可感知目标",
        "open_hooks": [{"id": "hook_id", "text": "可见钩子", "status": "open"}],
        "clues": [{"id": "clue_id", "text": "可见线索", "status": "discovered"}],
        "stakes": "当前风险",
        "pressure_clock": {"label": "压力名", "tick": 1, "max": 4, "status": "active"},
    },
    "notes_for_dm": ["只给 DM 的简短执行备注"],
}


STORY_FORGE_SCHEMA = {
    "campaign_diagnosis": {
        "grade": "A/B/C/D",
        "playable_state": "ready / needs_reinforcement / not_ready",
        "core_situation": "谁想要什么，为什么现在必须行动",
        "player_fantasy": "玩家主要体验承诺",
        "biggest_gaps": [],
        "dangerous_misdevelopment": [],
    },
    "campaign_director_pack": {
        "core_conflict": "",
        "non_negotiables": [],
        "player_agency_contract": [],
        "forbidden_drift": [],
        "clue_policy": "",
        "failure_policy": "失败必须推进局势",
        "pressure_policy": "",
        "canon_priority": [],
    },
    "playable_scene_cards": [
        {
            "scene_id": "stable_scene_id",
            "title": "场景名",
            "dramatic_task": "本场叙事任务",
            "player_objective": "玩家当前目标",
            "interactables": [
                {
                    "id": "object_id",
                    "verbs": ["inspect", "negotiate", "force"],
                    "feedback": "玩家互动后可见/可听/可追问的变化",
                }
            ],
            "npc_agendas": [
                {
                    "npc_id": "NPC_id",
                    "wants": "",
                    "fears": "",
                    "will_do_if_ignored": "",
                    "interaction_triggers": [],
                }
            ],
            "clue_channels": [
                {
                    "clue_id": "CLUE_id",
                    "routes": ["观察", "询问", "追踪"],
                    "fail_forward": True,
                }
            ],
            "pressure_clock": {
                "label": "压力名",
                "tick": 1,
                "max": 4,
                "tick_triggers": [],
            },
            "success_paths": [],
            "partial_success_costs": [],
            "failure_forward": [],
            "exit_conditions": [],
            "next_scene_candidates": [],
            "map_or_topology_hint": "可选：空间、社交或网络节点关系的一句话提示",
        }
    ],
    "runtime_brief": {
        "current_objective": "",
        "active_pressure": "",
        "available_clues": [],
        "do_not_reveal": [],
        "continuity_warnings": [],
    },
    "session_archive_plan": {
        "canon_facts_to_track": [],
        "player_known_state": [],
        "hidden_truth": [],
        "open_threads": [],
    },
    "campaign_assets": {
        "characters": [{"asset_id": "NPC_x", "name": "", "continuity_locks": []}],
        "locations": [{"asset_id": "LOC_x", "name": "", "continuity_locks": []}],
        "props": [{"asset_id": "PROP_x", "name": "", "game_functions": []}],
        "clues": [{"asset_id": "CLUE_x", "name": "", "visibility": "player"}],
    },
    "playability_selfcheck": {
        "score": 0,
        "blocking_issues": [],
        "high_priority": [],
        "optimizations": [],
    },
    "player_facing_opening": {
        "title": "",
        "opening_intro": "",
        "player_guidance": "",
    },
}


JUDGE_SCHEMA = {
    "scores": {
        "legacy": {
            "premise_and_tension": 0,
            "player_agency": 0,
            "interactive_gameplay": 0,
            "clue_and_failure_design": 0,
            "npc_motivation": 0,
            "dm_runability": 0,
            "archive_continuity": 0,
            "player_facing_prose": 0,
            "total": 0,
            "short_reason": "",
        },
        "story_forge": {
            "premise_and_tension": 0,
            "player_agency": 0,
            "interactive_gameplay": 0,
            "clue_and_failure_design": 0,
            "npc_motivation": 0,
            "dm_runability": 0,
            "archive_continuity": 0,
            "player_facing_prose": 0,
            "total": 0,
            "short_reason": "",
        },
    },
    "winner": "legacy / story_forge / tie",
    "key_deltas": [],
    "legacy_blockers": [],
    "story_forge_blockers": [],
    "story_forge_upgrade_notes": [],
    "runtime_risks": {
        "hidden_truth_leakage": "",
        "railroading": "",
        "over_structuring": "",
        "archive_gaps": "",
    },
    "best_next_prompt_changes": [],
}


REVISION_SCHEMA = {
    "revision_strategy": {
        "kept_strengths": [],
        "fixed_blockers": [],
        "intentional_non_changes": [],
    },
    "revised_story_forge": STORY_FORGE_SCHEMA,
    "revision_selfcheck": {
        "judge_notes_addressed": [],
        "remaining_risks": [],
        "dm_readiness_delta": "",
    },
}


REVISION_COMPARE_SCHEMA = {
    "winner": "draft_1 / revision / tie",
    "adoption_recommendation": "adopt_revision / keep_draft_1 / merge_selectively",
    "estimated_delta": {
        "premise_and_tension": 0,
        "player_agency": 0,
        "interactive_gameplay": 0,
        "clue_and_failure_design": 0,
        "npc_motivation": 0,
        "dm_runability": 0,
        "archive_continuity": 0,
        "player_facing_prose": 0,
        "overall": 0,
    },
    "improvements": [],
    "regressions": [],
    "best_elements_to_keep_from_draft_1": [],
    "best_elements_to_keep_from_revision": [],
    "next_revision_policy": [],
    "short_verdict": "",
}


SIMULATION_SCHEMA = {
    "player_facing_response": {
        "scene_title": "",
        "narration": "",
        "immediate_feedback": [],
        "observable_evidence": [
            {
                "evidence_id": "EVIDENCE_x",
                "observation": "玩家可感知到的表象证据",
                "game_use": "supports / contradicts / complicates / raises_question",
                "linked_thread_id": "THREAD_x",
            }
        ],
        "verification_paths": [
            {
                "path_id": "VERIFY_x",
                "action": "可执行验证行动",
                "target": "证据、NPC、地点或资产",
                "risk_or_cost": "时间、压力、暴露、资源或社交代价",
                "expected_answer_type": "会确认哪类可感知信息，而不是直接剧透 hidden_truth",
            }
        ],
        "available_actions": [],
    },
    "dm_private": {
        "matched_scene_ids": [],
        "triggered_npc_behaviors": [],
        "triggered_clues": [],
        "failure_forward_applied": [],
        "do_not_reveal_checked": [],
    },
    "archive_patch": {
        "canon_facts_add": [],
        "player_known_state_add": [],
        "hidden_truth_add": [],
        "open_threads_add": [],
        "open_threads_resolved": [],
        "thread_progress_add": [],
        "clue_ledger_add": [
            {
                "clue_id": "CLUE_LEDGER_x",
                "parent_thread_id": "THREAD_x",
                "parent_thread_ids": ["THREAD_x", "optional_related_THREAD_y"],
                "clue_text": "次级线索、样本、边缘疑问或可复用证据",
                "status": "observed / sampled / verified / spent",
                "game_use": "supports / contradicts / complicates / raises_question / leverage",
                "next_verification": "这条线索下一步如何验证或用于施压",
            }
        ],
        "convergence_actions_add": [
            {
                "thread_id": "THREAD_x",
                "action_type": "synthesis / core_lead / scene_entry / npc_move / pressure_consequence",
                "synthesis": "What the accumulated clues now let the players reasonably connect without revealing hidden truth.",
                "next_scene_entry": "Concrete scene, route, NPC, room, or social entry point opened by this turn.",
                "npc_move": "Observable proactive NPC behavior, if any.",
                "pressure_or_consequence": "Visible cost, pressure, clock movement, or fictional consequence.",
                "available_action": "A natural next action that can move this thread toward payoff.",
                "scene_goal": "Concrete player-facing objective opened by this convergence; not a forced choice.",
                "entry_cost": "Time, resource, social, risk, or positional cost to enter or pursue it.",
                "success_signal": "Observable result that tells players they made progress.",
                "failure_forward": "What happens if players fail, delay, or back off; play still advances.",
                "map_grid_seed": {
                    "map_id": "optional-next-scene-map",
                    "title": "Optional player-safe map title",
                    "grid": {
                        "width": 6,
                        "height": 5,
                        "cells": [],
                        "entities": [],
                        "doors": [],
                        "labels": [],
                    },
                },
            }
        ],
        "npc_state_updates": [],
        "asset_state_updates": [],
    },
    "pressure_patch": {
        "clock_id": "",
        "tick_delta": 0,
        "new_tick": 0,
        "trigger": "",
        "visible_effect": "",
    },
    "runtime_selfcheck": {
        "hidden_truth_leaked": False,
        "railroading_risk": "low / medium / high",
        "failure_forward_used": False,
        "archive_updated": False,
        "notes": [],
    },
}


SIMULATION_REPAIR_SCHEMA = {
    "repair_strategy": {
        "audit_issues_addressed": [],
        "kept_player_facing_response": True,
        "intentional_non_changes": [],
    },
    "repaired_simulation": SIMULATION_SCHEMA,
    "repair_selfcheck": {
        "archive_gaps_closed": [],
        "continuity_risks": [],
        "new_hidden_truth_leak_risk": False,
    },
}


SIMULATION_AUDIT_SCHEMA = {
    "scores": {
        "player_agency": 0,
        "fictional_positioning": 0,
        "failure_forward": 0,
        "hidden_truth_safety": 0,
        "archive_quality": 0,
        "dm_usability": 0,
        "narrative_payoff": 0,
        "sensory_feedback": 0,
        "actionable_next_steps": 0,
        "total": 0,
    },
    "verdict": "pass / needs_revision / fail",
    "leaks": [],
    "railroading_flags": [],
    "archive_gaps": [],
    "missed_gameplay_opportunities": [],
    "narrative_issues": [],
    "recommended_runtime_policy_changes": [],
    "evidence": {
        "positive": [],
        "negative": [],
    },
    "short_reason": "",
}


MULTI_TURN_AUDIT_SCHEMA = {
    "scores": {
        "continuity": 0,
        "player_agency_over_time": 0,
        "failure_forward_over_time": 0,
        "archive_consistency": 0,
        "hidden_truth_safety": 0,
        "pressure_pacing": 0,
        "thread_resolution": 0,
        "narrative_momentum": 0,
        "playability_over_time": 0,
        "total": 0,
    },
    "verdict": "pass / needs_revision / fail",
    "continuity_breaks": [],
    "archive_conflicts": [],
    "unresolved_threads": [],
    "resolved_threads": [],
    "thread_resolution_gaps": [],
    "narrative_or_gameplay_regressions": [],
    "good_continuity_examples": [],
    "recommended_system_changes": [],
    "short_reason": "",
}


RUBRIC = [
    {
        "id": "opening",
        "label": "玩家可见开场完整",
        "weight": 10,
        "kind": "text_keys",
        "keys": ("opening_intro", "initial_hook", "player_guidance"),
    },
    {
        "id": "objective",
        "label": "明确当前目标",
        "weight": 10,
        "kind": "key_text",
        "keys": ("current_objective", "player_objective"),
    },
    {
        "id": "hooks",
        "label": "至少两个开放钩子/线索",
        "weight": 10,
        "kind": "list_count",
        "keys": ("open_hooks", "clues", "clue_channels"),
        "minimum": 2,
    },
    {
        "id": "pressure",
        "label": "存在压力时钟或明确 stakes",
        "weight": 10,
        "kind": "pressure",
    },
    {
        "id": "interactables",
        "label": "场景含可互动对象",
        "weight": 10,
        "kind": "list_count",
        "keys": ("interactables",),
        "minimum": 2,
    },
    {
        "id": "npc_agendas",
        "label": "NPC 有主动议程",
        "weight": 10,
        "kind": "list_count",
        "keys": ("npc_agendas",),
        "minimum": 1,
    },
    {
        "id": "failure_forward",
        "label": "失败能推进局势",
        "weight": 10,
        "kind": "list_count",
        "keys": ("failure_forward", "partial_success_costs"),
        "minimum": 1,
    },
    {
        "id": "secret_layers",
        "label": "区分玩家已知与隐藏真相",
        "weight": 10,
        "kind": "secret_layers",
    },
    {
        "id": "assets",
        "label": "重要资产有稳定 ID",
        "weight": 10,
        "kind": "asset_ids",
        "minimum": 3,
    },
    {
        "id": "selfcheck",
        "label": "包含自检/风险清单",
        "weight": 10,
        "kind": "selfcheck",
    },
]


def build_legacy_messages(seed: str, preference: str = "") -> list[dict[str, str]]:
    system = (
        "你是旧版单体 TRPG DM 开场生成器。你要模拟当前项目较传统的实现方式："
        "由一个 DM 一次性补齐开场介绍、玩家行动引导、三段式以上剧情骨架和公开 scene_patch。"
        "重点生成能直接开场的内容，但不要引入编剧/记录员/资产库等新架构。"
        "只输出一个合法 JSON 对象，不要 Markdown，不要代码块。"
    )
    user = (
        "请基于同一个跑团种子生成旧版剧情开场，用于和新版 Story Forge 输出做 A/B 对比。\n\n"
        f"跑团种子：\n{seed.strip()}\n\n"
        f"风格偏好：\n{preference.strip() or '未指定，按种子保守补齐。'}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可以补充但不要删除：\n"
        + json.dumps(LEGACY_SCHEMA, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_story_forge_messages(seed: str, preference: str = "") -> list[dict[str, str]]:
    system = (
        "你是 paotuan Story Forge 剧情工坊测试器。你不是实时 DM，"
        "而是开场前的编剧/叙事架构生成器。请把影视剧本锻造流程改造成跑团可玩局势："
        "先诊断，再给导演包，再给可玩场景卡、运行时 brief、留档计划、资产表和游戏性质检。"
        "不要写固定玩家路线；写世界、NPC、线索、压力和失败推进如何响应玩家。"
        "只输出一个合法 JSON 对象，不要 Markdown，不要代码块。"
    )
    user = (
        "请基于同一个跑团种子生成新版 Story Forge 剧情方案，用于和旧版开场输出做 A/B 对比。\n\n"
        f"跑团种子：\n{seed.strip()}\n\n"
        f"风格偏好：\n{preference.strip() or '未指定，按种子保守补齐。'}\n\n"
        "硬约束：\n"
        "- 编剧层和叙事 DM 分离；本输出不直接替玩家做选择。\n"
        "- 每个场景卡必须有玩家目标、可互动对象、NPC 议程、线索路径、压力和失败推进。\n"
        "- canon_facts、player_known_state、hidden_truth 必须分层。\n"
        "- 重要 NPC、地点、道具、线索必须有稳定资产 ID。\n"
        "- 心理和气氛要转成可观察、可追问、可操作的动作、物件、空间或声音。\n\n"
        "质量要求：\n"
        "- NPC 不只写动机，还要写可触发的行为反馈；例如玩家调查某物时，NPC 如何阻拦、误导、求助或改变位置。\n"
        "- 压力时钟优先使用一个主时钟；如需要副时钟，必须说明触发条件，避免机制过载。\n"
        "- 复杂场景必须给 map_or_topology_hint：实体空间、社交关系或网络节点都可以，用于帮助 DM 快速跑场。\n"
        "- player_facing_opening 只能包含玩家可感知信息，不得泄露 hidden_truth；语言要有现场感，避免说明书和编号菜单感。\n"
        "- 每个失败推进都必须带来新局面、新代价或新线索，而不是简单阻断。\n\n"
        "输出必须严格符合这个 JSON 结构，字段可以补充但不要删除：\n"
        + json.dumps(STORY_FORGE_SCHEMA, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_judge_messages(
    *,
    seed: str,
    preference: str,
    legacy_payload: dict[str, Any] | None,
    legacy_text: str,
    forge_payload: dict[str, Any] | None,
    forge_text: str,
) -> list[dict[str, str]]:
    legacy_output = _model_output_for_judge(legacy_payload, legacy_text)
    forge_output = _model_output_for_judge(forge_payload, forge_text)
    system = (
        "你是专业 TRPG 剧本开发监制和实战 DM 评审。你的任务不是奖励字段完整，"
        "而是判断一个方案是否真的能提升跑团剧本水平和游戏性。请严格区分玩家可见信息、"
        "DM 后台信息、可执行场景工具和留档连续性。只输出合法 JSON 对象。"
    )
    user = (
        "请对同一跑团种子的旧版输出和新版 Story Forge 输出做专业 A/B 评审。\n\n"
        "评分原则：\n"
        "- 每个子项 0-10 分，total 为 0-100 的综合分，不要因为格式复杂就自动高分。\n"
        "- 玩家主动性优先：玩家是否有多种有效切入点，而不是被剧情推着走。\n"
        "- 游戏性优先：对象、NPC、线索、压力、失败推进是否能产生桌面互动。\n"
        "- 剧情专业性优先：冲突是否清楚，潜台词/动机是否能转成可观察行动。\n"
        "- DM 可执行性优先：实时 DM 拿到后是否知道下一场如何响应玩家。\n"
        "- 留档连续性优先：是否能沉淀 canon、player_known、hidden_truth、open_threads。\n"
        "- 玩家可见开场不能泄露 hidden truth，也不能出现明显编号菜单式铁路感。\n\n"
        f"跑团种子：\n{seed.strip()}\n\n"
        f"风格偏好：\n{preference.strip() or '未指定'}\n\n"
        "旧版输出：\n"
        f"{legacy_output}\n\n"
        "新版 Story Forge 输出：\n"
        f"{forge_output}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + json.dumps(JUDGE_SCHEMA, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_revision_messages(
    *,
    seed: str,
    preference: str,
    story_forge_payload: dict[str, Any] | None,
    story_forge_text: str,
    judge_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    story_forge_output = _model_output_for_judge(story_forge_payload, story_forge_text)
    judge_output = _model_output_for_judge(judge_payload, "")
    system = (
        "你是 paotuan Story Forge 的二稿编剧。你不是实时 DM，也不是重新抽一个新故事。"
        "你的任务是基于一稿 Story Forge 和专业评审报告做定向二稿：保留一稿优点，修正评审指出的可玩性、"
        "执行性、开场文笔、NPC 行为触发、压力机制和留档风险。只输出合法 JSON 对象。"
    )
    user = (
        "请把下面的一稿 Story Forge 修成二稿。\n\n"
        "二稿原则：\n"
        "- 不推倒重来；保留原始种子的核心冲突、风格和一稿中已经有效的可玩结构。\n"
        "- 高分项保护：如果专业评审中 Story Forge 某项已达到 8 分以上，只能做轻量增强，不能重写其核心设计。\n"
        "- 小修优先：优先补缺口、压缩噪音、增强触发反馈；不要为了显得完整而扩写过多场景或机制。\n"
        "- 优先修复 judge_report 中的 story_forge_blockers、story_forge_upgrade_notes、runtime_risks、best_next_prompt_changes。\n"
        "- NPC 必须有更具体的 interaction_triggers 和可观察行为反馈。\n"
        "- 压力机制必须更适合桌面执行：优先一个主时钟，副时钟必须有必要性和触发条件。\n"
        "- 复杂场景必须补 map_or_topology_hint，帮助 DM 快速理解空间、社交或网络节点。\n"
        "- player_facing_opening 要更有现场感，但仍只能包含玩家可感知信息，不泄露 hidden_truth。\n"
        "- 失败推进必须变成新局面、新代价或新线索，不要写成阻断。\n"
        "- revision_strategy 必须说明保留了什么、修了什么、哪些没改以及为什么。\n\n"
        f"跑团种子：\n{seed.strip()}\n\n"
        f"风格偏好：\n{preference.strip() or '未指定'}\n\n"
        "一稿 Story Forge：\n"
        f"{story_forge_output}\n\n"
        "专业评审报告：\n"
        f"{judge_output}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + json.dumps(REVISION_SCHEMA, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_revision_compare_messages(
    *,
    seed: str,
    preference: str,
    draft_payload: dict[str, Any] | None,
    draft_text: str,
    revision_payload: dict[str, Any] | None,
    revision_text: str,
    judge_payload: dict[str, Any] | None,
) -> list[dict[str, str]]:
    draft_output = _model_output_for_judge(draft_payload, draft_text)
    revision_output = _model_output_for_judge(revision_payload, revision_text)
    judge_output = _model_output_for_judge(judge_payload, "")
    system = (
        "你是 TRPG 剧本二稿审读。请只比较 Story Forge 一稿和二稿，不要再和旧版比较。"
        "重点判断二稿是否真的更适合跑团，而不是是否更长、更完整。只输出合法 JSON 对象。"
    )
    user = (
        "请比较 Story Forge 一稿和二稿，给出是否采用二稿的专业建议。\n\n"
        "审读原则：\n"
        "- 二稿必须保护一稿已经高分的部分；如果二稿修掉小问题但损害玩家主动性、DM清晰度或开场节奏，应标为 regression。\n"
        "- 优先采用小修、选择性合并，不鼓励为追求完整而信息过载。\n"
        "- estimated_delta 用 -3 到 +3 表示二稿相对一稿的变化，0 表示基本持平。\n"
        "- adoption_recommendation 只能是 adopt_revision、keep_draft_1、merge_selectively。\n\n"
        f"跑团种子：\n{seed.strip()}\n\n"
        f"风格偏好：\n{preference.strip() or '未指定'}\n\n"
        "一稿专业评审：\n"
        f"{judge_output}\n\n"
        "Story Forge 一稿：\n"
        f"{draft_output}\n\n"
        "Story Forge 二稿：\n"
        f"{revision_output}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + json.dumps(REVISION_COMPARE_SCHEMA, ensure_ascii=False, indent=2)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_simulation_messages(
    *,
    story_payload: dict[str, Any],
    player_actions: str,
    archive_state: dict[str, Any] | None = None,
    revelation_focus: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    focus = revelation_focus if isinstance(revelation_focus, dict) else _revelation_focus_from_archive(archive_state or {})
    story_payload = _story_payload_runtime_context(story_payload)
    archive_prompt = _archive_prompt_context(archive_state or {})
    system = (
        "你是 paotuan Runtime DM 模拟器。你不是编剧层，不改写剧本大纲；"
        "你只根据 Story Forge 方案和玩家行动运行当前回合。必须分离玩家可见回应和 DM 私有留档。"
        "不得替玩家做选择，不得泄露 hidden_truth/do_not_reveal。"
        "玩家试图直接套问秘密时，不能回答 true/false，也不能按玩家指定信号确认真相；"
        "要把问题转化为可感知表象、压力、代价和可验证下一步。只输出合法 JSON 对象。"
    )
    stable_contract = (
        "请根据 Story Forge 方案模拟一次玩家行动后的 Runtime DM 响应，并生成留档补丁。\n\n"
        "缓存友好说明：以下运行原则、Story Forge 方案和输出 schema 是稳定前缀；具体 archive、Revelation Focus 与玩家行动放在末尾。不要因为顺序变化而忽略末尾的当前回合输入。\n\n"
        "运行原则：\n"
        "- 玩家可见回应只能描述角色能感知到的事实、声音、物件、NPC 行为和当前可行动方向。\n"
        "- 如果玩家失败或绕开预设路线，必须使用 failure_forward：产生新局面、新代价或新线索。\n"
        "- archive_patch 必须只写增量，不能重写整个存档。\n"
        "- canon_facts_add 记录桌面上已经发生的事实。\n"
        "- player_known_state_add 记录玩家已经知道或可合理确认的信息。\n"
        "- hidden_truth_add 只给 DM，不得出现在 player_facing_response。\n"
        "- open_threads_add/resolved 记录待解决和已解决的剧情线。\n"
        "- 当玩家用“是不是/如果是就眨灯/直接告诉我”等方式套问 hidden_truth/do_not_reveal：player_facing_response 必须明确回应这次尝试，但只能给 observable non-confirmation（可见但不确认真相的表象）、NPC/环境压力反馈、必要的代价或压力变化，以及至少一个 concrete verification path（可验证下一步）。\n"
        "- 不要让世界按玩家指定的确认信号配合泄密；例如“眨灯”“眼神飘向某处”只能作为自然干扰、反常表象或误导风险，不能成为真相确认。\n"
        "- 玩家要求灯光、眼神、动作按秘密真相给信号时，不要写“灯光短暂暗淡/闪烁一瞬/刚好变化”等准确认信号；除非已有公开物理原因，否则表象应明确为不响应、固定周期、环境噪音、无关干扰或需要进一步验证的非定向变化。\n"
        "- 玩家提出错误理论时，不要平铺直叙判定“对/错”；应给 counter-evidence、partial support、异常缺口或一个可测试的新线索，让错误理论也产生游戏价值。\n"
        "- 不要把不可感知的隐藏主体拟人化；除非玩家能观察到行动来源，否则避免“灯光嘲笑你/实体回应你”这类暗示能动性的表述，改用中性的物理现象、NPC反应或道具变化。\n"
        "- player_facing_response.immediate_feedback 至少包含一种行动后果：线索推进、环境变化、NPC反应、压力变化、资源/时间代价或新风险。\n"
        "- player_facing_response.observable_evidence 必须列出 1-3 条本回合新出现或被确认的可感知证据，并说明它支持、反驳、复杂化或提出了哪个问题；证据只写表象，不写隐藏结论。\n"
        "- player_facing_response.verification_paths 必须列出 1-3 条具体可执行验证路径，每条要包含目标和风险/代价；路径要能帮助玩家验证表象证据，而不是让 DM 直接揭晓答案。\n"
        "- available_actions 给 2-4 个自然下一步方向，分别关联现场证据、NPC、资产或压力钟；不要写成强制编号菜单或唯一正确路线。\n"
        "- 如果本回合只得到表象证据，player_known_state_add 只能记录“玩家观察到什么”，不能记录 hidden_truth 结论；open_threads_add 应记录仍待验证的问题，thread_progress_add 应记录既有线程被怎样推进、还缺什么，避免把未确认推论写成 resolved。\n"
        "- thread_progress_add 必须优先用 archive_state.open_threads 里的原文或稳定 ID 作为 linked_thread/thread_id；不要随手创造 THREAD_1/THREAD_2。若确实是新线程，必须在同一回合 open_threads_add 定义，并说明为什么不能挂到已有主线。\n"
        "- Revelation Focus 是调查健康度提示，不是剧情强制跳转；它只提醒 DM 哪些开放疑问缺少 evidence/progress/verification。\n"
        "- 如果玩家行动可合理关联 Revelation Focus.priority_thread，本回合至少把 1 条 observable_evidence 或 verification_path 通过 linked_thread_id / linked_thread 挂到该 thread；优先补强弱线程，而不是继续开新坑。\n"
        "- 如果玩家行动与 priority_thread 明显无关，不得强行牵引、不得让无关线索突然出现；保持玩家主动性，只在 runtime_selfcheck.notes 说明本回合未使用 Revelation Focus。\n"
        "- 如果 Revelation Focus.convergence_thread 不为空，说明该线程已经积累足够线索；本回合不要只继续堆 clue_ledger。若玩家行动相关，必须给出至少一种收束性推进：core lead（新场景/NPC/入口）、thread_progress_add.synthesis、压力升级、NPC主动动作、或可确认但不剧透的阶段性结论。\n"
        "- 当 current_revelation_focus.convergence_thread 不为空，或现有 open_thread 已有多条 evidence/verification 但 progress 很少时，archive_patch.convergence_actions_add 优先级高于新增 clue_ledger；至少给一张可跑的下一场目标卡。\n"
        "- 收束性推进仍要尊重玩家行动：用玩家当前检查到的证据触发下一步，而不是替玩家做决定。available_actions 至少给一个能把 convergence_thread 推向下一场景或关键NPC的选项。\n"
        "- 如果玩家检查、推断、追踪某个 interactable、clue_channel 或既有已知事实，dm_private.triggered_clues 必须写出匹配到的 clue_id/线索名；如果没有匹配，必须在 runtime_selfcheck.notes 说明原因。\n"
        "- open_threads_add 有预算：每回合最多新增 1 条真正新线程；优先把细节线索写入 thread_progress_add 挂到既有主线程。只有出现新的行动目标、新风险主体或新地点/资产入口时，才新增 open_thread。\n"
        "- 布料纤维、金属碎屑、脚印、样本、传闻碎片这类次级线索，默认写入 archive_patch.clue_ledger_add，并用 parent_thread_id 挂到最相关 open_thread；除非它打开了新的地点/NPC/风险主体，否则不要升级成 open_threads_add。\n"
        "- clue_ledger_add 是可复用证据台账：每条要写 clue_text、status、game_use、next_verification。它服务于玩家后续验证、比对、施压和换取信息，不是新的主线坑。\n"
        "- 如果某条次级线索既推进当前主谜题，也合理连接 Revelation Focus.priority_thread，可以写 parent_thread_ids 同时挂两个父线程；例如人影、钥匙/锁、布料纤维、旧维护痕迹、照片、日志、老莫相关传闻可同时连接“灯塔为何重亮”和“十年前管理员失踪”。\n"
        "- 如果玩家发现新的可行动疑问，open_threads_add 不得为空；如果只是推进旧疑问，写 thread_progress_add 而不是重复新增同义 open_threads；如果某个疑问已被确认解决，必须写 open_threads_resolved。\n"
        "- npc_state_updates/asset_state_updates 必须使用稳定 npc_id/asset_id；同一 ID 只写当前最新状态，不要生成互相覆盖的重复状态。\n"
        "- pressure_clock 是桌面节奏工具，不是装饰。以下行动通常应让 pressure_patch.tick_delta=1：公开指认/栽赃/威胁 NPC、非法黑入或监听、触碰高风险法器/封印/机关、制造噪音或暴露、失败或部分成功、花费显著时间继续调查、直接向秘密施压导致 NPC/环境警觉。只有行动短暂且低风险、没有时间流逝、没有 NPC/环境反应时才写 tick_delta=0。\n"
        "- pressure_patch.clock_id 必须沿用当前 archive_state.pressure_clock 的 label/clock_id；不得临时创造“雾锁灯塔/安保升级2”等新名字。若 Story Forge 和 archive 名称不同，以 archive_state 当前时钟为准。\n"
        "- pressure_patch.tick_delta=1 时，visible_effect 必须写玩家能感知的节奏变化，例如安保靠近、频道静默、仪式钟声、灵压升高、NPC戒备、雨势/灯光/人群变化；不要只写“压力增加”。\n"
        "- pressure_patch 如果没有推进，也要说明 tick_delta=0 的具体理由，并在 verification_paths 或 available_actions 中给出会触发压力的高风险选择。\n"
        "- runtime_selfcheck 必须诚实检查是否泄密、是否铁路化、是否更新留档。\n\n"
        "Runtime convergence contract:\n"
        "- If this turn reasonably synthesizes an existing thread into a concrete next scene, NPC move, core lead, or consequence, write archive_patch.convergence_actions_add.\n"
        "- If convergence opens a playable next step, write scene_goal as a short string and put entry_cost, success_signal, failure_forward beside it as sibling fields.\n"
        "- If the next step benefits from spatial clarity, include a player-safe map_grid_seed render seed; it is not map authority and must not include hidden_truth.\n"
        "- If current_revelation_focus contains convergence_thread and the player action is reasonably related, archive_patch.convergence_actions_add should contain at least one action.\n"
        "- Each convergence action needs thread_id, action_type, and at least one of synthesis, next_scene_entry, npc_move, pressure_or_consequence, available_action, scene_goal.\n"
        "- Treat scene_goal as an executable scene-goal card: scene_goal must be a short string objective; entry_cost, success_signal, and failure_forward must be sibling fields, not nested inside scene_goal.\n"
        "- Prefer moving toward a concrete scene/NPC/pressure payoff over adding more minor clue_ledger items.\n"
        "- Do not use convergence_actions_add to force resolution, force the player choice, or reveal hidden_truth.\n\n"
        "当前 Story Forge 方案：\n"
        f"{_prompt_json(story_payload)}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        f"{_prompt_json(SIMULATION_SCHEMA)}\n\n"
    )
    current_turn = (
        "当前回合输入（变量区，从这里开始通常不参与高命中前缀）：\n"
        "current_archive_state:\n"
        f"{_prompt_json(archive_prompt)}\n\n"
        "current_revelation_focus:\n"
        f"{_prompt_json(focus)}\n\n"
        "player_action:\n"
        f"{player_actions.strip()}\n"
    )
    user = stable_contract + current_turn
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _story_payload_runtime_context(story_payload: dict[str, Any]) -> dict[str, Any]:
    """Keep runtime prompts focused on runnable scene facts."""
    if not isinstance(story_payload, dict):
        return {}
    if story_payload.get("runtime_context") and isinstance(story_payload.get("runtime_context"), dict):
        return copy.deepcopy(story_payload["runtime_context"])
    context: dict[str, Any] = {
        "runtime_context_version": 1,
    }
    for key in (
        "campaign_director_pack",
        "runtime_brief",
        "session_archive_plan",
        "player_facing_opening",
    ):
        value = story_payload.get(key)
        if value not in (None, "", [], {}):
            context[key] = copy.deepcopy(value)

    scene_cards = _compact_scene_cards(story_payload.get("playable_scene_cards"))
    if scene_cards:
        context["playable_scene_cards"] = scene_cards

    assets = _compact_campaign_assets(story_payload.get("campaign_assets"))
    if assets:
        context["campaign_assets"] = assets

    diagnosis = story_payload.get("campaign_diagnosis")
    if isinstance(diagnosis, dict):
        context["campaign_diagnosis"] = {
            key: copy.deepcopy(diagnosis[key])
            for key in ("playable_state", "core_situation", "player_fantasy", "biggest_gaps")
            if diagnosis.get(key) not in (None, "", [], {})
        }

    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


def _compact_scene_cards(value: Any, *, limit: int = 4) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for scene in _as_list(value):
        if not isinstance(scene, dict):
            continue
        card: dict[str, Any] = {}
        for key in (
            "scene_id",
            "title",
            "dramatic_task",
            "player_objective",
            "pressure_clock",
            "success_paths",
            "partial_success_costs",
            "failure_forward",
            "exit_conditions",
            "next_scene_candidates",
            "map_or_topology_hint",
        ):
            if scene.get(key) not in (None, "", [], {}):
                card[key] = copy.deepcopy(scene[key])
        for key in ("interactables", "npc_agendas", "clue_channels"):
            items = _compact_named_items(scene.get(key), limit=6)
            if items:
                card[key] = items
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards


def _compact_named_items(value: Any, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    keep_keys = (
        "id",
        "asset_id",
        "npc_id",
        "clue_id",
        "name",
        "title",
        "verbs",
        "feedback",
        "wants",
        "fears",
        "will_do_if_ignored",
        "interaction_triggers",
        "routes",
        "fail_forward",
        "game_functions",
        "continuity_locks",
        "visibility",
    )
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        compact = {
            key: copy.deepcopy(item[key])
            for key in keep_keys
            if item.get(key) not in (None, "", [], {})
        }
        if compact:
            items.append(compact)
        if len(items) >= limit:
            break
    return items


def _compact_campaign_assets(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("characters", "locations", "props", "clues"):
        items = _compact_named_items(value.get(key), limit=8)
        if items:
            result[key] = items
    return result


def _archive_prompt_context(archive: dict[str, Any]) -> dict[str, Any]:
    """Keep prompts focused while preserving the full archive on disk."""
    if not isinstance(archive, dict):
        return {}
    keep: dict[str, Any] = {
        "prompt_context_version": 1,
    }
    scalar_keys = ("pressure_clock", "summary", "current_objective", "current_conflict", "stakes")
    for key in scalar_keys:
        value = archive.get(key)
        if value not in (None, "", [], {}):
            keep[key] = copy.deepcopy(value)

    list_limits = {
        "canon_facts": 12,
        "player_known_state": 16,
        "hidden_truth": 8,
        "open_threads": 12,
        "resolved_threads": 8,
        "thread_hints": 8,
        "thread_progress": 12,
        "clue_ledger": 16,
        "convergence_actions": 8,
        "npc_state": 8,
        "asset_state": 8,
        "pressure_history": 6,
        "revelation_board": 12,
    }
    for key, limit in list_limits.items():
        items = _compact_archive_list(archive.get(key), limit=limit)
        if items:
            keep[key] = items
    return {key: value for key, value in keep.items() if value not in (None, "", [], {})}


def _compact_archive_list(value: Any, *, limit: int) -> list[Any]:
    items = _as_list(value)
    compacted: list[Any] = []
    for item in items[-limit:]:
        compacted.append(_compact_archive_item(item))
    return [item for item in compacted if item not in (None, "", [], {})]


def _compact_archive_item(value: Any) -> Any:
    if isinstance(value, str):
        return value[:600]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    keep_keys = (
        "id",
        "thread_id",
        "linked_thread",
        "linked_thread_id",
        "title",
        "text",
        "status",
        "source",
        "summary",
        "new_evidence",
        "remaining_unknown",
        "remaining_questions",
        "next_verification",
        "synthesis",
        "clue_id",
        "clue_text",
        "parent_thread_id",
        "parent_thread_ids",
        "game_use",
        "action_id",
        "action_type",
        "scene_goal",
        "entry_cost",
        "success_signal",
        "failure_forward",
        "available_action",
        "evidence",
        "npc_id",
        "asset_id",
        "state",
        "visible_state",
        "visibility",
        "tick",
        "max",
        "label",
        "clock_id",
        "trigger",
        "visible_effect",
        "evidence_count",
        "progress_count",
        "verification_count",
        "keys",
    )
    compact: dict[str, Any] = {}
    for key in keep_keys:
        if value.get(key) not in (None, "", [], {}):
            compact[key] = copy.deepcopy(value[key])
    if isinstance(value.get("map_grid_seed"), dict) and value.get("map_grid_seed"):
        compact["map_grid_seed"] = _compact_map_grid_seed(value["map_grid_seed"])
    return compact


def _compact_map_grid_seed(seed: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("map_id", "title", "description"):
        if seed.get(key) not in (None, "", [], {}):
            result[key] = copy.deepcopy(seed[key])
    grid = seed.get("grid")
    if isinstance(grid, dict):
        compact_grid = {
            key: copy.deepcopy(grid[key])
            for key in ("width", "height")
            if grid.get(key) not in (None, "", [], {})
        }
        cells = _as_list(grid.get("cells"))
        if cells:
            compact_grid["cells"] = [_compact_archive_item(item) for item in cells[:40] if isinstance(item, dict)]
        entities = _as_list(grid.get("entities"))
        if entities:
            compact_grid["entities"] = [_compact_archive_item(item) for item in entities[:20] if isinstance(item, dict)]
        if compact_grid:
            result["grid"] = compact_grid
    player_view = seed.get("player_view")
    if isinstance(player_view, dict):
        labels = _as_list(player_view.get("labels"))
        if labels:
            result["player_view"] = {
                "labels": [_compact_archive_item(item) for item in labels[:20] if isinstance(item, dict)]
            }
    return result


def _multi_turn_audit_prompt_context(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompt_turns: list[dict[str, Any]] = []
    for turn in _as_list(turns):
        if not isinstance(turn, dict):
            continue
        simulation = turn.get("repair")
        if isinstance(simulation, dict):
            repaired = _repaired_simulation_payload(simulation)
            if isinstance(repaired, dict):
                simulation = repaired
        if not isinstance(simulation, dict):
            simulation = turn.get("simulation") if isinstance(turn.get("simulation"), dict) else {}
        audit = turn.get("audit") if isinstance(turn.get("audit"), dict) else {}
        archive_after = turn.get("archive_after") if isinstance(turn.get("archive_after"), dict) else {}
        prompt_turns.append(
            {
                "turn_index": turn.get("turn_index"),
                "player_action": turn.get("player_action"),
                "revelation_focus": turn.get("revelation_focus") or {},
                "archive_source": turn.get("archive_source") or "",
                "archive_before": turn.get("archive_before") if isinstance(turn.get("archive_before"), dict) else {},
                "player_facing_response": _compact_player_facing_response(
                    simulation.get("player_facing_response") if isinstance(simulation, dict) else {}
                ),
                "archive_patch": _compact_archive_patch(
                    simulation.get("archive_patch") if isinstance(simulation, dict) else {}
                ),
                "pressure_patch": simulation.get("pressure_patch") if isinstance(simulation, dict) else {},
                "audit_summary": {
                    key: copy.deepcopy(audit[key])
                    for key in (
                        "verdict",
                        "short_reason",
                        "archive_gaps",
                        "thread_resolution_gaps",
                        "missed_gameplay_opportunities",
                        "narrative_issues",
                    )
                    if isinstance(audit, dict) and audit.get(key) not in (None, "", [], {})
                },
                "archive_after": archive_after,
            }
        )
    return prompt_turns


def _compact_player_facing_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in ("narration", "immediate_feedback", "available_actions", "observable_evidence", "verification_paths"):
        if value.get(key) not in (None, "", [], {}):
            result[key] = copy.deepcopy(value[key])
    return result


def _compact_archive_patch(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "canon_facts_add",
        "player_known_state_add",
        "hidden_truth_add",
        "open_threads_add",
        "open_threads_resolved",
        "thread_progress_add",
        "clue_ledger_add",
        "convergence_actions_add",
        "npc_state_updates",
        "asset_state_updates",
    ):
        items = _compact_archive_list(value.get(key), limit=8)
        if items:
            result[key] = items
    return result


def build_simulation_audit_messages(
    *,
    story_payload: dict[str, Any],
    player_actions: str,
    simulation_payload: dict[str, Any] | None,
    simulation_text: str,
) -> list[dict[str, str]]:
    story_payload = _story_payload_runtime_context(story_payload)
    simulation_output = _model_output_for_judge(simulation_payload, simulation_text)
    system = (
        "你是 TRPG Runtime DM 审计员。请检查一次运行模拟是否真正提高游戏性、"
        "是否尊重玩家主动性、是否失败推进、是否留档、是否泄露 hidden truth。只输出合法 JSON 对象。"
    )
    user = (
        "请审计下面这次 Runtime DM 模拟。\n\n"
        "审计原则：\n"
        "- 不奖励长文本；奖励清楚、可跑、尊重玩家行动的回应。\n"
        "- 如果玩家失败但剧情没有推进，要扣分。\n"
        "- 如果 player_facing_response 泄露 hidden_truth/do_not_reveal，要判定为严重问题。\n"
        "- 如果 archive_patch 没有沉淀 canon/player_known/open_threads/npc_state，要指出缺口。\n"
        "- 如果 player_facing_response.observable_evidence 为空，或 verification_paths 为空，要扣 narrative_payoff / actionable_next_steps；除非本回合明确是纯结算或结束场。\n"
        "- 如果玩家围绕既有 open_thread 追问或验证，而 archive_patch 既没有 thread_progress_add，也没有 open_threads_resolved，要指出线程推进缺口。\n"
        "- 如果单回合新增超过 1 条 open_threads，或把同一场景里的多个细节分别开成新坑而没有挂到 thread_progress_add，要扣 thread_resolution / archive_quality。\n"
        "- 如果玩家执行公开指认、非法黑入、触碰危险法器/封印、制造噪音、失败推进、长时间调查等高风险行动，而 pressure_patch.tick_delta=0 且没有有力理由，要扣 pressure / narrative_payoff，并在 missed_gameplay_opportunities 中指出。\n"
        "- 如果玩家连续直接向秘密施压（例如要求回答“里面是不是活人”），且 Runtime DM 只给线索但没有压力变化、NPC动作或局面变化，要扣 pressure / narrative_payoff。\n"
        "- 如果 available_actions 变成强制菜单或唯一选择，要标记 railroading。\n\n"
        "游戏性与叙事效果原则：\n"
        "- 即使玩家试图套问 hidden_truth，合格回应也不能只是拒绝；必须给可感知的表象、情绪张力、可验证线索或新的行动切口。\n"
        "- player_facing_response 应该让玩家感到行动产生了后果：环境变化、NPC反应、压力变化、线索推进或代价，至少占其一。\n"
        "- available_actions 必须是自然的下一步方向，不是死板编号菜单；如果没有可行动下一步，要扣 actionable_next_steps。\n"
        "- 如果为了安全而牺牲戏剧反馈、让场景变成空白拒答，要写入 narrative_issues。\n\n"
        "证据要求：\n"
        "- 所有 leaks、railroading_flags、archive_gaps 都必须引用具体 JSON 路径或原文片段。\n"
        "- 如果 archive_patch.player_known_state_add 已经记录了某个事实，不得再把同一事实列为 archive_gaps。\n"
        "- evidence.positive 写通过项证据，evidence.negative 写问题证据；不要凭印象扣分。\n\n"
        "Story Forge 方案：\n"
        f"{_prompt_json(story_payload)}\n\n"
        "玩家行动：\n"
        f"{player_actions.strip()}\n\n"
        "Runtime DM 模拟输出：\n"
        f"{simulation_output}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + _prompt_json(SIMULATION_AUDIT_SCHEMA)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_simulation_repair_messages(
    *,
    story_payload: dict[str, Any],
    player_actions: str,
    archive_state: dict[str, Any],
    simulation_payload: dict[str, Any],
    audit_payload: dict[str, Any],
    revelation_focus: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    story_payload = _story_payload_runtime_context(story_payload)
    archive_prompt = _archive_prompt_context(archive_state)
    focus = revelation_focus if isinstance(revelation_focus, dict) else _revelation_focus_from_archive(archive_state)
    system = (
        "你是 TRPG Runtime 留档修补器。你的任务不是重写剧情，而是根据审计意见最小化修补本回合 simulation 的 "
        "dm_private、archive_patch、pressure_patch、runtime_selfcheck，使下一回合 archive 更连续。"
        "默认保留 player_facing_response，除非它泄露 hidden_truth、明显铁路化，"
        "或审计明确指出它安全但空白、忽略玩家问题、直接判定对错而没有可玩反馈。只输出合法 JSON 对象。"
    )
    user = (
        "请根据审计报告修补这一次 Runtime DM 模拟。\n\n"
        "修补原则：\n"
        "- player_facing_response 默认原样保留；不要为了补档而重新写一段更长的玩家可见叙述。\n"
        "- 只有当 audit.narrative_issues / missed_gameplay_opportunities 明确指出“空白拒答、忽略玩家、直接否定、无反馈、无可验证下一步”时，才可以最小改写 player_facing_response；改写必须不泄露 hidden_truth，只补充可感知表象、后果、代价或 verification path。\n"
        "- 修补玩家可见回应时，不要回答 hidden_truth 的 true/false，不要顺从玩家指定的确认信号；错误理论要转成 counter-evidence / partial support / testable lead。\n"
        "- 如果原始 simulation 写了“灯光短暂暗淡/闪烁一瞬/眼神飘向某处”等容易被玩家理解成秘密确认的准信号，修补时必须改成不响应、固定周期、环境噪音、物理干扰或明确需要验证的非定向表象，并同步修正 canon_facts_add/player_known_state_add。\n"
        "- 如果 observable_evidence 或 verification_paths 缺失，优先补齐这两个字段；不要用泛泛的“继续调查”，要指向证据、NPC、地点、资产或压力风险。\n"
        "- archive_patch 必须补齐玩家行动结果、玩家直接观察到的信息、已触发线索、NPC/资产状态变化。\n"
        "- 不要把 hidden_truth 泄露到 player_facing_response 或 player_known_state_add。\n"
        "- 修补 missed_gameplay_opportunities 时，只能补玩家可观察到的反应、代价、风险、表象证据或可测试线索；不得为了增加戏剧张力而发明或坐实 do_not_reveal/hidden_truth 中的身份、阵营、动机、幕后关系。\n"
        "- canon_facts_add / player_known_state_add / npc_state_updates / asset_state_updates 只能写桌面已发生或玩家可观察确认的内容；未确认身份必须写成“疑似/表现为/可能在规避/出现异常动作”，不能写成“某人就是反对派/主谋/隐藏实体”。\n"
        "- dm_private 可以记录私有风险和模型推断，但如果该推断来自 hidden_truth/do_not_reveal，不能同步升级到 canon_facts_add 或 player_known_state_add。\n"
        "- open_threads_resolved 只能包含本回合确实被玩家行动解决的线程；误 resolved 必须移除。\n"
        "- 如果审计指出 thread_resolution_gaps，且玩家已经获得足以回答该线程的证据，必须把对应线程写入 open_threads_resolved，并在 canon_facts_add/player_known_state_add 记录答案。\n"
        "- 如果玩家只推进了旧线程但还不足以解决，必须写 thread_progress_add，说明 linked_thread、new_evidence、remaining_unknown、next_verification；不要重复新增同义 open_threads。\n"
        "- 如果审计指出 thread_resolution_gaps，或 archive/revelation_board 显示某线程 evidence/verification 已经不少但 progress/convergence 不足，必须补 archive_patch.convergence_actions_add：给 thread_id、action_type、scene_goal、entry_cost、success_signal、failure_forward、evidence；若有站位/入口/障碍/NPC，补 player-safe map_grid_seed。\n"
        "- 如果原 simulation 单回合新增超过 1 条 open_threads，修补时优先把细节型 open_threads 合并为 thread_progress_add，只保留最重要的一条新行动线程。\n"
        "- 如果原 simulation 把样本、痕迹、纤维、碎屑、脚印、传闻碎片开成 open_thread，但它没有新地点/NPC/风险主体，修补时应改入 clue_ledger_add，并在 parent_thread_id/parent_thread_ids 里挂到既有主线；不要让它丢失。\n"
        "- 修补 clue_ledger_add 时，若审计或 Revelation Focus 指出某个弱线程长期没有证据，优先把已存在且合理相关的线索桥接到该弱线程，而不是编造新事实。\n"
        "- thread_progress_add 必须优先引用 archive_before.open_threads 的原文或稳定 ID；不要产生悬空 THREAD_1/THREAD_3。若原 simulation 已经产生悬空线程，优先改写为 linked_thread 指向最相关的既有主线，而不是新增一堆 open_threads。\n"
        "- 含有“可能”“尚未”“仍不明确”“未锁定”“需要继续确认”等不确定结论的线程不得写入 open_threads_resolved，只能保留在 open_threads_add 或 runtime_selfcheck.notes。\n"
        "- 如果审计指出 pressure_pacing / missed_gameplay_opportunities 中存在高风险行动但 tick_delta=0，优先把 pressure_patch 修为 tick_delta=1，并补一个玩家可见 visible_effect；只有 Story Forge 明确禁止推进或行动确实低风险时才保持 0。\n"
        "- 修补 pressure_patch 时必须沿用 archive_before.pressure_clock 的 label/clock_id，不得重命名压力钟；如果原 simulation 写了别名，只改 tick_delta/new_tick/trigger/visible_effect，保留原时钟身份。\n"
        "- 同一 npc_id/asset_id 只保留最新状态，必要的历史由合并器处理。\n"
        "- 如果审计意见与实际 simulation 冲突，以 JSON 路径证据为准，避免凭印象修补。\n\n"
        "Story Forge 方案：\n"
        f"{_prompt_json(story_payload)}\n\n"
        "本回合 archive_before：\n"
        f"{_prompt_json(archive_prompt)}\n\n"
        "本回合 Revelation Focus：\n"
        f"{_prompt_json(focus)}\n\n"
        "玩家行动：\n"
        f"{player_actions.strip()}\n\n"
        "原始 simulation：\n"
        f"{_prompt_json(simulation_payload)}\n\n"
        "审计报告：\n"
        f"{_prompt_json(audit_payload)}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + _prompt_json(SIMULATION_REPAIR_SCHEMA)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def should_repair_from_audit(
    audit_payload: dict[str, Any] | None,
    *,
    policy: str = REPAIR_POLICY_HIGH_RISK,
) -> tuple[bool, str]:
    normalized_policy = str(policy or REPAIR_POLICY_HIGH_RISK).strip().lower()
    if normalized_policy == REPAIR_POLICY_ALWAYS:
        return True, "repair_policy_always"
    if normalized_policy == REPAIR_POLICY_NEVER:
        return False, "repair_policy_never"
    if not isinstance(audit_payload, dict):
        return False, "audit_payload_missing"

    verdict = str(audit_payload.get("verdict") or "").strip().lower()
    if verdict == "fail":
        return True, "audit_verdict_fail"

    scores = audit_payload.get("scores") if isinstance(audit_payload.get("scores"), dict) else {}
    total = _number_or_none(scores.get("total"))
    if total is not None and total < 78:
        return True, "audit_total_below_78"

    critical_lists = (
        "leaks",
        "railroading_flags",
        "archive_gaps",
        "missed_gameplay_opportunities",
        "narrative_issues",
    )
    nonempty = [key for key in critical_lists if _list_len(audit_payload.get(key))]
    if verdict == "needs_revision" and nonempty:
        return True, "needs_revision_with_" + ",".join(nonempty[:3])

    low_score_keys = (
        "hidden_truth_safety",
        "archive_quality",
        "actionable_next_steps",
        "narrative_payoff",
        "failure_forward",
    )
    low_scores = [
        key
        for key in low_score_keys
        if (_number_or_none(scores.get(key)) is not None and _number_or_none(scores.get(key)) < 7)
    ]
    if low_scores:
        return True, "low_audit_scores:" + ",".join(low_scores[:3])

    return False, "audit_passed_or_low_risk"


def build_multi_turn_audit_messages(
    *,
    story_payload: dict[str, Any],
    turns: list[dict[str, Any]],
    final_archive: dict[str, Any],
) -> list[dict[str, str]]:
    story_payload = _story_payload_runtime_context(story_payload)
    turns_prompt = _multi_turn_audit_prompt_context(turns)
    final_archive_prompt = _archive_prompt_context(final_archive)
    system = (
        "你是 TRPG 连续性审计员。请审计多回合 Runtime DM 模拟是否保持 canon、"
        "player_known、hidden_truth、open_threads、NPC 状态和压力钟连续。只输出合法 JSON 对象。"
    )
    user = (
        "请审计下面这组多回合 Runtime DM 模拟。\n\n"
        "审计原则：\n"
        "- 检查每回合 archive_patch 是否正确合并到下一回合 archive。\n"
        "- 检查 player_known_state 是否只包含玩家可知信息，hidden_truth 是否没有泄露到玩家可见回应。\n"
        "- 检查 open_threads 是否有新增、推进或解决，不要只堆积；调查型剧情应优先让线索收束到少数主线程。\n"
        "- 如果每回合持续新增多个 open_threads，而缺少 thread_progress 或 resolved，要降低 thread_resolution 和 archive_consistency。\n"
        "- 外部 archive_file / archive_before 代表当前运行时真实状态，优先级高于 Story Forge 模板里的示例 pressure_clock。若第一回合 archive_before.pressure_clock 已定义 label/clock_id/max，后续沿用它就是正确行为，不要因为和 Story Forge 模板不同而判冲突。\n"
        "- thread_progress 可以推进已有 open_threads；如果引用全新的 thread_id，必须在 open_threads 或 thread_hints 中有对应定义，否则记为 thread_resolution_gaps。\n"
        "- 次级线索如果已进入 final_archive.clue_ledger 或 thread_progress.remaining_questions，并且 parent_thread_id 指向主 open_thread，不要判定为线索丢失；它是被降级为证据台账，而不是主 open_thread。\n"
        "- 当 archive_source=repair 时，以 repair/repaired simulation 与 archive_after 为准；原 simulation 中被修补移除的 open_threads，不应单独记为 thread_resolution_gaps，除非 final_archive 中既没有 clue_ledger，也没有 thread_progress/remaining_questions 保存它。\n"
        "- 检查 thread_progress 是否持续记录旧线程的证据推进；调查型剧情不要求每三回合都解决线程，但必须能看出线索链在收束，而不是只新增同义问题。\n"
        "- 如果 final_archive.revelation_board 中某线程 evidence_count/verification_count 很高但 progress_count 很低，要把问题表述为“缺少 convergence/收束动作”，而不是“没有任何进展”；建议系统下一回合给 core lead、新场景/NPC入口、压力升级或阶段性结论。\n"
        "- 如果 archive_patch.convergence_actions_add 或 final_archive.convergence_actions 已经提供 core_lead、next_scene_entry、npc_move、pressure_or_consequence 或 available_action，应视为阶段性收束/转场进展；除非玩家已经获得足以回答该线程的确定答案，不要强行要求 open_threads_resolved。\n"
        "- 检查 final_archive.revelation_board：如果主 open_threads 没有 evidence/progress/verification，而旁边出现多个临时 THREAD_1/2/3，应视为线索没有挂到主线，降低 thread_resolution。\n"
        "- 如果玩家连续追查同一个开放线程，并获得可确认答案，必须检查 archive_patch.open_threads_resolved 是否记录该线程；若没有，写入 thread_resolution_gaps。\n"
        "- 如果玩家只获得部分线索，不能强行 resolved；应说明该线程如何被推进、还缺什么。\n"
        "- resolved_threads 写已经被最终 archive 标记解决的线程；unresolved_threads 只写仍需继续追查的线程。\n"
        "- 检查每回合是否保持叙事动量：玩家的行动是否改变局面、制造新张力、推进线索或改变NPC/资产状态。\n"
        "- 检查游戏性是否持续存在：每回合是否保留多个自然行动方向，是否尊重玩家策略，是否避免安全但无聊的空白回应。\n"
        "- 如果某回合安全但没有戏、没有反馈、没有可玩下一步，写入 narrative_or_gameplay_regressions。\n"
        "- 检查 pressure_clock 是否有节奏，不要无缘无故跳动或完全不动。\n"
        "- 检查 pressure_clock 的 label/clock_id 是否跨回合稳定；如果模型临时改名导致同一压力被当成新时钟，要写入 continuity_breaks 或 archive_conflicts。\n"
        "- 检查 NPC/资产状态是否在后续回合被尊重。\n"
        "- 所有 continuity_breaks/archive_conflicts 必须引用 turn_index 和具体字段。\n\n"
        "Story Forge 方案：\n"
        f"{_prompt_json(story_payload)}\n\n"
        "多回合记录：\n"
        f"{_prompt_json(turns_prompt)}\n\n"
        "最终 archive：\n"
        f"{_prompt_json(final_archive_prompt)}\n\n"
        "输出必须严格符合这个 JSON 结构，字段可补充但不要删除：\n"
        + _prompt_json(MULTI_TURN_AUDIT_SCHEMA)
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_chat_completion(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    json_mode: bool = True,
    thinking: str = "disabled",
) -> dict[str, Any]:
    result = _deepseek_call_chat_completion(
        api_key=api_key,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        json_mode=json_mode,
        thinking=thinking,
    )
    text = str(result.get("text") or "")
    parsed_payload, format_error, json_repaired = parse_json_object(text)
    return {
        **result,
        "payload": parsed_payload,
        "format_error": format_error,
        "json_repaired": json_repaired,
    }


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str, bool]:
    stripped = str(text or "").strip()
    if not stripped:
        return None, "empty_content", False
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
    candidates = [stripped]
    start = stripped.find("{")
    if start < 0:
        return None, "no_json_object_start", False
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                if candidate not in candidates:
                    candidates.append(candidate)
                break
    if len(candidates) == 1 and start > 0:
        candidates.append(stripped[start:])

    last_error = "unknown_json_parse_error"
    for candidate in candidates:
        value, error = _load_json_object(candidate)
        if value is not None:
            return value, "", False
        last_error = error
        repaired = _repair_common_json_issues(candidate)
        if repaired != candidate:
            value, repaired_error = _load_json_object(repaired)
            if value is not None:
                return value, f"repaired common JSON issue after: {error}", True
            last_error = repaired_error
    return None, last_error, False


def extract_json_object(text: str) -> dict[str, Any] | None:
    return parse_json_object(text)[0]


def _load_json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{exc.msg} at line {exc.lineno} column {exc.colno}"
    if not isinstance(value, dict):
        return None, f"json root is {type(value).__name__}, expected object"
    return value, ""


def _repair_common_json_issues(text: str) -> str:
    repaired = text
    repaired = re.sub(r'(?<=")[，、](?=\s*(?:"|\]|\}))', ",", repaired)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired


def score_output(payload: dict[str, Any] | None, raw_text: str) -> dict[str, Any]:
    max_score = sum(int(item["weight"]) for item in RUBRIC)
    if not isinstance(payload, dict):
        return {
            "score": 0,
            "max_score": max_score,
            "score_pct": 0,
            "valid_json": False,
            "checks": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "passed": False,
                    "earned": 0,
                    "weight": int(item["weight"]),
                    "evidence": "invalid_json",
                }
                for item in RUBRIC
            ],
        }
    data = payload or {}
    text = json.dumps(data, ensure_ascii=False) if data else str(raw_text or "")
    checks = []
    total = 0
    for item in RUBRIC:
        passed, evidence = _score_check(data, text, item)
        weight = int(item["weight"])
        earned = weight if passed else 0
        total += earned
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "passed": passed,
                "earned": earned,
                "weight": weight,
                "evidence": evidence,
            }
        )
    return {
        "score": total,
        "max_score": max_score,
        "score_pct": round(total / max_score * 100, 1) if max_score else 0,
        "valid_json": isinstance(payload, dict),
        "checks": checks,
    }


def compare_results(
    legacy_payload: dict[str, Any] | None,
    legacy_text: str,
    forge_payload: dict[str, Any] | None,
    forge_text: str,
) -> dict[str, Any]:
    legacy_score = score_output(legacy_payload, legacy_text)
    forge_score = score_output(forge_payload, forge_text)
    legacy_passed = {check["id"] for check in legacy_score["checks"] if check["passed"]}
    forge_passed = {check["id"] for check in forge_score["checks"] if check["passed"]}
    return {
        "legacy": legacy_score,
        "story_forge": forge_score,
        "delta_score": forge_score["score"] - legacy_score["score"],
        "story_forge_added_checks": sorted(forge_passed - legacy_passed),
        "legacy_only_checks": sorted(legacy_passed - forge_passed),
        "recommendation": _recommendation(legacy_score, forge_score),
    }


def write_run_artifacts(
    *,
    output_dir: Path,
    case_id: str,
    seed: str,
    preference: str,
    args: argparse.Namespace,
    legacy_messages: list[dict[str, str]],
    forge_messages: list[dict[str, str]],
    judge_messages: list[dict[str, str]] | None,
    revision_messages: list[dict[str, str]] | None,
    revision_judge_messages: list[dict[str, str]] | None,
    revision_compare_messages: list[dict[str, str]] | None,
    legacy_result: dict[str, Any] | None,
    forge_result: dict[str, Any] | None,
    judge_result: dict[str, Any] | None,
    revision_result: dict[str, Any] | None,
    revision_judge_result: dict[str, Any] | None,
    revision_compare_result: dict[str, Any] | None,
    comparison: dict[str, Any] | None,
) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + _slug(case_id or seed)
    run_dir = _unique_run_dir(output_dir, run_id)
    _write_json(
        run_dir / "request.redacted.json",
        {
            "case_id": case_id,
            "seed": seed,
            "preference": preference,
            "model": args.model,
            "judge_model": args.judge_model or args.model,
            "revision_model": args.revision_model or args.model,
            "base_url": args.base_url,
            "max_tokens": args.max_tokens,
            "judge_max_tokens": args.judge_max_tokens,
            "revision_max_tokens": args.revision_max_tokens,
            "temperature": args.temperature,
            "judge_temperature": args.judge_temperature,
            "revision_temperature": args.revision_temperature,
            "timeout": args.timeout,
            "thinking": args.thinking,
            "with_judge": args.with_judge,
            "revise_from_judge": args.revise_from_judge,
            "json_mode": not args.no_json_mode,
            "dry_run": args.dry_run,
            "api_key_source": _api_key_source(args),
        },
    )
    _write_json(run_dir / "legacy.messages.json", legacy_messages)
    _write_json(run_dir / "story_forge.messages.json", forge_messages)
    if judge_messages is not None:
        _write_json(run_dir / "judge.messages.json", judge_messages)
    if revision_messages is not None:
        _write_json(run_dir / "revision.messages.json", revision_messages)
    if revision_judge_messages is not None:
        _write_json(run_dir / "revision_judge.messages.json", revision_judge_messages)
    if revision_compare_messages is not None:
        _write_json(run_dir / "revision_compare.messages.json", revision_compare_messages)
    if legacy_result is not None:
        _write_json(run_dir / "legacy.response.json", _redact_raw_result(legacy_result))
    if forge_result is not None:
        _write_json(run_dir / "story_forge.response.json", _redact_raw_result(forge_result))
    if judge_result is not None:
        _write_json(run_dir / "judge.response.json", _redact_raw_result(judge_result))
        if isinstance(judge_result.get("payload"), dict):
            _write_json(run_dir / "judge_report.json", judge_result["payload"])
    if revision_result is not None:
        _write_json(run_dir / "revision.response.json", _redact_raw_result(revision_result))
        if isinstance(revision_result.get("payload"), dict):
            _write_json(run_dir / "revision_report.json", revision_result["payload"])
            revised = _revision_story_payload(revision_result.get("payload"))
            if isinstance(revised, dict):
                _write_json(run_dir / "revised_story_forge.json", revised)
    if revision_judge_result is not None:
        _write_json(run_dir / "revision_judge.response.json", _redact_raw_result(revision_judge_result))
        if isinstance(revision_judge_result.get("payload"), dict):
            _write_json(run_dir / "revision_judge_report.json", revision_judge_result["payload"])
    if revision_compare_result is not None:
        _write_json(run_dir / "revision_compare.response.json", _redact_raw_result(revision_compare_result))
        if isinstance(revision_compare_result.get("payload"), dict):
            _write_json(run_dir / "revision_compare_report.json", revision_compare_result["payload"])
    if comparison is not None:
        _write_json(run_dir / "comparison.json", comparison)
        (run_dir / "summary.md").write_text(_summary_markdown(comparison), encoding="utf-8")
    else:
        (run_dir / "summary.md").write_text(
            "# Story Forge A/B dry run\n\n已生成两套 prompt，未调用模型。\n",
            encoding="utf-8",
        )
    return run_dir


def run_case(
    *,
    args: argparse.Namespace,
    case: dict[str, str],
    api_key: str,
    output_dir: Path,
) -> tuple[Path, dict[str, Any] | None]:
    case_id = str(case.get("id") or "")
    seed = str(case.get("seed") or "")
    preference = str(case.get("preference") or "")
    legacy_messages = build_legacy_messages(seed, preference)
    forge_messages = build_story_forge_messages(seed, preference)
    judge_messages = None
    revision_messages = None
    revision_judge_messages = None
    revision_compare_messages = None
    legacy_result = None
    forge_result = None
    judge_result = None
    revision_result = None
    revision_judge_result = None
    revision_compare_result = None
    comparison = None
    if not args.dry_run:
        legacy_result = call_chat_completion(
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            messages=legacy_messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
            json_mode=not args.no_json_mode,
            thinking=args.thinking,
        )
        forge_result = call_chat_completion(
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            messages=forge_messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
            json_mode=not args.no_json_mode,
            thinking=args.thinking,
        )
        comparison = compare_results(
            legacy_result.get("payload"),
            legacy_result.get("text", ""),
            forge_result.get("payload"),
            forge_result.get("text", ""),
        )
        comparison["legacy_response"] = _response_diagnostics(legacy_result)
        comparison["story_forge_response"] = _response_diagnostics(forge_result)
        if args.with_judge:
            judge_messages = build_judge_messages(
                seed=seed,
                preference=preference,
                legacy_payload=legacy_result.get("payload"),
                legacy_text=legacy_result.get("text", ""),
                forge_payload=forge_result.get("payload"),
                forge_text=forge_result.get("text", ""),
            )
            judge_result = call_chat_completion(
                api_key=api_key,
                base_url=args.base_url,
                model=args.judge_model or args.model,
                messages=judge_messages,
                max_tokens=args.judge_max_tokens,
                temperature=args.judge_temperature,
                timeout=args.timeout,
                json_mode=not args.no_json_mode,
                thinking=args.thinking,
            )
            comparison["judge"] = judge_result.get("payload")
            comparison["judge_response"] = _response_diagnostics(judge_result)
            if args.revise_from_judge:
                revision_messages = build_revision_messages(
                    seed=seed,
                    preference=preference,
                    story_forge_payload=forge_result.get("payload"),
                    story_forge_text=forge_result.get("text", ""),
                    judge_payload=judge_result.get("payload"),
                )
                revision_result = call_chat_completion(
                    api_key=api_key,
                    base_url=args.base_url,
                    model=args.revision_model or args.model,
                    messages=revision_messages,
                    max_tokens=args.revision_max_tokens,
                    temperature=args.revision_temperature,
                    timeout=args.timeout,
                    json_mode=not args.no_json_mode,
                    thinking=args.thinking,
                )
                revised_payload = _revision_story_payload(revision_result.get("payload"))
                comparison["revision"] = _revision_diagnostics(revision_result)
                if isinstance(revised_payload, dict):
                    comparison["revision_structure"] = compare_results(
                        legacy_result.get("payload"),
                        legacy_result.get("text", ""),
                        revised_payload,
                        json.dumps(revised_payload, ensure_ascii=False),
                    )
                    revision_judge_messages = build_judge_messages(
                        seed=seed,
                        preference=preference,
                        legacy_payload=legacy_result.get("payload"),
                        legacy_text=legacy_result.get("text", ""),
                        forge_payload=revised_payload,
                        forge_text=json.dumps(revised_payload, ensure_ascii=False),
                    )
                    revision_judge_result = call_chat_completion(
                        api_key=api_key,
                        base_url=args.base_url,
                        model=args.judge_model or args.model,
                        messages=revision_judge_messages,
                        max_tokens=args.judge_max_tokens,
                        temperature=args.judge_temperature,
                        timeout=args.timeout,
                        json_mode=not args.no_json_mode,
                        thinking=args.thinking,
                    )
                    comparison["revision_judge"] = revision_judge_result.get("payload")
                    comparison["revision_judge_response"] = _response_diagnostics(revision_judge_result)
                    revision_compare_messages = build_revision_compare_messages(
                        seed=seed,
                        preference=preference,
                        draft_payload=forge_result.get("payload"),
                        draft_text=forge_result.get("text", ""),
                        revision_payload=revised_payload,
                        revision_text=json.dumps(revised_payload, ensure_ascii=False),
                        judge_payload=judge_result.get("payload"),
                    )
                    revision_compare_result = call_chat_completion(
                        api_key=api_key,
                        base_url=args.base_url,
                        model=args.judge_model or args.model,
                        messages=revision_compare_messages,
                        max_tokens=args.judge_max_tokens,
                        temperature=args.judge_temperature,
                        timeout=args.timeout,
                        json_mode=not args.no_json_mode,
                        thinking=args.thinking,
                    )
                    comparison["revision_compare"] = revision_compare_result.get("payload")
                    comparison["revision_compare_response"] = _response_diagnostics(revision_compare_result)
    run_dir = write_run_artifacts(
        output_dir=output_dir,
        case_id=case_id,
        seed=seed,
        preference=preference,
        args=args,
        legacy_messages=legacy_messages,
        forge_messages=forge_messages,
        judge_messages=judge_messages,
        revision_messages=revision_messages,
        revision_judge_messages=revision_judge_messages,
        revision_compare_messages=revision_compare_messages,
        legacy_result=legacy_result,
        forge_result=forge_result,
        judge_result=judge_result,
        revision_result=revision_result,
        revision_judge_result=revision_judge_result,
        revision_compare_result=revision_compare_result,
        comparison=comparison,
    )
    return run_dir, comparison


def run_simulation(
    *,
    args: argparse.Namespace,
    api_key: str,
    output_dir: Path,
) -> Path:
    story_payload = _read_story_file(args.story_file)
    player_actions = _read_player_actions(args)
    archive_state = _read_archive_state(args.archive_file, story_payload)
    simulation_messages = build_simulation_messages(
        story_payload=story_payload,
        player_actions=player_actions,
        archive_state=archive_state,
    )
    simulation_result = None
    audit_messages = None
    audit_result = None
    if not args.dry_run:
        simulation_result = call_chat_completion(
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            messages=simulation_messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            timeout=args.timeout,
            json_mode=not args.no_json_mode,
            thinking=args.thinking,
        )
        if args.audit_simulation:
            audit_messages = build_simulation_audit_messages(
                story_payload=story_payload,
                player_actions=player_actions,
                simulation_payload=simulation_result.get("payload"),
                simulation_text=simulation_result.get("text", ""),
            )
            audit_result = call_chat_completion(
                api_key=api_key,
                base_url=args.base_url,
                model=args.judge_model or args.model,
                messages=audit_messages,
                max_tokens=args.judge_max_tokens,
                temperature=args.judge_temperature,
                timeout=args.timeout,
                json_mode=not args.no_json_mode,
                thinking=args.thinking,
            )

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-simulation-" + _slug(
        player_actions
    )
    run_dir = _unique_run_dir(output_dir, run_id)
    _write_json(
        run_dir / "request.redacted.json",
        {
            "story_file": args.story_file,
            "archive_file": args.archive_file,
            "model": args.model,
            "judge_model": args.judge_model or args.model,
            "base_url": args.base_url,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "timeout": args.timeout,
            "audit_simulation": args.audit_simulation,
            "repair_from_audit": args.repair_from_audit,
            "json_mode": not args.no_json_mode,
            "dry_run": args.dry_run,
            "api_key_source": _api_key_source(args),
        },
    )
    _write_json(run_dir / "story.input.json", story_payload)
    _write_json(run_dir / "archive.input.json", archive_state)
    (run_dir / "player_actions.txt").write_text(player_actions, encoding="utf-8")
    _write_json(run_dir / "simulation.messages.json", simulation_messages)
    if audit_messages is not None:
        _write_json(run_dir / "simulation_audit.messages.json", audit_messages)
    if simulation_result is not None:
        _write_json(run_dir / "simulation.response.json", _redact_raw_result(simulation_result))
        if isinstance(simulation_result.get("payload"), dict):
            _write_json(run_dir / "simulation_report.json", simulation_result["payload"])
    if audit_result is not None:
        _write_json(run_dir / "simulation_audit.response.json", _redact_raw_result(audit_result))
        if isinstance(audit_result.get("payload"), dict):
            _write_json(run_dir / "simulation_audit_report.json", audit_result["payload"])
    (run_dir / "summary.md").write_text(
        _simulation_summary_markdown(simulation_result, audit_result),
        encoding="utf-8",
    )
    return run_dir


def run_multi_turn_simulation(
    *,
    args: argparse.Namespace,
    api_key: str,
    output_dir: Path,
) -> Path:
    story_payload = _read_story_file(args.story_file)
    actions = _read_player_actions_list(args.simulate_actions_list_file)
    archive_state = _read_archive_state(args.archive_file, story_payload)
    repair_policy = getattr(args, "repair_policy", REPAIR_POLICY_HIGH_RISK)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-multi-turn-" + _slug(
        actions[0] if actions else "simulation"
    )
    run_dir = _unique_run_dir(output_dir, run_id)
    _write_json(
        run_dir / "request.redacted.json",
        {
            "story_file": args.story_file,
            "archive_file": args.archive_file,
            "turn_count": len(actions),
            "model": args.model,
            "judge_model": args.judge_model or args.model,
            "base_url": args.base_url,
            "max_tokens": args.max_tokens,
            "temperature": args.temperature,
            "timeout": args.timeout,
            "audit_simulation": args.audit_simulation,
            "repair_from_audit": args.repair_from_audit,
            "repair_policy": repair_policy,
            "json_mode": not args.no_json_mode,
            "dry_run": args.dry_run,
            "api_key_source": _api_key_source(args),
        },
    )
    _write_json(run_dir / "story.input.json", story_payload)
    _write_json(run_dir / "archive.initial.json", archive_state)
    _write_json(run_dir / "player_actions.json", actions)

    turns: list[dict[str, Any]] = []
    current_archive = dict(archive_state)
    for index, action in enumerate(actions, 1):
        turn_dir = _unique_run_dir(run_dir, f"turn_{index:02d}")
        (turn_dir / "player_action.txt").write_text(action, encoding="utf-8")
        _write_json(turn_dir / "archive.before.json", current_archive)
        revelation_focus = _revelation_focus_from_archive(current_archive)
        _write_json(turn_dir / "revelation_focus.json", revelation_focus)
        simulation_messages = build_simulation_messages(
            story_payload=story_payload,
            player_actions=action,
            archive_state=current_archive,
            revelation_focus=revelation_focus,
        )
        _write_json(turn_dir / "simulation.messages.json", simulation_messages)
        simulation_result = None
        audit_messages = None
        audit_result = None
        repair_messages = None
        repair_result = None
        repair_decision = {"should_repair": False, "reason": "audit_not_run", "policy": repair_policy}
        simulation_payload = None
        archive_payload = None
        if not args.dry_run:
            simulation_result = call_chat_completion(
                api_key=api_key,
                base_url=args.base_url,
                model=args.model,
                messages=simulation_messages,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                timeout=args.timeout,
                json_mode=not args.no_json_mode,
                thinking=args.thinking,
            )
            _write_json(turn_dir / "simulation.response.json", _redact_raw_result(simulation_result))
            simulation_payload = simulation_result.get("payload")
            if isinstance(simulation_payload, dict):
                _write_json(turn_dir / "simulation_report.json", simulation_payload)
                archive_payload = simulation_payload
            if args.audit_simulation:
                audit_messages = build_simulation_audit_messages(
                    story_payload=story_payload,
                    player_actions=action,
                    simulation_payload=simulation_payload if isinstance(simulation_payload, dict) else None,
                    simulation_text=simulation_result.get("text", "") if simulation_result else "",
                )
                _write_json(turn_dir / "simulation_audit.messages.json", audit_messages)
                audit_result = call_chat_completion(
                    api_key=api_key,
                    base_url=args.base_url,
                    model=args.judge_model or args.model,
                    messages=audit_messages,
                    max_tokens=args.judge_max_tokens,
                    temperature=args.judge_temperature,
                    timeout=args.timeout,
                    json_mode=not args.no_json_mode,
                    thinking=args.thinking,
                )
                _write_json(turn_dir / "simulation_audit.response.json", _redact_raw_result(audit_result))
                if isinstance(audit_result.get("payload"), dict):
                    _write_json(turn_dir / "simulation_audit_report.json", audit_result["payload"])
                    should_repair, repair_reason = should_repair_from_audit(
                        audit_result["payload"],
                        policy=repair_policy,
                    )
                    repair_decision = {
                        "should_repair": bool(args.repair_from_audit and should_repair and isinstance(simulation_payload, dict)),
                        "would_repair": bool(should_repair),
                        "reason": repair_reason,
                        "policy": repair_policy,
                        "repair_from_audit": bool(args.repair_from_audit),
                        "simulation_payload_available": isinstance(simulation_payload, dict),
                    }
                    _write_json(turn_dir / "repair_decision.json", repair_decision)
                    if repair_decision["should_repair"]:
                        repair_messages = build_simulation_repair_messages(
                            story_payload=story_payload,
                            player_actions=action,
                            archive_state=current_archive,
                            simulation_payload=simulation_payload,
                            audit_payload=audit_result["payload"],
                            revelation_focus=revelation_focus,
                        )
                        _write_json(turn_dir / "simulation_repair.messages.json", repair_messages)
                        repair_result = call_chat_completion(
                            api_key=api_key,
                            base_url=args.base_url,
                            model=args.judge_model or args.model,
                            messages=repair_messages,
                            max_tokens=args.judge_max_tokens,
                            temperature=args.judge_temperature,
                            timeout=args.timeout,
                            json_mode=not args.no_json_mode,
                            thinking=args.thinking,
                        )
                        _write_json(turn_dir / "simulation_repair.response.json", _redact_raw_result(repair_result))
                        repaired = _repaired_simulation_payload(repair_result.get("payload"))
                        if isinstance(repaired, dict):
                            _write_json(turn_dir / "simulation_repair_report.json", repair_result["payload"])
                            _write_json(turn_dir / "simulation_repaired_report.json", repaired)
                            archive_payload = repaired
            _write_json(turn_dir / "repair_decision.json", repair_decision)
            if isinstance(archive_payload, dict):
                current_archive = merge_archive_patch(
                    current_archive,
                    archive_payload.get("archive_patch"),
                    archive_payload.get("pressure_patch"),
                    simulation_payload=archive_payload,
                )
        _write_json(turn_dir / "archive.after.json", current_archive)
        turns.append(
            {
                "turn_index": index,
                "player_action": action,
                "revelation_focus": revelation_focus,
                "archive_before": _read_json_if_exists(turn_dir / "archive.before.json"),
                "simulation": simulation_payload if isinstance(simulation_payload, dict) else None,
                "audit": audit_result.get("payload") if isinstance(audit_result, dict) else None,
                "repair": repair_result.get("payload") if isinstance(repair_result, dict) else None,
                "repair_decision": repair_decision,
                "archive_source": "repair" if isinstance(archive_payload, dict) and archive_payload is not simulation_payload else "simulation",
                "archive_after": current_archive,
                "turn_dir": str(turn_dir),
            }
        )

    _write_json(run_dir / "turns.json", turns)
    _write_json(run_dir / "final_archive.json", current_archive)
    _render_archive_map_seeds(current_archive, run_dir)
    multi_audit_result = None
    if args.audit_simulation and not args.dry_run:
        messages = build_multi_turn_audit_messages(
            story_payload=story_payload,
            turns=turns,
            final_archive=current_archive,
        )
        _write_json(run_dir / "multi_turn_audit.messages.json", messages)
        multi_audit_result = call_chat_completion(
            api_key=api_key,
            base_url=args.base_url,
            model=args.judge_model or args.model,
            messages=messages,
            max_tokens=args.judge_max_tokens,
            temperature=args.judge_temperature,
            timeout=args.timeout,
            json_mode=not args.no_json_mode,
            thinking=args.thinking,
        )
        _write_json(run_dir / "multi_turn_audit.response.json", _redact_raw_result(multi_audit_result))
        if isinstance(multi_audit_result.get("payload"), dict):
            _write_json(run_dir / "multi_turn_audit_report.json", multi_audit_result["payload"])
    (run_dir / "multi_turn_report.md").write_text(
        _multi_turn_summary_markdown(turns, current_archive, multi_audit_result),
        encoding="utf-8",
    )
    return run_dir


def run_multi_turn_suite(
    *,
    args: argparse.Namespace,
    api_key: str,
    output_dir: Path,
) -> Path:
    suite = _read_simulation_suite(args.simulation_suite_file)
    suite_id = str(suite.get("id") or Path(args.simulation_suite_file).stem or "simulation-suite")
    cases = suite["cases"]
    suite_dir = _unique_run_dir(
        output_dir,
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-suite-" + _slug(suite_id),
    )
    _write_json(
        suite_dir / "request.redacted.json",
        {
            "suite_file": args.simulation_suite_file,
            "suite_id": suite_id,
            "case_count": len(cases),
            "model": args.model,
            "judge_model": args.judge_model or args.model,
            "base_url": args.base_url,
            "max_tokens": args.max_tokens,
            "judge_max_tokens": args.judge_max_tokens,
            "temperature": args.temperature,
            "judge_temperature": args.judge_temperature,
            "timeout": args.timeout,
            "audit_simulation": args.audit_simulation,
            "repair_from_audit": args.repair_from_audit,
            "json_mode": not args.no_json_mode,
            "dry_run": args.dry_run,
            "api_key_source": _api_key_source(args),
        },
    )
    _write_json(suite_dir / "suite.input.json", suite)

    rows = []
    for index, case in enumerate(cases, 1):
        case_dir = _unique_run_dir(suite_dir, f"{index:02d}-{_slug(case.get('id') or 'case')}")
        case_args = copy.copy(args)
        case_args.story_file = case["story_file"]
        case_args.archive_file = case.get("archive_file", "")
        case_args.simulate_actions_list_file = _suite_actions_file(case, case_dir)
        case_args.simulate_actions = ""
        case_args.simulate_actions_file = ""
        run_dir = run_multi_turn_simulation(
            args=case_args,
            api_key=api_key,
            output_dir=case_dir,
        )
        row = _suite_row(index, case, run_dir)
        rows.append(row)
        print(_suite_status_line(row))

    _write_json(suite_dir / "suite_report.json", rows)
    (suite_dir / "suite_report.md").write_text(_suite_report_markdown(rows), encoding="utf-8")
    return suite_dir


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.simulation_suite_file:
        if not args.dry_run:
            api_key = _read_api_key(args)
            if not api_key:
                parser.error(
                    f"缺少 DeepSeek API key。请设置 {args.api_key_env}，或传 --api-key。"
                )
        else:
            api_key = ""
        suite_dir = run_multi_turn_suite(
            args=args,
            api_key=api_key,
            output_dir=Path(args.output_dir),
        )
        print(str(suite_dir))
        return 0
    if args.simulate_actions or args.simulate_actions_file or args.simulate_actions_list_file:
        if not args.story_file:
            parser.error("--simulate-actions/--simulate-actions-file/--simulate-actions-list-file 需要同时提供 --story-file。")
        if not args.dry_run:
            api_key = _read_api_key(args)
            if not api_key:
                parser.error(
                    f"缺少 DeepSeek API key。请设置 {args.api_key_env}，或传 --api-key。"
                )
        else:
            api_key = ""
        if args.simulate_actions_list_file:
            run_dir = run_multi_turn_simulation(
                args=args,
                api_key=api_key,
                output_dir=Path(args.output_dir),
            )
        else:
            run_dir = run_simulation(args=args, api_key=api_key, output_dir=Path(args.output_dir))
        print(str(run_dir))
        return 0
    if args.revise_from_judge:
        args.with_judge = True
    cases = _read_cases(args)
    if not cases:
        parser.error("请通过 --seed、--seed-file、--batch-file 或 stdin 提供跑团种子。")
    if not args.dry_run:
        api_key = _read_api_key(args)
        if not api_key:
            parser.error(
                f"缺少 DeepSeek API key。请设置 {args.api_key_env}，或传 --api-key。"
            )
    else:
        api_key = ""

    output_root = Path(args.output_dir)
    output_dir = output_root
    if len(cases) > 1:
        batch_slug = _slug(Path(args.batch_file).stem if args.batch_file else "batch")
        output_dir = _unique_run_dir(
            output_root,
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-batch-" + batch_slug,
        )

    rows = []
    for index, case in enumerate(cases, 1):
        run_dir, comparison = run_case(
            args=args,
            case=case,
            api_key=api_key,
            output_dir=output_dir,
        )
        row = _batch_row(index, case, run_dir, comparison)
        rows.append(row)
        print(_run_status_line(row))

    if len(cases) > 1:
        report = _batch_report_markdown(rows)
        (output_dir / "batch_report.md").write_text(report, encoding="utf-8")
        _write_json(output_dir / "batch_report.json", rows)
        print(str(output_dir / "batch_report.md"))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="独立剧情 A/B 测试工具：用同一跑团种子对比旧版 DM 开场和新版 Story Forge 方案。"
    )
    parser.add_argument("--seed", default="", help="跑团种子文本。")
    parser.add_argument("--seed-file", default="", help="从 UTF-8 文本文件读取跑团种子。")
    parser.add_argument(
        "--batch-file",
        default="",
        help="从 UTF-8 JSON 文件读取批量用例；可为数组，或包含 cases 数组的对象。",
    )
    parser.add_argument("--preference", default="", help="可选风格偏好，例如硬核、调查为主、低规则细节。")
    parser.add_argument("--preference-file", default="", help="从 UTF-8 文本文件读取风格偏好。")
    parser.add_argument("--api-key", default="", help="DeepSeek API key；建议使用环境变量，避免进入 shell 历史。")
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV, help="读取 API key 的环境变量名。")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="DeepSeek API base URL。")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="模型名，默认 deepseek-v4-flash。")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="单次输出 token 上限。")
    parser.add_argument("--temperature", type=float, default=0.8, help="采样温度。")
    parser.add_argument("--timeout", type=int, default=120, help="单次 HTTP 超时秒数。")
    parser.add_argument("--with-judge", action="store_true", help="额外调用一次 AI 专业评审，生成 judge_report.json。")
    parser.add_argument("--judge-model", default="", help="专业评审模型名；默认复用 --model。")
    parser.add_argument("--judge-max-tokens", type=int, default=DEFAULT_JUDGE_MAX_TOKENS, help="专业评审输出 token 上限。")
    parser.add_argument("--judge-temperature", type=float, default=0.2, help="专业评审采样温度。")
    parser.add_argument(
        "--revise-from-judge",
        action="store_true",
        help="基于 judge_report 自动生成 Story Forge 二稿，并再次评审二稿。",
    )
    parser.add_argument("--revision-model", default="", help="二稿修订模型名；默认复用 --model。")
    parser.add_argument("--revision-max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="二稿输出 token 上限。")
    parser.add_argument("--revision-temperature", type=float, default=0.5, help="二稿修订采样温度。")
    parser.add_argument("--story-file", default="", help="读取 Story Forge JSON，用于 Runtime DM 模拟。")
    parser.add_argument("--simulate-actions", default="", help="玩家行动文本；需要配合 --story-file。")
    parser.add_argument("--simulate-actions-file", default="", help="从 UTF-8 文本文件读取玩家行动；需要配合 --story-file。")
    parser.add_argument("--simulate-actions-list-file", default="", help="读取多回合玩家行动；支持 JSON 数组或 --- 分隔文本。")
    parser.add_argument("--simulation-suite-file", default="", help="读取多回合 Runtime DM 验收套件 JSON，批量运行多条模拟用例。")
    parser.add_argument("--archive-file", default="", help="可选：读取当前 archive_state JSON。")
    parser.add_argument("--audit-simulation", action="store_true", help="对 Runtime DM 模拟输出进行专业审计。")
    parser.add_argument("--repair-from-audit", action="store_true", help="多回合模拟中，基于每回合审计最小化修补留档补丁后再合并 archive。")
    parser.add_argument(
        "--repair-policy",
        choices=REPAIR_POLICY_VALUES,
        default=REPAIR_POLICY_HIGH_RISK,
        help="--repair-from-audit trigger policy: high_risk only repairs risky audit results; always keeps the old eager second pass; never records audit only.",
    )
    parser.add_argument(
        "--thinking",
        choices=("disabled", "enabled", "auto"),
        default="disabled",
        help="DeepSeek thinking 模式；默认关闭，降低结构化 JSON 输出被思考 token 挤占的概率。",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="输出目录。")
    parser.add_argument("--dry-run", action="store_true", help="只生成 prompt 和请求文件，不调用 API。")
    parser.add_argument("--no-json-mode", action="store_true", help="不发送 response_format=json_object。")
    return parser


def _score_check(data: dict[str, Any], text: str, item: dict[str, Any]) -> tuple[bool, Any]:
    kind = item["kind"]
    if kind == "text_keys":
        keys = tuple(item["keys"])
        hits = [key for key in keys if _has_key_text(data, key)]
        return len(hits) >= 2, hits
    if kind == "key_text":
        keys = tuple(item["keys"])
        hits = [key for key in keys if _has_key_text(data, key)]
        return bool(hits), hits
    if kind == "list_count":
        count = sum(_list_count_for_key(data, key) for key in item["keys"])
        return count >= int(item["minimum"]), {"count": count, "minimum": item["minimum"]}
    if kind == "pressure":
        has_pressure = _has_mapping_key(data, "pressure_clock") or _has_key_text(data, "stakes")
        return has_pressure, {"pressure_clock": _has_mapping_key(data, "pressure_clock"), "stakes": _has_key_text(data, "stakes")}
    if kind == "secret_layers":
        hits = [key for key in ("do_not_reveal", "hidden_truth", "player_known_state", "canon_facts_to_track") if _contains_key(data, key)]
        return len(hits) >= 2, hits
    if kind == "asset_ids":
        ids = sorted(
            set(
                re.findall(
                    r"\b(?:NPC|CHAR|SCN|LOC|PROP|MOTIF|FACTION|CLUE)_[A-Za-z0-9_]+",
                    text,
                    flags=re.I,
                )
            )
        )
        return len(ids) >= int(item["minimum"]), {"count": len(ids), "sample": ids[:8]}
    if kind == "selfcheck":
        hits = [key for key in ("playability_selfcheck", "blocking_issues", "high_priority", "optimizations") if _contains_key(data, key)]
        return bool(hits), hits
    return False, "unknown_check_kind"


def _has_key_text(value: Any, key: str) -> bool:
    for found in _values_for_key(value, key):
        if isinstance(found, str) and found.strip():
            return True
        if isinstance(found, (int, float)) and found:
            return True
        if isinstance(found, (dict, list)) and found:
            return True
    return False


def _has_mapping_key(value: Any, key: str) -> bool:
    for found in _values_for_key(value, key):
        if isinstance(found, dict) and bool(found):
            return True
    return False


def _contains_key(value: Any, key: str) -> bool:
    return any(True for _ in _values_for_key(value, key))


def _list_count_for_key(value: Any, key: str) -> int:
    total = 0
    for found in _values_for_key(value, key):
        if isinstance(found, list):
            total += len(found)
        elif isinstance(found, dict):
            total += len(found)
        elif isinstance(found, str) and found.strip():
            total += 1
    return total


def _values_for_key(value: Any, key: str):
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if str(item_key) == key:
                yield item_value
            yield from _values_for_key(item_value, key)
    elif isinstance(value, list):
        for item in value:
            yield from _values_for_key(item, key)


def _recommendation(legacy_score: dict[str, Any], forge_score: dict[str, Any]) -> str:
    if not legacy_score["valid_json"] or not forge_score["valid_json"]:
        return "至少一侧没有返回合法 JSON；先检查模型输出格式，再判断剧情质量。"
    delta = forge_score["score"] - legacy_score["score"]
    if delta >= 20:
        return "新版 Story Forge 在结构化可玩性上明显优于旧版，适合继续扩大测试集。"
    if delta >= 0:
        return "新版 Story Forge 没有退步，但优势有限；建议查看缺失项并继续调 prompt。"
    return "新版 Story Forge 本轮得分低于旧版；应检查是否过度结构化、牺牲开场叙事可读性。"


def _model_output_for_judge(payload: dict[str, Any] | None, text: str) -> str:
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return str(text or "")


def _revision_story_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    revised = payload.get("revised_story_forge")
    if isinstance(revised, dict):
        return revised
    if "playable_scene_cards" in payload and "player_facing_opening" in payload:
        return payload
    return None


def _repaired_simulation_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    repaired = payload.get("repaired_simulation")
    if isinstance(repaired, dict):
        return repaired
    if "archive_patch" in payload and "player_facing_response" in payload:
        return payload
    return None


def _revision_diagnostics(result: dict[str, Any] | None) -> dict[str, Any]:
    diagnostics = _response_diagnostics(result)
    payload = result.get("payload") if isinstance(result, dict) else None
    revised = _revision_story_payload(payload if isinstance(payload, dict) else None)
    diagnostics["valid_revised_story"] = isinstance(revised, dict)
    if isinstance(payload, dict):
        diagnostics["has_revision_strategy"] = isinstance(payload.get("revision_strategy"), dict)
        diagnostics["has_revision_selfcheck"] = isinstance(payload.get("revision_selfcheck"), dict)
    return diagnostics


def _judge_story_forge_total(judge: dict[str, Any] | None) -> float | None:
    if not isinstance(judge, dict):
        return None
    scores = judge.get("scores")
    if not isinstance(scores, dict):
        return None
    story_forge = scores.get("story_forge")
    if not isinstance(story_forge, dict):
        return None
    return _number_or_none(story_forge.get("total"))


def _judge_legacy_total(judge: dict[str, Any] | None) -> float | None:
    if not isinstance(judge, dict):
        return None
    scores = judge.get("scores")
    if not isinstance(scores, dict):
        return None
    legacy = scores.get("legacy")
    if not isinstance(legacy, dict):
        return None
    return _number_or_none(legacy.get("total"))


def _read_cases(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.batch_file:
        value = json.loads(Path(args.batch_file).read_text(encoding="utf-8-sig"))
        if isinstance(value, dict):
            raw_cases = value.get("cases")
            if raw_cases is None:
                raw_cases = [value]
        elif isinstance(value, list):
            raw_cases = value
        else:
            raise ValueError("--batch-file must contain a JSON array or object.")
        cases = []
        for index, item in enumerate(raw_cases, 1):
            if isinstance(item, str):
                cases.append({"id": f"case_{index}", "seed": item, "preference": _read_preference(args)})
            elif isinstance(item, dict):
                seed = str(item.get("seed") or "").strip()
                if not seed:
                    raise ValueError(f"batch case {index} is missing seed.")
                cases.append(
                    {
                        "id": str(item.get("id") or f"case_{index}"),
                        "seed": seed,
                        "preference": str(item.get("preference") or _read_preference(args)),
                    }
                )
            else:
                raise ValueError(f"batch case {index} must be a string or object.")
        return cases
    seed = _read_seed(args)
    if not seed.strip():
        return []
    return [{"id": "", "seed": seed, "preference": _read_preference(args)}]


def _batch_row(
    index: int,
    case: dict[str, str],
    run_dir: Path,
    comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index": index,
        "id": case.get("id") or f"case_{index}",
        "seed": case.get("seed") or "",
        "preference": case.get("preference") or "",
        "run_dir": str(run_dir),
    }
    if comparison is None:
        row["dry_run"] = True
        return row
    judge = comparison.get("judge") if isinstance(comparison.get("judge"), dict) else {}
    judge_scores = judge.get("scores") if isinstance(judge, dict) else {}
    legacy_judge = judge_scores.get("legacy") if isinstance(judge_scores, dict) else {}
    forge_judge = judge_scores.get("story_forge") if isinstance(judge_scores, dict) else {}
    legacy_judge_total = _number_or_none(legacy_judge.get("total") if isinstance(legacy_judge, dict) else None)
    forge_judge_total = _number_or_none(forge_judge.get("total") if isinstance(forge_judge, dict) else None)
    judge_delta = (
        forge_judge_total - legacy_judge_total
        if forge_judge_total is not None and legacy_judge_total is not None
        else None
    )
    revision_structure = comparison.get("revision_structure")
    revision_score = None
    revision_delta = None
    if isinstance(revision_structure, dict):
        revision_score = _number_or_none(
            revision_structure.get("story_forge", {}).get("score")
            if isinstance(revision_structure.get("story_forge"), dict)
            else None
        )
        revision_delta = (
            revision_score - _number_or_none(comparison["story_forge"]["score"])
            if revision_score is not None
            else None
        )
    revision_judge = comparison.get("revision_judge") if isinstance(comparison.get("revision_judge"), dict) else {}
    revision_judge_total = _judge_story_forge_total(revision_judge)
    revision_judge_delta = (
        revision_judge_total - forge_judge_total
        if revision_judge_total is not None and forge_judge_total is not None
        else None
    )
    revision_compare = (
        comparison.get("revision_compare")
        if isinstance(comparison.get("revision_compare"), dict)
        else {}
    )
    revision_compare_delta = None
    estimated_delta = revision_compare.get("estimated_delta") if isinstance(revision_compare, dict) else None
    if isinstance(estimated_delta, dict):
        revision_compare_delta = _number_or_none(estimated_delta.get("overall"))
    row.update(
        {
            "legacy_structure_score": comparison["legacy"]["score"],
            "story_forge_structure_score": comparison["story_forge"]["score"],
            "structure_delta": comparison["delta_score"],
            "legacy_valid_json": comparison["legacy"]["valid_json"],
            "story_forge_valid_json": comparison["story_forge"]["valid_json"],
            "legacy_finish_reason": comparison.get("legacy_response", {}).get("finish_reason"),
            "story_forge_finish_reason": comparison.get("story_forge_response", {}).get("finish_reason"),
            "judge_valid_json": bool(judge),
            "judge_winner": judge.get("winner") if isinstance(judge, dict) else None,
            "legacy_judge_total": legacy_judge_total,
            "story_forge_judge_total": forge_judge_total,
            "judge_delta": judge_delta,
            "judge_key_deltas": judge.get("key_deltas") if isinstance(judge, dict) else [],
            "story_forge_upgrade_notes": judge.get("story_forge_upgrade_notes") if isinstance(judge, dict) else [],
            "revision_valid_json": bool(comparison.get("revision", {}).get("valid_json"))
            if isinstance(comparison.get("revision"), dict)
            else False,
            "revision_valid_story": bool(comparison.get("revision", {}).get("valid_revised_story"))
            if isinstance(comparison.get("revision"), dict)
            else False,
            "revision_structure_score": revision_score,
            "revision_structure_delta": revision_delta,
            "revision_judge_valid_json": bool(revision_judge),
            "revision_judge_total": revision_judge_total,
            "revision_judge_delta": revision_judge_delta,
            "revision_compare_valid_json": bool(revision_compare),
            "revision_adoption_recommendation": revision_compare.get("adoption_recommendation")
            if isinstance(revision_compare, dict)
            else None,
            "revision_compare_winner": revision_compare.get("winner") if isinstance(revision_compare, dict) else None,
            "revision_compare_delta": revision_compare_delta,
            "revision_compare_verdict": revision_compare.get("short_verdict")
            if isinstance(revision_compare, dict)
            else None,
            "revision_upgrade_notes": revision_judge.get("story_forge_upgrade_notes")
            if isinstance(revision_judge, dict)
            else [],
        }
    )
    return row


def _run_status_line(row: dict[str, Any]) -> str:
    prefix = f"[{row['index']}] {row['run_dir']}"
    if row.get("dry_run"):
        return prefix
    judge_part = ""
    if row.get("judge_valid_json"):
        judge_part = " judge={legacy}/{forge} winner={winner}".format(
            legacy=row.get("legacy_judge_total"),
            forge=row.get("story_forge_judge_total"),
            winner=row.get("judge_winner"),
        )
    revision_part = ""
    if row.get("revision_judge_valid_json"):
        revision_part = " revision_judge={score} revision_delta={delta}".format(
            score=row.get("revision_judge_total"),
            delta=row.get("revision_judge_delta"),
        )
    if row.get("revision_compare_valid_json"):
        revision_part += " adoption={adoption}".format(
            adoption=row.get("revision_adoption_recommendation")
        )
    return (
        f"{prefix} legacy={row.get('legacy_structure_score')}/100 "
        f"story_forge={row.get('story_forge_structure_score')}/100 "
        f"delta={row.get('structure_delta')} json="
        f"{'ok' if row.get('legacy_valid_json') else 'bad'}/"
        f"{'ok' if row.get('story_forge_valid_json') else 'bad'}"
        f"{judge_part}"
        f"{revision_part}"
    )


def _batch_report_markdown(rows: list[dict[str, Any]]) -> str:
    completed = [row for row in rows if not row.get("dry_run")]
    lines = ["# Story Forge 批量 A/B 测试报告", ""]
    if not completed:
        lines.append("本次为 dry run，只生成 prompt 和请求文件。")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            f"- 样例数：{len(completed)}",
            f"- 旧版结构均分：{_avg(row.get('legacy_structure_score') for row in completed):.1f}",
            f"- 新版结构均分：{_avg(row.get('story_forge_structure_score') for row in completed):.1f}",
            f"- 结构平均分差：{_avg(row.get('structure_delta') for row in completed):.1f}",
        ]
    )
    judged = [row for row in completed if row.get("judge_valid_json")]
    if judged:
        lines.extend(
            [
                f"- 旧版专业评审均分：{_avg(row.get('legacy_judge_total') for row in judged):.1f}",
                f"- 新版专业评审均分：{_avg(row.get('story_forge_judge_total') for row in judged):.1f}",
                f"- 专业评审平均分差：{_avg(row.get('judge_delta') for row in judged):.1f}",
            ]
        )
    revised = [row for row in completed if row.get("revision_judge_valid_json")]
    if revised:
        lines.extend(
            [
                f"- 二稿专业评审均分：{_avg(row.get('revision_judge_total') for row in revised):.1f}",
                f"- 二稿相对一稿平均提升：{_avg(row.get('revision_judge_delta') for row in revised):.1f}",
            ]
        )
    lines.extend(
        [
            "",
            "| # | 用例 | 旧版结构 | 新版结构 | 评审旧版 | 评审新版 | 二稿评审 | 二稿提升 | 采用建议 | 胜者 |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in completed:
        label = row.get("id") or _slug(row.get("seed", "case"))
        lines.append(
            "| {index} | {label} | {legacy_s} | {forge_s} | {legacy_j} | {forge_j} | {revision_j} | {revision_delta} | {adoption} | {winner} |".format(
                index=row["index"],
                label=label,
                legacy_s=row.get("legacy_structure_score", ""),
                forge_s=row.get("story_forge_structure_score", ""),
                legacy_j=_display_score(row.get("legacy_judge_total")),
                forge_j=_display_score(row.get("story_forge_judge_total")),
                revision_j=_display_score(row.get("revision_judge_total")),
                revision_delta=_display_score(row.get("revision_judge_delta")),
                adoption=row.get("revision_adoption_recommendation") or "",
                winner=row.get("judge_winner") or "",
            )
        )
    if judged:
        lines.extend(["", "## 评审要点", ""])
        for row in judged:
            lines.append(f"### {row.get('id') or row['index']}")
            deltas = row.get("judge_key_deltas") or []
            if deltas:
                lines.extend(f"- {item}" for item in deltas[:5])
            notes = row.get("story_forge_upgrade_notes") or []
            if notes:
                lines.append("")
                lines.append("Story Forge 改进点：")
                lines.extend(f"- {item}" for item in notes[:5])
            if row.get("revision_compare_verdict"):
                lines.append("")
                lines.append(f"二稿采用判断：{row['revision_compare_verdict']}")
            lines.append("")
    return "\n".join(lines)


def _suite_row(index: int, case: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    audit = _read_json_if_exists(run_dir / "multi_turn_audit_report.json")
    final_archive = _read_json_if_exists(run_dir / "final_archive.json")
    initial_archive = _read_json_if_exists(run_dir / "archive.initial.json")
    turns = _read_json_if_exists(run_dir / "turns.json")
    usage = _usage_summary(run_dir)
    scores = audit.get("scores") if isinstance(audit, dict) and isinstance(audit.get("scores"), dict) else {}
    archive_counts = _archive_counts(final_archive if isinstance(final_archive, dict) else {})
    turn_count = len(turns) if isinstance(turns, list) else 0
    repair_count = 0
    repair_skipped_count = 0
    repair_would_run_count = 0
    archive_sources: list[str] = []
    if isinstance(turns, list):
        for turn in turns:
            if isinstance(turn, dict):
                archive_sources.append(str(turn.get("archive_source") or ""))
                if isinstance(turn.get("repair"), dict):
                    repair_count += 1
                decision = turn.get("repair_decision")
                if isinstance(decision, dict):
                    if decision.get("would_repair"):
                        repair_would_run_count += 1
                    if decision.get("repair_from_audit") and not decision.get("should_repair"):
                        repair_skipped_count += 1
    structure_counts = _runtime_structure_counts(run_dir)
    board_metrics = _revelation_board_metrics(final_archive if isinstance(final_archive, dict) else {})
    initial_board_metrics = _revelation_board_metrics(initial_archive if isinstance(initial_archive, dict) else {})
    weak_delta = board_metrics.get("weak_thread_count", 0) - initial_board_metrics.get("weak_thread_count", 0)
    return {
        "index": index,
        "id": case.get("id") or f"case_{index}",
        "description": case.get("description") or "",
        "tags": case.get("tags") or [],
        "run_dir": str(run_dir),
        "turn_count": turn_count,
        "verdict": audit.get("verdict") if isinstance(audit, dict) else "dry_run",
        "total_score": _number_or_none(scores.get("total") if isinstance(scores, dict) else None),
        "continuity": _number_or_none(scores.get("continuity") if isinstance(scores, dict) else None),
        "player_agency_over_time": _number_or_none(scores.get("player_agency_over_time") if isinstance(scores, dict) else None),
        "archive_consistency": _number_or_none(scores.get("archive_consistency") if isinstance(scores, dict) else None),
        "hidden_truth_safety": _number_or_none(scores.get("hidden_truth_safety") if isinstance(scores, dict) else None),
        "pressure_pacing": _number_or_none(scores.get("pressure_pacing") if isinstance(scores, dict) else None),
        "thread_resolution": _number_or_none(scores.get("thread_resolution") if isinstance(scores, dict) else None),
        "narrative_momentum": _number_or_none(scores.get("narrative_momentum") if isinstance(scores, dict) else None),
        "playability_over_time": _number_or_none(scores.get("playability_over_time") if isinstance(scores, dict) else None),
        "continuity_break_count": _list_len(audit.get("continuity_breaks")) if isinstance(audit, dict) else 0,
        "archive_conflict_count": _list_len(audit.get("archive_conflicts")) if isinstance(audit, dict) else 0,
        "unresolved_thread_count": _list_len(audit.get("unresolved_threads")) if isinstance(audit, dict) else archive_counts.get("open_threads", 0),
        "resolved_thread_count": _list_len(audit.get("resolved_threads")) if isinstance(audit, dict) else archive_counts.get("resolved_threads", 0),
        "thread_resolution_gap_count": _list_len(audit.get("thread_resolution_gaps")) if isinstance(audit, dict) else 0,
        "narrative_regression_count": _list_len(audit.get("narrative_or_gameplay_regressions")) if isinstance(audit, dict) else 0,
        "recommended_change_count": _list_len(audit.get("recommended_system_changes")) if isinstance(audit, dict) else 0,
        "repair_count": repair_count,
        "repair_skipped_count": repair_skipped_count,
        "repair_would_run_count": repair_would_run_count,
        "archive_sources": archive_sources,
        "archive_counts": archive_counts,
        "runtime_structure_counts": structure_counts,
        "initial_revelation_board_metrics": initial_board_metrics,
        "revelation_board_metrics": board_metrics,
        "weak_thread_delta": weak_delta,
        "usage": usage,
        "short_reason": audit.get("short_reason") if isinstance(audit, dict) else "",
    }


def _suite_status_line(row: dict[str, Any]) -> str:
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    return (
        "[{index}] {id} verdict={verdict} total={total} turns={turns} "
        "repairs={repairs} skipped_repairs={skipped} tokens={tokens} second_call_tokens={second_tokens} run={run_dir}"
    ).format(
        index=row.get("index"),
        id=row.get("id"),
        verdict=row.get("verdict"),
        total=_display_score(row.get("total_score")),
        turns=row.get("turn_count"),
        repairs=row.get("repair_count"),
        skipped=row.get("repair_skipped_count", 0),
        tokens=usage.get("total_tokens", 0),
        second_tokens=usage.get("no_tool_second_call_tokens", 0),
        run_dir=row.get("run_dir"),
    )


def _suite_report_markdown(rows: list[dict[str, Any]]) -> str:
    lines = ["# Runtime DM 多回合验收套件报告", ""]
    if not rows:
        lines.append("没有用例。")
        return "\n".join(lines) + "\n"
    completed = [row for row in rows if row.get("verdict") != "dry_run"]
    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict = str(row.get("verdict") or "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    token_total = sum(
        int(row.get("usage", {}).get("total_tokens") or 0)
        for row in rows
        if isinstance(row.get("usage"), dict)
    )
    cache_hit_total = sum(
        int(row.get("usage", {}).get("prompt_cache_hit_tokens") or 0)
        for row in rows
        if isinstance(row.get("usage"), dict)
    )
    cache_miss_total = sum(
        int(row.get("usage", {}).get("prompt_cache_miss_tokens") or 0)
        for row in rows
        if isinstance(row.get("usage"), dict)
    )
    cache_observed = cache_hit_total + cache_miss_total
    cache_ratio = cache_hit_total / cache_observed if cache_observed else None
    lines.extend(
        [
            f"- 用例数：{len(rows)}",
            f"- verdict 分布：{json.dumps(verdict_counts, ensure_ascii=False, sort_keys=True)}",
            f"- 平均总分：{_avg(row.get('total_score') for row in completed):.1f}" if completed else "- 平均总分：dry run",
            f"- token 总消耗：{token_total}",
            (
                f"- prompt cache：hit {cache_hit_total} / miss {cache_miss_total} / hit_ratio {cache_ratio:.2%}"
                if cache_ratio is not None
                else "- prompt cache：无可用命中统计"
            ),
            "",
            "| # | 用例 | verdict | total | continuity | agency | archive | hidden | pressure | thread | narrative | playability | evidence | verify | progress | converge | goals | maps | clues | board | weak | weak_delta | guard | hints | turns | repairs | conflicts | resolved | unresolved | tokens | cache_hit | cache_miss | cache_ratio |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        structure = row.get("runtime_structure_counts") if isinstance(row.get("runtime_structure_counts"), dict) else {}
        board = row.get("revelation_board_metrics") if isinstance(row.get("revelation_board_metrics"), dict) else {}
        lines.append(
            "| {index} | {id} | {verdict} | {total} | {continuity} | {agency} | {archive} | {hidden} | {pressure} | {thread} | {narrative} | {playability} | {evidence} | {verify} | {progress} | {converge} | {goals} | {maps} | {clues} | {board_count} | {weak} | {weak_delta} | {guard} | {hints} | {turns} | {repairs} | {conflicts} | {resolved} | {unresolved} | {tokens} | {cache_hit} | {cache_miss} | {cache_ratio} |".format(
                index=row.get("index"),
                id=row.get("id"),
                verdict=row.get("verdict"),
                total=_display_score(row.get("total_score")),
                continuity=_display_score(row.get("continuity")),
                agency=_display_score(row.get("player_agency_over_time")),
                archive=_display_score(row.get("archive_consistency")),
                hidden=_display_score(row.get("hidden_truth_safety")),
                pressure=_display_score(row.get("pressure_pacing")),
                thread=_display_score(row.get("thread_resolution")),
                narrative=_display_score(row.get("narrative_momentum")),
                playability=_display_score(row.get("playability_over_time")),
                evidence=structure.get("observable_evidence", 0),
                verify=structure.get("verification_paths", 0),
                progress=structure.get("thread_progress_add", 0),
                converge=structure.get("convergence_actions_add", 0),
                goals=structure.get("scene_goal_cards", 0),
                maps=structure.get("map_grid_seeds", 0),
                clues=row.get("archive_counts", {}).get("clue_ledger", 0)
                if isinstance(row.get("archive_counts"), dict)
                else 0,
                board_count=board.get("thread_count", 0),
                weak=board.get("weak_thread_count", 0),
                weak_delta=row.get("weak_thread_delta", 0),
                guard=row.get("archive_counts", {}).get("archive_guard_rejections", 0)
                if isinstance(row.get("archive_counts"), dict)
                else 0,
                hints=row.get("archive_counts", {}).get("thread_hints", 0)
                if isinstance(row.get("archive_counts"), dict)
                else 0,
                turns=row.get("turn_count"),
                repairs=row.get("repair_count"),
                conflicts=row.get("archive_conflict_count"),
                resolved=row.get("resolved_thread_count"),
                unresolved=row.get("unresolved_thread_count"),
                tokens=usage.get("total_tokens", 0),
                cache_hit=usage.get("prompt_cache_hit_tokens", 0),
                cache_miss=usage.get("prompt_cache_miss_tokens", 0),
                cache_ratio=(
                    f"{float(usage.get('prompt_cache_hit_ratio')):.2%}"
                    if usage.get("prompt_cache_hit_ratio") is not None
                    else ""
                ),
            )
        )
    lines.extend(["", "## 逐案摘要", ""])
    for row in rows:
        lines.append(f"### {row.get('id')}")
        if row.get("description"):
            lines.append(str(row["description"]))
        lines.append(f"- run_dir: {row.get('run_dir')}")
        lines.append(f"- short_reason: {row.get('short_reason') or ''}")
        lines.append(
            f"- archive_counts: {json.dumps(row.get('archive_counts') or {}, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append(
            f"- runtime_structure_counts: {json.dumps(row.get('runtime_structure_counts') or {}, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append(
            f"- initial_revelation_board_metrics: {json.dumps(row.get('initial_revelation_board_metrics') or {}, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append(
            f"- revelation_board_metrics: {json.dumps(row.get('revelation_board_metrics') or {}, ensure_ascii=False, sort_keys=True)}"
        )
        lines.append(f"- usage: {json.dumps(row.get('usage') or {}, ensure_ascii=False, sort_keys=True)}")
        lines.append("")
    return "\n".join(lines)


def _archive_counts(archive: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in (
        "canon_facts",
        "player_known_state",
        "hidden_truth",
        "open_threads",
        "resolved_threads",
        "npc_state",
        "asset_state",
        "pressure_history",
        "npc_state_history",
        "asset_state_history",
        "thread_progress",
        "thread_progress_history",
        "clue_ledger",
        "clue_ledger_history",
        "convergence_actions",
        "convergence_action_history",
        "archive_guard_rejections",
        "open_thread_overflow",
        "thread_hints",
        "revelation_board",
    ):
        value = archive.get(key)
        counts[key] = len(value) if isinstance(value, list) else (1 if value else 0)
    return counts


def _revelation_board_metrics(archive: dict[str, Any]) -> dict[str, int]:
    board = archive.get("revelation_board")
    if not isinstance(board, list):
        board = _build_revelation_board(archive)
    weak = 0
    resolved = 0
    for entry in board:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "resolved":
            resolved += 1
        evidence_count = _number_or_none(entry.get("evidence_count")) or _list_len(entry.get("evidence"))
        verification_count = _number_or_none(entry.get("verification_count")) or _list_len(entry.get("verification_paths"))
        progress_count = _number_or_none(entry.get("progress_count")) or _list_len(entry.get("progress"))
        if entry.get("status") != "resolved" and evidence_count <= 0 and verification_count <= 0 and progress_count <= 0:
            weak += 1
    return {
        "thread_count": len(board),
        "resolved_count": resolved,
        "weak_thread_count": weak,
    }


def _revelation_focus_from_archive(archive: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(archive, dict) or not archive:
        return {}
    board = archive.get("revelation_board")
    if not isinstance(board, list):
        board = _build_revelation_board(archive)
    weak_threads = [
        _revelation_focus_entry(entry)
        for entry in board
        if _is_weak_revelation_entry(entry)
    ]
    weak_threads = [entry for entry in weak_threads if entry]
    convergence = _revelation_convergence_entry(board)
    if not weak_threads:
        return {
            "priority_thread": None,
            "weak_threads": [],
            "convergence_thread": convergence,
            "instruction": (
                "当前没有完全空白的开放线程；避免无必要新开坑。"
                "如果 convergence_thread 存在，优先把它推向核心线索、下一场景/NPC、压力动作或阶段性结论，而不是继续堆次级证据。"
            ),
        }
    priority = weak_threads[0]
    return {
        "priority_thread": priority,
        "weak_threads": weak_threads[:5],
        "convergence_thread": convergence,
        "instruction": (
            "若玩家行动可合理关联 priority_thread，请优先给它补一条可感知证据或可执行验证路径，"
            "并在 archive_patch.thread_progress_add 或 player_facing_response.observable_evidence/verification_paths 中引用该 thread_id。"
            "若玩家行动无关，不要强行牵引；保持玩家主动性，并在 runtime_selfcheck.notes 说明未使用焦点。"
            "若 convergence_thread 已有足够 evidence/verification，请把它推进到可行动的下一场景、NPC动作、压力升级或阶段性结论。"
        ),
    }


def _is_weak_revelation_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    if entry.get("status") != "open":
        return False
    evidence_count = _number_or_none(entry.get("evidence_count")) or _list_len(entry.get("evidence"))
    verification_count = _number_or_none(entry.get("verification_count")) or _list_len(entry.get("verification_paths"))
    progress_count = _number_or_none(entry.get("progress_count")) or _list_len(entry.get("progress"))
    return evidence_count <= 0 and verification_count <= 0 and progress_count <= 0


def _revelation_convergence_entry(board: list[Any]) -> dict[str, Any] | None:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for entry in board:
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        evidence_count = int(_number_or_none(entry.get("evidence_count")) or _list_len(entry.get("evidence")))
        verification_count = int(_number_or_none(entry.get("verification_count")) or _list_len(entry.get("verification_paths")))
        progress_count = int(_number_or_none(entry.get("progress_count")) or _list_len(entry.get("progress")))
        if evidence_count + verification_count < 3:
            continue
        if _entry_has_convergence_action(entry):
            continue
        if progress_count >= 2:
            continue
        score = evidence_count + verification_count - (progress_count * 1.5)
        candidates.append((score, entry))
    if not candidates:
        return None
    _, entry = max(candidates, key=lambda item: item[0])
    focus = _revelation_focus_entry(entry)
    focus["evidence_count"] = int(_number_or_none(entry.get("evidence_count")) or _list_len(entry.get("evidence")))
    focus["verification_count"] = int(_number_or_none(entry.get("verification_count")) or _list_len(entry.get("verification_paths")))
    focus["progress_count"] = int(_number_or_none(entry.get("progress_count")) or _list_len(entry.get("progress")))
    focus["needed"] = [
        "synthesis",
        "core_lead",
        "scene_or_npc_entry",
        "pressure_or_consequence",
    ]
    return focus


def _entry_has_convergence_action(entry: dict[str, Any]) -> bool:
    for item in _as_list(entry.get("progress")):
        if not isinstance(item, dict):
            continue
        if item.get("action_type") and any(
            item.get(key)
            for key in (
                "synthesis",
                "next_scene_entry",
                "npc_move",
                "pressure_or_consequence",
                "available_action",
                "scene_goal",
            )
        ):
            return True
    return False


def _revelation_focus_entry(entry: dict[str, Any]) -> dict[str, Any]:
    thread_id = str(entry.get("thread_id") or entry.get("title") or "").strip()
    title = str(entry.get("title") or thread_id or "untitled_thread").strip()
    if not thread_id and not title:
        return {}
    result = {
        "thread_id": thread_id or title,
        "title": title,
        "status": entry.get("status") or "open",
        "missing": ["evidence", "progress", "verification"],
    }
    source = entry.get("source")
    if source not in (None, "", [], {}):
        result["source"] = copy.deepcopy(source)
    keys = entry.get("keys")
    if isinstance(keys, list) and keys:
        result["keys"] = keys[:5]
    return result


def _runtime_structure_counts(run_dir: Path) -> dict[str, int]:
    counts = {
        "observable_evidence": 0,
        "verification_paths": 0,
        "thread_progress_add": 0,
        "clue_ledger_add": 0,
        "convergence_actions_add": 0,
        "scene_goal_cards": 0,
        "map_grid_seeds": 0,
        "pressure_ticks": 0,
    }
    for turn_dir in sorted(run_dir.glob("turn_*")):
        payload = _read_json_if_exists(turn_dir / "simulation_repaired_report.json")
        if not isinstance(payload, dict):
            payload = _read_json_if_exists(turn_dir / "simulation_report.json")
        if not isinstance(payload, dict):
            continue
        response = payload.get("player_facing_response")
        if isinstance(response, dict):
            counts["observable_evidence"] += _list_len(response.get("observable_evidence"))
            counts["verification_paths"] += _list_len(response.get("verification_paths"))
        archive_patch = payload.get("archive_patch")
        if isinstance(archive_patch, dict):
            counts["thread_progress_add"] += _list_len(archive_patch.get("thread_progress_add"))
            counts["clue_ledger_add"] += _list_len(archive_patch.get("clue_ledger_add"))
            convergence_actions = _as_list(archive_patch.get("convergence_actions_add"))
            counts["convergence_actions_add"] += len(convergence_actions)
            counts["scene_goal_cards"] += sum(1 for action in convergence_actions if _has_scene_goal_card(action))
            counts["map_grid_seeds"] += sum(1 for action in convergence_actions if _has_map_grid_seed(action))
        pressure_patch = payload.get("pressure_patch")
        if isinstance(pressure_patch, dict):
            tick_delta = _number_or_none(pressure_patch.get("tick_delta"))
            if tick_delta and tick_delta > 0:
                counts["pressure_ticks"] += 1
    return counts


def _has_scene_goal_card(action: Any) -> bool:
    if not isinstance(action, dict):
        return False
    scene_goal = action.get("scene_goal")
    if isinstance(scene_goal, dict) and scene_goal:
        return True
    return any(action.get(key) not in (None, "", [], {}) for key in ("scene_goal", "entry_cost", "success_signal", "failure_forward"))


def _has_map_grid_seed(action: Any) -> bool:
    if not isinstance(action, dict):
        return False
    if isinstance(action.get("map_grid_seed"), dict) and action.get("map_grid_seed"):
        return True
    scene_goal = action.get("scene_goal")
    return isinstance(scene_goal, dict) and isinstance(scene_goal.get("map_grid_seed"), dict) and bool(scene_goal.get("map_grid_seed"))


def _usage_summary(run_dir: Path) -> dict[str, Any]:
    files = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    prompt_cache_hit_tokens = 0
    prompt_cache_miss_tokens = 0
    by_phase: dict[str, dict[str, int]] = {}
    no_tool_second_call_files = 0
    no_tool_second_call_tokens = 0
    for path in run_dir.rglob("*.response.json"):
        payload = _read_json_if_exists(path)
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        phase = _usage_phase_from_response_path(path)
        phase_summary = by_phase.setdefault(
            phase,
            {
                "response_files": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
            },
        )
        current_prompt = int(_number_or_none(usage.get("prompt_tokens")) or 0)
        current_completion = int(_number_or_none(usage.get("completion_tokens")) or 0)
        current_total = int(_number_or_none(usage.get("total_tokens")) or 0)
        current_cache_hit = int(_number_or_none(usage.get("prompt_cache_hit_tokens")) or 0)
        current_cache_miss = int(_number_or_none(usage.get("prompt_cache_miss_tokens")) or 0)
        files += 1
        prompt_tokens += current_prompt
        completion_tokens += current_completion
        total_tokens += current_total
        prompt_cache_hit_tokens += current_cache_hit
        prompt_cache_miss_tokens += current_cache_miss
        phase_summary["response_files"] += 1
        phase_summary["prompt_tokens"] += current_prompt
        phase_summary["completion_tokens"] += current_completion
        phase_summary["total_tokens"] += current_total
        phase_summary["prompt_cache_hit_tokens"] += current_cache_hit
        phase_summary["prompt_cache_miss_tokens"] += current_cache_miss
        if phase in {"simulation_audit", "simulation_repair", "multi_turn_audit"}:
            no_tool_second_call_files += 1
            no_tool_second_call_tokens += current_total
    cache_observed = prompt_cache_hit_tokens + prompt_cache_miss_tokens
    for phase_summary in by_phase.values():
        phase_cache_observed = (
            phase_summary.get("prompt_cache_hit_tokens", 0)
            + phase_summary.get("prompt_cache_miss_tokens", 0)
        )
        phase_summary["prompt_cache_hit_ratio"] = (
            round(phase_summary.get("prompt_cache_hit_tokens", 0) / phase_cache_observed, 4)
            if phase_cache_observed
            else None
        )
    return {
        "response_files": files,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_cache_hit_tokens": prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": prompt_cache_miss_tokens,
        "prompt_cache_hit_ratio": round(prompt_cache_hit_tokens / cache_observed, 4) if cache_observed else None,
        "by_phase": by_phase,
        "no_tool_second_call_files": no_tool_second_call_files,
        "no_tool_second_call_tokens": no_tool_second_call_tokens,
    }


def _usage_phase_from_response_path(path: Path) -> str:
    suffix = ".response.json"
    name = path.name
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    if name in {
        "simulation",
        "simulation_audit",
        "simulation_repair",
        "multi_turn_audit",
        "legacy",
        "story_forge",
        "judge",
        "revision",
        "revision_judge",
        "revision_compare",
    }:
        return name
    return "other"


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values) -> float:
    nums = [_number_or_none(value) for value in values]
    nums = [value for value in nums if value is not None]
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def _display_score(value: Any) -> str:
    number = _number_or_none(value)
    if number is None:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _unique_run_dir(parent: Path, name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / name
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    for suffix in range(2, 1000):
        candidate = parent / f"{name}-{suffix}"
        if not candidate.exists():
            candidate.mkdir(parents=True)
            return candidate
    raise RuntimeError(f"Could not create unique run directory for {name!r}.")


def _response_diagnostics(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "finish_reason": result.get("finish_reason"),
        "elapsed_ms": result.get("elapsed_ms"),
        "usage": result.get("usage") or {},
        "text_chars": len(str(result.get("text") or "")),
        "valid_json": isinstance(result.get("payload"), dict),
        "format_error": result.get("format_error") or "",
        "json_repaired": bool(result.get("json_repaired")),
        "request_fallbacks": result.get("request_fallbacks") or [],
    }


def _read_seed(args: argparse.Namespace) -> str:
    if args.seed_file:
        return Path(args.seed_file).read_text(encoding="utf-8")
    if args.seed:
        return str(args.seed)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _read_preference(args: argparse.Namespace) -> str:
    if args.preference_file:
        return Path(args.preference_file).read_text(encoding="utf-8")
    return str(args.preference or "")


def _read_story_file(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("--story-file must contain a JSON object.")
    revised = _revision_story_payload(value)
    if isinstance(revised, dict):
        return revised
    return value


def _read_player_actions(args: argparse.Namespace) -> str:
    if args.simulate_actions_file:
        return Path(args.simulate_actions_file).read_text(encoding="utf-8-sig").strip()
    return str(args.simulate_actions or "").strip()


def _read_player_actions_list(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8-sig")
    stripped = text.strip()
    if not stripped:
        raise ValueError("--simulate-actions-list-file is empty.")

    if stripped.startswith("[") or stripped.startswith("{"):
        value = json.loads(stripped)
        if isinstance(value, dict):
            for key in ("actions", "turns", "player_actions"):
                if isinstance(value.get(key), list):
                    value = value[key]
                    break
        if not isinstance(value, list):
            raise ValueError("--simulate-actions-list-file JSON must be an array, or an object with actions/turns.")
        actions = []
        for index, item in enumerate(value, 1):
            if isinstance(item, str):
                action = item.strip()
            elif isinstance(item, dict):
                action = str(item.get("action") or item.get("player_action") or "").strip()
            else:
                raise ValueError(f"Action item #{index} must be a string or an object with action.")
            if action:
                actions.append(action)
    else:
        actions = [part.strip() for part in re.split(r"(?m)^\s*---+\s*$", text) if part.strip()]

    if not actions:
        raise ValueError("--simulate-actions-list-file contains no player actions.")
    return actions


def _read_simulation_suite(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        raw_cases = value
        suite_id = Path(path).stem
    elif isinstance(value, dict):
        raw_cases = value.get("cases")
        suite_id = str(value.get("id") or Path(path).stem)
    else:
        raise ValueError("--simulation-suite-file must contain a JSON object or array.")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("--simulation-suite-file must contain a non-empty cases array.")

    base_dir = Path(path).resolve().parent
    cases = []
    for index, item in enumerate(raw_cases, 1):
        if not isinstance(item, dict):
            raise ValueError(f"simulation suite case {index} must be an object.")
        story_file = str(item.get("story_file") or "").strip()
        if not story_file:
            raise ValueError(f"simulation suite case {index} is missing story_file.")
        actions_file = str(item.get("actions_file") or item.get("simulate_actions_list_file") or "").strip()
        actions = item.get("actions")
        if not actions_file and actions is None:
            raise ValueError(f"simulation suite case {index} needs actions or actions_file.")
        case = {
            "id": str(item.get("id") or f"case_{index}"),
            "description": str(item.get("description") or ""),
            "story_file": _resolve_suite_path(base_dir, story_file),
            "archive_file": _resolve_suite_path(base_dir, str(item.get("archive_file") or "").strip())
            if item.get("archive_file")
            else "",
            "actions_file": _resolve_suite_path(base_dir, actions_file) if actions_file else "",
            "actions": actions,
            "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        }
        cases.append(case)
    return {"id": suite_id, "cases": cases}


def _resolve_suite_path(base_dir: Path, value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _suite_actions_file(case: dict[str, Any], case_dir: Path) -> str:
    if case.get("actions_file"):
        return str(case["actions_file"])
    actions = case.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"simulation suite case {case.get('id')} contains empty actions.")
    path = case_dir / "actions.generated.json"
    _write_json(path, actions)
    return str(path)


def _read_archive_state(path: str, story_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path:
        return _initial_archive_from_story(story_payload)
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("--archive-file must contain a JSON object.")
    return _archive_with_story_safety_layers(value, story_payload)


def _initial_archive_from_story(story_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(story_payload, dict):
        return {}
    archive: dict[str, Any] = {}
    plan = story_payload.get("session_archive_plan")
    if isinstance(plan, dict):
        archive["canon_facts"] = _as_list(plan.get("canon_facts") or plan.get("canon_facts_to_track"))
        archive["player_known_state"] = _as_list(plan.get("player_known_state"))
        archive["hidden_truth"] = _as_list(plan.get("hidden_truth"))
        archive["open_threads"] = _as_list(plan.get("open_threads"))
    runtime = story_payload.get("runtime_brief")
    if isinstance(runtime, dict):
        archive["do_not_reveal"] = _as_list(runtime.get("do_not_reveal"))
    pressure = _initial_pressure_from_story(story_payload)
    if pressure:
        archive["pressure_clock"] = pressure
    return {key: value for key, value in archive.items() if value not in ([], {}, None, "")}


def _archive_with_story_safety_layers(
    archive: dict[str, Any],
    story_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    result = copy.deepcopy(archive)
    if not isinstance(story_payload, dict):
        return result
    story_archive = _initial_archive_from_story(story_payload)
    for key in ("do_not_reveal", "hidden_truth"):
        _append_unique_values(result, key, story_archive.get(key))
    return {key: value for key, value in result.items() if value not in ([], {}, None, "")}


def _initial_pressure_from_story(story_payload: dict[str, Any]) -> dict[str, Any]:
    scenes = story_payload.get("playable_scene_cards")
    if isinstance(scenes, list):
        for scene in scenes:
            if isinstance(scene, dict) and isinstance(scene.get("pressure_clock"), dict):
                return copy.deepcopy(scene["pressure_clock"])
    runtime = story_payload.get("runtime_brief")
    if isinstance(runtime, dict) and runtime.get("active_pressure"):
        return {"label": str(runtime.get("active_pressure")), "tick": 0}
    return {}


def merge_archive_patch(
    archive: dict[str, Any],
    archive_patch: Any,
    pressure_patch: Any,
    *,
    simulation_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(archive) if isinstance(archive, dict) else {}
    patch = archive_patch if isinstance(archive_patch, dict) else {}

    _merge_clue_ledger(result, _clue_ledger_entries_from_payload(simulation_payload, patch))
    _append_guarded_archive_values(result, "canon_facts", patch.get("canon_facts_add"))
    _append_guarded_archive_values(result, "player_known_state", patch.get("player_known_state_add"))
    _append_unique_values(result, "hidden_truth", patch.get("hidden_truth_add"))
    _merge_open_threads(result, patch.get("open_threads_add"))
    _merge_thread_progress(
        result,
        _guarded_archive_values(result, "thread_progress", patch.get("thread_progress_add")),
    )
    _merge_convergence_actions(
        result,
        _guarded_archive_values(result, "convergence_actions", patch.get("convergence_actions_add")),
    )

    _merge_state_updates(
        result,
        "npc_state",
        _guarded_archive_values(result, "npc_state", patch.get("npc_state_updates")),
        id_keys=("npc_id", "asset_id", "id"),
        history_key="npc_state_history",
    )
    _merge_state_updates(
        result,
        "asset_state",
        _guarded_archive_values(result, "asset_state", patch.get("asset_state_updates")),
        id_keys=("asset_id", "id"),
        history_key="asset_state_history",
    )

    resolved = _as_list(patch.get("open_threads_resolved"))
    if resolved:
        confirmed_resolved = [item for item in resolved if _is_confirmed_resolved_thread(item)]
        if confirmed_resolved:
            _remove_resolved_threads(result, confirmed_resolved)
            _append_unique_values(result, "resolved_threads", confirmed_resolved)
        unconfirmed = [item for item in resolved if item not in confirmed_resolved]
        if unconfirmed:
            _append_unique_values(result, "open_threads", unconfirmed)

    pressure = copy.deepcopy(pressure_patch) if isinstance(pressure_patch, dict) else {}
    _apply_pressure_floor(result, pressure, simulation_payload)
    _merge_pressure_patch(result, pressure)
    _bridge_clue_ledger_parents(result)
    result["revelation_board"] = _build_revelation_board(result)
    return result


def _read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return None
    return json.loads(text)


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None and item != ""]
    return [value]


def _ensure_list(container: dict[str, Any], key: str) -> list[Any]:
    value = container.get(key)
    if isinstance(value, list):
        return value
    if value is None:
        container[key] = []
    else:
        container[key] = [value]
    return container[key]


def _dedupe_key(value: Any) -> str:
    if isinstance(value, str):
        return "str:" + re.sub(r"\s+", " ", value.strip()).casefold()
    try:
        return "json:" + json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return "repr:" + repr(value)


def _append_unique_values(container: dict[str, Any], key: str, values: Any) -> None:
    items = _as_list(values)
    if not items:
        return
    target = _ensure_list(container, key)
    seen = {_dedupe_key(item) for item in target}
    for item in items:
        dedupe_key = _dedupe_key(item)
        if dedupe_key not in seen:
            target.append(copy.deepcopy(item))
            seen.add(dedupe_key)


def _append_guarded_archive_values(container: dict[str, Any], key: str, values: Any) -> None:
    _append_unique_values(container, key, _guarded_archive_values(container, key, values))


def _merge_clue_ledger(container: dict[str, Any], values: Any) -> None:
    entries = _as_list(values)
    if not entries:
        return
    target = _ensure_list(container, "clue_ledger")
    index_by_key: dict[str, int] = {}
    for index, item in enumerate(target):
        for key in _clue_ledger_keys(item):
            index_by_key.setdefault(key, index)

    for entry in entries:
        if not isinstance(entry, dict):
            entry = {"clue_text": str(entry)}
        normalized = _normalize_clue_ledger_entry(entry)
        if not normalized:
            continue
        keys = _clue_ledger_keys(normalized)
        match_index = None
        for key in keys:
            if key in index_by_key:
                match_index = index_by_key[key]
                break
        if match_index is None:
            target.append(normalized)
            new_index = len(target) - 1
            for key in keys:
                index_by_key.setdefault(key, new_index)
            continue

        before = copy.deepcopy(target[match_index])
        target[match_index] = _coalesced_clue_ledger_entry(target[match_index], normalized)
        _append_unique_values(
            container,
            "clue_ledger_history",
            {
                "clue_key": _preferred_thread_key(keys, normalized),
                "update": normalized,
                "before": before,
                "after": copy.deepcopy(target[match_index]),
            },
        )
        for key in _clue_ledger_keys(target[match_index]):
            index_by_key.setdefault(key, match_index)


def _clue_ledger_entries_from_payload(
    simulation_payload: dict[str, Any] | None,
    archive_patch: dict[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    entries.extend(_guarded_archive_values({}, "clue_ledger", archive_patch.get("clue_ledger_add")))
    if entries:
        return entries

    response = simulation_payload.get("player_facing_response") if isinstance(simulation_payload, dict) else None
    evidence_items = response.get("observable_evidence") if isinstance(response, dict) else None
    verification_items = response.get("verification_paths") if isinstance(response, dict) else None
    verification_by_target: dict[str, list[Any]] = {}
    for verification in _as_list(verification_items):
        if not isinstance(verification, dict):
            continue
        target = _clue_target_key(verification.get("target") or verification.get("path_id") or verification.get("action"))
        if target:
            verification_by_target.setdefault(target, []).append(copy.deepcopy(verification))

    for evidence in _as_list(evidence_items):
        if not isinstance(evidence, dict):
            continue
        observation = str(evidence.get("observation") or evidence.get("text") or "").strip()
        if not observation:
            continue
        linked_thread = (
            evidence.get("linked_thread_id")
            or evidence.get("linked_thread")
            or evidence.get("thread_id")
            or _first_thread_id_from_progress(archive_patch.get("thread_progress_add"))
        )
        entry = {
            "clue_id": evidence.get("evidence_id") or _slug(observation) or "clue",
            "parent_thread_id": linked_thread or "",
            "clue_text": observation,
            "status": "observed",
            "game_use": evidence.get("game_use") or "",
            "source": "observable_evidence",
        }
        related_verifications: list[Any] = []
        observation_key = _clue_target_key(observation)
        for target_key, verifications in verification_by_target.items():
            if target_key and (target_key in observation_key or observation_key in target_key):
                related_verifications.extend(verifications)
        if related_verifications:
            entry["next_verification"] = related_verifications
        entries.append(entry)

    return entries


def _normalize_clue_ledger_entry(entry: dict[str, Any]) -> dict[str, Any]:
    clue_text = (
        entry.get("clue_text")
        or entry.get("text")
        or entry.get("observation")
        or entry.get("summary")
        or entry.get("title")
        or ""
    )
    clue_text = _one_line_excerpt(clue_text, 220)
    if not clue_text:
        return {}
    clue_id = str(entry.get("clue_id") or entry.get("id") or _slug(clue_text) or "clue").strip()
    parent_thread = (
        entry.get("parent_thread_id")
        or entry.get("linked_thread_id")
        or entry.get("linked_thread")
        or entry.get("thread_id")
        or ""
    )
    parent_thread_ids = _as_list(entry.get("parent_thread_ids"))
    if parent_thread:
        parent_thread_ids.insert(0, parent_thread)
    parent_thread_ids = _unique_strings(parent_thread_ids)
    normalized = {
        "clue_id": clue_id,
        "parent_thread_id": parent_thread_ids[0] if parent_thread_ids else "",
        "parent_thread_ids": parent_thread_ids,
        "clue_text": clue_text,
        "status": str(entry.get("status") or "observed").strip(),
        "game_use": str(entry.get("game_use") or "").strip(),
    }
    for key in ("next_verification", "source", "risk_or_cost", "asset_id", "npc_id"):
        value = entry.get(key)
        if value not in (None, "", [], {}):
            normalized[key] = copy.deepcopy(value)
    return normalized


def _bridge_clue_ledger_parents(container: dict[str, Any]) -> None:
    open_threads = _as_list(container.get("open_threads"))
    clue_ledger = _ensure_list(container, "clue_ledger")
    if not open_threads or not clue_ledger:
        return
    for clue in clue_ledger:
        if not isinstance(clue, dict):
            continue
        parents = _unique_strings(clue.get("parent_thread_ids") or clue.get("parent_thread_id"))
        for thread in open_threads:
            thread_text = _thread_plain_text(thread)
            if not thread_text:
                continue
            if _clue_relevant_to_thread(clue, thread_text):
                parents.append(thread_text)
        parents = _unique_strings(parents)
        if parents:
            clue["parent_thread_ids"] = parents
            clue["parent_thread_id"] = parents[0]


def _clue_relevant_to_thread(clue: dict[str, Any], thread_text: str) -> bool:
    clue_text = json.dumps(clue, ensure_ascii=False, sort_keys=True)
    thread = str(thread_text or "")
    if not clue_text or not thread:
        return False
    if _thread_text_key(thread) in _thread_text_key(clue_text):
        return True
    if any(marker in thread for marker in ("管理员", "老莫", "失踪")):
        return any(
            marker in clue_text
            for marker in ("人形", "人影", "影子", "钥匙", "锁", "撬", "布料", "纤维", "脚印", "维护", "日志", "照片", "档案", "老莫", "管理员")
        )
    return False


def _coalesced_clue_ledger_entry(existing: Any, update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        existing = _normalize_clue_ledger_entry({"clue_text": existing})
    merged = copy.deepcopy(existing)
    for key, value in update.items():
        if value in (None, "", [], {}):
            continue
        if key in {"next_verification", "parent_thread_ids"}:
            current = _as_list(merged.get(key))
            seen = {_dedupe_key(item) for item in current}
            for item in _as_list(value):
                item_key = _dedupe_key(item)
                if item_key not in seen:
                    current.append(copy.deepcopy(item))
                    seen.add(item_key)
            merged[key] = current
            if key == "parent_thread_ids" and current:
                merged["parent_thread_id"] = str(current[0])
        elif key == "status" and merged.get(key) != value:
            _append_unique_values(merged, "status_history", value)
            merged[key] = copy.deepcopy(value)
        elif key not in merged or len(str(value)) > len(str(merged.get(key) or "")):
            merged[key] = copy.deepcopy(value)
    return merged


def _clue_ledger_keys(value: Any) -> set[str]:
    if not isinstance(value, dict):
        return {_thread_text_key(str(value))}
    keys = set()
    for key in ("clue_id", "id"):
        current = value.get(key)
        if current:
            keys.add("id:" + _thread_text_key(str(current)))
    clue_text = value.get("clue_text") or value.get("text") or value.get("observation")
    if clue_text:
        keys.add("text:" + _clue_target_key(clue_text))
    parents = _as_list(value.get("parent_thread_ids"))
    parent = value.get("parent_thread_id") or value.get("thread_id") or value.get("linked_thread_id")
    if parent:
        parents.insert(0, parent)
    for parent_value in _unique_strings(parents):
        if parent_value and clue_text:
            keys.add("parent_text:" + _thread_text_key(str(parent_value)) + ":" + _clue_target_key(clue_text))
    return {key for key in keys if key and not key.endswith(":")}


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in _as_list(values):
        text = str(value or "").strip()
        if not text:
            continue
        key = _thread_text_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _clue_target_key(value: Any) -> str:
    text = _one_line_excerpt(value, 120).casefold()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _first_thread_id_from_progress(values: Any) -> str:
    for progress in _as_list(values):
        if not isinstance(progress, dict):
            continue
        for key in ("thread_id", "linked_thread_id", "linked_thread", "id"):
            current = progress.get(key)
            if current:
                return str(current)
    return ""


def _merge_open_threads(container: dict[str, Any], values: Any) -> None:
    items = _as_list(values)
    if not items:
        return
    target = _ensure_list(container, "open_threads")
    index_by_key: dict[str, int] = {}
    for index, item in enumerate(target):
        for key in _open_thread_keys(item):
            index_by_key.setdefault(key, index)

    new_thread_budget = 1
    for item in items:
        item_copy = copy.deepcopy(item)
        keys = _open_thread_keys(item_copy)
        match_index = None
        for key in keys:
            if key in index_by_key:
                match_index = index_by_key[key]
                break
        if match_index is None:
            if new_thread_budget <= 0:
                _append_unique_values(container, "thread_hints", _thread_hint_from_open_thread(item_copy))
                continue
            target.append(item_copy)
            new_thread_budget -= 1
            for key in keys:
                index_by_key.setdefault(key, len(target) - 1)
            continue

        existing = target[match_index]
        merged = _prefer_more_detailed_thread(existing, item_copy)
        if merged != existing:
            target[match_index] = merged
            _append_unique_values(
                container,
                "open_thread_history",
                {"merged_from": item_copy, "into": copy.deepcopy(merged)},
            )
        for key in _open_thread_keys(merged):
            index_by_key.setdefault(key, match_index)


def _prefer_more_detailed_thread(existing: Any, incoming: Any) -> Any:
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = copy.deepcopy(existing)
        merged.update({key: value for key, value in incoming.items() if value not in (None, "", [], {})})
        return merged
    if isinstance(existing, str) and isinstance(incoming, str):
        return incoming if len(incoming) > len(existing) else existing
    return existing


def _open_thread_keys(value: Any) -> set[str]:
    keys = _thread_match_keys(value)
    text = _thread_plain_text(value)
    parsed_id = _thread_id_from_text(text)
    if parsed_id:
        keys.add(parsed_id)
    subject = _thread_subject_from_text(text)
    if subject:
        keys.add(subject)
        keys.update(_thread_subject_aliases(subject))
    if isinstance(value, dict):
        for key in ("id", "thread_id", "linked_thread", "linked_thread_id"):
            current = value.get(key)
            if current:
                keys.add(_thread_text_key(str(current)))
    return {key for key in keys if key}


def _thread_plain_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "title", "summary", "id", "thread_id", "linked_thread", "linked_thread_id"):
            current = value.get(key)
            if current:
                return str(current)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "")


def _thread_id_from_text(text: str) -> str:
    match = re.search(r"\b(thread[_-]?[a-z0-9]+)\b", text, flags=re.I)
    if not match:
        return ""
    return _thread_text_key(match.group(1))


def _thread_subject_from_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return ""
    value = re.sub(r"^\s*THREAD[_-]?\w+\s*[:：]\s*", "", value, flags=re.I)
    paren = re.match(r"^\s*THREAD[_-]?\w+\s*[（(]([^）)]+)[）)]", value, flags=re.I)
    if paren:
        value = paren.group(1)
    value = re.split(r"[:：?？]", value, maxsplit=1)[0]
    value = re.sub(r"[（）()【】\[\]{}]", "", value).strip()
    return _thread_text_key(value)


def _thread_subject_aliases(subject: str) -> set[str]:
    aliases = {subject}
    if subject.endswith("为何重亮"):
        aliases.add(subject.replace("为何重亮", "为什么重亮"))
    if subject.endswith("为什么重亮"):
        aliases.add(subject.replace("为什么重亮", "为何重亮"))
    if "铁门撬痕" in subject:
        aliases.add("铁门撬痕")
    return {alias for alias in aliases if alias}


def _thread_text_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _guarded_archive_values(container: dict[str, Any], key: str, values: Any) -> list[Any]:
    accepted: list[Any] = []
    for item in _as_list(values):
        reason = _archive_guard_rejection_reason(container, item)
        if reason:
            _append_unique_values(
                container,
                "archive_guard_rejections",
                {
                    "target_key": key,
                    "reason": reason,
                    "value": copy.deepcopy(item),
                },
            )
        else:
            accepted.append(item)
    return accepted


def _archive_guard_rejection_reason(container: dict[str, Any], value: Any) -> str:
    text = _archive_guard_text(value)
    if not text:
        return ""
    secret_terms = _archive_secret_terms(container)
    if not secret_terms:
        return ""
    lowered = text.casefold()
    matched_terms = [term for term in secret_terms if term.casefold() in lowered]
    if not matched_terms:
        return ""
    certainty_markers = (
        "就是",
        "真实身份",
        "主谋",
        "幕后",
        "隐藏实体",
        "特工",
        "确认为",
        "确认是",
        "身份为",
        "属于",
        "opposition agent",
        "is opposition",
        "is an opposition",
        "mastermind",
        "true identity",
        "is the",
    )
    uncertainty_markers = (
        "疑似",
        "可能",
        "似乎",
        "表现为",
        "尚未确认",
        "未确认",
        "无法确认",
        "待验证",
        "可疑",
        "possibly",
        "appears",
        "unconfirmed",
        "suspected",
    )
    if any(marker.casefold() in lowered for marker in uncertainty_markers):
        return ""
    if any(marker.casefold() in lowered for marker in certainty_markers):
        return "possible_hidden_truth_promotion:" + ",".join(matched_terms[:3])
    return ""


def _archive_guard_text(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def _archive_secret_terms(container: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("do_not_reveal", "hidden_truth"):
        for item in _as_list(container.get(key)):
            text = _archive_guard_text(item)
            if text.strip():
                terms.append(text.strip())
                terms.extend(_secret_keyword_fragments(text))
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        normalized = re.sub(r"\s+", " ", term.strip()).casefold()
        if len(normalized) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(term)
    return unique


def _secret_keyword_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    cleaned = re.sub(r"[（）()【】\[\]{}，,。；;：:、/\\|]+", " ", text)
    for token in cleaned.split():
        token = token.strip()
        if len(token) >= 3:
            fragments.append(token)
    for keyword in (
        "反对派",
        "具体身份",
        "真实身份",
        "主谋",
        "幕后",
        "隐藏实体",
        "秘密联系",
        "间谍",
        "晶片",
        "剑灵",
        "撒谎",
    ):
        if keyword in text:
            fragments.append(keyword)
    return fragments


def _merge_state_updates(
    container: dict[str, Any],
    key: str,
    values: Any,
    *,
    id_keys: tuple[str, ...],
    history_key: str,
) -> None:
    updates = _as_list(values)
    if not updates:
        return

    target = _ensure_list(container, key)
    index_by_id: dict[str, int] = {}
    for index, item in enumerate(target):
        item_id = _state_update_id(item, id_keys)
        if item_id:
            index_by_id[item_id] = index

    for update in updates:
        update_copy = copy.deepcopy(update)
        item_id = _state_update_id(update_copy, id_keys)
        if item_id and item_id in index_by_id and isinstance(update_copy, dict):
            existing = target[index_by_id[item_id]]
            if isinstance(existing, dict):
                before = copy.deepcopy(existing)
                existing.update(update_copy)
                _append_unique_values(
                    container,
                    history_key,
                    {
                        "id": item_id,
                        "before": before,
                        "update": update_copy,
                        "after": copy.deepcopy(existing),
                    },
                )
                continue
        _append_unique_values(container, key, update_copy)
        item_id = _state_update_id(update_copy, id_keys)
        if item_id:
            index_by_id[item_id] = len(_ensure_list(container, key)) - 1
            _append_unique_values(
                container,
                history_key,
                {"id": item_id, "update": update_copy, "after": copy.deepcopy(update_copy)},
            )


def _state_update_id(value: Any, id_keys: tuple[str, ...]) -> str:
    if not isinstance(value, dict):
        return ""
    for key in id_keys:
        item_id = value.get(key)
        if item_id:
            return str(item_id).strip().casefold()
    return ""


def _merge_thread_progress(container: dict[str, Any], values: Any) -> None:
    updates = _as_list(values)
    if not updates:
        return

    target = _ensure_list(container, "thread_progress")
    index_by_key: dict[str, int] = {}
    for index, item in enumerate(target):
        for key in _thread_match_keys(item):
            index_by_key.setdefault(key, index)

    for update in updates:
        update_copy = copy.deepcopy(update)
        _ensure_progress_thread_defined(container, update_copy)
        update_keys = _thread_match_keys(update_copy)
        match_index = None
        for key in update_keys:
            if key in index_by_key:
                match_index = index_by_key[key]
                break
        if match_index is None:
            _append_unique_values(container, "thread_progress", update_copy)
            new_index = len(_ensure_list(container, "thread_progress")) - 1
            for key in _thread_match_keys(update_copy):
                index_by_key.setdefault(key, new_index)
            continue

        existing = target[match_index]
        merged = _coalesced_thread_progress(existing, update_copy)
        target[match_index] = merged
        _append_unique_values(
            container,
            "thread_progress_history",
            {
                "thread_key": _preferred_thread_key(update_keys, update_copy),
                "update": update_copy,
                "after": copy.deepcopy(merged),
            },
        )
        for key in _thread_match_keys(merged):
            index_by_key.setdefault(key, match_index)


def _merge_convergence_actions(container: dict[str, Any], values: Any) -> None:
    updates = _as_list(values)
    if not updates:
        return

    target = _ensure_list(container, "convergence_actions")
    index_by_key: dict[str, int] = {}
    for index, item in enumerate(target):
        for key in _convergence_action_keys(item):
            index_by_key.setdefault(key, index)

    for update in updates:
        normalized = _normalize_convergence_action(update)
        if not normalized:
            continue
        _ensure_progress_thread_defined(container, normalized)
        keys = _convergence_action_keys(normalized)
        match_index = None
        for key in keys:
            if key in index_by_key:
                match_index = index_by_key[key]
                break
        if match_index is None:
            target.append(normalized)
            new_index = len(target) - 1
            for key in keys:
                index_by_key.setdefault(key, new_index)
            continue

        before = copy.deepcopy(target[match_index])
        target[match_index] = _coalesced_convergence_action(target[match_index], normalized)
        _append_unique_values(
            container,
            "convergence_action_history",
            {
                "action_key": _preferred_thread_key(keys, normalized),
                "update": normalized,
                "before": before,
                "after": copy.deepcopy(target[match_index]),
            },
        )
        for key in _convergence_action_keys(target[match_index]):
            index_by_key.setdefault(key, match_index)


def _normalize_convergence_action(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        text = _one_line_excerpt(value, 220)
        return {"thread_id": "", "action_type": "synthesis", "synthesis": text} if text else {}
    thread_id = str(
        value.get("thread_id")
        or value.get("linked_thread_id")
        or value.get("linked_thread")
        or value.get("id")
        or ""
    ).strip()
    action_type = str(value.get("action_type") or value.get("type") or "synthesis").strip()
    normalized: dict[str, Any] = {
        "thread_id": thread_id,
        "action_type": action_type,
    }
    scene_goal_value = value.get("scene_goal")
    if isinstance(scene_goal_value, dict):
        scene_goal_text = _one_line_excerpt(
            scene_goal_value.get("goal")
            or scene_goal_value.get("objective")
            or scene_goal_value.get("scene_goal")
            or value.get("next_scene_entry")
            or value.get("available_action")
            or value.get("synthesis"),
            260,
        )
        if scene_goal_text:
            normalized["scene_goal"] = scene_goal_text
        for key in ("entry_cost", "success_signal", "failure_forward"):
            text = _one_line_excerpt(value.get(key) or scene_goal_value.get(key), 260)
            if text:
                normalized[key] = text
        nested_map_grid_seed = scene_goal_value.get("map_grid_seed")
        if isinstance(nested_map_grid_seed, dict) and nested_map_grid_seed:
            normalized["map_grid_seed"] = copy.deepcopy(nested_map_grid_seed)
    text_fields = (
        "synthesis",
        "next_scene_entry",
        "npc_move",
        "pressure_or_consequence",
        "available_action",
        "scene_goal",
        "entry_cost",
        "success_signal",
        "failure_forward",
    )
    for key in text_fields:
        if key == "scene_goal" and isinstance(scene_goal_value, dict):
            continue
        text = _one_line_excerpt(value.get(key), 260)
        if text:
            normalized[key] = text
    map_grid_seed = value.get("map_grid_seed")
    if isinstance(map_grid_seed, dict) and map_grid_seed:
        normalized["map_grid_seed"] = copy.deepcopy(map_grid_seed)
    for key in ("scene_id", "npc_id", "asset_id", "risk_or_cost", "source"):
        if value.get(key) not in (None, "", [], {}):
            normalized[key] = copy.deepcopy(value[key])
    if not any(normalized.get(key) for key in text_fields) and "map_grid_seed" not in normalized:
        return {}
    return normalized


def _convergence_action_keys(value: Any) -> set[str]:
    if not isinstance(value, dict):
        text = _thread_text_key(str(value or ""))
        return {"text:" + text} if text else set()
    keys = _thread_match_keys(value)
    thread_id = value.get("thread_id") or value.get("linked_thread_id") or value.get("linked_thread")
    action_type = str(value.get("action_type") or "").strip().casefold()
    signature_source = (
        value.get("scene_goal")
        or value.get("next_scene_entry")
        or value.get("available_action")
        or value.get("npc_move")
        or value.get("pressure_or_consequence")
        or value.get("synthesis")
    )
    signature = _clue_target_key(signature_source)
    if thread_id and action_type and signature:
        keys.add("converge:" + _thread_text_key(str(thread_id)) + ":" + action_type + ":" + signature)
    elif signature:
        keys.add("converge:" + signature)
    return {key for key in keys if key}


def _coalesced_convergence_action(existing: Any, update: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(existing, dict):
        existing = _normalize_convergence_action(existing)
    merged = copy.deepcopy(existing)
    for key, value in update.items():
        if value in (None, "", [], {}):
            continue
        if key == "map_grid_seed":
            if _prefer_incoming_map_grid_seed(merged.get(key), value):
                merged[key] = copy.deepcopy(value)
            continue
        if key not in merged or len(str(value)) > len(str(merged.get(key) or "")):
            merged[key] = copy.deepcopy(value)
    return merged


def _prefer_incoming_map_grid_seed(existing: Any, incoming: Any) -> bool:
    if not isinstance(incoming, dict) or not incoming:
        return False
    if not isinstance(existing, dict) or not existing:
        return True
    incoming_grid = incoming.get("grid") if isinstance(incoming.get("grid"), dict) else incoming
    existing_grid = existing.get("grid") if isinstance(existing.get("grid"), dict) else existing
    incoming_has_shape = isinstance(incoming_grid, dict) and (
        incoming_grid.get("width") not in (None, "", [], {})
        or incoming_grid.get("height") not in (None, "", [], {})
        or incoming_grid.get("cells") not in (None, "", [], {})
    )
    existing_has_shape = isinstance(existing_grid, dict) and (
        existing_grid.get("width") not in (None, "", [], {})
        or existing_grid.get("height") not in (None, "", [], {})
        or existing_grid.get("cells") not in (None, "", [], {})
    )
    return incoming_has_shape and not existing_has_shape


def _ensure_progress_thread_defined(container: dict[str, Any], update: Any) -> None:
    if not isinstance(update, dict):
        return
    thread_id = (
        update.get("thread_id")
        or update.get("linked_thread_id")
        or update.get("linked_thread")
        or update.get("id")
    )
    if not thread_id:
        return
    thread_key = _thread_text_key(str(thread_id))
    existing_keys: set[str] = set()
    for item in _ensure_list(container, "open_threads"):
        existing_keys.update(_open_thread_keys(item))
    for item in _as_list(container.get("thread_hints")):
        existing_keys.update(_open_thread_keys(item))
    if thread_key in existing_keys:
        return
    thread_text = _thread_definition_from_progress(update)
    _merge_open_threads(container, thread_text)


def _thread_definition_from_progress(update: dict[str, Any]) -> str:
    thread_id = (
        update.get("thread_id")
        or update.get("linked_thread_id")
        or update.get("linked_thread")
        or update.get("id")
        or "THREAD_unknown"
    )
    unknown = update.get("remaining_unknown") or update.get("remaining_questions") or update.get("progress")
    text = _one_line_excerpt(unknown, 80) if unknown else "待验证线索"
    return f"{thread_id}: {text}"


def _thread_hint_from_open_thread(value: Any) -> dict[str, Any]:
    return {
        "source": "open_threads_add_over_budget",
        "text": copy.deepcopy(value),
    }


def _build_revelation_board(archive: dict[str, Any]) -> list[dict[str, Any]]:
    board: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}

    for thread in _as_list(archive.get("open_threads")):
        entry = _revelation_entry_from_thread(thread, status="open")
        _append_revelation_entry(board, index_by_key, entry)

    for thread in _as_list(archive.get("resolved_threads")):
        entry = _revelation_entry_from_thread(thread, status="resolved")
        _append_revelation_entry(board, index_by_key, entry)

    for progress in _as_list(archive.get("thread_progress")):
        entry = _revelation_entry_from_progress(progress)
        index = _append_revelation_entry(board, index_by_key, entry)
        board[index]["progress"].append(copy.deepcopy(progress))
        for evidence in _progress_evidence_items(progress):
            _append_revelation_list_item(board[index], "evidence", evidence)
        for verification in _progress_verification_items(progress):
            _append_revelation_list_item(board[index], "verification_paths", verification)

    for clue in _as_list(archive.get("clue_ledger")):
        for entry in _revelation_entries_from_clue(clue):
            index = _append_revelation_entry(board, index_by_key, entry)
            _append_revelation_list_item(board[index], "evidence", clue)
            _append_revelation_list_item(board[index], "hints", clue)
            if isinstance(clue, dict):
                for verification in _as_list(clue.get("next_verification")):
                    _append_revelation_list_item(board[index], "verification_paths", verification)

    for action in _as_list(archive.get("convergence_actions")):
        entry = _revelation_entry_from_convergence_action(action)
        index = _append_revelation_entry(board, index_by_key, entry)
        _append_revelation_list_item(board[index], "progress", action)
        if isinstance(action, dict) and action.get("available_action"):
            _append_revelation_list_item(board[index], "verification_paths", action.get("available_action"))

    for hint in _as_list(archive.get("thread_hints")) + _as_list(archive.get("open_thread_overflow")):
        entry = _revelation_entry_from_thread(hint, status="hint")
        index = _append_revelation_entry(board, index_by_key, entry)
        _append_revelation_list_item(board[index], "hints", hint)

    for entry in board:
        entry["evidence_count"] = len(entry.get("evidence") or [])
        entry["verification_count"] = len(entry.get("verification_paths") or [])
        entry["hint_count"] = len(entry.get("hints") or [])
        entry["progress_count"] = len(entry.get("progress") or [])
    return board


def _append_revelation_entry(
    board: list[dict[str, Any]],
    index_by_key: dict[str, int],
    entry: dict[str, Any],
) -> int:
    keys = set(entry.get("keys") or [])
    for key in keys:
        if key in index_by_key:
            index = index_by_key[key]
            existing = board[index]
            if existing.get("status") != "resolved" and entry.get("status") == "resolved":
                existing["status"] = "resolved"
            for alias in entry.get("aliases") or []:
                _append_revelation_list_item(existing, "aliases", alias)
            for key_to_add in keys:
                if key_to_add not in existing["keys"]:
                    existing["keys"].append(key_to_add)
                    index_by_key[key_to_add] = index
            return index
    entry.setdefault("progress", [])
    entry.setdefault("evidence", [])
    entry.setdefault("verification_paths", [])
    entry.setdefault("hints", [])
    entry.setdefault("aliases", [])
    board.append(entry)
    index = len(board) - 1
    for key in keys:
        index_by_key[key] = index
    return index


def _revelation_entry_from_thread(thread: Any, *, status: str) -> dict[str, Any]:
    text = _thread_plain_text(thread)
    keys = sorted(_open_thread_keys(thread))
    thread_id = _thread_id_from_text(text) or _thread_primary_id(thread)
    title = _thread_title(text) or _one_line_excerpt(text, 80) or "untitled_thread"
    entry = {
        "thread_id": thread_id or title,
        "title": title,
        "status": status,
        "source": copy.deepcopy(thread),
        "keys": keys or [_thread_text_key(title)],
        "aliases": [],
        "progress": [],
        "evidence": [],
        "verification_paths": [],
        "hints": [],
    }
    if isinstance(thread, dict):
        for item in _as_list(thread.get("progress")):
            _append_revelation_list_item(entry, "progress", item)
        for item in _as_list(thread.get("evidence")):
            _append_revelation_list_item(entry, "evidence", item)
        for item in _as_list(thread.get("verification_paths")):
            _append_revelation_list_item(entry, "verification_paths", item)
        for item in _as_list(thread.get("hints")):
            _append_revelation_list_item(entry, "hints", item)
    return entry


def _revelation_entry_from_progress(progress: Any) -> dict[str, Any]:
    if isinstance(progress, dict):
        thread_id = (
            progress.get("thread_id")
            or progress.get("linked_thread_id")
            or progress.get("linked_thread")
            or progress.get("id")
            or ""
        )
        title_source = (
            progress.get("title")
            or progress.get("remaining_unknown")
            or progress.get("remaining_questions")
            or progress.get("progress")
            or thread_id
        )
        text = str(thread_id or title_source or "")
    else:
        thread_id = _thread_id_from_text(str(progress or ""))
        title_source = progress
        text = str(progress or "")
    keys = sorted(_open_thread_keys(progress))
    if thread_id:
        keys.append(_thread_text_key(str(thread_id)))
    title = _thread_title(str(title_source or text)) or _one_line_excerpt(title_source, 80) or str(thread_id or "thread_progress")
    return {
        "thread_id": _thread_text_key(str(thread_id)) if thread_id else title,
        "title": title,
        "status": "open",
        "source": None,
        "keys": sorted(set(key for key in keys if key)),
        "aliases": [],
    }


def _revelation_entries_from_clue(clue: Any) -> list[dict[str, Any]]:
    if isinstance(clue, dict):
        parents = _as_list(clue.get("parent_thread_ids"))
        parent = (
            clue.get("parent_thread_id")
            or clue.get("thread_id")
            or clue.get("linked_thread_id")
            or clue.get("linked_thread")
            or ""
        )
        if parent:
            parents.insert(0, parent)
        parents = _unique_strings(parents)
        if not parents:
            parents = [""]
    else:
        parents = [""]
    entries = []
    for parent in parents:
        if isinstance(clue, dict):
            title_source = parent or clue.get("clue_text") or clue.get("title") or clue.get("clue_id") or "clue_ledger"
            keys = _thread_match_keys({"thread_id": parent, "title": parent}) if parent else set()
            if parent:
                keys.update(_open_thread_keys(str(parent)))
        else:
            title_source = clue
            keys = set()
        title = _thread_title(str(title_source or "")) or _one_line_excerpt(title_source, 80) or "clue_ledger"
        entries.append(
            {
                "thread_id": _thread_text_key(str(parent)) if parent else title,
                "title": title,
                "status": "open" if parent else "hint",
                "source": None,
                "keys": sorted(set(key for key in keys if key)),
                "aliases": [],
            }
        )
    return entries


def _revelation_entry_from_clue(clue: Any) -> dict[str, Any]:
    entries = _revelation_entries_from_clue(clue)
    return entries[0] if entries else {
        "thread_id": "clue_ledger",
        "title": "clue_ledger",
        "status": "hint",
        "source": None,
        "keys": [],
        "aliases": [],
    }


def _revelation_entry_from_convergence_action(action: Any) -> dict[str, Any]:
    if isinstance(action, dict):
        thread_id = (
            action.get("thread_id")
            or action.get("linked_thread_id")
            or action.get("linked_thread")
            or action.get("id")
            or ""
        )
        title_source = (
            thread_id
            or action.get("scene_goal")
            or action.get("next_scene_entry")
            or action.get("synthesis")
            or "convergence_action"
        )
        keys = _thread_match_keys({"thread_id": thread_id, "title": title_source})
        if thread_id:
            keys.update(_open_thread_keys(str(thread_id)))
        title = _thread_title(str(title_source or "")) or _one_line_excerpt(title_source, 80) or "convergence_action"
        return {
            "thread_id": _thread_text_key(str(thread_id)) if thread_id else title,
            "title": title,
            "status": "open",
            "source": None,
            "keys": sorted(set(key for key in keys if key)),
            "aliases": [],
        }
    title = _one_line_excerpt(action, 80) or "convergence_action"
    return {
        "thread_id": title,
        "title": title,
        "status": "hint",
        "source": None,
        "keys": [_thread_text_key(title)] if title else [],
        "aliases": [],
    }


def _thread_primary_id(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "thread_id", "linked_thread_id", "linked_thread"):
            if value.get(key):
                return _thread_text_key(str(value[key]))
    return ""


def _thread_title(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = re.sub(r"^\s*THREAD[_-]?\w+\s*[:：]\s*", "", value, flags=re.I)
    paren = re.match(r"^\s*THREAD[_-]?\w+\s*[（(]([^）)]+)[）)]", value, flags=re.I)
    if paren:
        value = paren.group(1)
    value = re.split(r"[:：]", value, maxsplit=1)[0]
    return value.strip()


def _progress_evidence_items(progress: Any) -> list[Any]:
    if not isinstance(progress, dict):
        return [progress] if progress else []
    items: list[Any] = []
    for key in ("new_evidence", "evidence", "progress"):
        items.extend(_as_list(progress.get(key)))
    return items


def _progress_verification_items(progress: Any) -> list[Any]:
    if not isinstance(progress, dict):
        return []
    items: list[Any] = []
    for key in ("next_verification", "verification_paths"):
        items.extend(_as_list(progress.get(key)))
    return items


def _append_revelation_list_item(entry: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    items = entry.setdefault(key, [])
    dedupe = {_dedupe_key(item) for item in items}
    value_key = _dedupe_key(value)
    if value_key not in dedupe:
        items.append(copy.deepcopy(value))


def _coalesced_thread_progress(existing: Any, update: Any) -> Any:
    if not isinstance(existing, dict) or not isinstance(update, dict):
        return copy.deepcopy(update)
    merged = copy.deepcopy(existing)
    for key, value in update.items():
        if key in {"new_evidence", "evidence", "remaining_unknown", "remaining_questions", "next_verification", "verification_paths"}:
            current = _as_list(merged.get(key))
            seen = {_dedupe_key(item) for item in current}
            for item in _as_list(value):
                item_key = _dedupe_key(item)
                if item_key not in seen:
                    current.append(copy.deepcopy(item))
                    seen.add(item_key)
            merged[key] = current
        elif value not in (None, "", [], {}):
            merged[key] = copy.deepcopy(value)
    return merged


def _preferred_thread_key(keys: set[str], fallback: Any) -> str:
    for key in sorted(keys):
        if not key.startswith(("json:", "str:", "repr:")):
            return key
    return sorted(keys)[0] if keys else _dedupe_key(fallback)


def _thread_match_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key in ("id", "thread_id", "linked_thread", "linked_thread_id", "text", "title", "summary"):
            current = value.get(key)
            if current:
                keys.update(_thread_text_keys(current))
        keys.add(_dedupe_key(value))
    elif value:
        keys.update(_thread_text_keys(value))
        keys.add(_dedupe_key(value))
    return {key for key in keys if key}


def _thread_text_keys(value: Any) -> set[str]:
    text = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    if not text:
        return set()
    keys = {text}
    for delimiter in ("——", "--", " - ", "：", ":"):
        if delimiter in text:
            keys.add(text.split(delimiter, 1)[0].strip())
    return {key for key in keys if key}


def _is_confirmed_resolved_thread(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value or "")
    uncertain_markers = (
        "可能",
        "尚未",
        "仍不明确",
        "不明确",
        "未锁定",
        "未确认",
        "需要继续",
        "还需",
        "待确认",
        "unknown",
        "unclear",
        "not yet",
    )
    lowered = text.casefold()
    return not any(marker.casefold() in lowered for marker in uncertain_markers)


def _remove_resolved_threads(container: dict[str, Any], resolved: list[Any]) -> None:
    open_threads = _ensure_list(container, "open_threads")
    resolved_keys: set[str] = set()
    for item in resolved:
        resolved_keys.update(_open_thread_keys(item))
    if not resolved_keys:
        return
    container["open_threads"] = [
        item for item in open_threads if not _thread_is_resolved(item, resolved_keys)
    ]


def _thread_is_resolved(open_thread: Any, resolved_keys: set[str]) -> bool:
    open_keys = _open_thread_keys(open_thread)
    if not open_keys.isdisjoint(resolved_keys):
        return True
    for open_key in open_keys:
        for resolved_key in resolved_keys:
            if open_key and resolved_key and (open_key in resolved_key or resolved_key in open_key):
                return True
    return False


def _merge_pressure_patch(container: dict[str, Any], patch: dict[str, Any]) -> None:
    if not patch:
        return

    current = container.get("pressure_clock")
    if not isinstance(current, dict):
        current = {}

    previous_tick = _number_or_none(current.get("tick"))
    existing_label = current.get("label") or current.get("clock_id")
    label = existing_label or patch.get("label") or patch.get("clock_label") or patch.get("clock_id") or "pressure_clock"
    existing_clock_id = current.get("clock_id") or current.get("label")
    clock = copy.deepcopy(current)
    clock["label"] = label
    if existing_clock_id:
        clock["clock_id"] = existing_clock_id
    elif patch.get("clock_id"):
        clock["clock_id"] = patch.get("clock_id")
    if patch.get("max") is not None:
        clock["max"] = patch.get("max")
    elif "max" in current:
        clock["max"] = current.get("max")
    if patch.get("status") is not None:
        clock["status"] = patch.get("status")

    explicit_tick = _number_or_none(patch.get("new_tick"))
    if explicit_tick is None:
        explicit_tick = _number_or_none(patch.get("tick"))
    tick_delta = _number_or_none(patch.get("tick_delta"))
    if explicit_tick is not None:
        clock["tick"] = int(explicit_tick) if explicit_tick.is_integer() else explicit_tick
    elif tick_delta is not None:
        base_tick = previous_tick if previous_tick is not None else 0.0
        new_tick = base_tick + tick_delta
        clock["tick"] = int(new_tick) if new_tick.is_integer() else new_tick
    elif previous_tick is not None:
        clock["tick"] = int(previous_tick) if previous_tick.is_integer() else previous_tick

    container["pressure_clock"] = clock

    history_entry = {
        "label": label,
        "clock_id": clock.get("clock_id") or "",
        "patch_clock_id": patch.get("clock_id") or patch.get("label") or patch.get("clock_label") or "",
        "previous_tick": int(previous_tick) if previous_tick is not None and previous_tick.is_integer() else previous_tick,
        "new_tick": clock.get("tick"),
        "tick_delta": patch.get("tick_delta", 0),
        "trigger": patch.get("trigger") or "",
        "visible_effect": patch.get("visible_effect") or "",
    }
    _append_unique_values(container, "pressure_history", history_entry)


def _apply_pressure_floor(
    archive: dict[str, Any],
    pressure_patch: dict[str, Any],
    simulation_payload: dict[str, Any] | None,
) -> None:
    if not isinstance(pressure_patch, dict):
        return
    current = archive.get("pressure_clock")
    if not isinstance(current, dict):
        return
    tick_delta = _number_or_none(pressure_patch.get("tick_delta"))
    explicit_tick = _number_or_none(pressure_patch.get("new_tick") or pressure_patch.get("tick"))
    current_tick = _number_or_none(current.get("tick"))
    if tick_delta and tick_delta > 0:
        return
    if explicit_tick is not None and current_tick is not None and explicit_tick > current_tick:
        return
    if not _payload_shows_pressure_floor_trigger(simulation_payload):
        return
    pressure_patch["tick_delta"] = 1
    base_tick = current_tick or 0.0
    new_tick = base_tick + 1
    pressure_patch["new_tick"] = int(new_tick) if new_tick.is_integer() else new_tick
    pressure_patch.setdefault("clock_id", current.get("clock_id") or current.get("label") or "")
    pressure_patch["trigger"] = "玩家直接向隐藏真相施压并引发环境/NPC可感知反应，测试工具按压力下限推进。"
    pressure_patch["visible_effect"] = _pressure_floor_visible_effect(simulation_payload)
    pressure_patch["pressure_floor_applied"] = True


def _payload_shows_pressure_floor_trigger(simulation_payload: dict[str, Any] | None) -> bool:
    if not isinstance(simulation_payload, dict):
        return False
    text = json.dumps(simulation_payload, ensure_ascii=False)
    secret_probe_markers = (
        "是不是活人",
        "是不是",
        "直接回答",
        "直接告诉",
        "眨灯",
        "确认真相",
        "套问",
        "向隐藏真相施压",
    )
    reaction_markers = (
        "撞击",
        "刮擦",
        "警觉",
        "戒备",
        "靠近",
        "脚步",
        "灯光",
        "熄灭",
        "偏移",
        "回应",
        "NPC",
        "压力",
        "visible_effect",
    )
    lowered = text.casefold()
    return any(marker.casefold() in lowered for marker in secret_probe_markers) and any(
        marker.casefold() in lowered for marker in reaction_markers
    )


def _pressure_floor_visible_effect(simulation_payload: dict[str, Any] | None) -> str:
    if not isinstance(simulation_payload, dict):
        return "现场出现可感知反应，调查风险上升。"
    response = simulation_payload.get("player_facing_response")
    if isinstance(response, dict):
        for key in ("immediate_feedback", "narration"):
            value = response.get(key)
            excerpt = _one_line_excerpt(value, 120)
            if excerpt:
                return excerpt
    return "现场出现可感知反应，调查风险上升。"


def _read_api_key(args: argparse.Namespace) -> str:
    if args.api_key:
        return str(args.api_key).strip()
    env_name = str(args.api_key_env or DEFAULT_API_KEY_ENV).strip()
    return str(os.environ.get(env_name, "")).strip()


def _api_key_source(args: argparse.Namespace) -> str:
    if args.api_key:
        return "cli:--api-key"
    return f"env:{args.api_key_env or DEFAULT_API_KEY_ENV}"


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render_archive_map_seeds(archive: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    seeds = _archive_map_seed_payloads(archive)
    if not seeds:
        return []
    try:
        from scripts.render_story_grid_map import render_story_grid_map
    except Exception as exc:  # pragma: no cover - defensive import guard for standalone use
        manifest = [{"ok": False, "error": "map_renderer_import_failed", "detail": str(exc), "seed_count": len(seeds)}]
        _write_json(run_dir / "rendered_maps_manifest.json", manifest)
        return manifest

    output_dir = run_dir / "rendered_maps"
    manifest: list[dict[str, Any]] = []
    for index, seed in enumerate(seeds, 1):
        try:
            result = render_story_grid_map(seed, output_dir=output_dir)
            manifest.append(
                {
                    "ok": True,
                    "index": index,
                    "thread_id": seed.get("thread_id", ""),
                    "action_type": seed.get("action_type", ""),
                    "map_id": result.get("map_id"),
                    "title": result.get("title"),
                    "file_name": result.get("file_name"),
                    "safe_projection": result.get("safe_projection"),
                }
            )
        except Exception as exc:
            manifest.append(
                {
                    "ok": False,
                    "index": index,
                    "thread_id": seed.get("thread_id", ""),
                    "action_type": seed.get("action_type", ""),
                    "error": str(exc),
                }
            )
    _write_json(run_dir / "rendered_maps_manifest.json", manifest)
    return manifest


def _archive_map_seed_payloads(archive: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(archive, dict):
        return []
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in _as_list(archive.get("convergence_actions")):
        if not isinstance(action, dict):
            continue
        if not _has_map_grid_seed(action):
            continue
        payload = copy.deepcopy(action)
        seed_key = _dedupe_key(
            {
                "thread_id": payload.get("thread_id"),
                "action_type": payload.get("action_type"),
                "map_grid_seed": payload.get("map_grid_seed")
                or (payload.get("scene_goal") if isinstance(payload.get("scene_goal"), dict) else {}).get("map_grid_seed"),
            }
        )
        if seed_key in seen:
            continue
        seen.add(seed_key)
        payloads.append(payload)
    return payloads


def _redact_raw_result(result: dict[str, Any]) -> dict[str, Any]:
    clean = dict(result)
    raw = clean.get("raw_response")
    if isinstance(raw, dict):
        clean["raw_response"] = raw
    return clean


def _summary_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Story Forge A/B 对比摘要",
        "",
        f"- 旧版得分：{comparison['legacy']['score']} / {comparison['legacy']['max_score']}",
        f"- 新版得分：{comparison['story_forge']['score']} / {comparison['story_forge']['max_score']}",
        f"- 分差：{comparison['delta_score']}",
        f"- 结论：{comparison['recommendation']}",
        "",
        "## 新版新增通过项",
    ]
    added = comparison.get("story_forge_added_checks") or []
    lines.extend(f"- `{item}`" for item in added)
    if not added:
        lines.append("- 无")
    lines.extend(["", "## 逐项结果", "", "| 项目 | 旧版 | 新版 |", "|---|---:|---:|"])
    legacy_by_id = {item["id"]: item for item in comparison["legacy"]["checks"]}
    forge_by_id = {item["id"]: item for item in comparison["story_forge"]["checks"]}
    for item in RUBRIC:
        check_id = item["id"]
        legacy = "通过" if legacy_by_id[check_id]["passed"] else "未过"
        forge = "通过" if forge_by_id[check_id]["passed"] else "未过"
        lines.append(f"| {item['label']} | {legacy} | {forge} |")
    lines.append("")
    return "\n".join(lines)


def _simulation_summary_markdown(
    simulation_result: dict[str, Any] | None,
    audit_result: dict[str, Any] | None,
) -> str:
    lines = ["# Runtime DM 模拟摘要", ""]
    if simulation_result is None:
        lines.append("本次为 dry run，只生成 simulation prompt，未调用模型。")
        return "\n".join(lines) + "\n"
    payload = simulation_result.get("payload") if isinstance(simulation_result, dict) else None
    lines.append(f"- 模拟 JSON：{'ok' if isinstance(payload, dict) else 'bad'}")
    lines.append(f"- finish_reason：{simulation_result.get('finish_reason')}")
    if isinstance(payload, dict):
        selfcheck = payload.get("runtime_selfcheck") if isinstance(payload.get("runtime_selfcheck"), dict) else {}
        lines.append(f"- hidden truth 泄露：{selfcheck.get('hidden_truth_leaked')}")
        lines.append(f"- 铁路风险：{selfcheck.get('railroading_risk')}")
        lines.append(f"- 使用失败推进：{selfcheck.get('failure_forward_used')}")
        lines.append(f"- 留档已更新：{selfcheck.get('archive_updated')}")
        response = payload.get("player_facing_response")
        if isinstance(response, dict):
            lines.extend(
                [
                    "",
                    "## 玩家可见回应",
                    "",
                    str(response.get("narration") or "").strip(),
                ]
            )
    if audit_result is not None:
        audit = audit_result.get("payload") if isinstance(audit_result, dict) else None
        lines.extend(["", "## 审计", ""])
        if isinstance(audit, dict):
            scores = audit.get("scores") if isinstance(audit.get("scores"), dict) else {}
            lines.append(f"- verdict：{audit.get('verdict')}")
            lines.append(f"- total：{scores.get('total')}")
            lines.append(f"- short_reason：{audit.get('short_reason')}")
        else:
            lines.append("- 审计 JSON：bad")
    lines.append("")
    return "\n".join(lines)


def _multi_turn_summary_markdown(
    turns: list[dict[str, Any]],
    final_archive: dict[str, Any],
    multi_audit_result: dict[str, Any] | None,
) -> str:
    lines = ["# Multi-turn Runtime DM 模拟摘要", ""]
    lines.append(f"- turn_count: {len(turns)}")

    pressure = final_archive.get("pressure_clock") if isinstance(final_archive, dict) else None
    if isinstance(pressure, dict):
        label = pressure.get("label") or pressure.get("clock_id") or "pressure_clock"
        tick = pressure.get("tick", "")
        max_tick = pressure.get("max", "")
        status = pressure.get("status", "")
        suffix = f"/{max_tick}" if max_tick != "" else ""
        status_text = f" ({status})" if status else ""
        lines.append(f"- final_pressure: {label} {tick}{suffix}{status_text}")
    else:
        lines.append("- final_pressure: none")

    archive_counts = {}
    if isinstance(final_archive, dict):
        for key in (
            "canon_facts",
            "player_known_state",
            "hidden_truth",
            "open_threads",
            "resolved_threads",
            "npc_state",
            "asset_state",
            "pressure_history",
        ):
            value = final_archive.get(key)
            archive_counts[key] = len(value) if isinstance(value, list) else (1 if value else 0)
    lines.append(f"- archive_counts: {json.dumps(archive_counts, ensure_ascii=False, sort_keys=True)}")

    lines.extend(["", "## Turns", ""])
    if not turns:
        lines.append("- no turns")
    for turn in turns:
        index = turn.get("turn_index")
        action = _one_line_excerpt(turn.get("player_action"), 90)
        simulation = turn.get("simulation")
        audit = turn.get("audit")
        archive_after = turn.get("archive_after") if isinstance(turn.get("archive_after"), dict) else {}
        pressure_after = archive_after.get("pressure_clock") if isinstance(archive_after, dict) else None
        pressure_text = ""
        if isinstance(pressure_after, dict):
            pressure_text = f"; pressure={pressure_after.get('tick', '')}"
            if pressure_after.get("max") not in (None, ""):
                pressure_text += f"/{pressure_after.get('max')}"
        verdict_text = ""
        if isinstance(audit, dict):
            scores = audit.get("scores") if isinstance(audit.get("scores"), dict) else {}
            verdict_text = f"; audit={audit.get('verdict')}"
            if scores.get("total") is not None:
                verdict_text += f"/{scores.get('total')}"
        simulation_text = "ok" if isinstance(simulation, dict) else "dry_run_or_bad_json"
        lines.append(f"- turn {index}: simulation={simulation_text}{pressure_text}{verdict_text}; action={action}")

    lines.extend(["", "## Multi-turn Audit", ""])
    audit_payload = multi_audit_result.get("payload") if isinstance(multi_audit_result, dict) else None
    if isinstance(audit_payload, dict):
        scores = audit_payload.get("scores") if isinstance(audit_payload.get("scores"), dict) else {}
        lines.append(f"- verdict: {audit_payload.get('verdict')}")
        lines.append(f"- total: {scores.get('total')}")
        lines.append(f"- short_reason: {audit_payload.get('short_reason')}")
        for key in ("continuity_breaks", "archive_conflicts", "unresolved_threads", "recommended_system_changes"):
            value = audit_payload.get(key)
            if isinstance(value, list) and value:
                lines.append(f"- {key}: {len(value)}")
    elif multi_audit_result is None:
        lines.append("- no multi-turn audit (dry run or --audit-simulation not enabled)")
    else:
        lines.append("- multi-turn audit JSON: bad")

    lines.append("")
    return "\n".join(lines)


def _one_line_excerpt(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _slug(text: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", str(text or "").strip())
    compact = compact.strip("-")
    if not compact:
        return "seed"
    return compact[:48]


if __name__ == "__main__":
    raise SystemExit(main())
