from __future__ import annotations

import json
from typing import Any


TRACKING_LIST_KEYS = {"open_hooks", "clues", "mysteries"}
SCENE_TRACKING_KEYS = TRACKING_LIST_KEYS | {
    "current_objective",
    "stakes",
    "pressure_clock",
}
AUTHORITATIVE_SCENE_KEYS = (
    "summary",
    "location",
    "current_objective",
    "current_conflict",
    "stakes",
    "pressure_clock",
    "open_hooks",
    "clues",
    "mysteries",
    "event_timeline",
    "entity_facts",
    "last_resolution",
    "story_forge_player_brief",
    "_recent_narrative_events",
    "_encounter_ended_reason",
    "_manual_save_patch",
    "_manual_save_patch_reason",
    "_manual_save_patch_at",
)
SIMPLE_CLUE_STATUSES = {
    "open",
    "discovered",
    "suspected",
    "resolved",
    "false_lead",
    "blocked",
}
HIDDEN_VISIBILITIES = {
    "dm",
    "hidden",
    "secret",
    "private",
    "gm",
    "gm_only",
    "dm_only",
    "internal",
    "diagnostic",
    "backend",
}
HIDDEN_STATUSES = {"hidden", "secret", "undiscovered"}
HIDDEN_SCENE_KEYS = {
    "backstage",
    "culprit",
    "dm_notes",
    "gm_notes",
    "hidden_clues",
    "hidden_locations",
    "hidden_truth",
    "mastermind",
    "plot",
    "plot_truth",
    "secret",
    "secret_clues",
    "secrets",
    "spoiler",
    "truth",
    "_story_forge_archive",
    "幕后身份",
    "幕后黑手",
    "隐藏真相",
    "真相",
    "未发现地点",
}
HIDDEN_SCENE_KEY_TOKENS = (
    "secret_",
    "_secret",
    "hidden_",
    "_hidden",
    "dm_only",
    "gm_only",
    "backstage",
    "spoiler",
)
DEFAULT_RECORD_STATUS = {
    "open_hooks": "open",
    "clues": "discovered",
    "mysteries": "open",
}
HOOK_TERMS = (
    "冲突",
    "危机",
    "目标",
    "任务",
    "压力",
    "敌",
    "抉择",
    "线索",
    "钩子",
    "异常",
    "求救",
    "追踪",
    "逼近",
    "突袭",
    "扑出",
    "失踪",
    "爆炸",
    "倒计时",
    "hook",
    "conflict",
    "objective",
    "clue",
    "pressure",
    "stake",
)


def normalize_scene_tracking_patch(
    patch: dict[str, Any],
    *,
    fill_opening: bool = False,
    opening_intro: str = "",
    player_guidance: str = "",
    initial_hook: str = "",
) -> dict[str, Any]:
    """Normalize the public hook/clue fields while preserving unrelated scene keys."""
    normalized = dict(patch or {})
    for key in TRACKING_LIST_KEYS:
        if key in normalized:
            normalized[key] = normalize_scene_records(normalized.get(key), key)
    if "pressure_clock" in normalized:
        normalized["pressure_clock"] = normalize_pressure_clock(normalized.get("pressure_clock"))
    if fill_opening:
        ensure_opening_tracking_fields(
            normalized,
            opening_intro=opening_intro,
            player_guidance=player_guidance,
            initial_hook=initial_hook,
        )
    return normalized


def normalize_scene_records(value: Any, key: str) -> list[dict[str, Any]]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        if _looks_like_scene_record(value):
            items = [value]
        else:
            items = []
            for item_id, item in value.items():
                if isinstance(item, dict):
                    record = dict(item)
                    record.setdefault("id", str(item_id))
                    items.append(record)
                elif str(item).strip():
                    items.append({"id": str(item_id), "text": str(item)})
    elif isinstance(value, list):
        items = value
    else:
        items = [value]

    records: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        record = _coerce_scene_record(item, key, index)
        if record:
            records.append(record)
    return records


def normalize_pressure_clock(value: Any) -> Any:
    if value in (None, "", [], {}):
        return {}
    if isinstance(value, dict):
        clock = dict(value)
        if clock.get("status"):
            clock["status"] = _normalize_status(clock.get("status"), default="active")
        else:
            clock["status"] = "active"
        if clock.get("visibility") is None:
            clock["visibility"] = "player"
        return clock
    return {
        "label": "当前压力",
        "status": "active",
        "text": _short_text(value, 240),
        "visibility": "player",
    }


