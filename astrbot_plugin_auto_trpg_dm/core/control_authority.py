from __future__ import annotations

from copy import deepcopy
from typing import Any

from .map_core import load_active_strict_grid_entities


CONTROL_SCHEMA_VERSION = 1

CONTROL_STATUS_OWNED = "owned"
CONTROL_STATUS_HOSTED_BY_SYSTEM = "hosted_by_system"
CONTROL_STATUS_DELEGATED_TO_PLAYER = "delegated_to_player"
CONTROL_STATUS_RELINQUISHED = "relinquished"
CONTROL_STATUS_REVOKED = "revoked"
CONTROL_STATUS_EXPIRED = "expired"

CONTROL_ACTIVE_STATUSES = {
    CONTROL_STATUS_OWNED,
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROL_STATUS_DELEGATED_TO_PLAYER,
    CONTROL_STATUS_RELINQUISHED,
}

CONTROLLER_TYPE_OWNER = "owner"
CONTROLLER_TYPE_SYSTEM_HOST = "system_host"
CONTROLLER_TYPE_PLAYER_DELEGATE = "player_delegate"
CONTROLLER_TYPE_NONE = "none"

CONTROL_RISK_LOW = "low"
CONTROL_RISK_MEDIUM = "medium"
CONTROL_RISK_HIGH = "high"
CONTROL_RISKS = {CONTROL_RISK_LOW, CONTROL_RISK_MEDIUM, CONTROL_RISK_HIGH}

_CONTROL_EVENT_SAFE_FIELDS = (
    "type",
    "action",
    "character_id",
    "owner_player_id",
    "requester_player_id",
    "active_controller_id",
    "controller_type",
    "status",
    "previous_active_controller_id",
    "previous_controller_type",
    "previous_status",
    "risk_ceiling",
    "duration_type",
    "expires_at",
    "effective_at",
    "audit_ref",
)
_CONTROL_EVENT_FIELD_LIMIT = 160


