from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


TIME_OF_DAY_ORDER = ("dawn", "morning", "noon", "afternoon", "evening", "night", "late_night")
TIME_OF_DAY_ALIASES = {
    "dawn": "dawn",
    "黎明": "dawn",
    "清晨": "dawn",
    "morning": "morning",
    "上午": "morning",
    "早上": "morning",
    "noon": "noon",
    "中午": "noon",
    "午间": "noon",
    "afternoon": "afternoon",
    "下午": "afternoon",
    "evening": "evening",
    "傍晚": "evening",
    "黄昏": "evening",
    "晚上": "evening",
    "night": "night",
    "夜晚": "night",
    "入夜": "night",
    "深夜": "late_night",
    "late_night": "late_night",
    "midnight": "late_night",
    "午夜": "late_night",
}
TIME_OF_DAY_LABELS = {
    "dawn": "黎明",
    "morning": "上午",
    "noon": "中午",
    "afternoon": "下午",
    "evening": "傍晚/晚上",
    "night": "夜晚",
    "late_night": "深夜",
}

TIMELINE_KEYS = {
    "timeline",
    "time",
    "current_time",
    "scene_time",
    "campaign_time",
    "global_time",
    "calendar",
    "date",
    "day",
    "time_of_day",
    "clock",
    "时间",
    "时间线",
    "当前时间",
    "场景时间",
    "团内时间",
    "日期",
    "天数",
    "第几天",
    "时段",
}
PER_PLAYER_TIME_KEYS = {
    "player_time",
    "player_times",
    "per_player_time",
    "character_time",
    "character_times",
    "per_character_time",
    "个人时间",
    "玩家时间",
    "玩家时间线",
    "角色时间",
    "角色时间线",
    "分别时间",
}
TIMELINE_ADVANCE_TERMS = (
    "第二天",
    "下一天",
    "次日",
    "隔天",
    "天亮",
    "翌日",
    "入夜",
    "夜晚",
    "晚上",
    "深夜",
    "凌晨",
    "早上",
    "清晨",
    "黎明",
    "黄昏",
    "傍晚",
    "过了一夜",
    "睡到",
    "休息到",
    "等到",
    "直到",
    "tomorrow",
    "next day",
    "morning",
    "night",
    "dawn",
)
IMPLICIT_TIMELINE_ADVANCE_TERMS = (
    "第二天",
    "下一天",
    "次日",
    "隔天",
    "翌日",
    "明天",
    "明日",
    "明早",
    "明晨",
    "待明天",
    "待明早",
    "天亮",
    "过了一夜",
    "一夜过去",
    "睡到",
    "休息到",
    "长休",
    "短休到",
    "等到天亮",
    "等到早上",
    "等到清晨",
    "等到晚上",
    "等到夜晚",
    "等到入夜",
    "直到天亮",
    "直到早上",
    "直到清晨",
    "直到晚上",
    "直到夜晚",
    "直到入夜",
    "夜幕降临",
    "入夜",
    "亥时",
    "子时",
    "深夜",
    "凌晨",
    "tomorrow",
    "next day",
    "until morning",
    "until dawn",
    "until night",
)