def ensure_opening_tracking_fields(
    scene_patch: dict[str, Any],
    *,
    opening_intro: str = "",
    player_guidance: str = "",
    initial_hook: str = "",
) -> None:
    hook_text = _first_text(
        initial_hook,
        scene_patch.get("initial_hook"),
        scene_patch.get("current_objective"),
        scene_patch.get("current_conflict"),
        _first_record_text(scene_patch.get("open_hooks")),
        scene_patch.get("summary"),
        player_guidance,
    )
    pressure_text = _first_text(
        _pressure_text(scene_patch.get("pressure_clock")),
        scene_patch.get("stakes"),
        scene_patch.get("current_conflict"),
        initial_hook,
        scene_patch.get("summary"),
        opening_intro,
    )
    if hook_text and not _field_has_text(scene_patch.get("current_objective")):
        scene_patch["current_objective"] = _short_text(hook_text, 220)

    hooks = normalize_scene_records(scene_patch.get("open_hooks"), "open_hooks")
    _append_unique_record(hooks, "opening-hook", hook_text, "open")
    if pressure_text and pressure_text != hook_text:
        _append_unique_record(hooks, "opening-pressure", f"压力正在变化：{pressure_text}", "open")
    if len(hooks) < 2:
        summary_text = _first_text(scene_patch.get("summary"), opening_intro)
        _append_unique_record(hooks, "opening-scene-detail", f"现场异常值得继续处理：{summary_text}", "open")
    scene_patch["open_hooks"] = hooks[:8]

    if not _field_has_text(scene_patch.get("stakes")):
        scene_patch["stakes"] = _short_text(
            _first_text(
                scene_patch.get("stakes"),
                f"若拖延，{pressure_text}会继续升级。",
                "若拖延，当前压力会升级，目标、地点或相关 NPC 会承受代价。",
            ),
            260,
        )
    if not _field_has_text(scene_patch.get("pressure_clock")):
        scene_patch["pressure_clock"] = {
            "label": "开场压力",
            "status": "active",
            "text": _short_text(pressure_text or scene_patch["stakes"], 240),
            "visibility": "player",
        }
    else:
        scene_patch["pressure_clock"] = normalize_pressure_clock(scene_patch.get("pressure_clock"))


def opening_has_initial_hook(session_scene: dict[str, Any], scene_patch: dict[str, Any], initial_hook: str = "") -> bool:
    if _field_has_text(initial_hook):
        return True
    for key in ("initial_hook", "current_objective", "stakes", "pressure_clock", "current_conflict"):
        if _field_has_text(scene_patch.get(key)):
            return True
    for key in TRACKING_LIST_KEYS:
        if normalize_scene_records(scene_patch.get(key), key):
            return True
    scene_text = _flatten_text(scene_patch).lower()
    return any(term.lower() in scene_text for term in HOOK_TERMS)


def project_visible_scene_value(
    value: Any,
    *,
    key: str = "",
    depth: int = 3,
    text_limit: int = 360,
    item_limit: int = 16,
) -> Any:
    if _hidden_scene_key(key) or _hidden_record(value, key=key):
        return None
    if isinstance(value, dict):
        if depth <= 0:
            keys = [
                str(item_key)
                for item_key in value.keys()
                if not _hidden_scene_key(str(item_key))
            ][:item_limit]
            return {"keys": keys} if keys else None
        ordered_items = _authoritative_scene_items_first(value, key)
        projected: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(ordered_items):
            if index >= item_limit:
                projected["_truncated_items"] = max(0, len(ordered_items) - item_limit)
                break
            key_text = str(item_key)
            if _hidden_scene_key(key_text) or _hidden_record(item_value, key=key_text):
                continue
            projected_value = project_visible_scene_value(
                item_value,
                key=key_text,
                depth=depth - 1,
                text_limit=text_limit,
                item_limit=item_limit,
            )
            if projected_value not in ({}, [], "", None):
                projected[key_text] = projected_value
        return projected or None
    if isinstance(value, list):
        projected_items: list[Any] = []
        for item in value:
            if len(projected_items) >= item_limit:
                break
            if _hidden_record(item, key=key):
                continue
            projected_item = project_visible_scene_value(
                item,
                depth=depth - 1,
                text_limit=text_limit,
                item_limit=item_limit,
            )
            if projected_item not in ({}, [], "", None):
                projected_items.append(projected_item)
        if len(value) > item_limit:
            projected_items.append({"_truncated_items": len(value) - item_limit})
        return projected_items or None
    if isinstance(value, str):
        return _short_text(value, text_limit)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _short_text(value, min(text_limit, 200))


def _authoritative_scene_items_first(value: dict[str, Any], key: str = "") -> list[tuple[Any, Any]]:
    items = list(value.items())
    if key or not any(item_key in value for item_key in AUTHORITATIVE_SCENE_KEYS):
        return items
    priority = {item_key: index for index, item_key in enumerate(AUTHORITATIVE_SCENE_KEYS)}
    return sorted(
        items,
        key=lambda item: (
            priority.get(str(item[0]), len(priority)),
            items.index(item),
        ),
    )


