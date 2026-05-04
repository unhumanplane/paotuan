from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

MAP_SCHEMA_VERSION = 1

MAP_VIEW_PLAYER = "player_view"
MAP_VIEW_DM_NARRATION = "dm_narration_view"
MAP_VIEW_RA_AUTHORITY = "ra_authority_view"
MAP_VIEW_DIAGNOSTIC = "diagnostic_view"

MAP_VISIBILITY_PUBLIC = "public"
MAP_VISIBILITY_PLAYER = "player"
MAP_VISIBILITY_DM = "dm"
MAP_VISIBILITY_HIDDEN = "hidden"
MAP_VISIBILITY_DIAGNOSTIC = "diagnostic"

MAP_VISIBILITIES = {
    MAP_VISIBILITY_PUBLIC,
    MAP_VISIBILITY_PLAYER,
    MAP_VISIBILITY_DM,
    MAP_VISIBILITY_HIDDEN,
    MAP_VISIBILITY_DIAGNOSTIC,
}

MAP_AUTHORITY_CODE = "code"
MAP_AUTHORITY_SPATIAL = "spatial"
MAP_AUTHORITY_DM = "dm"
MAP_AUTHORITY_RA_CANDIDATE = "ra_candidate"
MAP_AUTHORITY_VISUAL = "visual"

MAP_AUTHORITIES = {
    MAP_AUTHORITY_CODE,
    MAP_AUTHORITY_SPATIAL,
    MAP_AUTHORITY_DM,
    MAP_AUTHORITY_RA_CANDIDATE,
    MAP_AUTHORITY_VISUAL,
}


def default_map_store() -> dict[str, Any]:
    return {
        "schema_version": MAP_SCHEMA_VERSION,
        "active_overview_map_id": "",
        "active_strict_map_id": "",
        "records": {},
        "archive_identity": {},
    }


def normalize_map_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return default_map_store()
    store = default_map_store()
    store["schema_version"] = _safe_int(value.get("schema_version"), MAP_SCHEMA_VERSION)
    store["active_overview_map_id"] = str(value.get("active_overview_map_id") or "")
    store["active_strict_map_id"] = str(value.get("active_strict_map_id") or "")
    store["archive_identity"] = _dict_or_empty(value.get("archive_identity"))
    records = value.get("records", {})
    if isinstance(records, dict):
        store["records"] = {
            str(map_id): _normalize_map_record(map_id, record)
            for map_id, record in records.items()
            if isinstance(record, dict) and str(map_id).strip()
        }
    if store["active_overview_map_id"] not in store["records"]:
        store["active_overview_map_id"] = ""
    if store["active_strict_map_id"] not in store["records"]:
        store["active_strict_map_id"] = ""
    return store


def create_map_record(
    store: dict[str, Any],
    map_id: str,
    *,
    title: str = "",
    map_type: str = "overview",
    authority: str = MAP_AUTHORITY_CODE,
    visibility: str = MAP_VISIBILITY_DM,
    set_active: bool = False,
) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    safe_id = _require_id(map_id)
    if safe_id in normalized["records"]:
        raise ValueError(f"map_record_exists:{safe_id}")
    record = _new_map_record(
        safe_id,
        title=title,
        map_type=map_type,
        authority=authority,
        visibility=visibility,
    )
    normalized["records"][safe_id] = record
    if set_active or not normalized["active_overview_map_id"]:
        normalized["active_overview_map_id"] = safe_id
    if map_type == "strict" and (set_active or not normalized["active_strict_map_id"]):
        normalized["active_strict_map_id"] = safe_id
    _replace_store(store, normalized)
    return deepcopy(record)


def get_map_record(store: dict[str, Any], map_id: str) -> dict[str, Any] | None:
    normalized = normalize_map_store(store)
    record = normalized["records"].get(str(map_id or ""))
    return deepcopy(record) if isinstance(record, dict) else None


