from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from .models import Character, GameMode, GameSession, infer_tag_layer, utc_now_iso
from .timeline import timeline_view


LlmGenerate = Callable[..., Awaitable[Any]]


CONTINUITY_AUDITOR_SYSTEM_PROMPT = """你是独立上下文的跑团连续性审计器。
你不是主 DM，不写叙事，不推进剧情，不创造新事实。
你只根据输入中的：当前存档快照、本轮玩家消息、本轮 DM 回复、本轮工具结果，检查状态是否自相矛盾。

重点检查：
- DM 回复是否否认了工具结果或较新的权威状态中已经发生的事实。
- 你要基于叙事事实判断角色是否已经离开当前故事，不要求玩家本人同意、主动选择或明确说“我要退场”。
- 角色死亡、被捕后无法继续参与、被驱逐/离船、被剧情永久隔离、失去当前故事参与资格、退休或主动退场后，相关 scene thread 是否仍被当作 active。
- 单个玩家退场是否错误地把整个团切到建卡模式。
- scene.summary/current_conflict/current_objective/open_hooks 是否和 scene_threads、last_resolution、角色 status tag 冲突。
- 状态查询、核对、抱怨类消息不应被当成新的剧情事实。
- 不要只因为出现“退休/退场/被捕/死亡”等字样就判定退场；背景经历、假设规则、条件说明、抱怨、询问、复述旧设定都不能当作新事实。

只输出一个 JSON 对象，不要输出 Markdown 或解释文字：
{
  "ok": true,
  "needs_repair": false,
  "issues": [
    {
      "severity": "low|medium|high",
      "problem": "一句话说明问题",
      "evidence": ["引用输入中已有事实，不要编造"],
      "repair": "建议如何修"
    }
  ],
  "safe_patches": {
    "mode": "narrative 或空字符串",
    "scene_threads": [
      {
        "thread_id": "已有 thread id",
        "patch": {
          "status": "closed|archived|resolved|retired|active|空字符串",
          "summary": "可选；只能改写为已由证据支持的收束事实",
          "current_objective": "可选；只能改写为已由证据支持的收束事实"
        }
      }
    ],
    "character_tags": [
      {
        "character_id": "已有角色 id",
        "tags": [
          {"key": "退场状态", "value": "已死亡/被捕且无法继续参与/被驱逐离船/已离开当前故事等由证据支持的状态", "layer": "status"}
        ]
      }
    ],
    "scene": {
      "summary": "可选；只有在新摘要完全由工具结果或较新存档事实支持时才给出",
      "current_conflict": "可选",
      "current_objective": "可选"
    }
  },
  "player_correction": "可选；若当前 DM 回复已经明显误导玩家，用一句话更正。否则空字符串。"
}

安全规则：
- 不要凭猜测补新地点、新 NPC、新战利品、新线索。
- 如果只是怀疑，写入 issues，不要给 safe_patches。
- safe_patches 只能修正一致性，不能扩展剧情。
- 只要本轮事实已经让角色无法继续参与当前故事，就应给出退场状态和关闭对应 scene thread；不要把玩家不愿意退场当作否定已发生事实。
- 给出退场状态或关闭线程时，issues.evidence 必须引用输入里已有的事实片段，不能只复述 safe_patches 自己打算写入的文字。
- 只有玩家明确要求恢复活跃，或本轮成功工具结果已证明角色继续活跃时，才允许把旧的 retired/closed 线程改回 active/空字符串。
"""


CLOSED_THREAD_STATUSES = {"archived", "closed", "resolved", "retired"}
TERMINAL_STATUS_TAG_KEYS = (
    "退场状态",
    "离场状态",
    "参与状态",
    "生命状态",
    "生死状态",
    "角色结局",
    "结局",
    "terminal_status",
    "out_of_play_status",
)
TERMINAL_TERMS = (
    "死亡",
    "已死亡",
    "确认死亡",
    "阵亡",
    "已死",
    "身亡",
    "牺牲",
    "永久退场",
    "确认退场",
    "已退场",
    "退场",
    "退休",
    "被驱逐",
    "驱逐离船",
    "被捕且无法继续参与",
    "无法继续参与",
    "不可继续参与",
    "离队",
    "永久离队",
    "角色结束",
    "角色结局",
    "不再扮演",
    "不再与本地故事交织",
    "不再与这座小镇交织",
    "不再参与当前故事",
    "已离开当前故事",
    "retired",
    "out of play",
    "out_of_play",
)
TERMINAL_REJOIN_TERMS = (
    "新角色",
    "建卡",
    "创建人物",
    "创建角色",
    "绑定角色",
    "换新角色",
    "重新加入",
    "重新进团",
    "恢复",
    "恢复状态",
    "恢复活跃",
    "活跃状态",
    "活跃中",
    "我仍在参团",
    "仍在参团",
    "还在参团",
    "没有退场",
    "不是退场",
    "未退场",
    "取消退场",
    "撤销退场",
    "继续参与",
)
STATE_QUERY_TERMS = (
    "当前我的状态",
    "我的状态",
    "当前状态",
    "现在什么情况",
    "我现在在哪",
    "身上有什么",
    "还有几个人",
    "几个人才能",
    "谁没睡",
    "谁还没睡",
    "进入第二天",
)
FACT_COMPLAINT_TERMS = (
    "你又忘",
    "忘了",
    "记错",
    "不一致",
    "核对",
    "复核",
    "丢事实",
    "剧情错乱",
    "听不懂",
    "换个ai",
    "没用",
)
DENIAL_TERMS = (
    "还没",
    "没有",
    "并未",
    "未曾",
    "尚未",
    "不成立",
    "不能算",
    "并没有",
    "没下咒",
    "诅咒还没",
)
TOOL_FACT_TOOLS = {
    "execute_rule",
    "update_scene",
    "update_character_tags",
    "update_world_tags",
    "cycle_control",
    "turn_control",
    "session_control",
}
SCENE_MIRROR_KEYS = (
    "summary",
    "location",
    "_location",
    "scene_time_label",
    "scene_time_of_day",
    "current_conflict",
    "current_objective",
    "open_hooks",
    "clues",
    "mysteries",
    "stakes",
    "pressure_clock",
    "npcs",
    "factions",
    "relations",
)
SCENE_PATCH_KEYS = {"summary", "current_conflict", "current_objective", "open_hooks", "clues", "stakes"}
LOW_RISK_STATUS_TAG_KEYS = {"当前所在", "当前状态", "最近行动"}