def format_scene_tracking_status(scene: dict[str, Any]) -> str:
    visible = project_visible_scene_value(
        {key: scene.get(key) for key in SCENE_TRACKING_KEYS if key in scene},
        depth=4,
        text_limit=260,
        item_limit=12,
    )
    visible = visible if isinstance(visible, dict) else {}
    objective = _record_display_text(visible.get("current_objective"))
    stakes = _record_display_text(visible.get("stakes"))
    pressure = _record_display_text(visible.get("pressure_clock"))
    hooks = _visible_record_lines(visible.get("open_hooks"))
    clues = _visible_record_lines(visible.get("clues"))
    mysteries = _visible_record_lines(visible.get("mysteries"))

    if not any([objective, stakes, pressure, hooks, clues, mysteries]):
        return "当前还没有记录可见目标、线索或任务；下一次开场、调查或场景推进后会写入状态。"

    lines: list[str] = []
    if objective:
        lines.append(f"当前目标：{objective}")
    if pressure:
        lines.append(f"当前压力：{pressure}")
    if stakes:
        lines.append(f"利害关系：{stakes}")
    if clues:
        lines.append("线索：" + "；".join(clues[:4]))
    if hooks:
        lines.append("开放钩子：" + "；".join(hooks[:4]))
    if mysteries:
        lines.append("未解问题：" + "；".join(mysteries[:4]))
    return "\n".join(lines)


def _coerce_scene_record(item: Any, key: str, index: int) -> dict[str, Any]:
    default_status = DEFAULT_RECORD_STATUS.get(key, "open")
    if isinstance(item, dict):
        record = dict(item)
        text = _first_text(
            record.get("text"),
            record.get("description"),
            record.get("summary"),
            record.get("title"),
            record.get("name"),
        )
        if not text:
            return {}
        record["text"] = _short_text(text, 360)
        record.setdefault("id", f"{_singular_key(key)}-{index + 1}")
        record["status"] = _normalize_status(record.get("status"), default=default_status)
        record.setdefault("visibility", "player")
        return record
    text = _short_text(item, 360)
    if not text:
        return {}
    return {
        "id": f"{_singular_key(key)}-{index + 1}",
        "text": text,
        "status": default_status,
        "visibility": "player",
    }


def _looks_like_scene_record(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("text", "description", "summary", "title", "name", "status", "visibility"))


def _normalize_status(value: Any, *, default: str) -> str:
    status = str(value or default).strip().lower()
    aliases = {
        "found": "discovered",
        "known": "discovered",
        "done": "resolved",
        "closed": "resolved",
        "false": "false_lead",
        "blocked_by_risk": "blocked",
    }
    status = aliases.get(status, status)
    if status in SIMPLE_CLUE_STATUSES or status in {"active", "inactive", "paused"}:
        return status
    return default


def _append_unique_record(records: list[dict[str, Any]], item_id: str, text: Any, status: str) -> None:
    clean_text = _short_text(text, 360)
    if not clean_text:
        return
    existing_texts = {str(record.get("text") or "").strip() for record in records}
    if clean_text in existing_texts:
        return
    records.append(
        {
            "id": item_id,
            "text": clean_text,
            "status": status,
            "visibility": "player",
        }
    )


def _hidden_record(value: Any, *, key: str = "") -> bool:
    if not isinstance(value, dict):
        return False
    visibility = str(value.get("visibility") or "").strip().lower()
    status = str(value.get("status") or "").strip().lower()
    return (
        visibility in HIDDEN_VISIBILITIES
        or status in HIDDEN_STATUSES
        or value.get("hidden") is True
        or value.get("secret") is True
    )


def _hidden_scene_key(key: str) -> bool:
    key_text = str(key or "").strip()
    key_lower = key_text.lower()
    if key_lower in HIDDEN_SCENE_KEYS or key_text in HIDDEN_SCENE_KEYS:
        return True
    return any(token in key_lower for token in HIDDEN_SCENE_KEY_TOKENS)


def _visible_record_lines(value: Any) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value not in (None, "", [], {}) else []
    lines: list[str] = []
    for item in value:
        text = _record_display_text(item)
        if text:
            lines.append(text)
    return lines


def _record_display_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, dict):
        text = _first_text(
            value.get("text"),
            value.get("description"),
            value.get("summary"),
            value.get("title"),
            value.get("name"),
        )
        status = str(value.get("status") or "").strip()
        if status and status not in {"active"}:
            return f"{text}（{status}）" if text else status
        return text
    if isinstance(value, list):
        return "；".join(_record_display_text(item) for item in value if _record_display_text(item))
    return _short_text(value, 260)


def _pressure_text(value: Any) -> str:
    if isinstance(value, dict):
        return _first_text(value.get("text"), value.get("label"), value.get("description"), value.get("summary"))
    return _first_text(value)


def _field_has_text(value: Any) -> bool:
    return bool(_first_text(value))


def _first_record_text(value: Any) -> str:
    records = normalize_scene_records(value, "open_hooks")
    for record in records:
        text = _first_text(record.get("text"), record.get("description"), record.get("summary"))
        if text:
            return text
    return ""


def _first_text(*values: Any) -> str:
    for value in values:
        if value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            text = _first_text(
                value.get("text"),
                value.get("description"),
                value.get("summary"),
                value.get("title"),
                value.get("name"),
                value.get("label"),
            )
            if text:
                return text
            continue
        if isinstance(value, list):
            for item in value:
                text = _first_text(item)
                if text:
                    return text
            continue
        text = _short_text(value, 360)
        if text:
            return text
    return ""


def _flatten_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


def _singular_key(key: str) -> str:
    return {
        "open_hooks": "hook",
        "clues": "clue",
        "mysteries": "mystery",
    }.get(key, "item")


def _short_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
