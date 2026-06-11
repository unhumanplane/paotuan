from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from ..core.control_authority import owner_player_id_for_entity, resolve_control_authority
from ..core.map_delivery_cadence import (
    MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
    MAP_RENDER_STRICT_GRID,
    MapDeliveryRequest,
    decide_map_delivery,
    get_map_delivery_cadence_state,
)
from ..core.map_core import (
    DEFAULT_STRICT_LOCAL_MAP_ID,
    MAP_AUTHORITY_SPATIAL,
    MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED,
    get_strict_map_lifecycle,
    load_active_strict_grid,
    load_active_strict_grid_entities,
    migrate_legacy_battle_grid,
    save_active_strict_grid,
)
from ..core.models import GameMode
from ..spatial.entity_state import ACTIVE_STATE, CORPSE_STATE, entity_life_state
from ..spatial.engine import SpatialEngine
from ..spatial.grid import Cell, Entity, GridState, Point
from ..spatial.map_calculator import MapCalculator
from ..storage.json_repository import JsonGameRepository
from .memory_tools import background_required_result
from .strict_grid_render_tools import StrictGridRenderTools


TURN_SEQUENCE_FLEXIBLE = "flexible"
TURN_SEQUENCE_STRICT = "strict"


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


class UpdateEntityStateArgs(BaseModel):
    entity_id: str = Field(..., description="战棋地图实体 ID")
    life_state: str = Field(
        default=ACTIVE_STATE,
        description="实体生命/行动状态：active、prone、incapacitated 或 corpse",
    )
    status: str = Field(default="", description="可见状态说明，例如：头部中弹阵亡、昏迷倒地、卧倒压制")
    blocks_move: Optional[bool] = Field(
        default=None,
        description="是否继续阻挡移动；corpse 会强制为 false，其余状态未传则保留原值",
    )
    tags: Dict[str, Any] = Field(default_factory=dict, description="要合并到实体 tags 的附加状态标签")


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
        grid_data = grid.to_dict()
        strict_record = save_active_strict_grid(
            session.maps,
            grid_data,
            map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
            title="Battle grid",
            authority=MAP_AUTHORITY_SPATIAL,
            source="spatial_tool_create_grid",
            authority_assumption="spatial_tool_create_grid",
        )
        session.battle = {
            "active": True,
            "map_id": strict_record["id"],
            "grid": grid_data,
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
                "sequence_mode": TURN_SEQUENCE_FLEXIBLE,
                "timeout_seconds": 120,
                "actions_this_round": {},
                "turn_log": [],
            },
        }
        self.repository.save_session(session)
        result = {"ok": True, "grid": grid_data, "map_id": strict_record["id"]}
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
            self._save_grid(session, grid)
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
        result = MapCalculator(grid).move_entity(entity_id, int(target_x), int(target_y))
        if result.get("ok"):
            self._save_grid(session, grid)
            self.repository.save_session(session)
        if _move_result_needs_map(result):
            result = dict(result)
            result["auto_map"] = await self._enqueue_spatial_adjudication_map(
                session,
                title="Tactical map - movement judgment",
                trigger_id=_spatial_trigger_id(
                    "move",
                    entity_id,
                    str(target_x),
                    str(target_y),
                    str(result.get("error_code") or result.get("reason") or "ok"),
                ),
            )
        self._audit("move_entity", {"entity_id": entity_id, "target_x": target_x, "target_y": target_y}, result)
        return result

    async def check_attack_vector(self, source_id: str, target_id: str) -> Dict[str, Any]:
        """检查攻击是否合法，包括距离、视线和遮挡；不会直接造成伤害。"""
        session, grid = self._load_grid()
        turn_error = self._validate_turn_actor(session, source_id)
        if turn_error:
            self._audit("check_attack_vector", {"source_id": source_id, "target_id": target_id}, turn_error)
            return turn_error
        result = MapCalculator(grid).check_attack_vector(source_id, target_id)
        if _attack_vector_result_needs_map(result):
            result = dict(result)
            result["auto_map"] = await self._enqueue_spatial_adjudication_map(
                session,
                title="Tactical map - attack vector",
                trigger_id=_spatial_trigger_id(
                    "attack",
                    source_id,
                    target_id,
                    str(result.get("reason") or result.get("error_code") or "unknown"),
                ),
            )
        self._audit("check_attack_vector", {"source_id": source_id, "target_id": target_id}, result)
        return result

    async def update_entity_state(
        self,
        entity_id: str,
        life_state: str = ACTIVE_STATE,
        status: str = "",
        blocks_move: Optional[bool] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """把已裁决的实体状态写回权威 strict grid。"""
        session, grid = self._load_grid()
        audit_input = {
            "entity_id": str(entity_id),
            "life_state": str(life_state or ""),
            "status": str(status or ""),
            "blocks_move": blocks_move,
            "tags": dict(tags or {}),
        }
        entity = grid.entities.get(str(entity_id))
        if entity is None:
            result = {"ok": False, "error_code": "entity_not_found", "entity_id": str(entity_id)}
            self._audit("update_entity_state", audit_input, result)
            return result

        previous = {
            "id": entity.id,
            "name": entity.name,
            "x": entity.x,
            "y": entity.y,
            "move_points": entity.move_points,
            "attack_range": entity.attack_range,
            "faction": entity.faction,
            "blocks_move": entity.blocks_move,
            "life_state": getattr(entity, "life_state", ACTIVE_STATE),
            "tags": dict(entity.tags or {}),
        }
        incoming_tags = dict(tags or {})
        normalized_state = _normalize_life_state(life_state, status=status, tags=incoming_tags)
        merged_tags = dict(entity.tags or {})
        merged_tags.update(incoming_tags)
        merged_tags["life_state"] = normalized_state
        if status:
            merged_tags["status"] = str(status)
        entity.tags = merged_tags
        entity.life_state = normalized_state
        if normalized_state == CORPSE_STATE:
            entity.blocks_move = False
        elif blocks_move is not None:
            entity.blocks_move = bool(blocks_move)

        self._save_grid(session, grid)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "entity_id": entity.id,
            "previous": previous,
            "entity": {
                "id": entity.id,
                "name": entity.name,
                "x": entity.x,
                "y": entity.y,
                "faction": entity.faction,
                "blocks_move": entity.blocks_move,
                "life_state": entity_life_state(entity),
                "tags": dict(entity.tags or {}),
            },
            "map_id": str((session.battle or {}).get("map_id") or session.maps.get("active_strict_map_id") or ""),
        }
        result["auto_map"] = await self._enqueue_spatial_adjudication_map(
            session,
            title="Tactical map - entity state",
            trigger_id=_spatial_trigger_id("state", entity.id, entity_life_state(entity)),
        )
        self._audit("update_entity_state", audit_input, result)
        return result

    async def get_battle_snapshot(self) -> Dict[str, Any]:
        """获取当前战棋地图的 prompt-safe 状态摘要。"""
        session, grid = self._load_grid()
        battle = session.battle or {}
        loaded = load_active_strict_grid(session.maps, battle)
        source = str(loaded.get("source") or "map_store")
        map_id = str(battle.get("map_id") or session.maps.get("active_strict_map_id") or "")
        return {
            "ok": True,
            "battle_status": _safe_battle_status(battle),
            "tactical_map": _safe_tactical_map_summary(grid, map_id=map_id, source=source),
            "compatibility": {
                "legacy_mirror_present": isinstance(battle.get("grid"), dict),
                "legacy_mirror_authoritative": False,
            },
        }

    async def _enqueue_spatial_adjudication_map(
        self,
        session: Any,
        *,
        title: str,
        trigger_id: str,
    ) -> Dict[str, Any]:
        battle = dict((session.battle or {}))
        turn = dict(battle.get("turn") or {})
        combat_id = str(battle.get("combat_id") or battle.get("encounter_id") or battle.get("map_id") or "")
        loaded = load_active_strict_grid(session.maps, battle)
        map_id = str(loaded.get("map_id") or battle.get("map_id") or session.maps.get("active_strict_map_id") or "")
        record = dict(loaded.get("record") or {})
        request = MapDeliveryRequest(
            trigger=MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
            render_type=MAP_RENDER_STRICT_GRID,
            map_id=map_id,
            map_revision=str(record.get("record_version") or ""),
            trigger_id=trigger_id,
            combat_id=combat_id,
            round_number=_safe_int(turn.get("round", 0)),
        )
        preflight = decide_map_delivery(get_map_delivery_cadence_state(session.scene), request)
        if not preflight.should_send:
            return {
                "ok": True,
                "queued": False,
                "delivery_reason": preflight.reason,
                "duplicate": bool(preflight.duplicate),
                "trigger": MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
                "trigger_id": trigger_id,
                "map_id": map_id,
                "file_name": "",
            }
        try:
            rendered = await StrictGridRenderTools(self.repository, self.session_id, self.actor).render_strict_grid_svg(
                title=title,
                send_to_chat=True,
                delivery_trigger=MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
                trigger_id=trigger_id,
                combat_id=combat_id,
                round_number=_safe_int(turn.get("round", 0)),
            )
        except Exception as exc:
            return {
                "ok": False,
                "queued": False,
                "error": str(exc),
                "trigger": MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
                "trigger_id": trigger_id,
            }
        delivery = dict(rendered.get("delivery") or {}) if isinstance(rendered, dict) else {}
        return {
            "ok": bool(isinstance(rendered, dict) and rendered.get("ok")),
            "queued": bool(delivery.get("should_send")),
            "delivery_reason": str(delivery.get("reason") or ""),
            "duplicate": bool(delivery.get("duplicate")),
            "trigger": MAP_DELIVERY_TRIGGER_SPATIAL_ADJUDICATION,
            "trigger_id": trigger_id,
            "map_id": str(rendered.get("map_id") or "") if isinstance(rendered, dict) else "",
            "file_name": str(rendered.get("file_name") or "") if isinstance(rendered, dict) else "",
        }

    def _load_grid(self) -> Tuple[Any, GridState]:
        session = self.repository.load_session(self.session_id)
        battle = session.battle or {}
        loaded = load_active_strict_grid(session.maps, battle)
        if loaded.get("source") == "legacy_battle_grid":
            migrated = migrate_legacy_battle_grid(session.maps, battle)
            if migrated.get("ok") and migrated.get("map_id"):
                battle = session.battle or {}
                battle["map_id"] = migrated["map_id"]
                battle["grid"] = migrated["grid"]
                session.battle = battle
                loaded = {"ok": True, "grid": migrated["grid"]}
                self.repository.save_session(session)
        grid_data = loaded.get("grid") if loaded.get("ok") else None
        if not isinstance(grid_data, dict):
            grid_data = {"width": 12, "height": 12, "cells": [], "entities": {}}
        grid = GridState.from_dict(grid_data)
        if not session.battle:
            self._save_grid(session, grid)
        return session, grid

    def _save_grid(self, session: Any, grid: GridState) -> None:
        grid_data = grid.to_dict()
        battle = session.battle or {}
        record = save_active_strict_grid(
            session.maps,
            grid_data,
            map_id=str(battle.get("map_id") or session.maps.get("active_strict_map_id") or DEFAULT_STRICT_LOCAL_MAP_ID),
            title="Battle grid",
            authority=MAP_AUTHORITY_SPATIAL,
            authority_assumption="spatial_tool_strict_grid_write",
        )
        lifecycle = get_strict_map_lifecycle(session.maps, record["id"])
        combat_linked = bool(battle.get("active") or lifecycle.get("lifecycle") == MAP_LIFECYCLE_ACTIVE_COMBAT_LINKED)
        battle["active"] = combat_linked
        if combat_linked:
            battle["map_id"] = record["id"]
        battle["grid"] = grid_data
        battle.setdefault("turn_entity_id", "")
        session.battle = battle

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
            if _strict_sequence(turn) and current and entity_id != current:
                return {
                    "ok": False,
                    "error_code": "wrong_turn_actor",
                    "message": "严格回合制已启用：必须按当前指针行动，不能抢先移动、攻击或选择后续角色的目标。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "sequence_mode": TURN_SEQUENCE_STRICT,
                    "phase": phase,
                }
            authority = resolve_control_authority(session, entity_id, self.actor)
            owner_id = str(authority.get("owner_player_id") or "")
            requester_id = str(self.actor.get("player_id", "") or "")
            if owner_id and not authority.get("ok"):
                return {
                    "ok": False,
                    "error_code": "character_control_denied",
                    "message": "该角色属于其他玩家；非持有人不能替他移动、攻击或选择目标。",
                    "current_entity_id": current,
                    "requested_entity_id": entity_id,
                    "owner_player_id": owner_id,
                    "active_controller_id": str(authority.get("active_controller_id") or ""),
                    "controller_type": str(authority.get("controller_type") or ""),
                    "requester_player_id": requester_id,
                    "reason": str(authority.get("reason") or ""),
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
        return owner_player_id_for_entity(session, entity_id)

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


def _safe_battle_status(battle: Dict[str, Any]) -> Dict[str, Any]:
    turn = battle.get("turn")
    turn_active = bool(
        isinstance(turn, dict)
        and turn.get("active", False)
        and str(turn.get("phase", "") or "") not in {"suspended", "ended", "idle"}
    )
    status = {
        "active": bool(battle.get("active", False) or turn_active),
        "map_id": str(battle.get("map_id", "") or ""),
        "turn_entity_id": str(battle.get("turn_entity_id", "") or ""),
    }
    if isinstance(turn, dict):
        status["turn"] = {
            "active": bool(turn.get("active", False)),
            "round": _safe_int(turn.get("round", 0)),
            "phase": str(turn.get("phase", "") or ""),
            "current_entity_id": str(turn.get("current_entity_id", "") or ""),
            "turn_order": [str(item) for item in list(turn.get("turn_order") or [])[:24]],
            "sequence_mode": _turn_sequence_mode(turn),
        }
    return status


def _safe_tactical_map_summary(grid: GridState, *, map_id: str, source: str) -> Dict[str, Any]:
    entities = [
        {
            "id": entity.id,
            "name": entity.name,
            "x": entity.x,
            "y": entity.y,
            "faction": entity.faction,
            "move_points": entity.move_points,
            "attack_range": entity.attack_range,
            "blocks_move": entity.blocks_move,
            "life_state": entity_life_state(entity),
        }
        for entity in sorted(grid.entities.values(), key=lambda item: item.id)
    ]
    terrain_features = [
        {
            "x": cell.x,
            "y": cell.y,
            "terrain": cell.terrain,
            "cost": cell.cost,
            "blocks_move": cell.blocks_move,
            "blocks_los": cell.blocks_los,
            "cover": cell.cover,
        }
        for cell in sorted(grid.cells.values(), key=lambda item: (item.y, item.x))
        if _cell_has_non_default_feature(cell)
    ]
    return {
        "map_id": map_id,
        "source": source,
        "width": grid.width,
        "height": grid.height,
        "entity_count": len(entities),
        "entities": entities[:24],
        "terrain_feature_count": len(terrain_features),
        "terrain_features": terrain_features[:24],
    }


def _cell_has_non_default_feature(cell: Cell) -> bool:
    return (
        cell.terrain != "normal"
        or cell.cost != 1
        or cell.blocks_move
        or cell.blocks_los
        or cell.cover != 0
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _turn_sequence_mode(turn: Dict[str, Any]) -> str:
    return TURN_SEQUENCE_STRICT if str(turn.get("sequence_mode") or "").strip().lower() == TURN_SEQUENCE_STRICT else TURN_SEQUENCE_FLEXIBLE


def _strict_sequence(turn: Dict[str, Any]) -> bool:
    return _turn_sequence_mode(turn) == TURN_SEQUENCE_STRICT


def _move_result_needs_map(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is True:
        return True
    error_code = str(result.get("error_code") or "")
    return (
        error_code == "out_of_bounds"
        or error_code == "no_path_or_insufficient_move_points"
        or error_code.startswith("terrain_blocks_move:")
        or error_code.startswith("occupied_by:")
    )


def _attack_vector_result_needs_map(result: Dict[str, Any]) -> bool:
    if not isinstance(result, dict) or result.get("ok") is not True:
        return False
    return "can_attack" in result or bool(result.get("reason"))


def _spatial_trigger_id(*pieces: Any) -> str:
    cleaned: list[str] = []
    for piece in pieces:
        text = str(piece or "").strip()
        if not text:
            continue
        cleaned.append(text.replace("|", "_").replace("\n", " ")[:48])
    return (":".join(cleaned) or "spatial")[:150]


def _normalize_life_state(value: Any, *, status: str = "", tags: Dict[str, Any] | None = None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"active", "prone", "incapacitated", "corpse"}:
        return candidate
    inferred = entity_life_state({"life_state": candidate, "status": status, "tags": dict(tags or {})})
    return inferred if inferred in {"active", "prone", "incapacitated", "corpse"} else ACTIVE_STATE