def update_map_record(
    store: dict[str, Any],
    map_id: str,
    *,
    title: str | None = None,
    authority: str | None = None,
    visibility: str | None = None,
    archive_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    safe_id = _require_id(map_id)
    record = normalized["records"].get(safe_id)
    if not isinstance(record, dict):
        raise ValueError(f"map_record_not_found:{safe_id}")
    if title is not None:
        record["title"] = _short_text(title, 160)
    if authority is not None:
        record["authority"] = _safe_authority(authority)
    if visibility is not None:
        record["visibility"] = _safe_visibility(visibility)
    if archive_identity is not None:
        record["archive_identity"] = _json_safe(archive_identity)
    record["updated_at"] = _utc_now_iso()
    _replace_store(store, normalized)
    return deepcopy(record)


def add_map_fact(
    store: dict[str, Any],
    map_id: str,
    *,
    fact_id: str,
    kind: str,
    text: str = "",
    payload: dict[str, Any] | None = None,
    authority: str = MAP_AUTHORITY_CODE,
    visibility: str = MAP_VISIBILITY_DM,
    source: str = "",
) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    safe_map_id = _require_id(map_id)
    record = normalized["records"].get(safe_map_id)
    if not isinstance(record, dict):
        raise ValueError(f"map_record_not_found:{safe_map_id}")
    safe_fact_id = _require_id(fact_id)
    fact = {
        "id": safe_fact_id,
        "kind": _short_text(kind or "fact", 80),
        "text": _short_text(text, 1000),
        "payload": _json_safe(payload or {}),
        "authority": _safe_authority(authority),
        "visibility": _safe_visibility(visibility),
        "source": _short_text(source, 120),
        "created_at": _utc_now_iso(),
    }
    facts = [item for item in record.get("facts", []) if isinstance(item, dict) and item.get("id") != safe_fact_id]
    facts.append(fact)
    record["facts"] = facts[-200:]
    record["updated_at"] = _utc_now_iso()
    _replace_store(store, normalized)
    return deepcopy(fact)


def add_render_ref(
    store: dict[str, Any],
    map_id: str,
    *,
    ref_type: str,
    title: str = "",
    name: str = "",
    path: str = "",
    url: str = "",
    visual_only: bool = True,
) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    safe_map_id = _require_id(map_id)
    record = normalized["records"].get(safe_map_id)
    if not isinstance(record, dict):
        raise ValueError(f"map_record_not_found:{safe_map_id}")
    ref = {
        "type": _short_text(ref_type or "render_ref", 80),
        "title": _short_text(title, 160),
        "name": _short_text(name, 160),
        "path": _short_text(path, 500),
        "url": _short_text(url, 500),
        "visual_only": bool(visual_only),
        "created_at": _utc_now_iso(),
    }
    record["render_refs"] = [*list(record.get("render_refs", []))[-23:], ref]
    record["updated_at"] = _utc_now_iso()
    _replace_store(store, normalized)
    return deepcopy(ref)


def _new_map_record(
    map_id: str,
    *,
    title: str,
    map_type: str,
    authority: str,
    visibility: str,
) -> dict[str, Any]:
    now = _utc_now_iso()
    return {
        "id": map_id,
        "record_version": 1,
        "type": _short_text(map_type or "overview", 80),
        "title": _short_text(title or map_id, 160),
        "authority": _safe_authority(authority),
        "visibility": _safe_visibility(visibility),
        "facts": [],
        "render_refs": [],
        "archive_identity": {},
        "created_at": now,
        "updated_at": now,
    }


def _normalize_map_record(map_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    record_id = _require_id(str(value.get("id") or map_id))
    record = _new_map_record(
        record_id,
        title=str(value.get("title") or record_id),
        map_type=str(value.get("type") or "overview"),
        authority=str(value.get("authority") or MAP_AUTHORITY_CODE),
        visibility=str(value.get("visibility") or MAP_VISIBILITY_DM),
    )
    record["record_version"] = _safe_int(value.get("record_version"), 1)
    record["facts"] = [
        _normalize_fact(item)
        for item in list(value.get("facts") or [])[:200]
        if isinstance(item, dict)
    ]
    record["render_refs"] = [
        _json_safe(item)
        for item in list(value.get("render_refs") or [])[:24]
        if isinstance(item, dict)
    ]
    record["archive_identity"] = _json_safe(_dict_or_empty(value.get("archive_identity")))
    record["created_at"] = str(value.get("created_at") or record["created_at"])
    record["updated_at"] = str(value.get("updated_at") or record["updated_at"])
    return record


def _normalize_fact(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _require_id(str(value.get("id") or "fact")),
        "kind": _short_text(value.get("kind") or "fact", 80),
        "text": _short_text(value.get("text") or "", 1000),
        "payload": _json_safe(value.get("payload") or {}),
        "authority": _safe_authority(str(value.get("authority") or MAP_AUTHORITY_CODE)),
        "visibility": _safe_visibility(str(value.get("visibility") or MAP_VISIBILITY_DM)),
        "source": _short_text(value.get("source") or "", 120),
        "created_at": str(value.get("created_at") or _utc_now_iso()),
    }


def _replace_store(target: dict[str, Any], normalized: dict[str, Any]) -> None:
    target.clear()
    target.update(normalized)


def _require_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("map_id_required")
    return _short_text(text, 120)


def _safe_visibility(value: str) -> str:
    text = str(value or "").strip()
    return text if text in MAP_VISIBILITIES else MAP_VISIBILITY_DM


def _safe_authority(value: str) -> str:
    text = str(value or "").strip()
    return text if text in MAP_AUTHORITIES else MAP_AUTHORITY_CODE


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _short_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
