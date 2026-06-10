from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from ..rendering import build_strict_grid_render_input, render_strict_grid_svg
from .map_delivery_cadence import (
    MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
    MAP_RENDER_STRICT_GRID,
    MapDeliveryRequest,
    enqueue_map_pending_output,
)
from .map_core import MAP_VIEW_PLAYER
from .models import GameSession, utc_now_iso
from .scene_hooks import project_visible_scene_value


STORY_FORGE_ARCHIVE_KEY = "_story_forge_archive"
STORY_FORGE_BRIEF_KEY = "story_forge_player_brief"
STORY_FORGE_SCHEMA_VERSION = 1

PLAYER_SAFE_PROJECTION = "player_view"
PLAYER_SAFE_VISIBILITIES = {"", "public", "player", "observed", "confirmed", "observed_or_confirmed"}
HIDDEN_STATUSES = {"hidden", "secret", "undiscovered"}
HIDDEN_VISIBILITIES = {"hidden", "secret", "private", "dm", "dm_only", "gm", "gm_only", "internal", "diagnostic"}
BLOCKED_HIDDEN_KEYS = {
    "backstage",
    "culprit",
    "dm_notes",
    "gm_notes",
    "hidden_clues",
    "hidden_locations",
    "hidden_truth",
    "mastermind",
    "plot_truth",
    "secret",
    "secret_clues",
    "secrets",
    "spoiler",
    "truth",
    "true_allegiance",
    "true_motive",
}
BLOCKED_HIDDEN_KEY_TOKENS = ("hidden_", "_hidden", "secret_", "_secret", "dm_only", "gm_only")


@dataclass(frozen=True)
class StoryForgeRuntimeConfig:
    enabled: bool = True
    archive_enabled: bool = True
    map_seed_render_enabled: bool = True
    render_send_to_chat: bool = True
    max_turns: int = 80
    max_turn_text_chars: int = 12000
    max_open_threads: int = 24
    max_clue_ledger: int = 48
    max_convergence_actions: int = 6
    max_encounter_contracts: int = 12


def apply_story_forge_turn(
    repository: Any,
    session_id: str,
    *,
    actor: dict[str, str] | None = None,
    player_message: str = "",
    dm_response: str = "",
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled or not runtime_config.archive_enabled:
        return {"ok": True, "skipped": True, "reason": "story_forge_runtime_disabled"}
    session = repository.load_session(session_id)
    archive = archive_story_forge_turn(
        session,
        actor=actor or {},
        player_message=player_message,
        dm_response=dm_response,
        config=runtime_config,
    )
    render_results = render_unrendered_story_forge_maps(
        session,
        repository,
        session_id,
        config=runtime_config,
    )
    repository.save_session(session)
    result = {
        "ok": True,
        "turns": len(archive.get("turns") or []),
        "open_threads": len(archive.get("open_threads") or []),
        "clue_ledger": len(archive.get("clue_ledger") or []),
        "convergence_actions": len(archive.get("convergence_actions") or []),
        "rendered_maps": len(archive.get("rendered_map_refs") or []),
        "render_attempts": len(render_results),
        "diagnostics": story_forge_runtime_diagnostics(session),
    }
    _append_story_forge_audit(repository, session_id, "story_forge_turn_archived", result)
    return result


def story_forge_runtime_diagnostics(session: GameSession | dict[str, Any]) -> dict[str, Any]:
    scene = session.get("scene", {}) if isinstance(session, dict) else getattr(session, "scene", {})
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY) if isinstance(scene, dict) else {})
    if isinstance(scene, dict):
        _bootstrap_scene_pressure_clock(scene, archive, now=_safe_text(archive.get("updated_at"), 80) or utc_now_iso())
    actions = _list_of_dicts(archive.get("convergence_actions"))
    action_with_map_seed = [action for action in actions if isinstance(action.get("map_grid_seed"), dict)]
    action_with_render = [action for action in actions if isinstance(action.get("rendered_map_ref"), dict)]
    rendered_refs = _list_of_dicts(archive.get("rendered_map_refs"))
    thread_progress = _list_of_dicts(archive.get("thread_progress"))
    open_threads = _list_of_dicts(archive.get("open_threads"))
    clue_ledger = _list_of_dicts(archive.get("clue_ledger"))
    pressure_clocks = _list_of_dicts(archive.get("pressure_clocks"))
    encounter_contracts = _list_of_dicts(archive.get("encounter_contracts"))
    active_pressure_clocks = [
        clock for clock in pressure_clocks if str(clock.get("status") or "active").lower() == "active"
    ]
    clock_events = _list_of_dicts(archive.get("clock_events"))
    return {
        "enabled_archive_present": bool(archive.get("turns") or open_threads or clue_ledger or actions),
        "turn_count": len(_list_of_dicts(archive.get("turns"))),
        "open_thread_count": len(open_threads),
        "thread_progress_count": len(thread_progress),
        "clue_ledger_count": len(clue_ledger),
        "convergence_action_count": len(actions),
        "scene_goal_card_count": sum(1 for action in actions if _has_scene_goal_fields(action)),
        "map_seed_action_count": len(action_with_map_seed),
        "rendered_action_count": len(action_with_render),
        "rendered_map_ref_count": len(rendered_refs),
        "pressure_clock_count": len(pressure_clocks),
        "active_pressure_clock_count": len(active_pressure_clocks),
        "visible_pressure_clock_count": sum(1 for clock in pressure_clocks if _clock_player_visible(clock)),
        "clock_event_count": len(clock_events),
        "encounter_contract_count": len(encounter_contracts),
        "latest_encounter_decision": _safe_text(
            (encounter_contracts[-1] if encounter_contracts else {}).get("encounter_decision"),
            80,
        ),
        "turns_since_last_clock_event": _turns_since_last_clock_event(archive),
        "map_seed_to_svg_closed": bool(action_with_map_seed) and len(action_with_render) >= len(action_with_map_seed),
        "needs_scene_goal_cards": len(actions) == 0,
        "needs_thread_progress": bool(open_threads) and not thread_progress and not actions,
        "needs_pressure": bool(actions) and not active_pressure_clocks,
        "updated_at": _safe_text(archive.get("updated_at"), 80),
    }


