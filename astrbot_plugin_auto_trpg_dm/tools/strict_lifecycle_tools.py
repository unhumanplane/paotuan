from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.map_core import (
    DEFAULT_STRICT_LOCAL_MAP_ID,
    MAP_AUTHORITY_SPATIAL,
    MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED,
    MAP_LIFECYCLE_ACTIVE_EXPLORATION,
    MAP_TYPE_STRICT_LOCAL,
    get_map_record,
    get_strict_map_lifecycle,
    save_active_strict_grid,
    set_strict_map_lifecycle,
)
from ..core.models import GameMode, utc_now_iso
from ..spatial.grid import Cell, GridState, Point
from ..storage.json_repository import JsonGameRepository
from .memory_tools import background_required_result


class CreateStrictMapArgs(BaseModel):
    width: int = Field(default=12, ge=2, le=64, description="严格局部地图宽度")
    height: int = Field(default=12, ge=2, le=64, description="严格局部地图高度")
    map_id: str = Field(default=DEFAULT_STRICT_LOCAL_MAP_ID, description="严格局部地图 ID；为空时使用默认当前地图")
    title: str = Field(default="Strict local map", description="地图标题")
    cells: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="特殊地形格列表，每项可包含 x,y,terrain,cost,blocks_move,blocks_los,cover",
    )


class StartCombatOnMapArgs(BaseModel):
    map_id: str = Field(default="", description="要链接为战斗地图的 strict_local_map ID；为空时使用当前 active strict map")
    summary: str = Field(default="", description="进入战斗的简短原因或场景摘要")


class EndCombatArgs(BaseModel):
    map_id: str = Field(default="", description="要解除战斗链接的 strict_local_map ID；为空时使用 battle.map_id")
    summary: str = Field(default="", description="战斗结束摘要")
    reason: str = Field(default="", description="结束战斗的裁定原因")


