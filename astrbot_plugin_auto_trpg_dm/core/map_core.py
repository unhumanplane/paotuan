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

MAP_TYPE_OVERVIEW = "overview"
MAP_TYPE_OVERVIEW_MAP = "overview_map"
MAP_TYPE_LEGACY_STRICT = "strict"
MAP_TYPE_STRICT_LOCAL = "strict_local_map"

DEFAULT_STRICT_LOCAL_MAP_ID = "strict-local-map"

STRICT_GRID_SOURCE_MAP_STORE = "map_store"
STRICT_GRID_SOURCE_LEGACY_BATTLE = "legacy_battle_grid"
STRICT_GRID_SOURCE_NONE = "none"

MAP_EVENT_CREATE_RECORD = "create_map_record"
MAP_EVENT_ADD_FACT = "add_fact"
MAP_EVENT_LINK_RENDER_REF = "link_render_ref"
MAP_EVENT_SET_ACTIVE = "set_active_map"

MAP_CANDIDATE_EVENT_TYPES = {
    MAP_EVENT_CREATE_RECORD,
    MAP_EVENT_ADD_FACT,
    MAP_EVENT_LINK_RENDER_REF,
    MAP_EVENT_SET_ACTIVE,
}
MAP_CANDIDATE_BLOCKED_KEYS = {
    "maps",
    "raw_map_store",
    "raw_store",
    "state_patch",
    "patch",
    "direct_patch",
}
MAP_CANDIDATE_ALLOWED_VISIBILITIES = {
    MAP_VISIBILITY_PUBLIC,
    MAP_VISIBILITY_PLAYER,
    MAP_VISIBILITY_DM,
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
    if _is_strict_map_record(record):
        if set_active or not normalized["active_strict_map_id"]:
            normalized["active_strict_map_id"] = safe_id
    elif set_active or not normalized["active_overview_map_id"]:
        normalized["active_overview_map_id"] = safe_id
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


def project_map_store(store: dict[str, Any], view: str) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    if view == MAP_VIEW_DIAGNOSTIC:
        return _diagnostic_map_view(normalized)
    allowed_visibility = _allowed_visibility_for_view(view)
    return {
        "schema_version": normalized["schema_version"],
        "active_overview_map_id": normalized["active_overview_map_id"],
        "active_strict_map_id": normalized["active_strict_map_id"],
        "records": {
            map_id: projected
            for map_id, record in normalized["records"].items()
            if (projected := _project_map_record(record, allowed_visibility)) is not None
        },
    }


def project_active_map_record(
    store: dict[str, Any],
    view: str,
    *,
    map_id: str = "",
    strict: bool = False,
) -> dict[str, Any] | None:
    projected = project_map_store(store, view)
    records = projected.get("records", {})
    if not isinstance(records, dict):
        return None
    selected_id = str(map_id or "")
    if not selected_id:
        selected_id = str(
            projected.get("active_strict_map_id" if strict else "active_overview_map_id") or ""
        )
    record = records.get(selected_id)
    return deepcopy(record) if isinstance(record, dict) else None


def load_active_strict_grid(store: dict[str, Any], legacy_battle: Any | None = None) -> dict[str, Any]:
    normalized = normalize_map_store(store)
    map_id = str(normalized.get("active_strict_map_id") or "")
    record = normalized["records"].get(map_id) if map_id else None
    if isinstance(record, dict):
        grid = record.get("grid")
        if isinstance(grid, dict):
            return {
                "ok": True,
                "source": STRICT_GRID_SOURCE_MAP_STORE,
                "map_id": map_id,
                "grid": deepcopy(grid),
                "record": deepcopy(record),
                "migration_required": False,
            }
        return {
            "ok": False,
            "source": STRICT_GRID_SOURCE_MAP_STORE,
            "map_id": map_id,
            "reason": "active_strict_grid_missing",
            "migration_required": False,
        }
    legacy_grid = _legacy_battle_grid(legacy_battle)
    if legacy_grid is not None:
        return {
            "ok": True,
            "source": STRICT_GRID_SOURCE_LEGACY_BATTLE,
            "map_id": "",
            "grid": legacy_grid,
            "migration_required": True,
            "authority_assumption": "legacy_battle_grid_until_strict_map_exists",
        }
    return {
        "ok": False,
        "source": STRICT_GRID_SOURCE_NONE,
        "reason": "strict_grid_not_found",
        "migration_required": False,
    }


def save_active_strict_grid(
    store: dict[str, Any],
    grid: dict[str, Any],
    *,
    map_id: str = "",
    title: str = "",
    authority: str = MAP_AUTHORITY_SPATIAL,
    source: str = "",
    migration_source: str = "",
    authority_assumption: str = "map_store_strict_grid",
) -> dict[str, Any]:
    if not isinstance(grid, dict):
        raise ValueError("strict_grid_invalid")
    normalized = normalize_map_store(store)
    safe_id = _require_id(map_id or normalized.get("active_strict_map_id") or DEFAULT_STRICT_LOCAL_MAP_ID)
    record = normalized["records"].get(safe_id)
    if not isinstance(record, dict):
        record = _new_map_record(
            safe_id,
            title=title or "Strict local map",
            map_type=MAP_TYPE_STRICT_LOCAL,
            authority=authority,
            visibility=MAP_VISIBILITY_DM,
        )
        normalized["records"][safe_id] = record
    elif not _is_strict_map_record(record):
        raise ValueError(f"strict_map_record_type_mismatch:{safe_id}")
    record["type"] = MAP_TYPE_STRICT_LOCAL
    record["authority"] = _safe_authority(authority)
    if title:
        record["title"] = _short_text(title, 160)
    record["grid"] = _json_safe(grid)
    record["archive_identity"] = _strict_grid_archive_identity(
        record.get("archive_identity"),
        source=source,
        migration_source=migration_source,
        authority_assumption=authority_assumption,
    )
    record["updated_at"] = _utc_now_iso()
    normalized["active_strict_map_id"] = safe_id
    _replace_store(store, normalized)
    return deepcopy(record)


def migrate_legacy_battle_grid(
    store: dict[str, Any],
    battle: Any,
    *,
    map_id: str = DEFAULT_STRICT_LOCAL_MAP_ID,
    title: str = "Legacy battle grid",
) -> dict[str, Any]:
    loaded = load_active_strict_grid(store, battle)
    if loaded.get("source") == STRICT_GRID_SOURCE_MAP_STORE:
        return {
            "ok": bool(loaded.get("ok")),
            "status": "strict_grid_already_authoritative" if loaded.get("ok") else "strict_grid_not_migrated",
            "source": STRICT_GRID_SOURCE_MAP_STORE,
            "map_id": loaded.get("map_id", ""),
            "grid": deepcopy(loaded.get("grid")) if isinstance(loaded.get("grid"), dict) else {},
            "migrated": False,
            "reason": loaded.get("reason", ""),
        }
    if loaded.get("source") != STRICT_GRID_SOURCE_LEGACY_BATTLE or not isinstance(loaded.get("grid"), dict):
        return {
            "ok": False,
            "status": "strict_grid_not_migrated",
            "source": loaded.get("source", STRICT_GRID_SOURCE_NONE),
            "reason": loaded.get("reason", "strict_grid_not_found"),
            "migrated": False,
        }
    record = save_active_strict_grid(
        store,
        loaded["grid"],
        map_id=map_id,
        title=title,
        authority=MAP_AUTHORITY_SPATIAL,
        migration_source="battle.grid",
        authority_assumption="legacy_battle_grid_wrapped_until_spatial_tools_write_map_store",
    )
    return {
        "ok": True,
        "status": "legacy_battle_grid_migrated",
        "source": STRICT_GRID_SOURCE_LEGACY_BATTLE,
        "map_id": record["id"],
        "grid": deepcopy(record["grid"]),
        "record": record,
        "migrated": True,
    }


def validate_candidate_map_event(store: dict[str, Any], event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return _candidate_rejected("invalid_candidate_type")
    blocked = _blocked_candidate_keys(event)
    if blocked:
        return _candidate_rejected("raw_patch_not_allowed", blocked_keys=blocked)
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    if event_type not in MAP_CANDIDATE_EVENT_TYPES:
        return _candidate_rejected("unsupported_event_type", event_type=event_type)
    map_id = str(event.get("map_id") or "").strip()
    if not map_id:
        return _candidate_rejected("map_id_required", event_type=event_type)
    normalized = normalize_map_store(store)
    payload = event.get("payload", {})
    if payload in (None, ""):
        payload = {}
    if not isinstance(payload, dict):
        return _candidate_rejected("invalid_payload_type", event_type=event_type, map_id=map_id)
    if event_type != MAP_EVENT_CREATE_RECORD and map_id not in normalized["records"]:
        return _candidate_rejected("unknown_map_id", event_type=event_type, map_id=map_id)
    visibility = str(payload.get("visibility") or event.get("visibility") or MAP_VISIBILITY_DM)
    if visibility not in MAP_CANDIDATE_ALLOWED_VISIBILITIES:
        return _candidate_rejected(
            "candidate_visibility_not_allowed",
            event_type=event_type,
            map_id=map_id,
            visibility=visibility,
        )
    field_error = _candidate_field_error(event_type, payload)
    if field_error:
        return _candidate_rejected(field_error, event_type=event_type, map_id=map_id)
    return {
        "ok": True,
        "status": "candidate_valid",
        "event": {
            "event_type": event_type,
            "map_id": map_id,
            "payload": _candidate_payload(event_type, payload),
            "source": _short_text(event.get("source") or "", 120),
            "confidence": _safe_confidence(event.get("confidence")),
        },
    }


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
        "type": _safe_map_type(map_type),
        "title": _short_text(title or map_id, 160),
        "authority": _safe_authority(authority),
        "visibility": _safe_visibility(visibility),
        "facts": [],
        "render_refs": [],
        "archive_identity": {},
        "created_at": now,
        "updated_at": now,
    }


def _project_map_record(
    record: dict[str, Any],
    allowed_visibility: set[str],
) -> dict[str, Any] | None:
    if record.get("visibility") not in allowed_visibility:
        return None
    facts = [
        _project_fact(fact)
        for fact in record.get("facts", [])
        if isinstance(fact, dict) and fact.get("visibility") in allowed_visibility
    ]
    return {
        "id": record.get("id", ""),
        "type": record.get("type", ""),
        "title": record.get("title", ""),
        "authority": record.get("authority", ""),
        "visibility": record.get("visibility", ""),
        "facts": facts,
        "render_refs": [
            _project_render_ref(ref)
            for ref in record.get("render_refs", [])
            if isinstance(ref, dict)
        ],
    }


def _project_fact(fact: dict[str, Any]) -> dict[str, Any]:
    projected = {
        "id": fact.get("id", ""),
        "kind": fact.get("kind", ""),
        "text": fact.get("text", ""),
        "authority": fact.get("authority", ""),
        "visibility": fact.get("visibility", ""),
        "source": fact.get("source", ""),
    }
    payload = fact.get("payload")
    if payload not in (None, "", [], {}):
        projected["payload"] = _json_safe(payload)
    return projected


def _project_render_ref(ref: dict[str, Any]) -> dict[str, Any]:
    return {
        key: ref.get(key)
        for key in ("type", "title", "name", "visual_only")
        if ref.get(key) not in (None, "", [], {})
    }


def _diagnostic_map_view(store: dict[str, Any]) -> dict[str, Any]:
    records = {}
    for map_id, record in store["records"].items():
        facts = [item for item in record.get("facts", []) if isinstance(item, dict)]
        records[map_id] = {
            "id": record.get("id", map_id),
            "type": record.get("type", ""),
            "title": record.get("title", ""),
            "authority": record.get("authority", ""),
            "visibility": record.get("visibility", ""),
            "fact_count": len(facts),
            "hidden_fact_count": sum(
                1 for item in facts if item.get("visibility") == MAP_VISIBILITY_HIDDEN
            ),
            "render_ref_count": len(
                [item for item in record.get("render_refs", []) if isinstance(item, dict)]
            ),
        }
    return {
        "schema_version": store["schema_version"],
        "active_overview_map_id": store["active_overview_map_id"],
        "active_strict_map_id": store["active_strict_map_id"],
        "record_count": len(store["records"]),
        "records": records,
    }


def _allowed_visibility_for_view(view: str) -> set[str]:
    if view == MAP_VIEW_PLAYER:
        return {MAP_VISIBILITY_PUBLIC, MAP_VISIBILITY_PLAYER}
    if view == MAP_VIEW_DM_NARRATION:
        return {MAP_VISIBILITY_PUBLIC, MAP_VISIBILITY_PLAYER, MAP_VISIBILITY_DM}
    if view == MAP_VIEW_RA_AUTHORITY:
        return {MAP_VISIBILITY_PUBLIC, MAP_VISIBILITY_PLAYER, MAP_VISIBILITY_DM}
    raise ValueError(f"unsupported_map_view:{view}")


def _candidate_field_error(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == MAP_EVENT_ADD_FACT:
        if not str(payload.get("fact_id") or payload.get("id") or "").strip():
            return "fact_id_required"
        if not str(payload.get("kind") or "").strip():
            return "fact_kind_required"
    if event_type == MAP_EVENT_LINK_RENDER_REF and not str(payload.get("ref_type") or payload.get("type") or "").strip():
        return "render_ref_type_required"
    if event_type == MAP_EVENT_SET_ACTIVE and not (payload.get("overview") or payload.get("strict")):
        return "active_slot_required"
    return ""


def _candidate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if event_type == MAP_EVENT_CREATE_RECORD:
        return {
            "title": _short_text(payload.get("title") or "", 160),
            "map_type": _short_text(payload.get("map_type") or payload.get("type") or "overview", 80),
            "visibility": str(payload.get("visibility") or MAP_VISIBILITY_DM),
        }
    if event_type == MAP_EVENT_ADD_FACT:
        return {
            "fact_id": _short_text(payload.get("fact_id") or payload.get("id") or "", 120),
            "kind": _short_text(payload.get("kind") or "", 80),
            "text": _short_text(payload.get("text") or "", 1000),
            "payload": _json_safe(payload.get("payload") or {}),
            "visibility": str(payload.get("visibility") or MAP_VISIBILITY_DM),
        }
    if event_type == MAP_EVENT_LINK_RENDER_REF:
        return {
            "ref_type": _short_text(payload.get("ref_type") or payload.get("type") or "", 80),
            "title": _short_text(payload.get("title") or "", 160),
            "name": _short_text(payload.get("name") or "", 160),
            "visual_only": bool(payload.get("visual_only", True)),
        }
    if event_type == MAP_EVENT_SET_ACTIVE:
        return {
            "overview": bool(payload.get("overview")),
            "strict": bool(payload.get("strict")),
        }
    return {}


def _blocked_candidate_keys(value: Any) -> list[str]:
    blocked: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text in MAP_CANDIDATE_BLOCKED_KEYS:
                blocked.add(key_text)
            blocked.update(_blocked_candidate_keys(item))
    elif isinstance(value, list):
        for item in value:
            blocked.update(_blocked_candidate_keys(item))
    return sorted(blocked)


def _candidate_rejected(reason: str, **details: Any) -> dict[str, Any]:
    result = {"ok": False, "status": "candidate_rejected", "reason": reason}
    result.update({key: _json_safe(value) for key, value in details.items() if value not in (None, "", [], {})})
    return result


def _safe_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


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
    grid = value.get("grid")
    if isinstance(grid, dict):
        record["grid"] = _json_safe(grid)
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


def _is_strict_map_record(record: dict[str, Any]) -> bool:
    return _safe_map_type(record.get("type")) == MAP_TYPE_STRICT_LOCAL


def _safe_map_type(value: Any) -> str:
    text = _short_text(value or MAP_TYPE_OVERVIEW, 80)
    if text == MAP_TYPE_LEGACY_STRICT:
        return MAP_TYPE_STRICT_LOCAL
    return text


def _legacy_battle_grid(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    grid = value.get("grid")
    return deepcopy(grid) if isinstance(grid, dict) else None


def _strict_grid_archive_identity(
    value: Any,
    *,
    source: str,
    migration_source: str,
    authority_assumption: str,
) -> dict[str, Any]:
    identity = _json_safe(_dict_or_empty(value))
    if source:
        identity["source"] = _short_text(source, 120)
    if migration_source:
        identity["migration_source"] = _short_text(migration_source, 120)
    if authority_assumption:
        identity["authority_assumption"] = _short_text(authority_assumption, 240)
    identity["strict_grid_adapter_version"] = 1
    return identity


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
