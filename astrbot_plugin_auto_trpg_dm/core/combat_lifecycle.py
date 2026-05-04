from __future__ import annotations

from typing import Any

from .map_core import MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED, get_strict_map_lifecycle


def combat_lifecycle_active(session: Any) -> bool:
    battle = getattr(session, "battle", {}) or {}
    if not isinstance(battle, dict):
        battle = {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if battle.get("active") or turn.get("active"):
        return True
    map_id = str(battle.get("map_id") or "").strip()
    maps = getattr(session, "maps", {}) or {}
    lifecycle = get_strict_map_lifecycle(maps, map_id)
    return bool(lifecycle.get("lifecycle") == MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED)
