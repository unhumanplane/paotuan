from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..core.models import GameMode
from ..spatial.engine import SpatialEngine
from ..spatial.grid import Cell, Entity, GridState, Point
from ..storage.json_repository import JsonGameRepository
from .memory_tools import background_required_result


class MoveEntityArgs(BaseModel):
    entity_id: str = Field(..., description="要移动的战棋实体 ID")
    target_x: int = Field(..., description="目标 X 坐标，从 0 开始")
    target_y: int = Field(..., description="目标 Y 坐标，从 0 开始")


class CheckAttackVectorArgs(BaseModel):
    source_id: str = Field(..., description="攻击者实体 ID")
    target_id: str = Field(..., description="目标实体 ID")


class CreateGridArgs(BaseModel):
    width: int = Field(default=12, ge=2, le=64, description="地图宽度")
    height: int = Field(default=12, ge=2, le=64, description="地图高度")
    cells: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="特殊地形格列表，每项可包含 x,y,terrain,cost,blocks_move,blocks_los,cover",
    )


class PlaceEntityArgs(BaseModel):
    entity_id: str = Field(..., description="实体 ID")
    name: str = Field(..., description="实体名称")
    x: int = Field(..., description="X 坐标")
    y: int = Field(..., description="Y 坐标")
    move_points: int = Field(default=6, ge=0, le=64, description="本回合移动力")
    attack_range: int = Field(default=1, ge=0, le=128, description="攻击射程，按曼哈顿距离计算")
    faction: str = Field(default="neutral", description="阵营")
    blocks_move: bool = Field(default=True, description="该实体是否阻挡移动")
    tags: Dict[str, Any] = Field(default_factory=dict, description="额外标签")