class ContinuityAuditor:
    def __init__(self, llm_generate: LlmGenerate, chat_provider_id: str, max_tokens: int = 0):
        self.llm_generate = llm_generate
        self.chat_provider_id = chat_provider_id
        self.max_tokens = max_tokens

    async def run(
        self,
        session: GameSession,
        *,
        actor: dict[str, Any],
        player_message: str,
        completion: str,
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = build_continuity_audit_prompt(
            session,
            actor=actor,
            player_message=player_message,
            completion=completion,
            tool_results=tool_results,
        )
        kwargs: dict[str, Any] = {
            "chat_provider_id": self.chat_provider_id,
            "prompt": prompt,
            "contexts": [],
            "system_prompt": CONTINUITY_AUDITOR_SYSTEM_PROMPT,
        }
        if self.max_tokens > 0:
            kwargs["max_tokens"] = self.max_tokens
        try:
            response = await self.llm_generate(**kwargs)
        except TypeError as exc:
            if "max_tokens" not in kwargs:
                return {"ok": False, "error": "continuity_auditor_llm_exception", "message": str(exc)[:240]}
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("max_tokens", None)
            try:
                response = await self.llm_generate(**retry_kwargs)
            except Exception as retry_exc:
                return {"ok": False, "error": "continuity_auditor_llm_exception", "message": str(retry_exc)[:240]}
        except Exception as exc:
            return {"ok": False, "error": "continuity_auditor_llm_exception", "message": str(exc)[:240]}

        text = _response_text(response)
        payload = _parse_json_object(text)
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "invalid_continuity_audit_json",
                "output_excerpt": text[:240],
                "prompt_chars": len(prompt),
                "output_chars": len(text),
            }
        return {
            "ok": True,
            "payload": _normalise_audit_payload(payload),
            "prompt_chars": len(prompt),
            "output_chars": len(text),
        }


