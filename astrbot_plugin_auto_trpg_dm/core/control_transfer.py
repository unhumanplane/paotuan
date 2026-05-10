from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .control_authority import (
    CONTROL_RISK_LOW,
    CONTROL_RISKS,
    CONTROL_SCHEMA_VERSION,
    CONTROL_STATUS_DELEGATED_TO_PLAYER,
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROL_STATUS_OWNED,
    CONTROLLER_TYPE_OWNER,
    CONTROLLER_TYPE_PLAYER_DELEGATE,
    CONTROLLER_TYPE_SYSTEM_HOST,
    character_id_for_entity,
    control_record_for_character,
    owner_player_id_for_entity,
)


CONTROL_ACTION_DELEGATE_TO_PLAYER = "delegate_to_player"
CONTROL_ACTION_RELINQUISH_TO_SYSTEM = "relinquish_to_system"
CONTROL_ACTION_RECLAIM = "reclaim"
CONTROL_ACTION_STATUS = "status"

CONTROL_ACTIONS = {
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
    CONTROL_ACTION_RECLAIM,
    CONTROL_ACTION_STATUS,
}
CONTROL_MUTATING_ACTIONS = {
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
    CONTROL_ACTION_RECLAIM,
}

CONTROL_DURATION_UNTIL_NEXT_TURN = "until_next_turn"
CONTROL_DURATION_UNTIL_COMBAT_END = "until_combat_end"
CONTROL_DURATION_UNTIL_SCENE_END = "until_scene_end"
CONTROL_DURATION_UNTIL_TIME = "until_time"
CONTROL_DURATION_UNTIL_REVOKED = "until_revoked"

CONTROL_DURATION_TYPES = {
    CONTROL_DURATION_UNTIL_NEXT_TURN,
    CONTROL_DURATION_UNTIL_COMBAT_END,
    CONTROL_DURATION_UNTIL_SCENE_END,
    CONTROL_DURATION_UNTIL_TIME,
    CONTROL_DURATION_UNTIL_REVOKED,
}

SYSTEM_HOST_CONTROLLER_ID = "__system__"

_TEXT_FIELD_LIMIT = 160