def default_timeline() -> dict[str, Any]:
    return {
        "day": 1,
        "time_of_day": "unknown",
        "label": "第 1 天，时间未定",
        "status": "global",
        "updated_at": "",
        "last_advance_reason": "",
        "last_advanced_cycle_id": -1,
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_timeline(value: Any) -> dict[str, Any]:
    timeline = default_timeline()
    if isinstance(value, Mapping):
        raw_day = value.get("day", value.get("current_day", value.get("calendar_day", 1)))
        timeline["day"] = _safe_day(raw_day)
        timeline["time_of_day"] = normalize_time_of_day(
            value.get("time_of_day", value.get("phase", value.get("period", "unknown")))
        )
        timeline["label"] = _compact_text(value.get("label") or value.get("description") or "", 120)
        timeline["status"] = "global"
        timeline["updated_at"] = _compact_text(value.get("updated_at") or "", 80)
        timeline["last_advance_reason"] = _compact_text(value.get("last_advance_reason") or "", 180)
        timeline["last_advanced_cycle_id"] = _safe_int(value.get("last_advanced_cycle_id", -1), -1)
    elif isinstance(value, str) and value.strip():
        timeline["label"] = _compact_text(value, 120)
    if not timeline["label"]:
        timeline["label"] = timeline_label(timeline)
    return timeline


def normalize_time_of_day(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in TIME_OF_DAY_ALIASES:
        return TIME_OF_DAY_ALIASES[text]
    for alias, canonical in TIME_OF_DAY_ALIASES.items():
        if alias and alias in text:
            return canonical
    return text[:40]


def timeline_label(timeline: Mapping[str, Any]) -> str:
    day = _safe_day(timeline.get("day", 1))
    time_of_day = normalize_time_of_day(timeline.get("time_of_day", "unknown"))
    label = TIME_OF_DAY_LABELS.get(time_of_day, "")
    return f"第 {day} 天，{label or '时间未定'}"


def timeline_view(timeline: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_timeline(timeline)
    return {
        "day": normalized["day"],
        "time_of_day": normalized["time_of_day"],
        "label": normalized["label"],
        "status": "global",
        "last_advance_reason": normalized.get("last_advance_reason", ""),
        "last_advanced_cycle_id": normalized.get("last_advanced_cycle_id", -1),
    }


def timeline_status_text(timeline: Mapping[str, Any]) -> str:
    normalized = normalize_timeline(timeline)
    return f"{normalized['label']}（全团同步）"


def extract_timeline_patch(patch: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    timeline_patch: dict[str, Any] = {}
    remaining: dict[str, Any] = {}
    for raw_key, value in patch.items():
        key = str(raw_key)
        if key.lower() in TIMELINE_KEYS or key in TIMELINE_KEYS:
            if isinstance(value, Mapping):
                timeline_patch.update(dict(value))
            else:
                timeline_patch["label"] = str(value)
            continue
        if key in {"current_day", "calendar_day"}:
            timeline_patch["day"] = value
            continue
        if key in {"phase", "period"} and _looks_like_time_of_day_value(value):
            timeline_patch["time_of_day"] = value
            continue
        remaining[raw_key] = value
    return timeline_patch, remaining


def patch_has_per_player_timeline(value: Any) -> bool:
    return _mapping_has_key(value, PER_PLAYER_TIME_KEYS)


def patch_touches_timeline(value: Any) -> bool:
    return bool(_mapping_has_key(value, TIMELINE_KEYS)) or _text_mentions_timeline_advance(value)


def apply_timeline_patch(
    current: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    reason: str = "",
    cycle_id: int = -1,
) -> dict[str, Any]:
    timeline = normalize_timeline(current)
    if "day" in patch or "current_day" in patch or "calendar_day" in patch:
        timeline["day"] = _safe_day(patch.get("day", patch.get("current_day", patch.get("calendar_day"))))
    if "day_delta" in patch or "advance_days" in patch:
        timeline["day"] = max(
            1,
            timeline["day"] + _safe_int(patch.get("day_delta", patch.get("advance_days")), 0),
        )
    if "time_of_day" in patch or "phase" in patch or "period" in patch:
        timeline["time_of_day"] = normalize_time_of_day(
            patch.get("time_of_day", patch.get("phase", patch.get("period")))
        )
    if "label" in patch or "description" in patch:
        timeline["label"] = _compact_text(patch.get("label") or patch.get("description") or "", 120)
    else:
        timeline["label"] = timeline_label(timeline)
    timeline["status"] = "global"
    timeline["updated_at"] = utc_now_iso()
    timeline["last_advance_reason"] = _compact_text(reason or patch.get("reason") or "", 180)
    timeline["last_advanced_cycle_id"] = _safe_int(cycle_id, -1)
    return timeline


def infer_timeline_patch_from_text(text: str, current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = str(text or "")
    lowered = source.lower()
    if not source.strip():
        return {}
    timeline = normalize_timeline(current or {})
    patch: dict[str, Any] = {}
    day = timeline["day"]
    if any(term in source for term in ("第二天", "下一天", "次日", "隔天", "翌日", "天亮", "过了一夜")) or "tomorrow" in lowered or "next day" in lowered:
        day += 1
        patch["day"] = day
    if "第" in source and "天" in source:
        match = re.search(r"第\s*(\d{1,3})\s*天", source)
        if match:
            patch["day"] = _safe_day(match.group(1))
    for token in ("黎明", "清晨", "早上", "上午", "中午", "下午", "傍晚", "黄昏", "晚上", "夜晚", "入夜", "深夜", "午夜", "凌晨"):
        if token in source:
            patch["time_of_day"] = normalize_time_of_day(token)
    if "morning" in lowered or "dawn" in lowered:
        patch["time_of_day"] = "morning" if "morning" in lowered else "dawn"
    elif "night" in lowered or "midnight" in lowered:
        patch["time_of_day"] = "late_night" if "midnight" in lowered else "night"
    if not patch:
        return {}
    next_timeline = apply_timeline_patch(timeline, patch, reason=source, cycle_id=timeline.get("last_advanced_cycle_id", -1))
    patch.setdefault("label", next_timeline["label"])
    return patch


def infer_timeline_patch_from_summary(summary: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> dict[str, Any]:
    for key in ("timeline", "time", "current_time", "scene_time"):
        value = summary.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def timeline_advance_requires_sync(value: Any) -> bool:
    if patch_has_per_player_timeline(value):
        return True
    return _text_mentions_timeline_advance(value)


def patch_mentions_implicit_timeline_advance(value: Any) -> bool:
    """Detect scene/character patches that smuggle time skips into prose.

    This intentionally uses a narrower term set than timeline_advance_requires_sync:
    "清晨" or "傍晚" can describe the current global time, but phrases like
    "睡到天亮" or "第二天" are actual timeline advancement and must go through
    the synchronized timeline patch path.
    """

    if isinstance(value, str):
        lowered = value.lower()
        return any(term in value or term in lowered for term in IMPLICIT_TIMELINE_ADVANCE_TERMS)
    if isinstance(value, Mapping):
        return any(patch_mentions_implicit_timeline_advance(item) for item in value.values())
    if isinstance(value, list):
        return any(patch_mentions_implicit_timeline_advance(item) for item in value)
    return False


def player_ids_with_cycle_actions(session: Any) -> set[str]:
    ids: set[str] = set()
    for action in getattr(getattr(session, "audit_buffer", None), "actions", []) or []:
        player_id = str(getattr(action, "player_id", "") or "")
        if player_id:
            ids.add(player_id)
    return ids


def active_player_ids(session: Any) -> set[str]:
    ids: set[str] = set()
    participants = getattr(session, "participants", {}) or {}
    if isinstance(participants, Mapping):
        ids.update(str(player_id) for player_id in participants.keys() if str(player_id))
    player_character_map = getattr(session, "player_character_map", {}) or {}
    if isinstance(player_character_map, Mapping):
        ids.update(str(player_id) for player_id in player_character_map.keys() if str(player_id))
    characters = getattr(session, "characters", {}) or {}
    if isinstance(characters, Mapping):
        for character in characters.values():
            player_id = str(getattr(character, "player_id", "") or "")
            if player_id:
                ids.add(player_id)
    return {player_id for player_id in ids if not _player_bound_character_is_terminal(session, player_id)}


def _player_bound_character_is_terminal(session: Any, player_id: str) -> bool:
    character_id = str((getattr(session, "player_character_map", {}) or {}).get(player_id) or "")
    if not character_id:
        return False
    characters = getattr(session, "characters", {}) or {}
    character = characters.get(character_id) if isinstance(characters, Mapping) else None
    if not character:
        return False
    for tag in getattr(character, "tags", []) or []:
        key = str(getattr(tag, "key", "") or "").lower()
        value = str(getattr(tag, "value", "") or "").lower()
        layer = str(getattr(tag, "layer", "") or "").lower()
        text = f"{key} {value}"
        if layer == "status" and any(
            term in text
            for term in (
                "死亡",
                "阵亡",
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
                "已离开当前故事",
                "不再参与当前故事",
                "dead",
                "deceased",
                "retired",
                "out_of_play",
                "out of play",
            )
        ):
            return True
    return False


def timeline_sync_status(session: Any, additional_player_ids: set[str] | None = None) -> dict[str, Any]:
    active = active_player_ids(session)
    acted = player_ids_with_cycle_actions(session)
    acted.update(str(player_id) for player_id in (additional_player_ids or set()) if str(player_id))
    if not active:
        return {
            "ok": True,
            "active_player_ids": [],
            "acted_player_ids": sorted(acted),
            "missing_player_ids": [],
        }
    missing = sorted(active - acted)
    return {
        "ok": not missing,
        "active_player_ids": sorted(active),
        "acted_player_ids": sorted(acted),
        "missing_player_ids": missing,
    }


def validate_global_timeline_advance(
    session: Any,
    patch: Mapping[str, Any] | None,
    additional_player_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not patch:
        return {"ok": True}
    if patch_has_per_player_timeline(patch):
        return {
            "ok": False,
            "error": "per_player_timeline_forbidden",
            "message": "时间线是全团共享权威状态，不能按玩家或角色分别写入不同日期/时段。",
        }
    sync = timeline_sync_status(session, additional_player_ids=additional_player_ids)
    if not sync.get("ok", True):
        return {
            "ok": False,
            "error": "timeline_sync_required",
            "message": "跨日、入夜、天亮或长时间跳转必须全团同步；仍有玩家未在本周期形成行动/确认，不能让部分玩家先进到下一时段。",
            **sync,
        }
    return {"ok": True, **sync}


def cycle_timeline_completion(
    session: Any,
    *,
    explicit_patch: Mapping[str, Any] | None = None,
    reason: str = "",
    require_sync: bool = True,
    additional_player_ids: set[str] | None = None,
) -> dict[str, Any]:
    patch = dict(explicit_patch or {})
    if not patch:
        return {"ok": True, "timeline_advanced": False}
    if require_sync:
        validation = validate_global_timeline_advance(session, patch, additional_player_ids=additional_player_ids)
        if not validation.get("ok"):
            return {**validation, "timeline_advanced": False}
    else:
        validation = {"ok": True}
    previous = normalize_timeline(getattr(session, "timeline", {}))
    session.timeline = apply_timeline_patch(
        previous,
        patch,
        reason=reason,
        cycle_id=getattr(session, "current_cycle_id", -1),
    )
    return {
        "ok": True,
        "timeline_advanced": True,
        "previous_timeline": timeline_view(previous),
        "timeline": timeline_view(session.timeline),
    }


def _mapping_has_key(value: Any, keys: set[str]) -> bool:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.lower() in keys or key in keys:
                return True
            if _mapping_has_key(item, keys):
                return True
    elif isinstance(value, list):
        return any(_mapping_has_key(item, keys) for item in value)
    return False


def _cycle_action_timeline_text(session: Any) -> str:
    parts: list[str] = []
    for action in getattr(getattr(session, "audit_buffer", None), "actions", []) or []:
        parts.append(str(getattr(action, "player_message", "") or ""))
        parts.append(str(getattr(action, "dm_narrative", "") or ""))
    return "\n".join(part for part in parts if part.strip())


def _text_mentions_timeline_advance(value: Any) -> bool:
    if isinstance(value, str):
        lowered = value.lower()
        return any(term in value or term in lowered for term in TIMELINE_ADVANCE_TERMS)
    if isinstance(value, Mapping):
        return any(_text_mentions_timeline_advance(item) for item in value.values())
    if isinstance(value, list):
        return any(_text_mentions_timeline_advance(item) for item in value)
    return False


def _looks_like_time_of_day_value(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and any(alias in text for alias in TIME_OF_DAY_ALIASES))


def _safe_day(value: Any) -> int:
    return max(1, _safe_int(value, 1))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
