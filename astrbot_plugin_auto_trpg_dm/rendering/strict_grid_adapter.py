from __future__ import annotations

from typing import Any

from ..spatial.entity_state import entity_life_state
from .strict_grid_svg import (
    GridCellRender,
    GridDoorRender,
    GridEntityRender,
    GridHazardRender,
    GridLabelRender,
    GridObstacleRender,
    GridRuleScale,
    StrictGridLayout,
    StrictGridRenderInput,
)


PLAYER_SAFE_PROJECTION = "player_view"
PLAYER_SAFE_VISIBILITIES = {"", "public", "player"}


def build_strict_grid_render_input(envelope: dict[str, Any]) -> StrictGridRenderInput:
    payload = dict(envelope or {})
    projection = str(payload.get("projection") or PLAYER_SAFE_PROJECTION)
    if projection != PLAYER_SAFE_PROJECTION:
        raise ValueError("strict_grid_projection_not_player_view")
    if not _player_visible(payload):
        raise ValueError("strict_grid_envelope_not_player_visible")

    grid_payload = _grid_payload(payload.get("grid"))
    raw_width = _positive_int(grid_payload.get("width", payload.get("width")), "grid_width")
    raw_height = _positive_int(grid_payload.get("height", payload.get("height")), "grid_height")
    bounds = _visible_bounds(payload.get("visible_bounds"), raw_width, raw_height)
    min_x, min_y, max_x, max_y = bounds
    cropped_width = max_x - min_x + 1
    cropped_height = max_y - min_y + 1

    cells = _build_cells(payload, grid_payload, bounds)
    return StrictGridRenderInput(
        map_id=str(payload.get("map_id") or ""),
        title=str(payload.get("title") or payload.get("map_id") or "Strict grid map"),
        width=cropped_width,
        height=cropped_height,
        rule_scale=_rule_scale(payload, grid_payload),
        layout=_layout(payload.get("layout")),
        cells=cells,
        entities=_entities(payload, grid_payload, bounds),
        doors=_doors(payload, bounds),
        hazards=_hazards(payload, bounds),
        obstacles=_obstacles(payload, bounds),
        labels=_labels(payload, bounds),
    )


def _grid_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        grid = to_dict()
        return dict(grid) if isinstance(grid, dict) else {}
    return {}


def _build_cells(
    payload: dict[str, Any],
    grid_payload: dict[str, Any],
    bounds: tuple[int, int, int, int],
) -> tuple[GridCellRender, ...]:
    cells_by_point: dict[tuple[int, int], GridCellRender] = {}
    for item in _list_of_dicts(payload.get("cells")) or _list_of_dicts(grid_payload.get("cells")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "cell")
        if point is None:
            continue
        abs_x, abs_y, x, y = point
        cells_by_point[(abs_x, abs_y)] = GridCellRender(
            x=x,
            y=y,
            terrain=str(item.get("terrain") or "normal"),
            blocks_move=_bool(item.get("blocks_move"), False),
            blocks_los=_bool(item.get("blocks_los"), False),
            cover=item.get("cover", 0),
            discovered=_bool(item.get("discovered"), True),
            visible=True,
        )

    discovered_points = _discovered_points(payload.get("discovered_areas"), bounds)
    if discovered_points is None:
        return tuple(sorted(cells_by_point.values(), key=lambda cell: (cell.y, cell.x)))

    min_x, min_y, max_x, max_y = bounds
    result: list[GridCellRender] = []
    for abs_y in range(min_y, max_y + 1):
        for abs_x in range(min_x, max_x + 1):
            existing = cells_by_point.get((abs_x, abs_y))
            discovered = (abs_x, abs_y) in discovered_points
            if existing is None:
                result.append(
                    GridCellRender(
                        x=abs_x - min_x,
                        y=abs_y - min_y,
                        discovered=discovered,
                    )
                )
                continue
            result.append(
                GridCellRender(
                    x=existing.x,
                    y=existing.y,
                    terrain=existing.terrain,
                    blocks_move=existing.blocks_move,
                    blocks_los=existing.blocks_los,
                    cover=existing.cover,
                    discovered=discovered and existing.discovered,
                    visible=True,
                )
            )
    return tuple(result)