def apply_control_change(
    session: Any,
    action: str,
    character_id: str,
    *,
    actor: dict[str, Any] | None = None,
    requester_player_id: str = "",
    target_player_id: str = "",
    risk_ceiling: str = CONTROL_RISK_LOW,
    duration_type: str = CONTROL_DURATION_UNTIL_REVOKED,
    expires_at: str = "",
    consent_reference: str = "",
    audit_ref: str = "",
    effective_at: str = "",
) -> dict[str, Any]:
    """Apply one structured control-authority change to a session object."""
    safe_action = _clean(action)
    if safe_action not in CONTROL_ACTIONS:
        return _failure("invalid_control_action", action=safe_action)

    safe_character_id = _resolve_character_id(session, character_id)
    if not safe_character_id:
        return _failure("character_id_required", action=safe_action)

    owner_id = _owner_id(session, character_id, safe_character_id)
    record = control_record_for_character(session, safe_character_id, owner_player_id=owner_id)
    owner_id = _clean(record.get("owner_player_id") or owner_id)

    if safe_action == CONTROL_ACTION_STATUS:
        return _status_result(session, record, safe_action)

    requester_id = _requester_id(actor, requester_player_id)
    if not requester_id:
        return _failure(
            "requester_player_id_required",
            action=safe_action,
            character_id=safe_character_id,
            owner_player_id=owner_id,
        )
    if not owner_id:
        return _failure(
            "character_owner_required",
            action=safe_action,
            character_id=safe_character_id,
            requester_player_id=requester_id,
        )
    if requester_id != owner_id:
        return _failure(
            _authorization_error(record, requester_id),
            action=safe_action,
            character_id=safe_character_id,
            owner_player_id=owner_id,
            requester_player_id=requester_id,
            active_controller_id=_clean(record.get("active_controller_id")),
            controller_type=_clean(record.get("controller_type")),
        )

    safe_risk = _clean(risk_ceiling or CONTROL_RISK_LOW)
    if safe_risk not in CONTROL_RISKS:
        return _failure(
            "invalid_risk_ceiling",
            action=safe_action,
            character_id=safe_character_id,
            risk_ceiling=safe_risk,
            allowed_risk_ceilings=sorted(CONTROL_RISKS),
        )

    safe_duration = _clean(duration_type or CONTROL_DURATION_UNTIL_REVOKED)
    if safe_duration not in CONTROL_DURATION_TYPES:
        return _failure(
            "invalid_duration_type",
            action=safe_action,
            character_id=safe_character_id,
            duration_type=safe_duration,
            allowed_duration_types=sorted(CONTROL_DURATION_TYPES),
        )

    safe_expires_at = _bounded_text(expires_at) if safe_duration == CONTROL_DURATION_UNTIL_TIME else ""
    if safe_duration == CONTROL_DURATION_UNTIL_TIME and not safe_expires_at:
        return _failure(
            "expires_at_required_for_until_time",
            action=safe_action,
            character_id=safe_character_id,
            duration_type=safe_duration,
        )

    now = _bounded_text(effective_at) or _utc_now_iso()
    if safe_action == CONTROL_ACTION_DELEGATE_TO_PLAYER:
        target_id = _clean(target_player_id)
        if not target_id:
            return _failure(
                "target_player_id_required",
                action=safe_action,
                character_id=safe_character_id,
                owner_player_id=owner_id,
            )
        new_record = _changed_record(
            record,
            active_controller_id=target_id,
            controller_type=CONTROLLER_TYPE_PLAYER_DELEGATE,
            status=CONTROL_STATUS_DELEGATED_TO_PLAYER,
            owner_player_id=owner_id,
            authorized_by=owner_id,
            risk_ceiling=safe_risk,
            duration_type=safe_duration,
            expires_at=safe_expires_at,
            effective_at=now,
            revoked_at="",
            consent_reference=consent_reference,
        )
    elif safe_action == CONTROL_ACTION_RELINQUISH_TO_SYSTEM:
        new_record = _changed_record(
            record,
            active_controller_id=SYSTEM_HOST_CONTROLLER_ID,
            controller_type=CONTROLLER_TYPE_SYSTEM_HOST,
            status=CONTROL_STATUS_HOSTED_BY_SYSTEM,
            owner_player_id=owner_id,
            authorized_by=owner_id,
            risk_ceiling=safe_risk,
            duration_type=safe_duration,
            expires_at=safe_expires_at,
            effective_at=now,
            revoked_at="",
            consent_reference=consent_reference,
        )
    else:
        new_record = _changed_record(
            record,
            active_controller_id=owner_id,
            controller_type=CONTROLLER_TYPE_OWNER,
            status=CONTROL_STATUS_OWNED,
            owner_player_id=owner_id,
            authorized_by=owner_id,
            risk_ceiling=safe_risk,
            duration_type=safe_duration,
            expires_at=safe_expires_at,
            effective_at=now,
            revoked_at=now,
            consent_reference=consent_reference,
        )

    store, records, events = _ensure_control_store(session)
    event_ref = _bounded_text(audit_ref) or f"control_authority_event_{len(events) + 1}"
    new_record["audit_ref"] = event_ref
    records[safe_character_id] = new_record
    event = _event_record(
        action=safe_action,
        record=new_record,
        previous_record=record,
        requester_player_id=requester_id,
        effective_at=now,
        audit_ref=event_ref,
    )
    events.append(event)
    store["schema_version"] = CONTROL_SCHEMA_VERSION
    return _success_result(new_record, safe_action, len(events), event)


def _resolve_character_id(session: Any, character_id: str) -> str:
    requested = _clean(character_id)
    if not requested:
        return ""
    return _clean(character_id_for_entity(session, requested) or requested)


def _owner_id(session: Any, requested_character_id: str, safe_character_id: str) -> str:
    owner_id = owner_player_id_for_entity(session, requested_character_id)
    if owner_id:
        return _clean(owner_id)
    return _clean(owner_player_id_for_entity(session, safe_character_id))


def _requester_id(actor: dict[str, Any] | None, requester_player_id: str) -> str:
    explicit = _clean(requester_player_id)
    if explicit:
        return explicit
    return _clean((actor or {}).get("player_id"))


def _authorization_error(record: dict[str, Any], requester_player_id: str) -> str:
    if (
        _clean(record.get("controller_type")) == CONTROLLER_TYPE_PLAYER_DELEGATE
        and _clean(record.get("active_controller_id")) == requester_player_id
    ):
        return "delegate_cannot_redelegate"
    return "requester_is_not_owner"


