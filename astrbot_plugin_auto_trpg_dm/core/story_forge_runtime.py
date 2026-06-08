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
    }
    _append_story_forge_audit(repository, session_id, "story_forge_turn_archived", result)
    return result


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
