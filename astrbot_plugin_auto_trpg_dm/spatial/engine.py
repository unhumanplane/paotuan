from __future__ import annotations

from dataclasses import asdict

from .grid import Entity, GridState, Point
from .los import check_line_of_sight


class SpatialEngine:
    def __init__(self, grid: GridState):
        self.grid = grid

    def move_entity(self, entity_id: str, target_x: int, target_y: int) -> dict:
        entity = self.grid.entities.get(entity_id)
        if not entity:
            return {"ok": False, "error_code": "entity_not_found", "entity_id": entity_id}
        target = Point(target_x, target_y)
        path, cost, reason = self.grid.find_path(entity, target)
        if path is None:
            suggestions = self._reachable_suggestions(entity, target)
            return {
                "ok": False,
                "error_code": reason,
                "message": f"移动失败：{reason}",
                "facts": {
                    "entity_id": entity_id,
                    "from": {"x": entity.x, "y": entity.y},
                    "target": {"x": target_x, "y": target_y},
                    "move_points": entity.move_points,
                },
                "suggestions": suggestions,
            }
        entity.x = target_x
        entity.y = target_y
        return {
            "ok": True,
            "entity_id": entity_id,
            "from": {"x": path[0].x, "y": path[0].y},
            "to": {"x": target_x, "y": target_y},
            "path": [{"x": point.x, "y": point.y} for point in path],
            "cost": cost,
            "remaining_move_points": max(0, entity.move_points - cost),
        }

    def check_attack_vector(self, source_id: str, target_id: str) -> dict:
        source = self.grid.entities.get(source_id)
        target = self.grid.entities.get(target_id)
        if not source:
            return {"ok": False, "error_code": "source_not_found", "source_id": source_id}
        if not target:
            return {"ok": False, "error_code": "target_not_found", "target_id": target_id}
        source_point = Point(source.x, source.y)
        target_point = Point(target.x, target.y)
        distance = abs(source.x - target.x) + abs(source.y - target.y)
        los = check_line_of_sight(self.grid, source_point, target_point)
        in_range = distance <= source.attack_range
        can_attack = in_range and los["los_clear"]
        reason = "ok"
        if not in_range:
            reason = "out_of_range"
        elif not los["los_clear"]:
            reason = "line_of_sight_blocked"
        return {
            "ok": True,
            "can_attack": can_attack,
            "reason": reason,
            "source": asdict(source),
            "target": asdict(target),
            "distance": distance,
            "range": source.attack_range,
            **los,
        }

    def place_entity(self, entity: Entity) -> dict:
        point = Point(entity.x, entity.y)
        ok, reason = self.grid.is_passable(point, moving_entity_id=entity.id)
        if not ok:
            return {"ok": False, "error_code": reason, "entity": asdict(entity)}
        self.grid.entities[entity.id] = entity
        return {"ok": True, "entity": asdict(entity)}

    def _reachable_suggestions(self, entity: Entity, target: Point) -> list[dict]:
        candidates: list[tuple[int, Point]] = []
        for x in range(self.grid.width):
            for y in range(self.grid.height):
                point = Point(x, y)
                path, cost, reason = self.grid.find_path(entity, point)
                if path is None:
                    continue
                distance_to_target = abs(point.x - target.x) + abs(point.y - target.y)
                candidates.append((distance_to_target, point))
        suggestions = []
        for _, point in sorted(candidates, key=lambda item: item[0])[:3]:
            suggestions.append(
                {
                    "x": point.x,
                    "y": point.y,
                    "reason": "closest_reachable",
                }
            )
        return suggestions

