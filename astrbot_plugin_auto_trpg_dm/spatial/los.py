from __future__ import annotations

from .grid import GridState, Point


def bresenham_line(start: Point, end: Point) -> list[Point]:
    points: list[Point] = []
    x0, y0 = start.x, start.y
    x1, y1 = end.x, end.y
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        points.append(Point(x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return points


def check_line_of_sight(grid: GridState, start: Point, end: Point) -> dict:
    line = bresenham_line(start, end)
    blocked_by: list[dict] = []
    cover = 0
    for point in line[1:-1]:
        cell = grid.cell_at(point)
        cover = max(cover, cell.cover)
        if cell.blocks_los:
            blocked_by.append(
                {
                    "x": point.x,
                    "y": point.y,
                    "terrain": cell.terrain,
                }
            )
    return {
        "los_clear": not blocked_by,
        "line": [{"x": point.x, "y": point.y} for point in line],
        "blocked_by": blocked_by,
        "cover": cover,
    }