def continuity_audit_should_run(
    session: GameSession,
    *,
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    text = f"{player_message}\n{completion}".lower()
    if _looks_like_state_query(player_message):
        return False
    if _contains_any(text, FACT_COMPLAINT_TERMS):
        return True
    if str(completion or "").strip():
        return True
    if _contains_any(completion.lower(), DENIAL_TERMS) and _state_has_completed_fact(session):
        return True
    for item in tool_results or []:
        if str(item.get("tool") or "") in TOOL_FACT_TOOLS:
            return True
    return False


def apply_deterministic_continuity_repairs(
    session: GameSession,
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    result = {"applied": [], "rejected": []}
    if _should_reset_mode_to_narrative(session, player_message, tool_results):
        old = session.mode.value
        session.mode = GameMode.NARRATIVE
        result["applied"].append({"type": "mode", "from": old, "to": GameMode.NARRATIVE.value})

    actor_character_id = _actor_character_id(session, actor)
    if actor_character_id and _looks_like_reactivation_request(player_message):
        reactivated = _reactivate_character_threads(session, actor_character_id)
        if reactivated:
            result["applied"].append(
                {
                    "type": "character_reactivated",
                    "character_id": actor_character_id,
                    **reactivated,
                }
            )

    active_result = normalize_active_scene_thread(session)
    if active_result.get("changed"):
        result["applied"].append(active_result)
    return result


def apply_continuity_audit_patches(
    session: GameSession,
    payload: dict[str, Any],
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    result = {"applied": [], "rejected": []}
    patches = payload.get("safe_patches")
    if not isinstance(patches, dict):
        return result
    terminal_evidence = _terminal_patch_evidence_map(
        session,
        payload,
        actor=actor,
        player_message=player_message,
        completion=completion,
        tool_results=tool_results,
    )

    mode = str(patches.get("mode") or "").strip().lower()
    if mode == GameMode.NARRATIVE.value:
        if _should_reset_mode_to_narrative(session, player_message, tool_results):
            old = session.mode.value
            session.mode = GameMode.NARRATIVE
            result["applied"].append({"type": "mode", "from": old, "to": GameMode.NARRATIVE.value})
        elif session.mode == GameMode.NARRATIVE:
            pass
        else:
            result["rejected"].append({"type": "mode", "reason": "unsafe_mode_reset"})

    for item in _list_of_dicts(patches.get("character_tags"))[:8]:
        character_id = str(item.get("character_id") or "").strip()
        character = session.characters.get(character_id)
        if not character:
            result["rejected"].append({"type": "character_tags", "character_id": character_id, "reason": "missing_character"})
            continue
        safe_tags = []
        for tag in _list_of_dicts(item.get("tags"))[:4]:
            key = str(tag.get("key") or "").strip()
            value = str(tag.get("value") or "").strip()
            layer = str(tag.get("layer") or infer_tag_layer(key)).strip() or "status"
            if layer == "status" and key in LOW_RISK_STATUS_TAG_KEYS and _low_risk_status_tag_is_evidence_backed(
                session,
                character_id,
                key,
                value,
                actor=actor,
                player_message=player_message,
                completion=completion,
                tool_results=tool_results,
            ):
                safe_tags.append(
                    {
                        "key": key,
                        "value": _short_text(value, 240),
                        "type": "text",
                        "source": "continuity_auditor",
                        "layer": "status",
                    }
                )
                continue
            if layer != "status" or not _is_terminal_status_tag(key, value):
                result["rejected"].append(
                    {
                        "type": "character_tag",
                        "character_id": character_id,
                        "key": key,
                        "reason": "only_terminal_status_tags_are_auto_applied",
                    }
                )
                continue
            if not _llm_terminal_evidence_for_character(
                terminal_evidence,
                session,
                character_id,
            ):
                result["rejected"].append(
                    {
                        "type": "character_tag",
                        "character_id": character_id,
                        "key": key,
                        "reason": "missing_terminal_evidence",
                    }
                )
                continue
            safe_tags.append(
                {
                    "key": key or "退场状态",
                    "value": value or "已退场",
                    "type": "text",
                    "source": "continuity_auditor",
                    "layer": "status",
                }
            )
        if safe_tags:
            character.upsert_tags(safe_tags)
            result["applied"].append(
                {
                    "type": "character_tags",
                    "character_id": character_id,
                    "tags": safe_tags,
                }
            )

    for item in _list_of_dicts(patches.get("scene_threads"))[:8]:
        thread_id = str(item.get("thread_id") or "").strip()
        patch = item.get("patch")
        if not isinstance(patch, dict):
            result["rejected"].append({"type": "scene_thread", "thread_id": thread_id, "reason": "invalid_patch"})
            continue
        applied = _apply_safe_scene_thread_patch(
            session,
            thread_id,
            patch,
            actor=actor,
            player_message=player_message,
            completion=completion,
            tool_results=tool_results,
            terminal_evidence=terminal_evidence,
        )
        if applied.get("ok"):
            result["applied"].append(applied)
        else:
            result["rejected"].append(applied)

    scene_patch = patches.get("scene")
    if isinstance(scene_patch, dict) and _has_recent_tool_backed_scene_fact(tool_results):
        applied_scene_patch: dict[str, Any] = {}
        for key, value in scene_patch.items():
            if key not in SCENE_PATCH_KEYS:
                continue
            if value in (None, "", [], {}):
                continue
            if _scene_patch_value_is_backed(session, value, tool_results):
                applied_scene_patch[key] = _compact_json_value(value, depth=3)
        if applied_scene_patch:
            session.scene.update(applied_scene_patch)
            result["applied"].append({"type": "scene", "patch": applied_scene_patch})
        elif scene_patch:
            result["rejected"].append({"type": "scene", "reason": "scene_patch_not_evidence_backed"})

    active_result = normalize_active_scene_thread(session)
    if active_result.get("changed"):
        result["applied"].append(active_result)
    return result


def normalize_active_scene_thread(session: GameSession) -> dict[str, Any]:
    scene = session.scene if isinstance(session.scene, dict) else {}
    threads = _scene_threads(scene)
    alias_result = _coalesce_character_scene_thread_aliases(session, scene, threads)
    alias_changed = bool(alias_result.pop("changed", False))
    closed_thread_ids: list[str] = []
    active_id = str(scene.get("active_scene_thread_id") or "").strip()
    active = threads.get(active_id) if active_id else None
    if isinstance(active, dict) and not _scene_thread_is_closed(active):
        return {
            "type": "active_scene_thread_normalized",
            "changed": bool(alias_changed or closed_thread_ids),
            **alias_result,
            "closed_thread_ids": closed_thread_ids,
        }

    replacement_id = _find_replacement_scene_thread_id(scene, exclude_thread_id=active_id)
    if replacement_id:
        scene["active_scene_thread_id"] = replacement_id
        _mirror_scene_thread_fields(scene, dict(threads.get(replacement_id) or {}))
        return {
            "type": "active_scene_thread_normalized",
            "changed": True,
            "from": active_id,
            "to": replacement_id,
            **alias_result,
            "closed_thread_ids": closed_thread_ids,
        }
    if active_id:
        scene.pop("active_scene_thread_id", None)
        return {
            "type": "active_scene_thread_normalized",
            "changed": True,
            "from": active_id,
            "to": "",
            **alias_result,
            "closed_thread_ids": closed_thread_ids,
        }
    return {
        "type": "active_scene_thread_normalized",
        "changed": bool(alias_changed or closed_thread_ids),
        **alias_result,
        "closed_thread_ids": closed_thread_ids,
    }


def build_continuity_audit_prompt(
    session: GameSession,
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> str:
    payload = {
        "instruction": "审计以下本轮跑团回复是否和权威状态冲突，只返回 JSON。",
        "player_message": _short_text(player_message, 1000),
        "dm_completion": _short_text(completion, 1800),
        "actor": {
            "player_id": str(actor.get("player_id") or ""),
            "display_name": _short_text(actor.get("display_name") or "", 80),
            "character_id": _actor_character_id(session, actor),
        },
        "tool_results": _compact_tool_results(tool_results),
        "state": build_continuity_audit_view(session, actor=actor),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_continuity_audit_view(session: GameSession, *, actor: dict[str, Any]) -> dict[str, Any]:
    scene = session.scene if isinstance(session.scene, dict) else {}
    active_character_id = _actor_character_id(session, actor)
    character_ids = {
        active_character_id,
        str(session.active_character_id or ""),
    }
    for bound_id in (session.player_character_map or {}).values():
        if bound_id:
            character_ids.add(str(bound_id))
    for thread in _scene_threads(scene).values():
        if not isinstance(thread, dict):
            continue
        if thread.get("active_character_id"):
            character_ids.add(str(thread.get("active_character_id")))
        for participant in thread.get("participants") or []:
            if participant:
                character_ids.add(str(participant))
    characters = [
        _character_audit_view(session.characters[character_id])
        for character_id in sorted(character_ids)
        if character_id in session.characters
    ][:16]
    return {
        "mode": session.mode.value,
        "title": session.title,
        "timeline": timeline_view(session.timeline),
        "participants": [
            {
                "player_id": player_id,
                "display_name": _short_text(data.get("display_name", ""), 80),
                "character_id": session.player_character_map.get(player_id, ""),
            }
            for player_id, data in list((session.participants or {}).items())[:24]
        ],
        "characters": characters,
        "scene": _scene_audit_view(scene),
        "battle": _compact_json_value(session._compact_battle(), depth=3),
    }


def _scene_audit_view(scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": _short_text(scene.get("summary"), 900),
        "current_conflict": _short_text(scene.get("current_conflict"), 500),
        "current_objective": _short_text(scene.get("current_objective"), 500),
        "open_hooks": _compact_json_value(scene.get("open_hooks"), depth=3),
        "active_scene_thread_id": scene.get("active_scene_thread_id", ""),
        "last_resolution": _compact_json_value(scene.get("last_resolution"), depth=3),
        "recent_events": [
            _compact_recent_event(event)
            for event in (scene.get("_recent_narrative_events") or [])[-8:]
            if isinstance(event, dict)
        ],
        "scene_threads": [
            {"thread_id": thread_id, **_scene_thread_audit_view(thread)}
            for thread_id, thread in sorted(
                _scene_threads(scene).items(),
                key=lambda item: str((item[1] or {}).get("updated_at", "")) if isinstance(item[1], dict) else "",
                reverse=True,
            )[:12]
            if isinstance(thread, dict)
        ],
    }


def _scene_thread_audit_view(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": thread.get("status", ""),
        "updated_at": thread.get("updated_at", ""),
        "location": _short_text(thread.get("location") or thread.get("_location"), 160),
        "summary": _short_text(thread.get("summary"), 700),
        "current_conflict": _short_text(thread.get("current_conflict"), 360),
        "current_objective": _short_text(thread.get("current_objective"), 360),
        "participants": list(thread.get("participants") or [])[:12],
        "active_character_id": thread.get("active_character_id", ""),
        "last_actor_player_id": thread.get("last_actor_player_id", ""),
    }


def _character_audit_view(character: Character) -> dict[str, Any]:
    tags = []
    for tag in character.tags or []:
        layer = str(tag.layer or infer_tag_layer(tag.key)).lower()
        key = str(tag.key or "")
        if layer not in {"status", "relations", "notes"} and not _contains_any(key, ("状态", "位置", "行动", "协议", "仪式", "诅咒", "退场")):
            continue
        tags.append(
            {
                "key": key,
                "value": _compact_json_value(tag.value, depth=2),
                "layer": layer,
            }
        )
    return {
        "id": character.id,
        "name": character.name,
        "player_id": character.player_id,
        "summary": _short_text(character.summary, 240),
        "tags": tags[-24:],
    }


def _compact_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted = []
    for item in (tool_results or [])[-12:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "tool": item.get("tool", ""),
                "args": _compact_json_value(item.get("args"), depth=3),
                "result": _compact_json_value(item.get("result"), depth=4),
            }
        )
    return compacted


def _compact_recent_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "at": event.get("at", ""),
        "player_id": event.get("player_id", ""),
        "character_id": event.get("character_id", ""),
        "message": _short_text(event.get("message"), 240),
        "outcome": _short_text(event.get("outcome"), 500),
    }


def _normalise_audit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    issues = []
    for issue in _list_of_dicts(payload.get("issues"))[:12]:
        issues.append(
            {
                "severity": str(issue.get("severity") or "medium")[:20],
                "problem": _short_text(issue.get("problem"), 240),
                "evidence": [_short_text(item, 180) for item in list(issue.get("evidence") or [])[:6]],
                "repair": _short_text(issue.get("repair"), 240),
            }
        )
    safe_patches = payload.get("safe_patches") if isinstance(payload.get("safe_patches"), dict) else {}
    return {
        "ok": bool(payload.get("ok", True)),
        "needs_repair": bool(payload.get("needs_repair", False)),
        "issues": issues,
        "safe_patches": _compact_json_value(safe_patches, depth=5),
        "player_correction": _short_text(payload.get("player_correction"), 260),
    }


def safe_player_correction(payload: dict[str, Any], apply_result: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    text = _short_text(payload.get("player_correction"), 260)
    if not text:
        return ""
    if not apply_result.get("applied") and not payload.get("needs_repair"):
        return ""
    if any(token in text for token in ("系统提示", "prompt", "JSON", "工具协议")):
        return ""
    return text


def _apply_safe_scene_thread_patch(
    session: GameSession,
    thread_id: str,
    patch: dict[str, Any],
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
    terminal_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    threads = _scene_threads(session.scene)
    thread = threads.get(thread_id)
    if not isinstance(thread, dict):
        return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "missing_thread"}
    status = str(patch.get("status") or "").strip().lower()
    if status in {"active", "open", "reopened"}:
        if not _reactivation_evidence_for_thread(
            session,
            thread_id,
            thread,
            actor=actor,
            player_message=player_message,
            completion=completion,
            tool_results=tool_results,
        ):
            return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "missing_reactivation_evidence"}
        thread.pop("status", None)
        for key in ("summary", "current_objective", "current_conflict", "location"):
            value = patch.get(key)
            if isinstance(value, str) and value.strip() and not _terminal_status_text_match(value):
                thread[key] = _short_text(value, 700 if key == "summary" else 360)
        thread["updated_at"] = utc_now_iso()
        return {"ok": True, "type": "scene_thread", "thread_id": thread_id, "patch": {"status": "active"}}
    if not status and any(key in patch for key in ("status", "summary", "current_objective", "current_conflict", "location")):
        if not _reactivation_evidence_for_thread(
            session,
            thread_id,
            thread,
            actor=actor,
            player_message=player_message,
            completion=completion,
            tool_results=tool_results,
        ):
            return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "missing_reactivation_evidence"}
        thread.pop("status", None)
        for key in ("summary", "current_objective", "current_conflict", "location"):
            value = patch.get(key)
            if isinstance(value, str) and value.strip() and not _terminal_status_text_match(value):
                thread[key] = _short_text(value, 700 if key == "summary" else 360)
        thread["updated_at"] = utc_now_iso()
        return {"ok": True, "type": "scene_thread", "thread_id": thread_id, "patch": {"status": ""}}
    if status and status not in CLOSED_THREAD_STATUSES:
        return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "unsupported_status"}
    if status in CLOSED_THREAD_STATUSES:
        if not _llm_terminal_evidence_for_thread(
            terminal_evidence or {},
            session,
            thread_id,
            thread,
        ):
            return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "missing_terminal_evidence"}
        thread["status"] = status
        for key in ("summary", "current_objective", "current_conflict"):
            value = patch.get(key)
            if isinstance(value, str) and value.strip():
                thread[key] = _short_text(value, 700 if key == "summary" else 360)
        thread["updated_at"] = utc_now_iso()
        return {"ok": True, "type": "scene_thread", "thread_id": thread_id, "patch": {"status": status}}
    return {"ok": False, "type": "scene_thread", "thread_id": thread_id, "reason": "no_safe_change"}


