from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine import SpatialEngine
from .grid import GridState


STRICT_GRID_MAP_TYPES = {"strict", "strict_local_map"}
STRICT_GRID_RULE_SCALES = {"", "grid", "grid_5ft", "square_grid", "strict_grid"}
STRICT_GRID_OPERATIONS = {"move_entity", "check_attack_vector"}


@dataclass(frozen=True)
class MapCalculationRoute:
    operation: str
    map_type: str = "strict_local_map"
    rule_scale: str = "grid"
    strict: bool = True
    purpose: str = "combat"
    map_id: str = ""

    @classmethod
    def from_map_record(
        cls,
        record: dict[str, Any] | None,
        *,
        operation: str,
        strict: bool = True,
    ) -> "MapCalculationRoute":
        record = dict(record or {})
        return cls(
            operation=str(operation or ""),
            map_type=str(record.get("type") or "strict_local_map"),
            rule_scale=str(record.get("rule_scale") or "grid"),
            strict=bool(strict),
            purpose=str(record.get("purpose") or "combat"),
            map_id=str(record.get("id") or ""),
        )

    def for_operation(self, operation: str) -> "MapCalculationRoute":
        return MapCalculationRoute(
            operation=str(operation or ""),
            map_type=self.map_type,
            rule_scale=self.rule_scale,
            strict=self.strict,
            purpose=self.purpose,
            map_id=self.map_id,
        )


class MapCalculator:
    def __init__(self, grid: GridState, route: MapCalculationRoute | None = None):
        self.grid = grid
        self.route = route or MapCalculationRoute(operation="")

    def move_entity(self, entity_id: str, target_x: int, target_y: int) -> dict[str, Any]:
        route = self.route.for_operation("move_entity")
        engine = self._strict_grid_engine(route)
        if engine is None:
            return self._unsupported_route(route)
        return engine.move_entity(entity_id, target_x, target_y)

    def check_attack_vector(self, source_id: str, target_id: str) -> dict[str, Any]:
        route = self.route.for_operation("check_attack_vector")
        engine = self._strict_grid_engine(route)
        if engine is None:
            return self._unsupported_route(route)
        return engine.check_attack_vector(source_id, target_id)

    def _strict_grid_engine(self, route: MapCalculationRoute) -> SpatialEngine | None:
        if not route.strict:
            return None
        if route.operation not in STRICT_GRID_OPERATIONS:
            return None
        if route.map_type not in STRICT_GRID_MAP_TYPES:
            return None
        if route.rule_scale not in STRICT_GRID_RULE_SCALES:
            return None
        return SpatialEngine(self.grid)

    def _unsupported_route(self, route: MapCalculationRoute) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": "unsupported_map_calculator_route",
            "calculation": {
                "calculator": "none",
                "operation": route.operation,
                "map_type": route.map_type,
                "rule_scale": route.rule_scale,
                "strict": route.strict,
                "purpose": route.purpose,
                "map_id": route.map_id,
            },
        }