class StrictLifecycleTools:
    def __init__(self, repository: JsonGameRepository, session_id: str, actor: Optional[Dict[str, str]] = None):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def create_strict_map(
        self,
        width: int = 12,
        height: int = 12,
        map_id: str = DEFAULT_STRICT_LOCAL_MAP_ID,
        title: str = "Strict local map",
        cells: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "create_strict_map")
        if gate:
            self._audit("create_strict_map", locals_without_session(locals()), gate)
            return gate
        if _combat_active(session.battle):
            result = {"ok": False, "error": "combat_already_active", "message": "战斗进行中不能重建独立 strict map。"}
            self._audit("create_strict_map", locals_without_session(locals()), result)
            return result

        grid_data = _grid_data(width, height, cells or [])
        try:
            record = save_active_strict_grid(
                session.maps,
                grid_data,
                map_id=(map_id or DEFAULT_STRICT_LOCAL_MAP_ID),
                title=title or "Strict local map",
                authority=MAP_AUTHORITY_SPATIAL,
                source="strict_lifecycle_create_strict_map",
                authority_assumption="strict_lifecycle_create_strict_map",
                lifecycle=MAP_LIFECYCLE_ACTIVE_EXPLORATION,
            )
        except ValueError as exc:
            result = {"ok": False, "error": str(exc).split(":", 1)[0], "reason": str(exc)}
            self._audit("create_strict_map", locals_without_session(locals()), result)
            return result

        battle = dict(session.battle or {})
        battle["active"] = False
        battle.setdefault("turn_entity_id", "")
        session.battle = battle
        self.repository.save_session(session)
        result = {
            "ok": True,
            "map_id": record["id"],
            "grid": grid_data,
            "lifecycle": record.get("lifecycle", MAP_LIFECYCLE_ACTIVE_EXPLORATION),
            "battle_active": False,
        }
        self._audit("create_strict_map", locals_without_session(locals()), result)
        return result

    async def start_combat_on_map(self, map_id: str = "", summary: str = "") -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "start_combat_on_map")
        if gate:
            self._audit("start_combat_on_map", locals_without_session(locals()), gate)
            return gate
        if _combat_active(session.battle):
            result = {"ok": False, "error": "combat_already_active"}
            self._audit("start_combat_on_map", locals_without_session(locals()), result)
            return result

        selected_id = str(map_id or session.maps.get("active_strict_map_id") or "").strip()
        record = get_map_record(session.maps, selected_id) if selected_id else None
        if not isinstance(record, dict) or record.get("type") != MAP_TYPE_STRICT_LOCAL:
            result = {"ok": False, "error": "strict_map_not_found", "map_id": selected_id}
            self._audit("start_combat_on_map", locals_without_session(locals()), result)
            return result
        grid = record.get("grid")
        if not isinstance(grid, dict):
            result = {"ok": False, "error": "strict_grid_not_found", "map_id": selected_id}
            self._audit("start_combat_on_map", locals_without_session(locals()), result)
            return result

        record = set_strict_map_lifecycle(
            session.maps,
            selected_id,
            MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED,
            source="strict_lifecycle_start_combat_on_map",
        )
        battle = dict(session.battle or {})
        battle["active"] = True
        battle["map_id"] = selected_id
        battle["grid"] = grid
        battle["turn_entity_id"] = ""
        battle["turn"] = _idle_turn_state(battle.get("turn"))
        session.battle = battle
        session.mode = GameMode.TACTICAL
        self.repository.save_session(session)
        result = {
            "ok": True,
            "map_id": selected_id,
            "battle_active": True,
            "lifecycle": record.get("lifecycle", MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED),
            "grid": grid,
        }
        self._audit("start_combat_on_map", locals_without_session(locals()), result)
        return result

    async def end_combat(self, map_id: str = "", summary: str = "", reason: str = "") -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        battle = dict(session.battle or {})
        selected_id = str(map_id or battle.get("map_id") or session.maps.get("active_strict_map_id") or "").strip()
        lifecycle = get_strict_map_lifecycle(session.maps, selected_id)
        if not (_combat_active(battle) or lifecycle.get("combat_linked")):
            result = {"ok": False, "error": "combat_not_active", "map_id": selected_id}
            self._audit("end_combat", locals_without_session(locals()), result)
            return result

        record = get_map_record(session.maps, selected_id) if selected_id else None
        if not isinstance(record, dict) or record.get("type") != MAP_TYPE_STRICT_LOCAL:
            result = {"ok": False, "error": "strict_map_not_found", "map_id": selected_id}
            self._audit("end_combat", locals_without_session(locals()), result)
            return result

        record = set_strict_map_lifecycle(
            session.maps,
            selected_id,
            MAP_LIFECYCLE_ACTIVE_EXPLORATION,
            source="strict_lifecycle_end_combat",
        )
        battle["active"] = False
        battle["map_id"] = ""
        if isinstance(record.get("grid"), dict):
            battle["grid"] = record["grid"]
        battle["turn_entity_id"] = ""
        battle["turn"] = _ended_turn_state(battle.get("turn"), summary=summary, reason=reason)
        session.battle = battle
        session.mode = GameMode.NARRATIVE
        self.repository.save_session(session)
        result = {
            "ok": True,
            "map_id": selected_id,
            "battle_active": False,
            "lifecycle": record.get("lifecycle", MAP_LIFECYCLE_ACTIVE_EXPLORATION),
            "grid": record.get("grid", {}),
        }
        self._audit("end_combat", locals_without_session(locals()), result)
        return result

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": tool, "input": input_payload, "result": result},
        )


def _grid_data(width: int, height: int, cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    safe_width = max(2, min(64, int(width)))
    safe_height = max(2, min(64, int(height)))
    grid = GridState.empty(safe_width, safe_height)
    for item in cells:
        cell = Cell.from_dict(item)
        if grid.in_bounds(Point(cell.x, cell.y)):
            grid.cells[(cell.x, cell.y)] = cell
    return grid.to_dict()


def _combat_active(battle: Any) -> bool:
    if not isinstance(battle, dict):
        return False
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    return bool(battle.get("active") or turn.get("active"))


def _idle_turn_state(value: Any) -> Dict[str, Any]:
    turn = dict(value or {}) if isinstance(value, dict) else {}
    turn.update(
        {
            "active": False,
            "phase": "idle",
            "current_index": -1,
            "current_entity_id": "",
        }
    )
    turn.setdefault("round", 0)
    turn.setdefault("turn_order", [])
    turn.setdefault("output_limit_chars", 720)
    turn.setdefault("auto_policy", "defend_or_follow")
    turn.setdefault("timeout_seconds", 120)
    turn.setdefault("actions_this_round", {})
    turn.setdefault("turn_log", [])
    return turn


def _ended_turn_state(value: Any, *, summary: str = "", reason: str = "") -> Dict[str, Any]:
    turn = _idle_turn_state(value)
    turn["phase"] = "ended"
    if summary:
        log = list(turn.get("turn_log") or [])
        log.append(
            {
                "at": utc_now_iso(),
                "round": turn.get("round", 0),
                "phase": "ended",
                "type": "combat_end",
                "summary": summary[:240],
                "reason": reason[:160],
            }
        )
        turn["turn_log"] = log
    return turn


def locals_without_session(values: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if key not in {"self", "session", "record", "battle"}}