def _changed_record(
    record: dict[str, Any],
    *,
    active_controller_id: str,
    controller_type: str,
    status: str,
    owner_player_id: str,
    authorized_by: str,
    risk_ceiling: str,
    duration_type: str,
    expires_at: str,
    effective_at: str,
    revoked_at: str,
    consent_reference: str,
) -> dict[str, Any]:
    changed = deepcopy(record)
    changed.update(
        {
            "character_id": _clean(record.get("character_id")),
            "owner_player_id": owner_player_id,
            "active_controller_id": _clean(active_controller_id),
            "controller_type": controller_type,
            "status": status,
            "authorized_by": _clean(authorized_by),
            "consent_reference": _bounded_text(consent_reference),
            "risk_ceiling": risk_ceiling,
            "scope": _clean(record.get("scope")) or "character",
            "duration_type": duration_type,
            "expires_at": expires_at,
            "effective_at": effective_at,
            "revoked_at": revoked_at,
        }
    )
    return changed


def _ensure_control_store(session: Any) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    store = getattr(session, "control_authority", None)
    if not isinstance(store, dict):
        store = {}
        setattr(session, "control_authority", store)
    records = store.get("records")
    if not isinstance(records, dict):
        records = {}
        store["records"] = records
    events = store.get("events")
    if not isinstance(events, list):
        events = []
        store["events"] = events
    store.setdefault("schema_version", CONTROL_SCHEMA_VERSION)
    return store, records, events


def _read_events(session: Any) -> list[dict[str, Any]]:
    store = getattr(session, "control_authority", None)
    if not isinstance(store, dict):
        return []
    events = store.get("events")
    if not isinstance(events, list):
        return []
    return [dict(item) for item in events if isinstance(item, dict)]


def _event_record(
    *,
    action: str,
    record: dict[str, Any],
    previous_record: dict[str, Any],
    requester_player_id: str,
    effective_at: str,
    audit_ref: str,
) -> dict[str, Any]:
    return {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "type": "control_authority_change",
        "action": action,
        "character_id": _clean(record.get("character_id")),
        "owner_player_id": _clean(record.get("owner_player_id")),
        "requester_player_id": _clean(requester_player_id),
        "active_controller_id": _clean(record.get("active_controller_id")),
        "controller_type": _clean(record.get("controller_type")),
        "status": _clean(record.get("status")),
        "previous_active_controller_id": _clean(previous_record.get("active_controller_id")),
        "previous_controller_type": _clean(previous_record.get("controller_type")),
        "previous_status": _clean(previous_record.get("status")),
        "risk_ceiling": _clean(record.get("risk_ceiling")),
        "duration_type": _clean(record.get("duration_type")),
        "expires_at": _clean(record.get("expires_at")),
        "effective_at": _clean(effective_at),
        "audit_ref": _bounded_text(audit_ref),
    }


def _status_result(session: Any, record: dict[str, Any], action: str) -> dict[str, Any]:
    result = _safe_record_fields(record)
    result.update(
        {
            "ok": True,
            "action": action,
            "read_only": True,
            "event_count": len(_read_events(session)),
        }
    )
    return result


def _success_result(
    record: dict[str, Any],
    action: str,
    event_count: int,
    event: dict[str, Any],
) -> dict[str, Any]:
    result = _safe_record_fields(record)
    result.update(
        {
            "ok": True,
            "action": action,
            "event_count": event_count,
            "event": event,
        }
    )
    return result


def _safe_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "character_id": _clean(record.get("character_id")),
        "owner_player_id": _clean(record.get("owner_player_id")),
        "active_controller_id": _clean(record.get("active_controller_id")),
        "controller_type": _clean(record.get("controller_type")),
        "status": _clean(record.get("status")),
        "risk_ceiling": _clean(record.get("risk_ceiling")) or CONTROL_RISK_LOW,
        "duration_type": _clean(record.get("duration_type")) or CONTROL_DURATION_UNTIL_REVOKED,
        "expires_at": _clean(record.get("expires_at")),
        "audit_ref": _clean(record.get("audit_ref")),
    }


def _failure(error: str, **fields: Any) -> dict[str, Any]:
    result = {"ok": False, "error": error}
    for key, value in fields.items():
        if isinstance(value, list):
            result[key] = [str(item) for item in value]
        else:
            result[key] = _clean(value)
    return result


def _bounded_text(value: Any, limit: int = _TEXT_FIELD_LIMIT) -> str:
    text = _clean(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