def archive_story_forge_turn(
    session: GameSession,
    *,
    actor: dict[str, str] | None = None,
    player_message: str = "",
    dm_response: str = "",
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    now = utc_now_iso()
    _bootstrap_scene_pressure_clock(scene, archive, actor=actor or {}, now=now)
    turn_id = _turn_id(session.session_id, player_message, dm_response, now)
    visible_tracking = _visible_tracking_state(scene)
    turn = {
        "turn_id": turn_id,
        "created_at": now,
        "actor": _safe_actor(actor or {}),
        "player_message": _safe_text(player_message, runtime_config.max_turn_text_chars),
        "dm_response": _safe_text(dm_response, runtime_config.max_turn_text_chars),
        "player_message_hash": _short_hash(player_message),
        "dm_response_hash": _short_hash(dm_response),
        "observable_state": visible_tracking,
    }
    turns = [item for item in archive.get("turns") or [] if isinstance(item, dict)]
    turns.append(turn)
    archive["turns"] = turns[-max(1, runtime_config.max_turns) :]
    archive["open_threads"] = _merge_open_threads(
        archive.get("open_threads"),
        visible_tracking,
        limit=runtime_config.max_open_threads,
    )
    archive["clue_ledger"] = _merge_clue_ledger(
        archive.get("clue_ledger"),
        visible_tracking,
        limit=runtime_config.max_clue_ledger,
    )
    archive["updated_at"] = now
    scene[STORY_FORGE_ARCHIVE_KEY] = archive
    _write_story_forge_player_brief(scene, archive, config=runtime_config)
    return archive


def normalize_story_forge_archive(value: Any) -> dict[str, Any]:
    archive = dict(value) if isinstance(value, dict) else {}
    return {
        "schema_version": STORY_FORGE_SCHEMA_VERSION,
        "turns": _list_of_dicts(archive.get("turns"))[-500:],
        "open_threads": _list_of_dicts(archive.get("open_threads"))[-200:],
        "thread_progress": _list_of_dicts(archive.get("thread_progress"))[-300:],
        "clue_ledger": _list_of_dicts(archive.get("clue_ledger"))[-300:],
        "convergence_actions": _list_of_dicts(archive.get("convergence_actions"))[-120:],
        "encounter_contracts": _normalize_encounter_contracts(archive.get("encounter_contracts")),
        "pressure_clocks": _normalize_pressure_clocks(archive.get("pressure_clocks")),
        "clock_events": _normalize_clock_events(archive.get("clock_events")),
        "rendered_map_refs": _list_of_dicts(archive.get("rendered_map_refs"))[-120:],
        "updated_at": _safe_text(archive.get("updated_at"), 80),
    }


def normalize_convergence_action(
    payload: dict[str, Any],
    *,
    actor: dict[str, str] | None = None,
    now: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("story_forge_action_payload_not_object")
    blocked = _blocked_hidden_paths(payload, ignore_keys={"map_grid_seed", "grid", "cells", "entities", "doors", "hazards", "obstacles", "labels"})
    if blocked:
        raise ValueError(f"hidden_story_forge_fields_not_allowed:{','.join(blocked[:4])}")
    merged = dict(payload)
    scene_goal_payload = merged.get("scene_goal")
    if isinstance(scene_goal_payload, dict):
        for key in ("entry_cost", "success_signal", "failure_forward", "map_grid_seed"):
            if merged.get(key) in (None, "", [], {}) and scene_goal_payload.get(key) not in (None, "", [], {}):
                merged[key] = scene_goal_payload.get(key)
        merged["scene_goal"] = _first_text(
            scene_goal_payload.get("scene_goal"),
            scene_goal_payload.get("goal"),
            scene_goal_payload.get("objective"),
            scene_goal_payload.get("title"),
            scene_goal_payload.get("summary"),
            scene_goal_payload.get("description"),
        )
    scene_goal = _safe_text(_first_text(merged.get("scene_goal"), merged.get("available_action")), 500)
    if not scene_goal:
        raise ValueError("story_forge_scene_goal_required")
    action_id = _safe_id(
        merged.get("action_id")
        or merged.get("id")
        or _stable_hash(
            {
                "thread_id": merged.get("thread_id"),
                "scene_goal": scene_goal,
                "entry_cost": merged.get("entry_cost"),
                "success_signal": merged.get("success_signal"),
            }
        )
    )
    map_grid_seed = _sanitize_player_visible_value(merged.get("map_grid_seed"))
    if not isinstance(map_grid_seed, dict):
        map_grid_seed = {}
    action = {
        "action_id": action_id,
        "thread_id": _safe_text(merged.get("thread_id"), 160),
        "action_type": _safe_text(merged.get("action_type") or "next_scene", 80),
        "available_action": _safe_text(merged.get("available_action"), 360),
        "scene_goal": scene_goal,
        "entry_cost": _safe_text(merged.get("entry_cost"), 320),
        "success_signal": _safe_text(merged.get("success_signal"), 320),
        "failure_forward": _safe_text(merged.get("failure_forward"), 360),
        "evidence": _safe_text_list(merged.get("evidence"), limit=8, item_limit=240),
        "visibility": "player",
        "created_at": _safe_text(now or utc_now_iso(), 80),
        "created_by": _safe_actor(actor or {}),
    }
    if map_grid_seed:
        action["map_grid_seed"] = map_grid_seed
    return {key: value for key, value in action.items() if value not in (None, "", [], {})}


def record_story_forge_convergence(
    repository: Any,
    session_id: str,
    *,
    actor: dict[str, str] | None = None,
    payload: dict[str, Any],
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled:
        return {"ok": False, "error": "story_forge_runtime_disabled"}
    session = repository.load_session(session_id)
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    try:
        action = normalize_convergence_action(payload, actor=actor or {}, now=utc_now_iso())
    except ValueError as exc:
        result = {"ok": False, "error": "story_forge_convergence_rejected", "reason": str(exc)}
        _append_story_forge_audit(repository, session_id, "record_story_forge_convergence", result)
        return result
    merged_action, replaced = _merge_convergence_action(
        archive,
        action,
        limit=runtime_config.max_convergence_actions,
    )
    render_result = {}
    if runtime_config.map_seed_render_enabled and merged_action.get("map_grid_seed"):
        render_result = render_story_forge_map_seed(
            session,
            repository,
            session_id,
            merged_action,
            send_to_chat=runtime_config.render_send_to_chat and bool(payload.get("send_to_chat", True)),
        )
        if render_result.get("ok"):
            _attach_render_ref_to_action(merged_action, render_result)
            _upsert_render_ref(archive, render_result)
    archive["updated_at"] = utc_now_iso()
    scene[STORY_FORGE_ARCHIVE_KEY] = archive
    _write_story_forge_player_brief(scene, archive, config=runtime_config)
    repository.save_session(session)
    result = {
        "ok": True,
        "action_id": merged_action.get("action_id", ""),
        "replaced": replaced,
        "scene_goal": merged_action.get("scene_goal", ""),
        "has_map_grid_seed": bool(merged_action.get("map_grid_seed")),
        "rendered_map": _public_render_result(render_result),
        "convergence_actions": len(archive.get("convergence_actions") or []),
        "message": "Story Forge convergence action recorded as a player-safe scene goal card.",
    }
    _append_story_forge_audit(repository, session_id, "record_story_forge_convergence", result)
    return result


def normalize_encounter_contract(
    payload: dict[str, Any],
    *,
    actor: dict[str, str] | None = None,
    now: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("encounter_contract_payload_not_object")
    blocked = _blocked_hidden_paths(payload)
    if blocked:
        raise ValueError(f"hidden_encounter_contract_fields_not_allowed:{','.join(blocked[:4])}")
    decision = _normalize_choice(
        payload.get("encounter_decision") or payload.get("decision"),
        allowed={
            "free_narrative",
            "single_check",
            "pressure_scene",
            "soft_turns",
            "strict_turns",
            "strict_grid",
        },
        default="single_check",
    )
    scene_goal = _safe_text(
        _first_text(payload.get("scene_goal"), payload.get("goal"), payload.get("objective")),
        500,
    )
    reason = _safe_text(payload.get("reason"), 500)
    stakes = _safe_text(payload.get("stakes"), 500)
    if not scene_goal:
        raise ValueError("encounter_contract_scene_goal_required")
    if not reason:
        raise ValueError("encounter_contract_reason_required")
    if decision in {"pressure_scene", "soft_turns", "strict_turns", "strict_grid"} and not stakes:
        raise ValueError("encounter_contract_stakes_required")

    action_economy = _normalize_choice(
        payload.get("action_economy"),
        allowed={"none", "one_actor_focus", "side_based", "strict_order"},
        default=_default_action_economy(decision),
    )
    map_need = _normalize_choice(
        payload.get("map_need"),
        allowed={"none", "sketch", "strict_grid"},
        default="strict_grid" if decision == "strict_grid" else "none",
    )
    turn_order_source = _normalize_choice(
        payload.get("turn_order_source"),
        allowed={"none", "derived_scene", "derived_battle_state", "rule_initiative", "existing_state"},
        default="none",
    )
    recommended_next_tool = _normalize_choice(
        payload.get("recommended_next_tool"),
        allowed={
            "none",
            "resolve_check",
            "execute_rule",
            "turn_control",
            "create_strict_map",
            "start_combat_on_map",
            "record_story_forge_pressure_clock",
            "advance_story_forge_pressure_clock",
            "update_scene",
            "final_response",
        },
        default=_default_encounter_next_tool(decision),
    )
    participants = _safe_text_list(payload.get("participants"), limit=16, item_limit=120)
    pressure_vectors = [
        vector
        for vector in (
            _normalize_choice(
                item,
                allowed={"time", "resource", "relation", "space", "moral", "information", "danger"},
                default="",
            )
            for item in _safe_text_list(payload.get("pressure_vectors"), limit=8, item_limit=80)
        )
        if vector
    ]
    evidence = _safe_text_list(payload.get("evidence"), limit=10, item_limit=240)
    if decision in {"soft_turns", "strict_turns", "strict_grid"}:
        if action_economy == "none":
            raise ValueError("encounter_contract_action_economy_required")
        if recommended_next_tool not in {"turn_control", "create_strict_map", "start_combat_on_map", "resolve_check", "execute_rule"}:
            raise ValueError("encounter_contract_turn_tool_required")
    if decision in {"strict_turns", "strict_grid"}:
        if action_economy != "strict_order":
            raise ValueError("encounter_contract_strict_requires_strict_order")
        if turn_order_source not in {"derived_battle_state", "rule_initiative", "existing_state"}:
            raise ValueError("encounter_contract_strict_requires_order_source")
    if decision == "strict_grid" and map_need != "strict_grid":
        raise ValueError("encounter_contract_strict_grid_requires_map")

    created_at = _safe_text(payload.get("created_at") or now or utc_now_iso(), 80)
    updated_at = _safe_text(now or payload.get("updated_at") or created_at, 80)
    created_by = _safe_actor(actor or {}) or (
        _safe_actor(payload.get("created_by")) if isinstance(payload.get("created_by"), dict) else {}
    )
    contract_id = _safe_id(
        payload.get("contract_id")
        or payload.get("id")
        or "enc:" + _stable_hash(
            {
                "decision": decision,
                "scene_goal": scene_goal,
                "participants": participants,
                "created_at": created_at,
            }
        )[:16]
    )
    contract = {
        "contract_id": contract_id,
        "created_at": created_at,
        "updated_at": updated_at,
        "status": _normalize_choice(
            payload.get("status"),
            allowed={"active", "superseded", "resolved", "cancelled"},
            default="active",
        ),
        "encounter_decision": decision,
        "reason": reason,
        "scene_goal": scene_goal,
        "stakes": stakes,
        "participants": participants,
        "pressure_vectors": pressure_vectors,
        "action_economy": action_economy,
        "map_need": map_need,
        "turn_order_source": turn_order_source,
        "recommended_next_tool": recommended_next_tool,
        "player_visible_brief": _safe_text(payload.get("player_visible_brief"), 360),
        "evidence": evidence,
        "created_by": created_by,
    }
    return {key: value for key, value in contract.items() if value not in (None, "", [], {})}


def record_story_forge_encounter_contract(
    repository: Any,
    session_id: str,
    *,
    actor: dict[str, str] | None = None,
    payload: dict[str, Any],
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled:
        return {"ok": False, "error": "story_forge_runtime_disabled"}
    session = repository.load_session(session_id)
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    try:
        contract = normalize_encounter_contract(payload, actor=actor or {}, now=utc_now_iso())
    except ValueError as exc:
        result = {"ok": False, "error": "story_forge_encounter_contract_rejected", "reason": str(exc)}
        _append_story_forge_audit(repository, session_id, "record_story_forge_encounter_contract", result)
        return result

    contracts = _list_of_dicts(archive.get("encounter_contracts"))
    replaced = False
    merged: list[dict[str, Any]] = []
    for existing in contracts:
        if str(existing.get("contract_id") or "") == str(contract.get("contract_id") or ""):
            merged_contract = {
                **existing,
                **contract,
                "created_at": existing.get("created_at") or contract.get("created_at"),
                "updated_at": contract.get("updated_at"),
            }
            merged.append(merged_contract)
            contract = merged_contract
            replaced = True
        else:
            merged.append(existing)
    if not replaced:
        merged.append(contract)
    archive["encounter_contracts"] = merged[-max(1, runtime_config.max_encounter_contracts) :]
    archive["updated_at"] = utc_now_iso()
    scene[STORY_FORGE_ARCHIVE_KEY] = archive
    _write_story_forge_player_brief(scene, archive, config=runtime_config)
    repository.save_session(session)
    result = {
        "ok": True,
        "contract_id": contract.get("contract_id", ""),
        "encounter_decision": contract.get("encounter_decision", ""),
        "action_economy": contract.get("action_economy", ""),
        "map_need": contract.get("map_need", ""),
        "recommended_next_tool": contract.get("recommended_next_tool", ""),
        "replaced": replaced,
        "message": "Story Forge encounter contract recorded.",
    }
    _append_story_forge_audit(repository, session_id, "record_story_forge_encounter_contract", result)
    return result


def record_story_forge_pressure_clock(
    repository: Any,
    session_id: str,
    *,
    actor: dict[str, str] | None = None,
    clock: dict[str, Any],
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled:
        return {"ok": False, "error": "story_forge_runtime_disabled"}
    session = repository.load_session(session_id)
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    try:
        normalized = normalize_pressure_clock(clock, actor=actor or {}, now=utc_now_iso())
    except ValueError as exc:
        result = {"ok": False, "error": "story_forge_pressure_clock_rejected", "reason": str(exc)}
        _append_story_forge_audit(repository, session_id, "record_story_forge_pressure_clock", result)
        return result

    clocks = _list_of_dicts(archive.get("pressure_clocks"))
    replaced = False
    merged: list[dict[str, Any]] = []
    for existing in clocks:
        if str(existing.get("clock_id") or "") == str(normalized.get("clock_id") or ""):
            merged_clock = {
                **existing,
                **normalized,
                "created_at": existing.get("created_at") or normalized.get("created_at"),
                "updated_at": normalized.get("updated_at"),
            }
            merged.append(merged_clock)
            normalized = merged_clock
            replaced = True
        else:
            merged.append(existing)
    if not replaced:
        merged.append(normalized)
    archive["pressure_clocks"] = merged[-60:]
    archive["updated_at"] = utc_now_iso()
    scene[STORY_FORGE_ARCHIVE_KEY] = archive
    _write_story_forge_player_brief(scene, archive, config=runtime_config)
    repository.save_session(session)
    result = {
        "ok": True,
        "clock_id": normalized.get("clock_id", ""),
        "label": normalized.get("label", ""),
        "value": normalized.get("value", 0),
        "max": normalized.get("max", 0),
        "replaced": replaced,
        "message": "Story Forge pressure clock recorded.",
    }
    _append_story_forge_audit(repository, session_id, "record_story_forge_pressure_clock", result)
    return result


def advance_story_forge_pressure_clock(
    repository: Any,
    session_id: str,
    *,
    actor: dict[str, str] | None = None,
    clock_id: str,
    delta: int = 1,
    trigger: str = "",
    cause: str = "",
    visible_effect: str = "",
    config: StoryForgeRuntimeConfig | None = None,
) -> dict[str, Any]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled:
        return {"ok": False, "error": "story_forge_runtime_disabled"}
    requested_clock_id = _safe_text(clock_id, 240)
    safe_clock_id = _safe_clock_id(clock_id)
    if not safe_clock_id:
        return {"ok": False, "error": "story_forge_clock_id_required"}
    if _safe_text(visible_effect, 500) == "":
        return {"ok": False, "error": "story_forge_clock_visible_effect_required"}
    if _safe_text(trigger, 160) == "" or _safe_text(cause, 500) == "":
        return {"ok": False, "error": "story_forge_clock_trigger_cause_required"}
    if int(delta or 0) == 0:
        return {"ok": False, "error": "story_forge_clock_delta_required"}

    session = repository.load_session(session_id)
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    clocks = _list_of_dicts(archive.get("pressure_clocks"))
    clock_index = _find_pressure_clock_index(clocks, safe_clock_id)
    fallback_clock_id = ""
    if clock_index < 0:
        bootstrapped = _bootstrap_scene_pressure_clock(scene, archive, actor=actor or {}, now=utc_now_iso())
        clocks = _list_of_dicts(archive.get("pressure_clocks"))
        clock_index = _find_pressure_clock_index(clocks, safe_clock_id)
        visible_active_clocks = [clock for clock in _active_pressure_clocks(clocks) if _clock_player_visible(clock)]
        if clock_index < 0 and (bootstrapped or len(visible_active_clocks) == 1) and len(visible_active_clocks) == 1:
            fallback_clock = visible_active_clocks[0]
            fallback_clock_id = str(fallback_clock.get("clock_id") or "")
            clock_index = _find_pressure_clock_index(clocks, fallback_clock_id)
        if clock_index < 0:
            return {
                "ok": False,
                "error": "story_forge_pressure_clock_not_found",
                "clock_id": safe_clock_id,
                "requested_clock_id": requested_clock_id,
            }

    clock = dict(clocks[clock_index])
    actual_clock_id = str(clock.get("clock_id") or safe_clock_id)
    old_value = _safe_int(clock.get("value"), 0)
    max_value = max(1, _safe_int(clock.get("max"), 4))
    new_value = max(0, min(max_value, old_value + int(delta)))
    clock["value"] = new_value
    clock["max"] = max_value
    clock["updated_at"] = utc_now_iso()
    completed = old_value < max_value and new_value >= max_value
    if completed:
        completion = clock.get("on_complete") if isinstance(clock.get("on_complete"), dict) else {}
        clock["status"] = "completed"
        clock["completed_at"] = clock["updated_at"]
    else:
        completion = {}
    event = normalize_clock_event(
        {
            "clock_id": actual_clock_id,
            "clock_type": "pressure",
            "delta": int(delta),
            "old_value": old_value,
            "new_value": new_value,
            "max": max_value,
            "trigger": trigger,
            "cause": cause,
            "visible_effect": visible_effect,
            "completed": completed,
            "turn_index": len(_list_of_dicts(archive.get("turns"))),
            "player_visible": _clock_player_visible(clock),
            "actor": _safe_actor(actor or {}),
        },
        now=clock["updated_at"],
    )
    clocks[clock_index] = clock
    events = _list_of_dicts(archive.get("clock_events"))
    events.append(event)
    archive["pressure_clocks"] = clocks[-60:]
    archive["clock_events"] = events[-200:]
    archive["updated_at"] = clock["updated_at"]
    scene[STORY_FORGE_ARCHIVE_KEY] = archive
    _write_story_forge_player_brief(scene, archive, config=runtime_config)
    repository.save_session(session)
    result = {
        "ok": True,
        "clock_id": actual_clock_id,
        "old_value": old_value,
        "new_value": new_value,
        "max": max_value,
        "delta": int(delta),
        "completed": completed,
        "event_id": event.get("event_id", ""),
        "visible_effect": event.get("visible_effect", ""),
    }
    if completed:
        result["completion"] = _project_clock_completion(completion)
    if fallback_clock_id and fallback_clock_id != safe_clock_id:
        result["requested_clock_id"] = requested_clock_id or safe_clock_id
        result["normalized_requested_clock_id"] = safe_clock_id
        result["clock_id_fallback"] = True
    _append_story_forge_audit(repository, session_id, "advance_story_forge_pressure_clock", result)
    return result


def render_unrendered_story_forge_maps(
    session: GameSession,
    repository: Any,
    session_id: str,
    *,
    config: StoryForgeRuntimeConfig | None = None,
) -> list[dict[str, Any]]:
    runtime_config = config or StoryForgeRuntimeConfig()
    if not runtime_config.enabled or not runtime_config.map_seed_render_enabled:
        return []
    scene = _ensure_scene(session)
    archive = normalize_story_forge_archive(scene.get(STORY_FORGE_ARCHIVE_KEY))
    results: list[dict[str, Any]] = []
    changed = False
    for action in archive.get("convergence_actions") or []:
        if not isinstance(action, dict) or not action.get("map_grid_seed"):
            continue
        if isinstance(action.get("rendered_map_ref"), dict):
            continue
        result = render_story_forge_map_seed(
            session,
            repository,
            session_id,
            action,
            send_to_chat=runtime_config.render_send_to_chat,
        )
        results.append(result)
        if result.get("ok"):
            _attach_render_ref_to_action(action, result)
            _upsert_render_ref(archive, result)
            changed = True
    if changed:
        archive["updated_at"] = utc_now_iso()
        scene[STORY_FORGE_ARCHIVE_KEY] = archive
        _write_story_forge_player_brief(scene, archive, config=runtime_config)
    return results


def render_story_forge_map_seed(
    session: GameSession,
    repository: Any,
    session_id: str,
    action: dict[str, Any],
    *,
    send_to_chat: bool = True,
) -> dict[str, Any]:
    seed = action.get("map_grid_seed")
    if not isinstance(seed, dict) or not seed:
        return {"ok": False, "error": "map_grid_seed_missing"}
    envelope = build_story_grid_render_envelope(
        {
            "map_grid_seed": seed,
            "map_id": seed.get("map_id") or action.get("action_id") or "story-grid",
            "title": seed.get("title") or action.get("scene_goal") or "Story grid",
            "scene_goal": action,
        }
    )
    try:
        render_input = build_strict_grid_render_input(envelope)
        svg = render_strict_grid_svg(render_input)
    except ValueError as exc:
        return {"ok": False, "error": "story_grid_render_failed", "reason": str(exc)}
    maps_dir = repository.maps_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_stem = _safe_file_stem(render_input.title or render_input.map_id or "story-grid")
    path = maps_dir / f"{stamp}-{file_stem}.svg"
    path.write_text(svg, encoding="utf-8")
    map_record = {
        "type": "svg_map",
        "render_type": MAP_RENDER_STRICT_GRID,
        "title": render_input.title,
        "name": path.name,
        "path": str(path),
        "created_at": utc_now_iso(),
        "map_id": render_input.map_id,
        "projection": MAP_VIEW_PLAYER,
        "width": render_input.width,
        "height": render_input.height,
        "visual_only": True,
        "source": "story_forge_runtime",
        "trigger_id": str(action.get("action_id") or ""),
    }
    delivery = None
    if send_to_chat:
        delivery, _state = enqueue_map_pending_output(
            _ensure_scene(session),
            map_record,
            MapDeliveryRequest(
                trigger=MAP_DELIVERY_TRIGGER_AREA_DISCOVERY,
                render_type=MAP_RENDER_STRICT_GRID,
                map_id=render_input.map_id,
                map_revision=str(action.get("created_at") or ""),
                trigger_id=str(action.get("action_id") or render_input.map_id),
            ),
        )
    result = {
        "ok": True,
        "type": "svg_map",
        "render_type": MAP_RENDER_STRICT_GRID,
        "visual_only": True,
        "source": "story_forge_runtime",
        "map_id": render_input.map_id,
        "title": render_input.title,
        "file_name": path.name,
        "file_path": str(path),
        "width": render_input.width,
        "height": render_input.height,
        "svg_chars": len(svg),
    }
    if delivery is not None:
        result["delivery"] = dict(delivery.__dict__)
    return result


def build_story_grid_render_envelope(payload: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("story_grid_payload_not_object")
    source = _grid_source(payload)
    grid = _grid_payload(source)
    width = _positive_int(grid.get("width", source.get("width")), "grid_width")
    height = _positive_int(grid.get("height", source.get("height")), "grid_height")
    envelope: dict[str, Any] = {
        "projection": PLAYER_SAFE_PROJECTION,
        "visibility": "player",
        "map_id": _safe_text(source.get("map_id") or payload.get("map_id") or "story-grid", 120),
        "title": title
        or _safe_text(source.get("title") or payload.get("title") or source.get("map_title") or "Story grid", 160),
        "grid": {
            "width": width,
            "height": height,
            "cells": _normalize_cells(grid.get("cells", source.get("cells")), width=width, height=height),
            "entities": _normalize_entities(grid.get("entities", source.get("entities")), width=width, height=height),
            "rule_scale": source.get("rule_scale") or grid.get("rule_scale") or {"distance_per_cell": 5, "unit": "ft"},
        },
    }
    for key in ("visible_bounds", "layout", "rule_scale", "discovered_areas"):
        value = source.get(key, payload.get(key))
        if value not in (None, "", [], {}):
            envelope[key] = value
    for key in ("doors", "hazards", "obstacles", "labels"):
        value = _derived_overlay_values(
            key,
            grid.get(key, source.get(key)),
            cells=envelope["grid"]["cells"],
        )
        normalized = _normalize_overlay(value, width=width, height=height)
        if normalized:
            envelope[key] = normalized
    return envelope


def _grid_source(payload: dict[str, Any]) -> dict[str, Any]:
    for value in (
        payload.get("map_grid_seed"),
        _nested_map_grid_seed(payload.get("scene_goal")),
        _nested_map_grid_seed(payload.get("convergence_action")),
    ):
        if isinstance(value, dict):
            return _with_inherited_map_fields(dict(value), payload)
    for key in ("strict_grid", "tactical_grid", "map_grid", "grid"):
        value = payload.get(key)
        if isinstance(value, dict):
            return _with_inherited_map_fields(dict(value), payload)
    return dict(payload)


def _nested_map_grid_seed(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and isinstance(value.get("map_grid_seed"), dict):
        return value["map_grid_seed"]
    return None


def _with_inherited_map_fields(source: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    for inherited in ("map_id", "title", "visible_bounds", "layout", "rule_scale", "discovered_areas"):
        if inherited not in source and payload.get(inherited) not in (None, "", [], {}):
            source[inherited] = payload[inherited]
    return source


def _grid_payload(source: dict[str, Any]) -> dict[str, Any]:
    value = source.get("grid")
    return dict(value) if isinstance(value, dict) else source


def _normalize_cells(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                cell = dict(item)
            elif isinstance(item, list) and len(item) >= 2:
                cell = {"x": item[0], "y": item[1]}
                if len(item) >= 3:
                    cell["terrain"] = item[2]
            else:
                continue
            if not _player_visible_record(cell):
                continue
            _adapt_cell_type(cell)
            if _point_in_bounds(cell, width=width, height=height):
                cells.append(cell)
    return cells


def _normalize_entities(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if isinstance(value, dict):
        iterable = (
            {"id": str(entity_id), **dict(item)}
            for entity_id, item in value.items()
            if isinstance(item, dict)
        )
    elif isinstance(value, list):
        iterable = (dict(item) for item in value if isinstance(item, dict))
    else:
        iterable = ()
    for item in iterable:
        if not _player_visible_record(item):
            continue
        _adapt_entity_type(item)
        if _point_in_bounds(item, width=width, height=height):
            entities.append(item)
    return entities


def _adapt_cell_type(cell: dict[str, Any]) -> None:
    raw_type = str(cell.get("type") or cell.get("kind") or "").strip().lower()
    if raw_type and cell.get("terrain") in (None, "", [], {}):
        if raw_type in {"wall", "floor", "stone", "dirt", "grass", "water", "mud", "normal"}:
            cell["terrain"] = raw_type
        elif raw_type == "door":
            cell["terrain"] = "floor"
    if raw_type == "wall":
        cell.setdefault("blocks_move", True)
        cell.setdefault("blocks_los", True)


def _adapt_entity_type(entity: dict[str, Any]) -> None:
    raw_type = str(entity.get("type") or entity.get("kind") or "").strip()
    if raw_type and entity.get("name") in (None, "", [], {}):
        entity["name"] = str(entity.get("label") or raw_type)
    if raw_type and entity.get("faction") in (None, "", [], {}):
        entity["faction"] = "npc" if raw_type.lower() in {"npc", "character"} else raw_type.lower()


def _derived_overlay_values(key: str, value: Any, *, cells: list[dict[str, Any]]) -> Any:
    items = list(value) if isinstance(value, list) else []
    for cell in cells:
        raw_type = str(cell.get("type") or cell.get("kind") or "").strip().lower()
        if key == "labels" and cell.get("label") not in (None, "", [], {}):
            items.append(
                {
                    "id": cell.get("id") or f"cell-label-{cell.get('x')}-{cell.get('y')}",
                    "x": cell.get("x"),
                    "y": cell.get("y"),
                    "text": cell.get("label"),
                    "visibility": cell.get("visibility", "player"),
                }
            )
        elif key == "doors" and raw_type == "door":
            items.append(
                {
                    "id": cell.get("id") or f"door-{cell.get('x')}-{cell.get('y')}",
                    "x": cell.get("x"),
                    "y": cell.get("y"),
                    "side": cell.get("side") or "north",
                    "state": cell.get("state") or "closed",
                    "visibility": cell.get("visibility", "player"),
                }
            )
    return items


def _normalize_overlay(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or not _player_visible_record(item):
            continue
        current = dict(item)
        if _point_in_bounds(current, width=width, height=height):
            items.append(current)
    return items


def _merge_convergence_action(
    archive: dict[str, Any],
    action: dict[str, Any],
    *,
    limit: int,
) -> tuple[dict[str, Any], bool]:
    actions = [item for item in archive.get("convergence_actions") or [] if isinstance(item, dict)]
    action_id = str(action.get("action_id") or "")
    replaced = False
    merged: list[dict[str, Any]] = []
    selected = action
    for existing in actions:
        if str(existing.get("action_id") or "") == action_id:
            selected = {**existing, **action, "updated_at": utc_now_iso()}
            merged.append(selected)
            replaced = True
        else:
            merged.append(existing)
    if not replaced:
        merged.append(selected)
    archive["convergence_actions"] = merged[-max(1, limit) :]
    return selected, replaced


def _bootstrap_scene_pressure_clock(
    scene: dict[str, Any],
    archive: dict[str, Any],
    *,
    actor: dict[str, str] | None = None,
    now: str = "",
) -> bool:
    if not isinstance(scene, dict):
        return False
    existing = _list_of_dicts(archive.get("pressure_clocks"))
    if _active_pressure_clocks(existing):
        return False
    legacy = scene.get("pressure_clock")
    if legacy in (None, "", [], {}):
        return False
    payload = _legacy_scene_pressure_clock_payload(legacy, scene)
    if not payload:
        return False
    try:
        clock = normalize_pressure_clock(payload, actor=actor or {}, now=now or utc_now_iso())
    except ValueError:
        return False
    archive["pressure_clocks"] = (existing + [clock])[-60:]
    archive["updated_at"] = _safe_text(now or utc_now_iso(), 80)
    return True


def _legacy_scene_pressure_clock_payload(value: Any, scene: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        if not _player_visible_record(value):
            return {}
        label = _safe_text(value.get("label") or value.get("title") or value.get("name") or "当前压力", 160)
        public_signal = _safe_text(
            value.get("public_signal")
            or value.get("visible_signal")
            or value.get("text")
            or value.get("description")
            or value.get("summary"),
            360,
        )
        stakes = _safe_text(value.get("stakes") or value.get("consequence") or scene.get("stakes"), 360)
        raw_max = value.get("max", value.get("segments", 4))
        raw_tick = value.get("value", value.get("tick", 0))
        pressure_type = value.get("pressure_type") or value.get("type") or "time"
        visibility = value.get("visibility") or "public"
        status = value.get("status") or "active"
        raw_clock_id = value.get("clock_id") or value.get("id")
    else:
        label = "当前压力"
        public_signal = _safe_text(value, 360)
        stakes = _safe_text(scene.get("stakes"), 360)
        raw_max = 4
        raw_tick = 0
        pressure_type = "time"
        visibility = "public"
        status = "active"
        raw_clock_id = ""
    if not label and not public_signal:
        return {}
    return {
        "clock_id": raw_clock_id,
        "label": label or "当前压力",
        "pressure_type": pressure_type,
        "value": raw_tick,
        "max": raw_max,
        "status": status,
        "visibility": _normalize_clock_visibility(visibility),
        "public_signal": public_signal,
        "stakes": stakes or "压力升满后，局面会变得更困难，但仍保留可执行的补救路线。",
        "next_risk_hint": "拖延、失败检定、制造噪音或放弃窗口会推进这个压力。",
        "counterplay_hint": "快速行动、隐藏踪迹、切断风险来源或付出资源可减缓压力。",
        "on_complete": {
            "failure_forward": "压力升满后，局面会升级或入口条件变差，但角色仍能通过新的路线、代价或场景目标继续推进。",
            "new_scene_goal": "处理升级后的风险，寻找替代路线或补救机会。",
        },
    }


def _find_pressure_clock_index(clocks: list[dict[str, Any]], clock_id: str) -> int:
    for index, item in enumerate(clocks):
        if str(item.get("clock_id") or "") == clock_id:
            return index
    return -1


def _active_pressure_clocks(clocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [clock for clock in clocks if str(clock.get("status") or "active").lower() == "active"]


def _write_story_forge_player_brief(
    scene: dict[str, Any],
    archive: dict[str, Any],
    *,
    config: StoryForgeRuntimeConfig,
) -> None:
    scene[STORY_FORGE_BRIEF_KEY] = {
        "schema_version": STORY_FORGE_SCHEMA_VERSION,
        "updated_at": _safe_text(archive.get("updated_at"), 80),
        "open_threads": _project_records_for_brief(archive.get("open_threads"), limit=config.max_open_threads),
        "clue_ledger": _project_records_for_brief(archive.get("clue_ledger"), limit=config.max_clue_ledger),
        "convergence_actions": [
            _project_action_for_brief(action)
            for action in (archive.get("convergence_actions") or [])[-max(1, config.max_convergence_actions) :]
            if isinstance(action, dict)
        ],
        "encounter_contracts": [
            _project_encounter_contract_for_brief(contract)
            for contract in (archive.get("encounter_contracts") or [])[-max(1, config.max_encounter_contracts) :]
            if isinstance(contract, dict)
        ],
        "visible_pressure_clocks": [
            _project_pressure_clock_for_brief(clock)
            for clock in (archive.get("pressure_clocks") or [])[-12:]
            if isinstance(clock, dict) and _clock_player_visible(clock)
        ],
        "recent_clock_events": [
            _project_clock_event_for_brief(event)
            for event in (archive.get("clock_events") or [])[-8:]
            if isinstance(event, dict) and event.get("player_visible", True)
        ],
        "rendered_maps": [
            _safe_render_ref(ref)
            for ref in (archive.get("rendered_map_refs") or [])[-max(1, config.max_convergence_actions) :]
            if isinstance(ref, dict)
        ],
    }


def _visible_tracking_state(scene: dict[str, Any]) -> dict[str, Any]:
    visible = project_visible_scene_value(
        {
            key: scene.get(key)
            for key in ("current_objective", "open_hooks", "clues", "mysteries", "stakes", "pressure_clock")
            if key in scene
        },
        depth=4,
        text_limit=500,
        item_limit=24,
    )
    return visible if isinstance(visible, dict) else {}


def _merge_open_threads(value: Any, visible_tracking: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    records = _list_of_dicts(value)
    by_id: dict[str, dict[str, Any]] = {str(item.get("id") or item.get("thread_id") or ""): dict(item) for item in records}
    for key in ("open_hooks", "mysteries"):
        for record in _list_of_dicts(visible_tracking.get(key)):
            status = str(record.get("status") or "open").lower()
            thread_id = _safe_id(record.get("id") or _stable_hash(record.get("text")))
            if status in {"resolved", "closed", "archived", "retired"}:
                by_id.pop(thread_id, None)
                continue
            by_id[thread_id] = {
                "id": thread_id,
                "source": key,
                "text": _safe_text(record.get("text"), 360),
                "status": status or "open",
                "updated_at": utc_now_iso(),
            }
    return [item for item in by_id.values() if item.get("text")][-max(1, limit) :]


def _merge_clue_ledger(value: Any, visible_tracking: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    records = _list_of_dicts(value)
    by_id: dict[str, dict[str, Any]] = {str(item.get("id") or ""): dict(item) for item in records}
    for record in _list_of_dicts(visible_tracking.get("clues")):
        clue_id = _safe_id(record.get("id") or _stable_hash(record.get("text")))
        by_id[clue_id] = {
            "id": clue_id,
            "text": _safe_text(record.get("text"), 360),
            "status": _safe_text(record.get("status") or "discovered", 80),
            "updated_at": utc_now_iso(),
        }
    return [item for item in by_id.values() if item.get("text")][-max(1, limit) :]


def _attach_render_ref_to_action(action: dict[str, Any], render_result: dict[str, Any]) -> None:
    action["rendered_map_ref"] = _safe_render_ref(render_result)
    action["rendered_at"] = utc_now_iso()


def _upsert_render_ref(archive: dict[str, Any], render_result: dict[str, Any]) -> None:
    ref = _safe_render_ref(render_result, include_path=True)
    refs = [
        item
        for item in _list_of_dicts(archive.get("rendered_map_refs"))
        if str(item.get("file_name") or "") != str(ref.get("file_name") or "")
    ]
    refs.append(ref)
    archive["rendered_map_refs"] = refs[-120:]


def _public_render_result(render_result: dict[str, Any]) -> dict[str, Any]:
    if not render_result:
        return {}
    public = _safe_render_ref(render_result)
    if isinstance(render_result.get("delivery"), dict):
        public["delivery"] = {
            "should_send": bool(render_result["delivery"].get("should_send")),
            "reason": _safe_text(render_result["delivery"].get("reason"), 80),
        }
    return public


def _safe_render_ref(render_result: dict[str, Any], *, include_path: bool = False) -> dict[str, Any]:
    if not isinstance(render_result, dict) or not render_result.get("ok"):
        return {}
    ref = {
        "type": "strict_grid_svg",
        "source": "story_forge_runtime",
        "map_id": _safe_text(render_result.get("map_id"), 120),
        "title": _safe_text(render_result.get("title"), 160),
        "file_name": _safe_text(render_result.get("file_name"), 180),
        "width": _safe_int(render_result.get("width"), 0),
        "height": _safe_int(render_result.get("height"), 0),
        "visual_only": True,
        "created_at": utc_now_iso(),
    }
    if include_path:
        ref["file_path"] = _safe_text(render_result.get("file_path"), 500)
    return {key: value for key, value in ref.items() if value not in ("", 0, None)}


def _project_action_for_brief(action: dict[str, Any]) -> dict[str, Any]:
    projected = {
        "action_id": _safe_text(action.get("action_id"), 120),
        "thread_id": _safe_text(action.get("thread_id"), 160),
        "action_type": _safe_text(action.get("action_type"), 80),
        "scene_goal": _safe_text(action.get("scene_goal"), 360),
        "entry_cost": _safe_text(action.get("entry_cost"), 240),
        "success_signal": _safe_text(action.get("success_signal"), 240),
        "failure_forward": _safe_text(action.get("failure_forward"), 260),
        "map_seed_available": bool(action.get("map_grid_seed")),
    }
    if isinstance(action.get("rendered_map_ref"), dict):
        projected["rendered_map"] = _safe_render_ref({"ok": True, **action["rendered_map_ref"]})
    return {key: value for key, value in projected.items() if value not in (None, "", [], {})}


def _project_encounter_contract_for_brief(contract: dict[str, Any]) -> dict[str, Any]:
    projected = {
        "contract_id": _safe_text(contract.get("contract_id"), 120),
        "status": _safe_text(contract.get("status") or "active", 80),
        "encounter_decision": _safe_text(contract.get("encounter_decision"), 80),
        "scene_goal": _safe_text(contract.get("scene_goal"), 300),
        "stakes": _safe_text(contract.get("stakes"), 260),
        "participants": _safe_text_list(contract.get("participants"), limit=12, item_limit=100),
        "pressure_vectors": _safe_text_list(contract.get("pressure_vectors"), limit=8, item_limit=80),
        "action_economy": _safe_text(contract.get("action_economy"), 80),
        "map_need": _safe_text(contract.get("map_need"), 80),
        "turn_order_source": _safe_text(contract.get("turn_order_source"), 80),
        "recommended_next_tool": _safe_text(contract.get("recommended_next_tool"), 80),
        "player_visible_brief": _safe_text(contract.get("player_visible_brief"), 260),
    }
    return {key: value for key, value in projected.items() if value not in (None, "", [], {})}


def normalize_pressure_clock(
    payload: dict[str, Any],
    *,
    actor: dict[str, str] | None = None,
    now: str = "",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("pressure_clock_payload_not_object")
    blocked = _blocked_hidden_paths(payload)
    if blocked:
        raise ValueError(f"hidden_pressure_clock_fields_not_allowed:{','.join(blocked[:4])}")
    label = _safe_text(payload.get("label") or payload.get("title") or payload.get("name"), 160)
    if not label:
        raise ValueError("pressure_clock_label_required")
    max_value = max(1, _safe_int(payload.get("max"), 4))
    value = max(0, min(max_value, _safe_int(payload.get("value", payload.get("tick")), 0)))
    clock_id = _safe_clock_id(payload.get("clock_id") or payload.get("id"), fallback_label=label)
    visibility = _normalize_clock_visibility(payload.get("visibility"))
    public_signal = _safe_text(payload.get("public_signal") or payload.get("visible_signal"), 360)
    stakes = _safe_text(payload.get("stakes") or payload.get("consequence"), 360)
    on_complete = _sanitize_player_visible_value(payload.get("on_complete"))
    if not isinstance(on_complete, dict):
        on_complete = {}
    if not _clock_completion_playable(on_complete):
        raise ValueError("pressure_clock_completion_must_be_failure_forward")
    clock = {
        "clock_id": clock_id,
        "label": label,
        "pressure_type": _safe_text(payload.get("pressure_type") or payload.get("type") or "time", 80),
        "scope": _safe_text(payload.get("scope") or "scene", 80),
        "value": value,
        "max": max_value,
        "status": _safe_text(payload.get("status") or "active", 80),
        "visibility": visibility,
        "public_signal": public_signal,
        "stakes": stakes,
        "next_risk_hint": _safe_text(payload.get("next_risk_hint") or payload.get("next_risk"), 240),
        "counterplay_hint": _safe_text(payload.get("counterplay_hint") or payload.get("counterplay"), 240),
        "on_complete": on_complete,
        "created_at": _safe_text(payload.get("created_at") or now or utc_now_iso(), 80),
        "updated_at": _safe_text(now or utc_now_iso(), 80),
        "created_by": _safe_actor(actor or {}),
    }
    return {key: value for key, value in clock.items() if value not in (None, "", [], {})}


def normalize_clock_event(payload: dict[str, Any], *, now: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("clock_event_payload_not_object")
    blocked = _blocked_hidden_paths(payload)
    if blocked:
        raise ValueError(f"hidden_clock_event_fields_not_allowed:{','.join(blocked[:4])}")
    created_at = _safe_text(now or payload.get("created_at") or utc_now_iso(), 80)
    clock_id = _safe_clock_id(payload.get("clock_id"))
    trigger = _safe_text(payload.get("trigger"), 160)
    cause = _safe_text(payload.get("cause"), 500)
    visible_effect = _safe_text(payload.get("visible_effect"), 500)
    if not clock_id:
        raise ValueError("clock_event_clock_id_required")
    if not trigger or not cause or not visible_effect:
        raise ValueError("clock_event_trigger_cause_effect_required")
    event = {
        "event_id": _safe_id(payload.get("event_id") or "cevt:" + _stable_hash({**payload, "created_at": created_at})[:16]),
        "created_at": created_at,
        "clock_id": clock_id,
        "clock_type": _safe_text(payload.get("clock_type") or "pressure", 80),
        "delta": _safe_int(payload.get("delta"), 0),
        "old_value": _safe_int(payload.get("old_value"), 0),
        "new_value": _safe_int(payload.get("new_value"), 0),
        "max": max(1, _safe_int(payload.get("max"), 4)),
        "trigger": trigger,
        "cause": cause,
        "visible_effect": visible_effect,
        "completed": bool(payload.get("completed")),
        "turn_index": max(0, _safe_int(payload.get("turn_index"), 0)),
        "player_visible": _safe_bool(payload.get("player_visible", True), default=True),
    }
    actor = payload.get("actor")
    if isinstance(actor, dict):
        event["actor"] = _safe_actor(actor)
    return event


def _normalize_pressure_clocks(value: Any) -> list[dict[str, Any]]:
    clocks: list[dict[str, Any]] = []
    for item in _list_of_dicts(value)[-60:]:
        try:
            clocks.append(normalize_pressure_clock(item, now=_safe_text(item.get("updated_at"), 80)))
        except ValueError:
            continue
    return clocks


def _normalize_clock_events(value: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in _list_of_dicts(value)[-200:]:
        try:
            events.append(normalize_clock_event(item, now=_safe_text(item.get("created_at"), 80)))
        except ValueError:
            continue
    return events


def _normalize_encounter_contracts(value: Any) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for item in _list_of_dicts(value)[-80:]:
        try:
            contracts.append(normalize_encounter_contract(item, now=_safe_text(item.get("updated_at"), 80)))
        except ValueError:
            continue
    return contracts


def _project_pressure_clock_for_brief(clock: dict[str, Any]) -> dict[str, Any]:
    projected = {
        "clock_id": _safe_text(clock.get("clock_id"), 120),
        "label": _safe_text(clock.get("label"), 160),
        "pressure_type": _safe_text(clock.get("pressure_type"), 80),
        "value": _safe_int(clock.get("value"), 0),
        "max": max(1, _safe_int(clock.get("max"), 4)),
        "status": _safe_text(clock.get("status") or "active", 80),
        "visibility": _normalize_clock_visibility(clock.get("visibility")),
        "public_signal": _safe_text(clock.get("public_signal"), 240),
        "stakes": _safe_text(clock.get("stakes"), 240),
        "next_risk_hint": _safe_text(clock.get("next_risk_hint"), 180),
        "counterplay_hint": _safe_text(clock.get("counterplay_hint"), 180),
    }
    completion = _project_clock_completion(clock.get("on_complete"))
    if completion:
        projected["on_complete"] = completion
    return {key: value for key, value in projected.items() if value not in (None, "", [], {})}


def _project_clock_event_for_brief(event: dict[str, Any]) -> dict[str, Any]:
    projected = {
        "event_id": _safe_text(event.get("event_id"), 120),
        "clock_id": _safe_text(event.get("clock_id"), 120),
        "delta": _safe_int(event.get("delta"), 0),
        "new_value": _safe_int(event.get("new_value"), 0),
        "max": max(1, _safe_int(event.get("max"), 4)),
        "trigger": _safe_text(event.get("trigger"), 120),
        "visible_effect": _safe_text(event.get("visible_effect"), 240),
        "completed": bool(event.get("completed")),
    }
    return {key: value for key, value in projected.items() if value not in (None, "", [], {})}


def _project_clock_completion(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    projected = {
        "effect": _safe_text(value.get("effect"), 260),
        "failure_forward": _safe_text(value.get("failure_forward"), 260),
        "new_scene_goal": _safe_text(value.get("new_scene_goal"), 260),
        "state_change": _safe_text(value.get("state_change"), 260),
    }
    return {key: item for key, item in projected.items() if item not in ("", None)}


def _clock_completion_playable(value: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_safe_text(value.get(key), 500) for key in ("failure_forward", "new_scene_goal", "state_change"))


def _normalize_clock_visibility(value: Any) -> str:
    visibility = str(value or "public").strip().lower()
    if visibility in {"public", "partial", "hidden"}:
        return visibility
    if visibility in {"player", "visible", "observed"}:
        return "public"
    if visibility in {"secret", "private", "dm", "gm", "dm_only", "gm_only"}:
        return "hidden"
    return "public"


def _normalize_choice(value: Any, *, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in allowed else default


def _default_action_economy(decision: str) -> str:
    if decision in {"strict_turns", "strict_grid"}:
        return "strict_order"
    if decision == "soft_turns":
        return "one_actor_focus"
    if decision == "pressure_scene":
        return "one_actor_focus"
    return "none"


def _default_encounter_next_tool(decision: str) -> str:
    if decision in {"strict_turns", "strict_grid"}:
        return "turn_control"
    if decision == "soft_turns":
        return "turn_control"
    if decision == "pressure_scene":
        return "record_story_forge_pressure_clock"
    if decision == "single_check":
        return "resolve_check"
    return "final_response"


def _safe_clock_id(value: Any, *, fallback_label: str = "") -> str:
    raw = _safe_text(value, 240)
    source = raw or _safe_text(fallback_label, 240)
    if not source:
        return ""
    candidate = _safe_id(raw or "clock:" + source)
    clock_payload = candidate
    if clock_payload.startswith("clock:"):
        clock_payload = clock_payload[len("clock:") :]
    elif clock_payload.startswith("clock-"):
        clock_payload = clock_payload[len("clock-") :]
    if candidate in {"story-forge-item", "clock"} or not clock_payload.strip("-._:"):
        return "clock:" + _stable_hash(source)[:12]
    return candidate


def _clock_player_visible(clock: dict[str, Any]) -> bool:
    return _normalize_clock_visibility(clock.get("visibility")) in {"public", "partial"}


def _safe_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "on"}:
            return True
        if text in {"false", "0", "no", "n", "off"}:
            return False
    if value in (None, ""):
        return default
    return bool(value)


def _turns_since_last_clock_event(archive: dict[str, Any]) -> int | None:
    events = _list_of_dicts(archive.get("clock_events"))
    turns = _list_of_dicts(archive.get("turns"))
    if not events:
        return len(turns) if turns else None
    last_event = events[-1]
    current_turn_index = len(turns)
    event_turn_index = _safe_int(last_event.get("turn_index"), current_turn_index)
    return max(0, current_turn_index - event_turn_index)


def _has_scene_goal_fields(action: dict[str, Any]) -> bool:
    return any(
        _safe_text(action.get(key), 500)
        for key in ("scene_goal", "entry_cost", "success_signal", "failure_forward")
    )


def _project_records_for_brief(value: Any, *, limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _list_of_dicts(value)[-max(1, limit) :]:
        record = {
            "id": _safe_text(item.get("id") or item.get("thread_id"), 120),
            "source": _safe_text(item.get("source"), 80),
            "text": _safe_text(item.get("text") or item.get("summary"), 360),
            "status": _safe_text(item.get("status"), 80),
        }
        records.append({key: current for key, current in record.items() if current not in ("", None)})
    return records


def _sanitize_player_visible_value(value: Any) -> Any:
    if isinstance(value, dict):
        if not _player_visible_record(value):
            return None
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _hidden_key(key_text):
                continue
            cleaned_value = _sanitize_player_visible_value(item)
            if cleaned_value not in (None, "", [], {}):
                cleaned[key_text] = cleaned_value
        return cleaned
    if isinstance(value, list):
        return [
            item
            for item in (_sanitize_player_visible_value(entry) for entry in value)
            if item not in (None, "", [], {})
        ]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _blocked_hidden_paths(value: Any, *, ignore_keys: set[str] | None = None, path: str = "") -> list[str]:
    ignore = ignore_keys or set()
    blocked: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in ignore:
                continue
            if _hidden_key(key_text):
                blocked.append(child_path)
                continue
            if isinstance(item, dict) and not _player_visible_record(item):
                blocked.append(child_path)
                continue
            blocked.extend(_blocked_hidden_paths(item, ignore_keys=ignore, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            blocked.extend(_blocked_hidden_paths(item, ignore_keys=ignore, path=f"{path}[{index}]"))
    return blocked


def _player_visible_record(value: dict[str, Any]) -> bool:
    visibility = str(value.get("visibility") or "").strip().lower()
    status = str(value.get("status") or "").strip().lower()
    if visibility in HIDDEN_VISIBILITIES or status in HIDDEN_STATUSES:
        return False
    if value.get("hidden") is True or value.get("secret") is True:
        return False
    return True


def _hidden_key(key: str) -> bool:
    key_lower = str(key or "").strip().lower()
    return key_lower in BLOCKED_HIDDEN_KEYS or any(token in key_lower for token in BLOCKED_HIDDEN_KEY_TOKENS)


def _ensure_scene(session: GameSession) -> dict[str, Any]:
    if not isinstance(session.scene, dict):
        session.scene = {}
    return session.scene


def _safe_actor(actor: dict[str, Any]) -> dict[str, str]:
    return {
        "player_id": _safe_text(actor.get("player_id"), 120),
        "display_name": _safe_text(actor.get("display_name"), 120),
        "platform": _safe_text(actor.get("platform"), 80),
    }


def _turn_id(session_id: str, player_message: str, dm_response: str, now: str) -> str:
    return "sf-turn-" + _stable_hash({"session_id": session_id, "player": player_message, "dm": dm_response, "at": now})[:16]


def _stable_hash(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        text = str(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _short_hash(value: Any) -> str:
    return _stable_hash(value)[:16]


def _safe_id(value: Any) -> str:
    text = re.sub(r"[^0-9A-Za-z_.:-]+", "-", str(value or "").strip())
    return text.strip("-._:")[:120] or "story-forge-item"


def _safe_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_text_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value not in (None, "", [], {}) else []
    return [_safe_text(item, item_limit) for item in value[: max(0, limit)] if _safe_text(item, item_limit)]


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "", [], {}):
            text = str(value).strip()
            if text:
                return text
    return ""


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _point_in_bounds(item: dict[str, Any], *, width: int, height: int) -> bool:
    try:
        x = _coordinate(item.get("x"), "x")
        y = _coordinate(item.get("y"), "y")
    except ValueError:
        return False
    return 0 <= x < width and 0 <= y < height


def _coordinate(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field}_not_integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ValueError(f"{field}_not_integer")


def _positive_int(value: Any, field: str) -> int:
    number = _coordinate(value, field)
    if number <= 0:
        raise ValueError(f"{field}_invalid")
    return number


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^\w.-]+", "_", str(value or ""), flags=re.UNICODE)
    return stem.strip("._")[:60] or "story-grid"


def _append_story_forge_audit(repository: Any, session_id: str, event_type: str, result: dict[str, Any]) -> None:
    try:
        repository.append_audit(
            session_id,
            {
                "type": event_type,
                "result": _audit_safe_result(result),
            },
        )
    except Exception:
        return


def _audit_safe_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _audit_safe_result(item)
            for key, item in value.items()
            if str(key) not in {"file_path", "path", "map_grid_seed", "player_message", "dm_response"}
        }
    if isinstance(value, list):
        return [_audit_safe_result(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return value.name
    return str(value)
