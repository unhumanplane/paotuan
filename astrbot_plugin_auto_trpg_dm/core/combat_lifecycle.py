from __future__ import annotations

from typing import Any

from .map_core import (
    MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED,
    MAP_LIFECYCLE_ACTIVE_EXPLORATION,
    get_map_record,
    get_strict_map_lifecycle,
    set_strict_map_lifecycle,
)


def combat_lifecycle_active(session: Any) -> bool:
    battle = getattr(session, "battle", {}) or {}
    if not isinstance(battle, dict):
        battle = {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if battle.get("active"):
        return True
    if turn.get("active") and str(turn.get("phase") or "") not in {"suspended", "ended", "idle"}:
        return True
    map_id = str(battle.get("map_id") or "").strip()
    maps = getattr(session, "maps", {}) or {}
    lifecycle = get_strict_map_lifecycle(maps, map_id)
    return bool(lifecycle.get("lifecycle") == MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED)


def close_combat_lifecycle(
    session: Any,
    *,
    summary: str = "",
    reason: str = "",
    source: str = "combat_lifecycle_close",
) -> dict[str, Any]:
    battle = getattr(session, "battle", {}) or {}
    if not isinstance(battle, dict):
        battle = {}
    selected_id = str(battle.get("map_id") or getattr(session, "maps", {}).get("active_strict_map_id") or "").strip()
    maps = getattr(session, "maps", {}) or {}
    lifecycle_before = get_strict_map_lifecycle(maps, selected_id) if selected_id else {"combat_linked": False, "map_id": ""}
    record = None
    if selected_id and lifecycle_before.get("ok") and lifecycle_before.get("combat_linked"):
        try:
            record = set_strict_map_lifecycle(
                maps,
                selected_id,
                MAP_LIFECYCLE_ACTIVE_EXPLORATION,
                source=source,
            )
        except ValueError:
            record = None
    elif selected_id:
        record = get_map_record(maps, selected_id)

    battle["active"] = False
    battle["map_id"] = ""
    if isinstance(record, dict) and isinstance(record.get("grid"), dict):
        battle["grid"] = record["grid"]
    battle["turn_entity_id"] = ""
    battle["turn"] = ended_turn_state(battle.get("turn"), summary=summary, reason=reason)
    session.battle = battle
    if hasattr(session, "mode"):
        from .models import GameMode

        session.mode = GameMode.NARRATIVE
    lifecycle_after = get_strict_map_lifecycle(maps, selected_id) if selected_id else {"combat_linked": False, "map_id": ""}
    return {
        "changed": True,
        "map_id": selected_id,
        "lifecycle_before": lifecycle_before.get("lifecycle", ""),
        "lifecycle_after": lifecycle_after.get("lifecycle", ""),
    }


def ended_turn_state(value: Any, *, summary: str = "", reason: str = "") -> dict[str, Any]:
    turn = dict(value or {}) if isinstance(value, dict) else {}
    turn.update(
        {
            "active": False,
            "phase": "ended",
            "turn_order": [],
            "current_index": -1,
            "current_entity_id": "",
            "actions_this_round": {},
            "scene_resolution_done": True,
        }
    )
    for key in ("waiting_since_at", "deadline_at", "pause_reason", "paused_at"):
        turn.pop(key, None)
    turn.setdefault("round", 0)
    turn.setdefault("output_limit_chars", 720)
    turn.setdefault("auto_policy", "defend_or_follow")
    turn.setdefault("timeout_seconds", 120)
    log = list(turn.get("turn_log") or [])
    if summary:
        from .models import utc_now_iso

        log.append(
            {
                "at": utc_now_iso(),
                "round": turn.get("round", 0),
                "phase": "ended",
                "type": "combat_end",
                "summary": str(summary)[:240],
                "reason": str(reason)[:160],
            }
        )
    turn["turn_log"] = log
    return turn
