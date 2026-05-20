from __future__ import annotations

import json
from typing import Any


def compute_session_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a semantic diff focused on analyst-relevant mutations."""
    diff: dict[str, Any] = {}

    diff["characters"] = _diff_characters(
        before.get("characters") or {},
        after.get("characters") or {},
    )

    diff["scene"] = _diff_dict_keys(
        before.get("scene") or {},
        after.get("scene") or {},
        text_limit=240,
    )

    diff["battle"] = _diff_battle(
        before.get("battle") or {},
        after.get("battle") or {},
    )

    diff["timeline"] = _diff_timeline(
        before.get("timeline") or {},
        after.get("timeline") or {},
    )

    diff["maps"] = _diff_maps(
        before.get("maps") or {},
        after.get("maps") or {},
    )

    diff["world_tags"] = _diff_dict_keys(
        before.get("world_tags") or {},
        after.get("world_tags") or {},
        text_limit=240,
    )

    diff["rules"] = {
        "count_changed": len(before.get("rules") or {}) != len(after.get("rules") or {}),
        "registered": list(set(after.get("rules") or {}) - set(before.get("rules") or {})),
    }

    diff["player_character_map"] = _diff_simple_mapping(
        before.get("player_character_map") or {},
        after.get("player_character_map") or {},
    )

    diff["memory_summary_changed"] = (before.get("memory_summary") or "") != (after.get("memory_summary") or "")

    diff["cycle_state_changed"] = (before.get("cycle_state") or "") != (after.get("cycle_state") or "")
    diff["current_cycle_id_changed"] = (before.get("current_cycle_id") or 0) != (after.get("current_cycle_id") or 0)

    diff["environment_summaries_added"] = max(
        0,
        len(after.get("environment_summaries") or []) - len(before.get("environment_summaries") or []),
    )

    return diff


def _diff_characters(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    all_ids = set(before.keys()) | set(after.keys())
    for cid in sorted(all_ids):
        b = before.get(cid)
        a = after.get(cid)
        if b is None and a is not None:
            result[cid] = {"created": True, "name": a.get("name", "")}
            continue
        if b is not None and a is None:
            result[cid] = {"removed": True, "name": b.get("name", "")}
            continue
        if isinstance(b, dict) and isinstance(a, dict):
            tag_diff = _diff_character_tags(b.get("tags") or [], a.get("tags") or [])
            summary_changed = (b.get("summary") or "") != (a.get("summary") or "")
            name_changed = (b.get("name") or "") != (a.get("name") or "")
            if tag_diff or summary_changed or name_changed:
                record: dict[str, Any] = {}
                if name_changed:
                    record["name_changed"] = {"old": b.get("name", ""), "new": a.get("name", "")}
                if summary_changed:
                    record["summary_changed"] = True
                if tag_diff:
                    record["tags"] = tag_diff
                if record:
                    result[cid] = record
    return result


def _diff_character_tags(before: list[Any], after: list[Any]) -> dict[str, Any] | None:
    def keyify(tag: Any) -> tuple[str, str]:
        if isinstance(tag, dict):
            return (str(tag.get("key") or ""), str(tag.get("layer") or infer_tag_layer(str(tag.get("key") or ""))))
        return ("", "")

    def valueify(tag: Any) -> Any:
        if isinstance(tag, dict):
            return tag.get("value")
        return tag

    before_map = {keyify(t): t for t in before}
    after_map = {keyify(t): t for t in after}

    all_keys = set(before_map.keys()) | set(after_map.keys())
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []

    for k in sorted(all_keys):
        b = before_map.get(k)
        a = after_map.get(k)
        if b is None and a is not None:
            added.append({"key": k[0], "layer": k[1], "value": _short_value(valueify(a))})
        elif b is not None and a is None:
            removed.append({"key": k[0], "layer": k[1]})
        elif json.dumps(valueify(b), ensure_ascii=False, sort_keys=True, default=str) != json.dumps(valueify(a), ensure_ascii=False, sort_keys=True, default=str):
            modified.append({
                "key": k[0],
                "layer": k[1],
                "old": _short_value(valueify(b)),
                "new": _short_value(valueify(a)),
            })

    if not (added or removed or modified):
        return None
    return {"added": added, "removed": removed, "modified": modified}


def _diff_battle(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    b_turn = before.get("turn") or {}
    a_turn = after.get("turn") or {}

    if before.get("active") != after.get("active"):
        result["active_changed"] = {"old": before.get("active"), "new": after.get("active")}

    for key in ("round", "phase", "current_entity_id", "current_index"):
        if b_turn.get(key) != a_turn.get(key):
            result[key] = {"old": b_turn.get(key), "new": a_turn.get(key)}

    b_order = list(b_turn.get("turn_order") or [])
    a_order = list(a_turn.get("turn_order") or [])
    if b_order != a_order:
        result["turn_order_changed"] = True

    b_actions = set(b_turn.get("acted_entity_ids") or [])
    a_actions = set(a_turn.get("acted_entity_ids") or [])
    if b_actions != a_actions:
        result["acted_changed"] = True

    return result


def _diff_timeline(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b_events = list(before.get("events") or [])
    a_events = list(after.get("events") or [])
    return {
        "events_added": max(0, len(a_events) - len(b_events)),
        "last_advanced_cycle_id_changed": (before.get("last_advanced_cycle_id") or -1) != (after.get("last_advanced_cycle_id") or -1),
    }


def _diff_maps(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if (before.get("active_strict_map_id") or "") != (after.get("active_strict_map_id") or ""):
        result["active_strict_map_id_changed"] = True
    if (before.get("active_overview_map_id") or "") != (after.get("active_overview_map_id") or ""):
        result["active_overview_map_id_changed"] = True

    b_records = set((before.get("records") or {}).keys())
    a_records = set((after.get("records") or {}).keys())
    if b_records != a_records:
        result["records_added"] = list(a_records - b_records)
        result["records_removed"] = list(b_records - a_records)
    else:
        modified: list[str] = []
        for rid in sorted(a_records):
            b_rec = (before.get("records") or {}).get(rid) or {}
            a_rec = (after.get("records") or {}).get(rid) or {}
            if json.dumps(b_rec, ensure_ascii=False, sort_keys=True, default=str) != json.dumps(a_rec, ensure_ascii=False, sort_keys=True, default=str):
                modified.append(rid)
        if modified:
            result["records_modified"] = modified

    return result


def _diff_dict_keys(before: dict[str, Any], after: dict[str, Any], text_limit: int = 240) -> dict[str, Any]:
    result: dict[str, Any] = {}
    all_keys = set(before.keys()) | set(after.keys())
    keys_changed: dict[str, Any] = {}
    keys_added: list[str] = []
    keys_removed: list[str] = []

    for key in sorted(all_keys):
        if key.startswith("_"):
            continue
        b = before.get(key)
        a = after.get(key)
        if b is None and a is not None:
            keys_added.append(key)
        elif b is not None and a is None:
            keys_removed.append(key)
        elif json.dumps(b, ensure_ascii=False, sort_keys=True, default=str) != json.dumps(a, ensure_ascii=False, sort_keys=True, default=str):
            keys_changed[key] = {"old": _short_value(b, text_limit), "new": _short_value(a, text_limit)}

    if keys_added:
        result["keys_added"] = keys_added
    if keys_removed:
        result["keys_removed"] = keys_removed
    if keys_changed:
        result["keys_changed"] = keys_changed
    return result


def _diff_simple_mapping(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    all_keys = set(before.keys()) | set(after.keys())
    for key in sorted(all_keys):
        if before.get(key) != after.get(key):
            result[key] = {"old": before.get(key), "new": after.get(key)}
    return result


def _short_value(value: Any, limit: int = 240) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def infer_tag_layer(key: str) -> str:
    text = str(key or "").strip().lower()
    if not text:
        return "notes"
    if any(token in text for token in ("关系", "attitude", "trust", "fear", "debt", "leverage", "known_facts", "last_interaction", "npc", "faction")):
        return "relations"
    for layer, keywords in _TAG_LAYER_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return layer
    return "notes"


_TAG_LAYER_KEYWORDS = {
    "identity": ("职业", "种族", "背景", "风格", "身份", "阵营", "出身", "姓名", "年龄"),
    "abilities": ("能力", "专长", "法术", "技能", "属性", "核心专长", "次要能力", "特性", "天赋"),
    "equipment": ("装备", "武器", "主武器", "常用装备", "护甲", "道具", "物品", "弹药"),
    "combat": ("默认战斗行为", "战斗习惯", "战术", "攻击", "防御", "射击", "近战", "施法偏好"),
    "status": ("状态", "伤势", "生命", "体力", "资源", "buff", "debuff", "增益", "减益", "异常", "弱点", "缺陷", "限制", "克制"),
}