def _reactivate_character_threads(session: GameSession, character_id: str) -> dict[str, Any]:
    character = session.characters.get(character_id)
    if not character:
        return {}
    applied_tags = []
    existing_tags = list(character.tags or [])
    retained_tags = []
    for tag in existing_tags:
        layer = str(tag.layer or infer_tag_layer(tag.key))
        key = str(tag.key or "")
        if layer == "status" and _terminal_status_text_match(f"{key} {tag.value}"):
            continue
        retained_tags.append(tag)
    if len(retained_tags) != len(existing_tags):
        character.tags = retained_tags
        active_tag = {
            "key": "退场状态",
            "value": "活跃中",
            "type": "text",
            "source": "system_continuity",
            "layer": "status",
        }
        character.upsert_tags([active_tag])
        applied_tags.append(active_tag)

    reopened = []
    for thread_id, thread in _scene_threads(session.scene).items():
        if not isinstance(thread, dict):
            continue
        participants = {str(item) for item in thread.get("participants") or [] if str(item)}
        if not (
            str(thread.get("active_character_id") or "") == character_id
            or character_id in participants
            or character_id in str(thread_id)
        ):
            continue
        if _scene_thread_is_closed(thread):
            thread.pop("status", None)
            if _terminal_status_text_match(thread.get("summary", "")):
                thread["summary"] = f"{character.name or character_id}继续作为活跃角色参与当前故事。"
            thread["updated_at"] = utc_now_iso()
            reopened.append(str(thread_id))

    if reopened:
        active_id = str(session.scene.get("active_scene_thread_id") or "")
        if not active_id or active_id in reopened or _scene_thread_is_closed(_scene_threads(session.scene).get(active_id) or {}):
            session.scene["active_scene_thread_id"] = reopened[-1]
            _mirror_scene_thread_fields(session.scene, dict(_scene_threads(session.scene).get(reopened[-1]) or {}))

    result: dict[str, Any] = {}
    if applied_tags:
        result["tags"] = applied_tags
    if reopened:
        result["thread_ids"] = reopened
    return result


