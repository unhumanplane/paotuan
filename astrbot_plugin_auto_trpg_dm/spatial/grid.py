from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass
class Cell:
    x: int
    y: int
    terrain: str = "normal"
    cost: int = 1
    blocks_move: bool = False
    blocks_los: bool = False
    cover: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cell":
        return cls(
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            terrain=str(data.get("terrain", "normal")),
            cost=max(1, int(data.get("cost", 1))),
            blocks_move=bool(data.get("blocks_move", False)),
            blocks_los=bool(data.get("blocks_los", False)),
            cover=int(data.get("cover", 0)),
        )


@dataclass
class Entity:
    id: str
    name: str
    x: int
    y: int
    move_points: int = 6
    attack_range: int = 1
    faction: str = "neutral"
    blocks_move: bool = True
    tags: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            move_points=int(data.get("move_points", 6)),
            attack_range=int(data.get("attack_range", 1)),
            faction=str(data.get("faction", "neutral")),
            blocks_move=bool(data.get("blocks_move", True)),
            tags=dict(data.get("tags", {})),
        )


@dataclass
class GridState:
    width: int
    height: int
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)

    @classmethod
    def empty(cls, width: int = 12, height: int = 12) -> "GridState":
        return cls(width=width, height=height)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GridState":
        grid = cls(width=int(data.get("width", 12)), height=int(data.get("height", 12)))
        for item in data.get("cells", []):
            cell = Cell.from_dict(item)
            grid.cells[(cell.x, cell.y)] = cell
        for entity_id, item in dict(data.get("entities", {})).items():
            entity = Entity.from_dict({"id": entity_id, **dict(item)})
            grid.entities[entity.id] = entity
        return grid

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "cells": [asdict(cell) for cell in self.cells.values()],
            "entities": {entity_id: asdict(entity) for entity_id, entity in self.entities.items()},
        }

    def in_bounds(self, point: Point) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def cell_at(self, point: Point) -> Cell:
        return self.cells.get((point.x, point.y), Cell(point.x, point.y))

    def entity_at(self, point: Point, ignore_id: str | None = None) -> Entity | None:
        for entity in self.entities.values():
            if ignore_id and entity.id == ignore_id:
                continue
            if entity.x == point.x and entity.y == point.y:
                return entity
        return None

    def is_passable(self, point: Point, moving_entity_id: str | None = None) -> tuple[bool, str]:
        if not self.in_bounds(point):
            return False, "out_of_bounds"
        cell = self.cell_at(point)
        if cell.blocks_move:
            return False, f"terrain_blocks_move:{cell.terrain}"
        entity = self.entity_at(point, ignore_id=moving_entity_id)
        if entity and entity.blocks_move:
            return False, f"occupied_by:{entity.id}"
        return True, "ok"

    def neighbors(self, point: Point) -> list[Point]:
        candidates = [
            Point(point.x + 1, point.y),
            Point(point.x - 1, point.y),
            Point(point.x, point.y + 1),
            Point(point.x, point.y - 1),
        ]
        return [candidate for candidate in candidates if self.in_bounds(candidate)]

    def find_path(self, entity: Entity, target: Point) -> tuple[list[Point] | None, int, str]:
        start = Point(entity.x, entity.y)
        ok, reason = self.is_passable(target, moving_entity_id=entity.id)
        if not ok:
            return None, 0, reason
        frontier: deque[Point] = deque([start])
        came_from: dict[Point, Point | None] = {start: None}
        cost_so_far: dict[Point, int] = {start: 0}
        while frontier:
            current = frontier.popleft()
            if current == target:
                break
            for next_point in self.neighbors(current):
                passable, _ = self.is_passable(next_point, moving_entity_id=entity.id)
                if not passable:
                    continue
                new_cost = cost_so_far[current] + self.cell_at(next_point).cost
                if new_cost > entity.move_points:
                    continue
                if next_point not in cost_so_far or new_cost < cost_so_far[next_point]:
                    cost_so_far[next_point] = new_cost
                    came_from[next_point] = current
                    frontier.append(next_point)
        if target not in came_from:
            return None, 0, "no_path_or_insufficient_move_points"
        path: list[Point] = []
        current: Point | None = target
        while current is not None:
            path.append(current)
            current = came_from[current]
        path.reverse()
        return path, cost_so_far[target], "ok"