def _entities(
    payload: dict[str, Any],
    grid_payload: dict[str, Any],
    bounds: tuple[int, int, int, int],
) -> tuple[GridEntityRender, ...]:
    entities: list[GridEntityRender] = []
    for entity_id, item in _entity_items(payload.get("entities"), grid_payload.get("entities")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "entity")
        if point is None:
            continue
        _, _, x, y = point
        entities.append(
            GridEntityRender(
                id=str(item.get("id") or entity_id),
                name=str(item.get("name") or item.get("id") or entity_id),
                x=x,
                y=y,
                faction=str(item.get("faction") or "neutral"),
                life_state=entity_life_state(item),
                visible=True,
            )
        )
    return tuple(sorted(entities, key=lambda item: (item.y, item.x, item.id)))


def _doors(payload: dict[str, Any], bounds: tuple[int, int, int, int]) -> tuple[GridDoorRender, ...]:
    doors: list[GridDoorRender] = []
    for item in _list_of_dicts(payload.get("doors")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "door")
        if point is None:
            continue
        _, _, x, y = point
        doors.append(
            GridDoorRender(
                id=str(item.get("id") or f"door-{x}-{y}"),
                x=x,
                y=y,
                side=str(item.get("side") or "north"),
                state=str(item.get("state") or "closed"),
                blocks_move=_bool(item.get("blocks_move"), False),
                blocks_los=_bool(item.get("blocks_los"), False),
                visible=True,
            )
        )
    return tuple(sorted(doors, key=lambda item: (item.y, item.x, item.id)))


def _hazards(payload: dict[str, Any], bounds: tuple[int, int, int, int]) -> tuple[GridHazardRender, ...]:
    hazards: list[GridHazardRender] = []
    for item in _list_of_dicts(payload.get("hazards")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "hazard")
        if point is None:
            continue
        _, _, x, y = point
        hazards.append(
            GridHazardRender(
                id=str(item.get("id") or f"hazard-{x}-{y}"),
                x=x,
                y=y,
                kind=str(item.get("kind") or "hazard"),
                severity=str(item.get("severity") or ""),
                visible=True,
            )
        )
    return tuple(sorted(hazards, key=lambda item: (item.y, item.x, item.id)))


def _obstacles(payload: dict[str, Any], bounds: tuple[int, int, int, int]) -> tuple[GridObstacleRender, ...]:
    obstacles: list[GridObstacleRender] = []
    for item in _list_of_dicts(payload.get("obstacles")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "obstacle")
        if point is None:
            continue
        _, _, x, y = point
        obstacles.append(
            GridObstacleRender(
                id=str(item.get("id") or f"obstacle-{x}-{y}"),
                x=x,
                y=y,
                kind=str(item.get("kind") or "obstacle"),
                blocks_move=_bool(item.get("blocks_move"), True),
                blocks_los=_bool(item.get("blocks_los"), False),
                visible=True,
            )
        )
    return tuple(sorted(obstacles, key=lambda item: (item.y, item.x, item.id)))


def _labels(payload: dict[str, Any], bounds: tuple[int, int, int, int]) -> tuple[GridLabelRender, ...]:
    labels: list[GridLabelRender] = []
    for item in _list_of_dicts(payload.get("labels")):
        if not _player_visible(item):
            continue
        point = _translated_point(item, bounds, "label")
        if point is None:
            continue
        _, _, x, y = point
        labels.append(
            GridLabelRender(
                id=str(item.get("id") or f"label-{x}-{y}"),
                x=x,
                y=y,
                text=str(item.get("text") or item.get("label") or ""),
                visible=True,
            )
        )
    return tuple(sorted(labels, key=lambda item: (item.y, item.x, item.id)))


def _entity_items(value: Any, fallback: Any) -> list[tuple[str, dict[str, Any]]]:
    source = value if value not in (None, "", [], {}) else fallback
    if isinstance(source, dict):
        return [
            (str(entity_id), {"id": str(entity_id), **dict(item)})
            for entity_id, item in source.items()
            if isinstance(item, dict)
        ]
    if isinstance(source, list):
        return [
            (str(item.get("id") or index), dict(item))
            for index, item in enumerate(source)
            if isinstance(item, dict)
        ]
    return []