def _should_reset_mode_to_narrative(
    session: GameSession,
    player_message: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    if session.mode == GameMode.NARRATIVE:
        return False
    if not _campaign_started(session):
        return False
    if _looks_like_character_creation_request(player_message):
        return False
    for item in tool_results or []:
        if str(item.get("tool") or "") in {"create_character", "bind_player_character"}:
            result = item.get("result")
            if isinstance(result, dict) and result.get("ok"):
                return False
    if session.mode == GameMode.CHARACTER_CREATION:
        return True
    return False


def _terminal_patch_evidence_map(
    session: GameSession,
    payload: dict[str, Any],
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    actor_character_id = _actor_character_id(session, actor)
    source_text = _terminal_source_text(
        session,
        actor=actor,
        player_message=player_message,
        completion=completion,
        tool_results=tool_results,
    )
    by_character: dict[str, list[str]] = {}
    by_thread: dict[str, list[str]] = {}
    any_evidence: list[str] = []
    for issue in _list_of_dicts(payload.get("issues"))[:12]:
        evidence_items = issue.get("evidence")
        if not isinstance(evidence_items, list):
            continue
        text = _flatten_text([issue.get("problem"), issue.get("repair"), evidence_items])
        accepted = []
        for raw in evidence_items[:8]:
            evidence = _short_text(raw, 260)
            if not _external_audit_evidence_supported(evidence, source_text):
                continue
            accepted.append(evidence)
        if not accepted:
            continue
        any_evidence.extend(accepted)
        for character_id, character in session.characters.items():
            if _text_mentions_character(text, str(character_id), character):
                by_character.setdefault(str(character_id), []).extend(accepted)
        for thread_id, thread in _scene_threads(session.scene).items():
            if isinstance(thread, dict) and _text_mentions_thread(text, str(thread_id), thread, session):
                by_thread.setdefault(str(thread_id), []).extend(accepted)
    return {
        "any": any_evidence,
        "actor_character_id": actor_character_id,
        "by_character": by_character,
        "by_thread": by_thread,
    }


def _llm_terminal_evidence_for_character(
    evidence_map: dict[str, Any],
    session: GameSession,
    character_id: str,
) -> bool:
    if evidence_map.get("by_character", {}).get(character_id):
        return True
    if character_id and character_id == str(evidence_map.get("actor_character_id") or "") and evidence_map.get("any"):
        return True
    character = session.characters.get(character_id)
    if character:
        for evidence in evidence_map.get("any", []):
            if _text_mentions_character(str(evidence), character_id, character):
                return True
    return False


def _llm_terminal_evidence_for_thread(
    evidence_map: dict[str, Any],
    session: GameSession,
    thread_id: str,
    thread: dict[str, Any],
) -> bool:
    if evidence_map.get("by_thread", {}).get(thread_id):
        return True
    active_character_id = str(thread.get("active_character_id") or "")
    if not active_character_id and thread_id.startswith("character:"):
        active_character_id = thread_id.split(":", 1)[1]
    if active_character_id and _llm_terminal_evidence_for_character(evidence_map, session, active_character_id):
        return True
    for participant in thread.get("participants") or []:
        participant_id = str(participant or "")
        if participant_id and _llm_terminal_evidence_for_character(evidence_map, session, participant_id):
            return True
    return False


def _terminal_source_text(
    session: GameSession,
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> str:
    scene = session.scene if isinstance(session.scene, dict) else {}
    return _flatten_text(
        [
            player_message,
            completion,
            _successful_tool_results_text(tool_results),
            scene.get("last_resolution"),
            scene.get("_recent_narrative_events"),
            build_continuity_audit_view(session, actor=actor),
        ]
    )


def _external_audit_evidence_supported(evidence: str, source_text: str) -> bool:
    evidence = _short_text(evidence, 260)
    if not evidence or not source_text:
        return False
    if evidence in source_text:
        return True
    tokens = _salient_tokens(evidence)
    if not tokens:
        return False
    hits = sum(1 for token in tokens[:8] if token in source_text)
    return hits >= max(1, min(2, len(tokens)))


def _text_mentions_character(text: str, character_id: str, character: Character | None) -> bool:
    text = str(text or "")
    if character_id and character_id in text:
        return True
    name = str(getattr(character, "name", "") or "").strip()
    return bool(name and name in text)


def _text_mentions_thread(text: str, thread_id: str, thread: dict[str, Any], session: GameSession) -> bool:
    text = str(text or "")
    if thread_id and thread_id in text:
        return True
    active_character_id = str(thread.get("active_character_id") or "")
    if active_character_id and _text_mentions_character(text, active_character_id, session.characters.get(active_character_id)):
        return True
    for participant in thread.get("participants") or []:
        participant_id = str(participant or "")
        if participant_id and _text_mentions_character(text, participant_id, session.characters.get(participant_id)):
            return True
    return False


def _has_recent_tool_backed_scene_fact(tool_results: list[dict[str, Any]]) -> bool:
    for item in tool_results or []:
        if str(item.get("tool") or "") in {"update_scene", "execute_rule", "update_character_tags"}:
            result = item.get("result")
            if not isinstance(result, dict) or result.get("ok", True):
                return True
    return False


def _scene_patch_value_is_backed(session: GameSession, value: Any, tool_results: list[dict[str, Any]]) -> bool:
    value_text = _flatten_text(value)
    if not value_text:
        return False
    evidence = _flatten_text([_tool_results_text(tool_results), session.scene.get("last_resolution")])
    if not evidence:
        return False
    tokens = _salient_tokens(value_text)
    if not tokens:
        return False
    return any(token in evidence for token in tokens)


def _reactivation_evidence_for_thread(
    session: GameSession,
    thread_id: str,
    thread: dict[str, Any],
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    active_character_id = str(thread.get("active_character_id") or "")
    if not active_character_id and thread_id.startswith("character:"):
        active_character_id = thread_id.split(":", 1)[1]
    if not active_character_id:
        participants = [str(item) for item in thread.get("participants") or [] if str(item)]
        active_character_id = participants[0] if participants else ""
    actor_character_id = _actor_character_id(session, actor)
    if active_character_id and _character_has_terminal_status(session, active_character_id):
        return actor_character_id == active_character_id and _looks_like_reactivation_request(player_message)
    if _looks_like_reactivation_request(player_message):
        return True
    if active_character_id and _has_successful_active_tool_evidence(tool_results, active_character_id):
        return True
    if active_character_id and _character_has_active_status(session, active_character_id):
        return True
    if actor_character_id and actor_character_id == active_character_id and _looks_like_reactivation_request(player_message):
        return True
    return False


def _has_successful_active_tool_evidence(tool_results: list[dict[str, Any]], character_id: str) -> bool:
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            continue
        text = _flatten_text({"tool": item.get("tool"), "args": item.get("args"), "result": result})
        if character_id not in text:
            continue
        lowered = text.lower()
        if _contains_any(lowered, ("活跃", "恢复", "当前所在", "最近行动", "scene_thread", "update_scene")):
            return True
    return False


def _character_has_active_status(session: GameSession, character_id: str) -> bool:
    character = session.characters.get(character_id)
    if not character:
        return False
    for tag in character.tags or []:
        if str(tag.layer or infer_tag_layer(tag.key)) != "status":
            continue
        text = f"{tag.key} {tag.value}"
        if _contains_any(text, ("活跃", "恢复", "参团", "当前所在", "最近行动")) and not _terminal_status_text_match(text):
            return True
    return False


def _character_has_terminal_status(session: GameSession, character_id: str) -> bool:
    character = session.characters.get(character_id)
    if not character:
        return False
    for tag in character.tags or []:
        if str(tag.layer or infer_tag_layer(tag.key)) != "status":
            continue
        text = f"{tag.key} {tag.value}"
        if _terminal_status_text_match(text):
            return True
    return False


def _state_has_completed_fact(session: GameSession) -> bool:
    scene = session.scene if isinstance(session.scene, dict) else {}
    text = _flatten_text(
        [
            scene.get("current_conflict"),
            scene.get("current_objective"),
            scene.get("last_resolution"),
            scene.get("_recent_narrative_events"),
            scene.get("scene_threads"),
        ]
    )
    return _contains_any(text, ("完成", "已完成", "成功", "诅咒", "标记", "已退场", "退场"))


def _tool_results_text(tool_results: list[dict[str, Any]]) -> str:
    return _flatten_text(
        [
            {
                "tool": item.get("tool"),
                "args": item.get("args"),
                "result": item.get("result"),
            }
            for item in (tool_results or [])[-12:]
            if isinstance(item, dict)
        ]
    )


def _successful_tool_results_text(tool_results: list[dict[str, Any]]) -> str:
    values = []
    for item in (tool_results or [])[-12:]:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            continue
        values.append(
            {
                "tool": item.get("tool"),
                "args": item.get("args"),
                "result": result,
            }
        )
    return _flatten_text(values)


def _scene_threads(scene: dict[str, Any]) -> dict[str, Any]:
    threads = scene.get("scene_threads")
    if isinstance(threads, dict):
        return threads
    threads = {}
    scene["scene_threads"] = threads
    return threads


def _coalesce_character_scene_thread_aliases(
    session: GameSession,
    scene: dict[str, Any],
    threads: dict[str, Any],
) -> dict[str, Any]:
    alias_map = {}
    for thread_id in list(threads.keys()):
        if not isinstance(thread_id, str):
            continue
        canonical = _canonical_scene_thread_id(session, thread_id)
        if canonical != thread_id:
            alias_map[thread_id] = canonical
    merged_aliases = []
    for alias, canonical in alias_map.items():
        legacy = threads.pop(alias, None)
        if not isinstance(legacy, dict):
            continue
        current = threads.get(canonical)
        if isinstance(current, dict):
            threads[canonical] = _merge_scene_thread_alias_records(legacy, current)
        else:
            threads[canonical] = dict(legacy)
        if scene.get("active_scene_thread_id") == alias:
            scene["active_scene_thread_id"] = canonical
        merged_aliases.append({"from": alias, "to": canonical})
    if not merged_aliases:
        return {"changed": False}
    return {"changed": True, "merged_thread_aliases": merged_aliases}


def _canonical_scene_thread_id(session: GameSession, value: Any) -> str:
    safe = _safe_scene_thread_id(value)
    if safe.startswith("character:"):
        return safe
    if safe in (session.characters or {}):
        return _safe_scene_thread_id(f"character:{safe}")
    return safe


def _safe_scene_thread_id(value: Any) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    safe = safe.strip("._:-")
    return safe[:96] or "default"


def _merge_scene_thread_alias_records(legacy: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    legacy_updated = str((legacy or {}).get("updated_at") or "")
    current_updated = str((current or {}).get("updated_at") or "")
    primary_is_open = False
    if _scene_thread_is_closed(current) and not _scene_thread_is_closed(legacy):
        primary, secondary = legacy, current
        primary_is_open = True
    elif _scene_thread_is_closed(legacy) and not _scene_thread_is_closed(current):
        primary, secondary = current, legacy
        primary_is_open = True
    elif legacy_updated > current_updated:
        primary, secondary = legacy, current
    else:
        primary, secondary = current, legacy
    merged = dict(secondary or {})
    merged.update(dict(primary or {}))
    if primary_is_open:
        merged.pop("status", None)
    return merged


def _scene_thread_is_closed(thread: dict[str, Any]) -> bool:
    return str((thread or {}).get("status") or "").strip().lower() in CLOSED_THREAD_STATUSES


def _find_replacement_scene_thread_id(scene: dict[str, Any], *, exclude_thread_id: str) -> str:
    candidates = []
    for candidate_id, thread in _scene_threads(scene).items():
        if candidate_id == exclude_thread_id or not isinstance(thread, dict):
            continue
        if _scene_thread_is_closed(thread):
            continue
        candidates.append((str(thread.get("updated_at") or ""), str(candidate_id)))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


def _mirror_scene_thread_fields(scene: dict[str, Any], scene_thread: dict[str, Any]) -> None:
    for key in SCENE_MIRROR_KEYS:
        if key in scene_thread:
            scene[key] = scene_thread[key]
        else:
            scene.pop(key, None)


def _actor_character_id(session: GameSession, actor: dict[str, Any]) -> str:
    player_id = str((actor or {}).get("player_id") or "").strip()
    if not player_id:
        return ""
    return str((session.player_character_map or {}).get(player_id, "") or "")


def _campaign_started(session: GameSession) -> bool:
    scene = session.scene if isinstance(session.scene, dict) else {}
    world_tags = session.world_tags if isinstance(session.world_tags, dict) else {}
    return bool(scene.get("_game_started") or scene.get("_legacy_live_campaign") or world_tags.get("_plot_locked") is True)


def _looks_like_reactivation_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return _contains_any(
        normalized,
        (
            "恢复状态",
            "恢复活跃",
            "恢复成活跃",
            "改回活跃",
            "我仍在参团",
            "仍在参团",
            "还在参团",
            "我还在团",
            "没有退场",
            "不是退场",
            "未退场",
            "我没退场",
            "取消退场",
            "撤销退场",
            "继续参与",
        ),
    )


def _looks_like_character_creation_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return _contains_any(normalized, ("新角色", "建卡", "创建人物", "创建角色", "绑定角色", "换新角色", "重新加入", "重新进团"))


def _looks_like_state_query(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if _contains_any(normalized, ("继续", "搜索", "调查", "攻击", "移动", "施法", "取走")):
        return False
    return _contains_any(normalized, STATE_QUERY_TERMS)


def _is_terminal_status_tag(key: Any, value: Any) -> bool:
    key_text = str(key or "").strip().lower()
    value_text = str(value or "").strip().lower()
    if not value_text:
        return False
    if _contains_any(value_text, TERMINAL_REJOIN_TERMS):
        return False
    return _contains_any(key_text, TERMINAL_STATUS_TAG_KEYS) or _terminal_status_text_match(value_text)


def _low_risk_status_tag_is_evidence_backed(
    session: GameSession,
    character_id: str,
    key: str,
    value: str,
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    if not character_id or not value.strip():
        return False
    source = _low_risk_status_evidence_text(
        session,
        character_id,
        actor=actor,
        player_message=player_message,
        completion=completion,
        tool_results=tool_results,
    )
    if not source:
        return False
    value_text = _normalized_projection_text(value)
    if not value_text:
        return False
    if value_text in _normalized_projection_text(source):
        return True
    if key == "当前所在":
        tokens = [
            token
            for token in re.split(r"[\s,，。；;、/\\|:：.!！?？()\[\]{}<>《》\"'“”]+", str(value))
            if len(token.strip()) >= 2
        ]
        return bool(tokens and any(_normalized_projection_text(token) in _normalized_projection_text(source) for token in tokens))
    return False


def _low_risk_status_evidence_text(
    session: GameSession,
    character_id: str,
    *,
    actor: dict[str, Any],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> str:
    character = session.characters.get(character_id)
    names = [character_id]
    if character and character.name:
        names.append(str(character.name))
    actor_character_id = _actor_character_id(session, actor)
    pieces: list[Any] = []
    if actor_character_id == character_id:
        pieces.extend([player_message, completion])
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") is False:
            continue
        text = _flatten_text({"tool": item.get("tool"), "args": item.get("args"), "result": result})
        if any(name and name in text for name in names):
            pieces.append(text)
    recent = (session.scene or {}).get("_recent_narrative_events") if isinstance(session.scene, dict) else []
    for event in recent[-8:] if isinstance(recent, list) else []:
        text = _flatten_text(event)
        if any(name and name in text for name in names):
            pieces.append(text)
    return _flatten_text(pieces)


def _terminal_status_text_match(text: Any) -> bool:
    normalized = str(text or "").lower()
    return _contains_any(normalized, TERMINAL_TERMS) and not _contains_any(normalized, TERMINAL_REJOIN_TERMS)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _normalized_projection_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = [
            str(value.get(key) or "")
            for key in ("name", "title", "id", "text", "summary", "value")
            if value.get(key)
        ]
        text = " ".join(parts) if parts else _flatten_text(value)
    elif isinstance(value, list):
        text = " ".join(_normalized_projection_text(item) for item in value[:8])
    else:
        text = str(value or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _salient_tokens(text: str) -> list[str]:
    tokens = []
    for token in re.split(r"[\s,，。；;、/\\|:：.!！?？()\[\]{}<>《》\"'“”]+", text):
        token = token.strip()
        if len(token) < 2:
            continue
        if token in {"当前", "场景", "目标", "状态", "等待", "一个", "已经"}:
            continue
        tokens.append(token)
    return tokens[:24]


def _response_text(response: Any) -> str:
    return str(getattr(response, "completion_text", "") or response or "")


def _parse_json_object(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _compact_json_value(value: Any, *, depth: int = 3, text_limit: int = 700, item_limit: int = 16) -> Any:
    if depth <= 0:
        return _short_text(value, text_limit)
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= item_limit:
                break
            result[str(key)] = _compact_json_value(item, depth=depth - 1, text_limit=text_limit, item_limit=item_limit)
        return result
    if isinstance(value, list):
        return [
            _compact_json_value(item, depth=depth - 1, text_limit=text_limit, item_limit=item_limit)
            for item in value[:item_limit]
        ]
    if isinstance(value, str):
        return _short_text(value, text_limit)
    return value


def _short_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _flatten_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value or "")