def resolve_control_authority(session: Any, character_id: str, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve whether actor is the active controller for a character/entity."""
    requested_id = str(character_id or "").strip()
    safe_character_id = character_id_for_entity(session, requested_id)
    actor_player_id = str((actor or {}).get("player_id") or "").strip()
    owner_player_id = owner_player_id_for_entity(session, requested_id or safe_character_id)
    record = control_record_for_character(session, safe_character_id, owner_player_id=owner_player_id)
    active_controller_id = str(record.get("active_controller_id") or "").strip()
    status = str(record.get("status") or CONTROL_STATUS_OWNED)
    controller_type = str(record.get("controller_type") or CONTROLLER_TYPE_OWNER)

    if not safe_character_id:
        return _deny(
            "character_id_required",
            requested_id,
            owner_player_id,
            active_controller_id,
            controller_type,
            status,
            actor_player_id,
        )
    if not owner_player_id and not active_controller_id:
        return _deny(
            "character_has_no_owner_or_controller",
            safe_character_id,
            owner_player_id,
            active_controller_id,
            controller_type,
            status,
            actor_player_id,
        )
    if status not in CONTROL_ACTIVE_STATUSES:
        return _deny(
            "control_record_inactive",
            safe_character_id,
            owner_player_id,
            active_controller_id,
            controller_type,
            status,
            actor_player_id,
            record,
        )
    if not actor_player_id:
        return _deny(
            "actor_player_id_required",
            safe_character_id,
            owner_player_id,
            active_controller_id,
            controller_type,
            status,
            actor_player_id,
            record,
        )
    if actor_player_id != active_controller_id:
        return _deny(
            "actor_is_not_active_controller",
            safe_character_id,
            owner_player_id,
            active_controller_id,
            controller_type,
            status,
            actor_player_id,
            record,
        )
    return {
        "ok": True,
        "character_id": safe_character_id,
        "owner_player_id": owner_player_id,
        "active_controller_id": active_controller_id,
        "controller_type": controller_type,
        "status": status,
        "risk_ceiling": str(record.get("risk_ceiling") or CONTROL_RISK_LOW),
        "duration_type": str(record.get("duration_type") or "until_revoked"),
        "expires_at": str(record.get("expires_at") or ""),
        "reason": _success_reason(controller_type),
        "audit_ref": str(record.get("audit_ref") or ""),
    }


def control_record_for_character(
    session: Any,
    character_id: str,
    *,
    owner_player_id: str = "",
) -> dict[str, Any]:
    safe_character_id = str(character_id or "").strip()
    records = _control_records(session)
    stored = records.get(safe_character_id)
    if isinstance(stored, dict):
        return _normalize_record(stored, safe_character_id, owner_player_id)
    owner_id = str(owner_player_id or owner_player_id_for_entity(session, safe_character_id) or "").strip()
    return {
        "character_id": safe_character_id,
        "owner_player_id": owner_id,
        "active_controller_id": owner_id,
        "controller_type": CONTROLLER_TYPE_OWNER if owner_id else CONTROLLER_TYPE_NONE,
        "status": CONTROL_STATUS_OWNED if owner_id else CONTROL_STATUS_REVOKED,
        "authorized_by": owner_id,
        "consent_reference": "",
        "risk_ceiling": CONTROL_RISK_LOW,
        "scope": "character",
        "duration_type": "until_revoked",
        "expires_at": "",
        "effective_at": "",
        "revoked_at": "",
        "audit_ref": "",
    }


def owner_player_id_for_entity(session: Any, entity_id: str) -> str:
    safe_entity_id = str(entity_id or "").strip()
    if not safe_entity_id:
        return ""
    entities = load_active_strict_grid_entities(
        getattr(session, "maps", {}),
        getattr(session, "battle", {}),
    )
    grid_entity = dict(entities.get(safe_entity_id, {}))
    tags = dict(grid_entity.get("tags", {}))
    if tags.get("player_id"):
        return str(tags["player_id"])
    character_id = str(tags.get("character_id") or safe_entity_id)
    character = getattr(session, "characters", {}).get(character_id)
    if character and getattr(character, "player_id", ""):
        return str(character.player_id)
    for player_id, bound_id in getattr(session, "player_character_map", {}).items():
        if str(bound_id) == character_id or str(bound_id) == safe_entity_id:
            return str(player_id)
    return ""


def character_id_for_entity(session: Any, entity_id: str) -> str:
    safe_entity_id = str(entity_id or "").strip()
    if not safe_entity_id:
        return ""
    entities = load_active_strict_grid_entities(
        getattr(session, "maps", {}),
        getattr(session, "battle", {}),
    )
    grid_entity = dict(entities.get(safe_entity_id, {}))
    tags = dict(grid_entity.get("tags", {}))
    character_id = str(tags.get("character_id") or "").strip()
    if character_id:
        return character_id
    return safe_entity_id


def project_control_authority(session: Any, view: str = "dm_narration_view") -> dict[str, Any]:
    records = []
    for character_id in sorted(getattr(session, "characters", {}).keys()):
        record = control_record_for_character(session, character_id)
        if not record.get("owner_player_id") and not record.get("active_controller_id"):
            continue
        records.append(_project_record(record, view))
    if view == "diagnostic_view":
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "record_count": len(records),
            "records": records,
        }
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "records": records,
    }


def normalize_control_authority_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    records = value.get("records", {})
    normalized: dict[str, Any] = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "records": {},
    }
    if isinstance(records, dict):
        for character_id, record in records.items():
            safe_character_id = str(character_id or "").strip()
            if safe_character_id and isinstance(record, dict):
                normalized["records"][safe_character_id] = _normalize_record(record, safe_character_id, "")
    events = value.get("events", [])
    if isinstance(events, list):
        normalized_events = [_normalize_event(item) for item in events if isinstance(item, dict)]
        if normalized_events:
            normalized["events"] = normalized_events
    return normalized


def _control_records(session: Any) -> dict[str, Any]:
    store = getattr(session, "control_authority", None)
    if not isinstance(store, dict):
        return {}
    records = store.get("records", {})
    return dict(records) if isinstance(records, dict) else {}


def _normalize_record(record: dict[str, Any], character_id: str, owner_player_id: str) -> dict[str, Any]:
    normalized = deepcopy(record)
    normalized["character_id"] = str(normalized.get("character_id") or character_id)
    owner_id = str(normalized.get("owner_player_id") or owner_player_id or "").strip()
    normalized["owner_player_id"] = owner_id
    controller_id = str(normalized.get("active_controller_id") or "").strip()
    status = str(normalized.get("status") or CONTROL_STATUS_OWNED)
    controller_type = str(normalized.get("controller_type") or "")
    if not controller_id and status == CONTROL_STATUS_OWNED:
        controller_id = owner_id
    if not controller_type:
        controller_type = CONTROLLER_TYPE_OWNER if controller_id == owner_id else CONTROLLER_TYPE_PLAYER_DELEGATE
    normalized["active_controller_id"] = controller_id
    normalized["controller_type"] = _safe_controller_type(controller_type)
    normalized["status"] = _safe_status(status)
    normalized["risk_ceiling"] = _safe_risk(normalized.get("risk_ceiling"))
    normalized["duration_type"] = str(normalized.get("duration_type") or "until_revoked")
    for key in ("authorized_by", "consent_reference", "scope", "expires_at", "effective_at", "revoked_at", "audit_ref"):
        normalized[key] = str(normalized.get(key) or "")
    return normalized


def _safe_status(value: Any) -> str:
    text = str(value or "").strip()
    allowed = CONTROL_ACTIVE_STATUSES | {CONTROL_STATUS_REVOKED, CONTROL_STATUS_EXPIRED}
    return text if text in allowed else CONTROL_STATUS_OWNED


def _safe_controller_type(value: Any) -> str:
    text = str(value or "").strip()
    allowed = {
        CONTROLLER_TYPE_OWNER,
        CONTROLLER_TYPE_SYSTEM_HOST,
        CONTROLLER_TYPE_PLAYER_DELEGATE,
        CONTROLLER_TYPE_NONE,
    }
    return text if text in allowed else CONTROLLER_TYPE_OWNER


def _safe_risk(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in CONTROL_RISKS else CONTROL_RISK_LOW


def _normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    schema_version = event.get("schema_version", CONTROL_SCHEMA_VERSION)
    normalized: dict[str, Any] = {
        "schema_version": schema_version if isinstance(schema_version, int) else CONTROL_SCHEMA_VERSION,
    }
    for key in _CONTROL_EVENT_SAFE_FIELDS:
        text = str(event.get(key) or "").strip().replace("\r", " ").replace("\n", " ")
        normalized[key] = text[:_CONTROL_EVENT_FIELD_LIMIT]
    return normalized


def _success_reason(controller_type: str) -> str:
    if controller_type == CONTROLLER_TYPE_OWNER:
        return "owner_controls_character"
    if controller_type == CONTROLLER_TYPE_PLAYER_DELEGATE:
        return "delegate_controls_character"
    if controller_type == CONTROLLER_TYPE_SYSTEM_HOST:
        return "system_host_controls_character"
    return "active_controller_authorized"


def _deny(
    reason: str,
    character_id: str,
    owner_player_id: str,
    active_controller_id: str,
    controller_type: str,
    status: str,
    actor_player_id: str,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "error": "character_control_denied",
        "reason": reason,
        "character_id": character_id,
        "owner_player_id": owner_player_id,
        "active_controller_id": active_controller_id,
        "controller_type": controller_type,
        "status": status,
        "requester_player_id": actor_player_id,
    }
    if record:
        result["risk_ceiling"] = str(record.get("risk_ceiling") or CONTROL_RISK_LOW)
        result["duration_type"] = str(record.get("duration_type") or "until_revoked")
        result["expires_at"] = str(record.get("expires_at") or "")
        result["audit_ref"] = str(record.get("audit_ref") or "")
    return result


def _project_record(record: dict[str, Any], view: str) -> dict[str, Any]:
    projected = {
        "character_id": str(record.get("character_id") or ""),
        "owner_player_id": str(record.get("owner_player_id") or ""),
        "active_controller_id": str(record.get("active_controller_id") or ""),
        "controller_type": str(record.get("controller_type") or ""),
        "status": str(record.get("status") or ""),
        "risk_ceiling": str(record.get("risk_ceiling") or CONTROL_RISK_LOW),
        "duration_type": str(record.get("duration_type") or "until_revoked"),
        "expires_at": str(record.get("expires_at") or ""),
    }
    if view in {"ra_authority_view", "diagnostic_view"}:
        projected["authorized_by"] = str(record.get("authorized_by") or "")
        projected["audit_ref"] = str(record.get("audit_ref") or "")
        projected["effective_at"] = str(record.get("effective_at") or "")
        projected["revoked_at"] = str(record.get("revoked_at") or "")
    return projected