def _translated_point(
    item: dict[str, Any],
    bounds: tuple[int, int, int, int],
    kind: str,
) -> tuple[int, int, int, int] | None:
    min_x, min_y, max_x, max_y = bounds
    abs_x = _coordinate(item.get("x"), f"{kind}_x")
    abs_y = _coordinate(item.get("y"), f"{kind}_y")
    if abs_x < min_x or abs_x > max_x or abs_y < min_y or abs_y > max_y:
        return None
    return abs_x, abs_y, abs_x - min_x, abs_y - min_y


def _visible_bounds(value: Any, width: int, height: int) -> tuple[int, int, int, int]:
    if value in (None, "", [], {}):
        return 0, 0, width - 1, height - 1
    if not isinstance(value, dict):
        raise ValueError("visible_bounds_invalid")
    min_x = _coordinate(value.get("min_x", 0), "visible_bounds_min_x")
    min_y = _coordinate(value.get("min_y", 0), "visible_bounds_min_y")
    max_x = _coordinate(value.get("max_x", width - 1), "visible_bounds_max_x")
    max_y = _coordinate(value.get("max_y", height - 1), "visible_bounds_max_y")
    if min_x < 0 or min_y < 0 or max_x >= width or max_y >= height or min_x > max_x or min_y > max_y:
        raise ValueError("visible_bounds_invalid")
    return min_x, min_y, max_x, max_y


def _discovered_points(value: Any, bounds: tuple[int, int, int, int]) -> set[tuple[int, int]] | None:
    if value in (None, ""):
        return None
    items = _list_of_dicts(value)
    if not items:
        return set()
    min_x, min_y, max_x, max_y = bounds
    points: set[tuple[int, int]] = set()
    for item in items:
        if not _player_visible(item):
            continue
        if "x" in item and "y" in item:
            x = _coordinate(item.get("x"), "discovered_x")
            y = _coordinate(item.get("y"), "discovered_y")
            if min_x <= x <= max_x and min_y <= y <= max_y:
                points.add((x, y))
            continue
        rect = _visible_bounds(item, max_x + 1, max_y + 1)
        rect_min_x, rect_min_y, rect_max_x, rect_max_y = rect
        for y in range(max(min_y, rect_min_y), min(max_y, rect_max_y) + 1):
            for x in range(max(min_x, rect_min_x), min(max_x, rect_max_x) + 1):
                points.add((x, y))
    return points


def _rule_scale(payload: dict[str, Any], grid_payload: dict[str, Any]) -> GridRuleScale:
    raw = payload.get("rule_scale", grid_payload.get("rule_scale"))
    if isinstance(raw, str):
        return GridRuleScale(label=raw)
    if not isinstance(raw, dict):
        return GridRuleScale()
    distance = raw.get("distance_per_cell", raw.get("feet_per_cell", raw.get("value", 5)))
    return GridRuleScale(
        distance_per_cell=_number(distance, "rule_scale_distance_per_cell"),
        unit=str(raw.get("unit") or raw.get("units") or ("ft" if raw.get("feet_per_cell") else "ft")),
        label=str(raw.get("label") or ""),
    )


def _layout(value: Any) -> StrictGridLayout:
    if not isinstance(value, dict):
        return StrictGridLayout()
    return StrictGridLayout(
        margin=_non_negative_int(value.get("margin", 24), "layout_margin"),
        header_height=_non_negative_int(value.get("header_height", 56), "layout_header_height"),
        legend_height=_non_negative_int(value.get("legend_height", 72), "layout_legend_height"),
        cell_size=_positive_int(value.get("cell_size", 48), "layout_cell_size"),
    )


def _player_visible(item: dict[str, Any]) -> bool:
    visibility = str(item.get("visibility") or "").strip().lower()
    if visibility not in PLAYER_SAFE_VISIBILITIES:
        return False
    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    tag_visibility = str(tags.get("visibility") or "").strip().lower()
    if tag_visibility and tag_visibility not in PLAYER_SAFE_VISIBILITIES:
        return False
    return _bool(item.get("visible", tags.get("visible", True)), True)


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"false", "0", "no", "hidden"}:
        return False
    if text in {"true", "1", "yes", "visible"}:
        return True
    return default


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


def _non_negative_int(value: Any, field: str) -> int:
    number = _coordinate(value, field)
    if number < 0:
        raise ValueError(f"{field}_invalid")
    return number


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{field}_invalid")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if number <= 0:
        raise ValueError(f"{field}_invalid")
    return int(number) if number.is_integer() else number


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
