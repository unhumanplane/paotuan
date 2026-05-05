"""Deterministic map delivery cadence policy.

This module owns when a player-facing map delivery is eligible to enqueue.
Renderer modules still own drawing, and chat integration still owns attachment
delivery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAP_DELIVERY_CADENCE_SCENE_KEY = "_map_delivery_cadence"
MAP_DELIVERY_CADENCE_SCHEMA_VERSION = 1
MAP_DELIVERY_SENT_LIMIT = 200

MAP_RENDER_STRICT_GRID = "strict_grid_svg"
MAP_RENDER_OVERVIEW_TOPOLOGY = "overview_topology_svg"
MAP_RENDER_LEGACY_LLM_SVG = "legacy_generate_map_svg"

MAP_DELIVERY_TRIGGER_PLAYER_REQUEST = "player_request"
MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION = "overview_transition"
MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_START = "strict_exploration_start"
MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_END = "strict_exploration_end"
MAP_DELIVERY_TRIGGER_COMBAT_START = "combat_start"
MAP_DELIVERY_TRIGGER_COMBAT_END = "combat_end"
MAP_DELIVERY_TRIGGER_AREA_DISCOVERY = "area_discovery"
MAP_DELIVERY_TRIGGER_COMBAT_ROUND = "combat_round"
MAP_DELIVERY_TRIGGER_SPATIAL_CHANGE = "ordinary_spatial_change"

DEFAULT_COMBAT_ROUND_INTERVAL = 5

_STRICT_TRIGGERS = {
    MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_START,
    MAP_DELIVERY_TRIGGER_STRICT_EXPLORATION_END,
    MAP_DELIVERY_TRIGGER_COMBAT_START,
    MAP_DELIVERY_TRIGGER_COMBAT_END,
    MAP_DELIVERY_TRIGGER_COMBAT_ROUND,
}
_OVERVIEW_TRIGGERS = {
    MAP_DELIVERY_TRIGGER_OVERVIEW_TRANSITION,
    MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
}
_SUPPORTED_TRIGGERS = {
    MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
    MAP_DELIVERY_TRIGGER_SPATIAL_CHANGE,
    *_STRICT_TRIGGERS,
    *_OVERVIEW_TRIGGERS,
}


@dataclass(frozen=True)
class MapDeliveryRequest:
    trigger: str
    render_type: str = ""
    map_id: str = ""
    map_revision: str = ""
    layout_revision: str = ""
    trigger_id: str = ""
    combat_id: str = ""
    round_number: int = 0
    renderer_available: bool = True
    legacy_fallback_allowed: bool = False
    combat_round_interval: int = DEFAULT_COMBAT_ROUND_INTERVAL


@dataclass(frozen=True)
class MapDeliveryDecision:
    should_send: bool
    reason: str
    trigger: str
    render_type: str = ""
    preferred_render_type: str = ""
    cadence_key: str = ""
    duplicate: bool = False
    legacy_fallback: bool = False


def default_map_delivery_cadence_state() -> dict[str, Any]:
    return {
        "schema_version": MAP_DELIVERY_CADENCE_SCHEMA_VERSION,
        "sent": {},
    }


def normalize_map_delivery_cadence_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return default_map_delivery_cadence_state()
    sent = value.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    normalized_sent: dict[str, dict[str, Any]] = {}
    for key, raw_record in sent.items():
        safe_key = _safe_text(key, 260)
        if not safe_key or not isinstance(raw_record, dict):
            continue
        normalized_sent[safe_key] = {
            "key": safe_key,
            "trigger": _safe_text(raw_record.get("trigger"), 80),
            "render_type": _safe_text(raw_record.get("render_type"), 80),
            "preferred_render_type": _safe_text(raw_record.get("preferred_render_type"), 80),
            "map_id": _safe_text(raw_record.get("map_id"), 160),
            "map_revision": _safe_text(raw_record.get("map_revision"), 80),
            "layout_revision": _safe_text(raw_record.get("layout_revision"), 80),
            "trigger_id": _safe_text(raw_record.get("trigger_id"), 160),
            "combat_id": _safe_text(raw_record.get("combat_id"), 160),
            "round_number": _safe_int(raw_record.get("round_number"), 0),
            "count": max(1, _safe_int(raw_record.get("count"), 1)),
            "legacy_fallback": bool(raw_record.get("legacy_fallback")),
        }
    return {
        "schema_version": MAP_DELIVERY_CADENCE_SCHEMA_VERSION,
        "sent": _trim_sent_records(normalized_sent),
    }


def get_map_delivery_cadence_state(scene: Any) -> dict[str, Any]:
    if not isinstance(scene, dict):
        return default_map_delivery_cadence_state()
    return normalize_map_delivery_cadence_state(scene.get(MAP_DELIVERY_CADENCE_SCENE_KEY))


def set_map_delivery_cadence_state(scene: dict[str, Any], state: Any) -> dict[str, Any]:
    normalized = normalize_map_delivery_cadence_state(state)
    scene[MAP_DELIVERY_CADENCE_SCENE_KEY] = normalized
    return normalized


def default_render_type_for_trigger(trigger: str) -> str:
    safe_trigger = _safe_text(trigger, 80)
    if safe_trigger in _STRICT_TRIGGERS:
        return MAP_RENDER_STRICT_GRID
    if safe_trigger in _OVERVIEW_TRIGGERS:
        return MAP_RENDER_OVERVIEW_TOPOLOGY
    return ""


def decide_map_delivery(state: Any, request: MapDeliveryRequest) -> MapDeliveryDecision:
    normalized = normalize_map_delivery_cadence_state(state)
    trigger = _safe_text(request.trigger, 80)
    if trigger not in _SUPPORTED_TRIGGERS:
        return _skip(request, "unsupported_trigger")
    if trigger == MAP_DELIVERY_TRIGGER_SPATIAL_CHANGE:
        return _skip(request, "ordinary_spatial_change_not_auto_sent")

    preferred_render_type = _safe_text(request.render_type, 80) or default_render_type_for_trigger(trigger)
    if not preferred_render_type:
        return _skip(request, "render_type_required")

    round_number = max(0, _safe_int(request.round_number, 0))
    if trigger == MAP_DELIVERY_TRIGGER_COMBAT_ROUND:
        interval = max(1, _safe_int(request.combat_round_interval, DEFAULT_COMBAT_ROUND_INTERVAL))
        if round_number <= 0:
            return _skip(request, "combat_round_required", preferred_render_type=preferred_render_type)
        if round_number % interval != 0:
            return _skip(request, "combat_round_cadence_not_due", preferred_render_type=preferred_render_type)

    effective_render_type = preferred_render_type
    legacy_fallback = False
    if not request.renderer_available:
        if not request.legacy_fallback_allowed:
            return _skip(request, "renderer_unavailable", preferred_render_type=preferred_render_type)
        effective_render_type = MAP_RENDER_LEGACY_LLM_SVG
        legacy_fallback = True

    cadence_key = build_map_delivery_cadence_key(
        request,
        effective_render_type,
        preferred_render_type=preferred_render_type,
    )
    already_sent = cadence_key in normalized["sent"]
    if already_sent and trigger != MAP_DELIVERY_TRIGGER_PLAYER_REQUEST:
        return MapDeliveryDecision(
            should_send=False,
            reason="duplicate_suppressed",
            trigger=trigger,
            render_type=effective_render_type,
            preferred_render_type=preferred_render_type,
            cadence_key=cadence_key,
            duplicate=True,
            legacy_fallback=legacy_fallback,
        )
    reason = "player_request_override" if already_sent else "eligible"
    return MapDeliveryDecision(
        should_send=True,
        reason=reason,
        trigger=trigger,
        render_type=effective_render_type,
        preferred_render_type=preferred_render_type,
        cadence_key=cadence_key,
        duplicate=already_sent,
        legacy_fallback=legacy_fallback,
    )


def record_map_delivery_sent(
    state: Any,
    request: MapDeliveryRequest,
    decision: MapDeliveryDecision,
) -> dict[str, Any]:
    normalized = normalize_map_delivery_cadence_state(state)
    if not decision.should_send or not decision.cadence_key:
        return normalized
    sent = dict(normalized["sent"])
    existing = sent.get(decision.cadence_key, {})
    count = max(0, _safe_int(existing.get("count"), 0)) + 1
    sent[decision.cadence_key] = {
        "key": decision.cadence_key,
        "trigger": decision.trigger,
        "render_type": decision.render_type,
        "preferred_render_type": decision.preferred_render_type,
        "map_id": _safe_text(request.map_id, 160),
        "map_revision": _safe_text(request.map_revision, 80),
        "layout_revision": _safe_text(request.layout_revision, 80),
        "trigger_id": _trigger_identity(request),
        "combat_id": _safe_text(request.combat_id, 160),
        "round_number": max(0, _safe_int(request.round_number, 0)),
        "count": count,
        "legacy_fallback": decision.legacy_fallback,
    }
    normalized["sent"] = _trim_sent_records(sent)
    return normalized


def build_map_delivery_cadence_key(
    request: MapDeliveryRequest,
    render_type: str,
    *,
    preferred_render_type: str = "",
) -> str:
    trigger = _safe_text(request.trigger, 80)
    trigger_id = _trigger_identity(request)
    round_number = max(0, _safe_int(request.round_number, 0))
    if trigger == MAP_DELIVERY_TRIGGER_COMBAT_ROUND and round_number:
        trigger_id = f"round:{round_number}"
    pieces = [
        _safe_text(render_type, 80) or "unknown_render",
        _safe_text(preferred_render_type, 80) or _safe_text(render_type, 80) or "unknown_preferred",
        trigger or "unknown_trigger",
        _safe_text(request.map_id, 160) or "current_map",
        _safe_text(request.map_revision, 80) or "no_map_revision",
        _safe_text(request.layout_revision, 80) or "no_layout_revision",
        _safe_text(request.combat_id, 160) or "no_combat",
        trigger_id or "no_trigger_id",
    ]
    return "|".join(pieces)


def _skip(
    request: MapDeliveryRequest,
    reason: str,
    *,
    preferred_render_type: str = "",
) -> MapDeliveryDecision:
    trigger = _safe_text(request.trigger, 80)
    preferred = preferred_render_type or _safe_text(request.render_type, 80) or default_render_type_for_trigger(trigger)
    return MapDeliveryDecision(
        should_send=False,
        reason=reason,
        trigger=trigger,
        preferred_render_type=preferred,
    )


def _trigger_identity(request: MapDeliveryRequest) -> str:
    explicit = _safe_text(request.trigger_id, 160)
    if explicit:
        return explicit
    if request.trigger == MAP_DELIVERY_TRIGGER_COMBAT_ROUND and request.round_number:
        return f"round:{max(0, _safe_int(request.round_number, 0))}"
    return ""


def _trim_sent_records(sent: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(sent) <= MAP_DELIVERY_SENT_LIMIT:
        return dict(sent)
    items = list(sent.items())[-MAP_DELIVERY_SENT_LIMIT:]
    return dict(items)


def _safe_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