class SpatialTools:
    def __init__(self, repository: JsonGameRepository, session_id: str, actor: Optional[Dict[str, str]] = None):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def create_grid(
        self,
        width: int = 12,
        height: int = 12,
        cells: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """创建或重置当前战棋地图，并进入战棋模式。"""
        width = max(2, min(64, int(width)))
        height = max(2, min(64, int(height)))
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "create_grid")
        if gate:
            self._audit("create_grid", {"width": width, "height": height, "cells": cells or []}, gate)
            return gate
        grid = GridState.empty(width, height)
        for item in cells or []:
            cell = Cell.from_dict(item)
            if grid.in_bounds(Point(cell.x, cell.y)):
                grid.cells[(cell.x, cell.y)] = cell
        session.mode = GameMode.TACTICAL
        session.battle = {
            "active": True,
            "grid": grid.to_dict(),
            "turn_entity_id": "",
            "turn": {
                "active": False,
                "round": 0,
                "phase": "idle",
                "turn_order": [],
                "current_index": -1,
                "current_entity_id": "",
                "output_limit_chars": 720,
                "auto_policy": "defend_or_follow",
                "timeout_seconds": 120,
                "actions_this_round": {},
                "turn_log": [],
            },
        }
        self.repository.save_session(session)
        result = {"ok": True, "grid": grid.to_dict()}
        self._audit("create_grid", {"width": width, "height": height, "cells": cells or []}, result)
        return result

    async def place_entity(
        self,
        entity_id: str,
        name: str,
        x: int,
        y: int,
        move_points: int = 6,
        attack_range: int = 1,
        faction: str = "neutral",
        blocks_move: bool = True,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """在当前战棋地图上放置实体。"""
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "place_entity")
        if gate:
            self._audit("place_entity", locals_without_self(locals()), gate)
            return gate
        session, grid = self._load_grid()
        engine = SpatialEngine(grid)
        result = engine.place_entity(
            Entity(
                id=entity_id,
                name=name,
                x=x,
                y=y,
                move_points=move_points,
                attack_range=attack_range,
                faction=faction,
                blocks_move=blocks_move,
                tags=tags or {},
            )
        )
        if result.get("ok"):
            session.battle["grid"] = grid.to_dict()
            self.repository.save_session(session)
        self._audit("place_entity", locals_without_self(locals()), result)
        return result

    async def move_entity(self, entity_id: str, target_x: int, target_y: int) -> Dict[str, Any]:
        """移动实体。底层会严格校验边界、障碍、占位、路径和移动力。"""
        session, grid = self._load_grid()
        turn_error = self._validate_turn_actor(session, entity_id)
        if turn_error:
            self._audit("move_entity", {"entity_id": entity_id, "target_x": target_x, "target_y": target_y}, turn_error)
            return turn_error
        engine = SpatialEngine(grid)
        result = engine.move_entity(entity_id, int(target_x), int(target_y))
        if result.get("ok"):
            session.battle["grid"] = grid.to_dict()
            self.repository.save_session(session)
        self._audit("move_entity", {"entity_id": entity_id, "target_x": target_x, "target_y": target_y}, result)
        return result

    async def check_attack_vector(self, source_id: str, target_id: str) -> Dict[str, Any]:
        """检查攻击是否合法，包括距离、视线和遮挡；不会直接造成伤害。"""
        session, grid = self._load_grid()
        turn_error = self._validate_turn_actor(session, source_id)
        if turn_error:
            self._audit("check_attack_vector", {"source_id": source_id, "target_id": target_id}, turn_error)
            return turn_error
        result = SpatialEngine(grid).check_attack_vector(source_id, target_id)
        self._audit("check_attack_vector", {"source_id": source_id, "target_id": target_id}, result)
        return result

    async def get_battle_snapshot(self) -> Dict[str, Any]:
        """获取当前战棋地图的结构化快照。"""
        session, grid = self._load_grid()
        return {"ok": True, "battle": session.battle, "grid": grid.to_dict()}

    def _load_grid(self) -> Tuple[Any, GridState]:
        session = self.repository.load_session(self.session_id)
        battle = session.battle or {}
        grid_data = battle.get("grid") or {"width": 12, "height": 12, "cells": [], "entities": {}}
        grid = GridState.from_dict(grid_data)
        if not session.battle:
            session.battle = {"active": True, "grid": grid.to_dict(), "turn_entity_id": ""}
        return session, grid

    def _validate_turn_actor(self, session: Any, entity_id: str) -> Optional[Dict[str, Any]]:
        turn = dict((session.battle or {}).get("turn") or {})
        if not turn.get("active"):
            return None
        phase = str(turn.get("phase", ""))
        current = str(turn.get("current_entity_id", "") or (session.battle or {}).get("turn_entity_id", ""))
        if phase == "character_turn":
            actions = dict(turn.get("actions_this_round") or {})
            if entity_id in actions:
                return {
                    "ok": False,
                    "error_code": "entity_already_acted_this_round",
                    "message": "该角色本轮已经行动过；不能再次移动、攻击或选择目标。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "phase": phase,
                }
            order = _clean_order(list(turn.get("turn_order") or []))
            if order and entity_id not in order:
                return {
                    "ok": False,
                    "error_code": "entity_not_in_turn_order",
                    "message": "该实体不在本轮行动列表里；不能在当前轮次操作。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "phase": phase,
                }
            owner_id = self._owner_player_id(session, entity_id)
            requester_id = str(self.actor.get("player_id", "") or "")
            if owner_id and requester_id != owner_id:
                return {
                    "ok": False,
                    "error_code": "character_control_denied",
                    "message": "该角色属于其他玩家；非持有人不能替他移动、攻击或选择目标。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "owner_player_id": owner_id,
                    "requester_player_id": requester_id,
                    "phase": phase,
                }
            if not owner_id and current and entity_id != current:
                return {
                    "ok": False,
                    "error_code": "wrong_turn_actor",
                    "message": "无持有人的单位仍按当前指针行动；不能乱序操作 NPC 或敌方单位。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "phase": phase,
                }
        return None

    @staticmethod
    def _owner_player_id(session: Any, entity_id: str) -> str:
        entities = dict(((session.battle or {}).get("grid") or {}).get("entities", {}))
        grid_entity = dict(entities.get(entity_id, {}))
        tags = dict(grid_entity.get("tags", {}))
        if tags.get("player_id"):
            return str(tags["player_id"])
        character_id = str(tags.get("character_id", "") or entity_id)
        character = getattr(session, "characters", {}).get(character_id)
        if character and getattr(character, "player_id", ""):
            return str(character.player_id)
        for player_id, bound_id in getattr(session, "player_character_map", {}).items():
            if bound_id == character_id or bound_id == entity_id:
                return str(player_id)
        return ""

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": tool, "input": input_payload, "result": result},
        )


def locals_without_self(values: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if key not in {"self", "session", "grid", "engine", "result"}}


def _clean_order(order: List[str]) -> List[str]:
    cleaned: List[str] = []
    seen = set()
    for item in order:
        value = str(item).strip()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    return cleaned
