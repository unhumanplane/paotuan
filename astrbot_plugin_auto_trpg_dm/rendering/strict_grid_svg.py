from __future__ import annotations

from dataclasses import dataclass, field
import xml.etree.ElementTree as ET


SVG_NS = "http://www.w3.org/2000/svg"
FONT_FAMILY = "Arial, sans-serif"
VALID_DOOR_SIDES = {"north", "east", "south", "west"}
TERRAIN_FILLS = {
    "normal": "#f8fafc",
    "stone": "#e2e8f0",
    "floor": "#f1f5f9",
    "dirt": "#d8c7a3",
    "grass": "#bbf7d0",
    "water": "#bfdbfe",
    "mud": "#c4a484",
    "wall": "#94a3b8",
}
FACTION_FILLS = {
    "ally": "#2563eb",
    "enemy": "#dc2626",
    "neutral": "#475569",
    "npc": "#7c3aed",
}


@dataclass(frozen=True)
class GridRuleScale:
    distance_per_cell: int | float = 5
    unit: str = "ft"
    label: str = ""

    def legend_label(self) -> str:
        if self.label:
            return _safe_text(self.label, 48)
        distance = _format_number(self.distance_per_cell)
        unit = _safe_text(self.unit or "units", 12)
        return f"{distance} {unit} per cell"


@dataclass(frozen=True)
class StrictGridLayout:
    margin: int = 24
    header_height: int = 56
    legend_height: int = 128
    cell_size: int = 48


@dataclass(frozen=True)
class StrictGridCanvas:
    width: int
    height: int
    grid_x: int
    grid_y: int
    grid_width_px: int
    grid_height_px: int
    legend_y: int


@dataclass(frozen=True)
class GridCellRender:
    x: int
    y: int
    terrain: str = "normal"
    blocks_move: bool = False
    blocks_los: bool = False
    cover: int | str = 0
    discovered: bool = True
    visible: bool = True


@dataclass(frozen=True)
class GridEntityRender:
    id: str
    name: str
    x: int
    y: int
    faction: str = "neutral"
    visible: bool = True


@dataclass(frozen=True)
class GridDoorRender:
    id: str
    x: int
    y: int
    side: str
    state: str = "closed"
    blocks_move: bool = False
    blocks_los: bool = False
    visible: bool = True


@dataclass(frozen=True)
class GridHazardRender:
    id: str
    x: int
    y: int
    kind: str = "hazard"
    severity: str = ""
    visible: bool = True


@dataclass(frozen=True)
class GridObstacleRender:
    id: str
    x: int
    y: int
    kind: str = "obstacle"
    blocks_move: bool = True
    blocks_los: bool = False
    visible: bool = True


@dataclass(frozen=True)
class GridLabelRender:
    id: str
    x: int
    y: int
    text: str
    visible: bool = True


@dataclass(frozen=True)
class StrictGridRenderInput:
    map_id: str
    title: str
    width: int
    height: int
    rule_scale: GridRuleScale = field(default_factory=GridRuleScale)
    layout: StrictGridLayout = field(default_factory=StrictGridLayout)
    cells: tuple[GridCellRender, ...] = ()
    entities: tuple[GridEntityRender, ...] = ()
    doors: tuple[GridDoorRender, ...] = ()
    hazards: tuple[GridHazardRender, ...] = ()
    obstacles: tuple[GridObstacleRender, ...] = ()
    labels: tuple[GridLabelRender, ...] = ()


def calculate_strict_grid_canvas(grid: StrictGridRenderInput) -> StrictGridCanvas:
    _validate_grid_input(grid)
    layout = grid.layout
    grid_width_px = grid.width * layout.cell_size
    grid_height_px = grid.height * layout.cell_size
    canvas_width = layout.margin * 2 + grid_width_px
    canvas_height = layout.margin * 2 + layout.header_height + grid_height_px + layout.legend_height
    return StrictGridCanvas(
        width=canvas_width,
        height=canvas_height,
        grid_x=layout.margin,
        grid_y=layout.margin + layout.header_height,
        grid_width_px=grid_width_px,
        grid_height_px=grid_height_px,
        legend_y=layout.margin + layout.header_height + grid_height_px + 18,
    )


def render_strict_grid_svg(grid: StrictGridRenderInput) -> str:
    canvas = calculate_strict_grid_canvas(grid)
    root = ET.Element(
        "svg",
        {
            "xmlns": SVG_NS,
            "width": str(canvas.width),
            "height": str(canvas.height),
            "viewBox": f"0 0 {canvas.width} {canvas.height}",
            "role": "img",
        },
    )
    title_el = ET.SubElement(root, "title")
    title_el.text = _safe_text(grid.title or "Strict grid map", 80)
    _draw_background(root, canvas)
    _draw_header(root, grid, canvas)
    _draw_cells(root, grid, canvas)
    _draw_doors(root, grid, canvas)
    _draw_hazards(root, grid, canvas)
    _draw_obstacles(root, grid, canvas)
    _draw_grid_lines(root, grid, canvas)
    _draw_entities(root, grid, canvas)
    _draw_labels(root, grid, canvas)
    _draw_legend(root, grid, canvas)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def _draw_background(root: ET.Element, canvas: StrictGridCanvas) -> None:
    ET.SubElement(
        root,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(canvas.width),
            "height": str(canvas.height),
            "fill": "#f8fafc",
        },
    )


def _draw_header(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    ET.SubElement(
        root,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(canvas.width),
            "height": str(grid.layout.margin + grid.layout.header_height - 8),
            "fill": "#e2e8f0",
        },
    )
    title = _safe_text(grid.title or grid.map_id or "Strict grid map", 48)
    _text(
        root,
        x=grid.layout.margin,
        y=grid.layout.margin + 22,
        text=title,
        size=22,
        weight="700",
        fill="#0f172a",
    )
    subtitle = f"{grid.width} x {grid.height} cells"
    _text(
        root,
        x=grid.layout.margin,
        y=grid.layout.margin + 46,
        text=subtitle,
        size=13,
        fill="#334155",
    )


def _draw_cells(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    explicit = {(cell.x, cell.y): cell for cell in grid.cells if cell.visible}
    for y in range(grid.height):
        for x in range(grid.width):
            cell = explicit.get((x, y), GridCellRender(x=x, y=y))
            px, py = _cell_origin(canvas, grid.layout.cell_size, x, y)
            fill = "#cbd5e1" if not cell.discovered else TERRAIN_FILLS.get(cell.terrain, "#f1f5f9")
            ET.SubElement(
                root,
                "rect",
                {
                    "x": str(px),
                    "y": str(py),
                    "width": str(grid.layout.cell_size),
                    "height": str(grid.layout.cell_size),
                    "fill": fill,
                    "stroke": "none",
                },
            )
            if cell.blocks_move:
                ET.SubElement(
                    root,
                    "rect",
                    {
                        "x": str(px + 4),
                        "y": str(py + 4),
                        "width": str(max(0, grid.layout.cell_size - 8)),
                        "height": str(max(0, grid.layout.cell_size - 8)),
                        "fill": "#334155",
                        "opacity": "0.20",
                    },
                )
            if cell.blocks_los:
                _draw_cell_cross(root, px, py, grid.layout.cell_size, stroke="#0f172a", width=2)
            if str(cell.cover or "0") not in {"", "0", "none", "False"}:
                _draw_cover_marker(root, px, py, grid.layout.cell_size, cell.cover)


def _draw_grid_lines(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    stroke = "#64748b"
    for x in range(grid.width + 1):
        px = canvas.grid_x + x * grid.layout.cell_size
        ET.SubElement(
            root,
            "line",
            {
                "x1": str(px),
                "y1": str(canvas.grid_y),
                "x2": str(px),
                "y2": str(canvas.grid_y + canvas.grid_height_px),
                "stroke": stroke,
                "stroke-width": "1",
            },
        )
    _draw_coordinate_labels(root, grid, canvas)
    for y in range(grid.height + 1):
        py = canvas.grid_y + y * grid.layout.cell_size
        ET.SubElement(
            root,
            "line",
            {
                "x1": str(canvas.grid_x),
                "y1": str(py),
                "x2": str(canvas.grid_x + canvas.grid_width_px),
                "y2": str(py),
                "stroke": stroke,
                "stroke-width": "1",
            },
        )


def _draw_doors(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for door in sorted((item for item in grid.doors if item.visible), key=lambda item: (item.y, item.x, item.id)):
        _validate_point("door", grid, door.x, door.y)
        side = door.side.lower()
        if side not in VALID_DOOR_SIDES:
            raise ValueError(f"invalid_door_side:{side}")
        x1, y1, x2, y2 = _door_line(canvas, grid.layout.cell_size, door.x, door.y, side)
        color = "#16a34a" if door.state == "open" else "#92400e"
        dash = "5 4" if door.state == "open" else ""
        attrs = {
            "x1": str(x1),
            "y1": str(y1),
            "x2": str(x2),
            "y2": str(y2),
            "stroke": color,
            "stroke-width": "5",
            "stroke-linecap": "round",
        }
        if dash:
            attrs["stroke-dasharray"] = dash
        ET.SubElement(root, "line", attrs)
        if door.blocks_los:
            ET.SubElement(
                root,
                "line",
                {
                    "x1": str(x1),
                    "y1": str(y1),
                    "x2": str(x2),
                    "y2": str(y2),
                    "stroke": "#111827",
                    "stroke-width": "1",
                },
            )


def _draw_hazards(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for hazard in sorted((item for item in grid.hazards if item.visible), key=lambda item: (item.y, item.x, item.id)):
        _validate_point("hazard", grid, hazard.x, hazard.y)
        cx, cy = _cell_center(canvas, grid.layout.cell_size, hazard.x, hazard.y)
        radius = max(8, grid.layout.cell_size // 4)
        points = [
            (cx, cy - radius),
            (cx + radius, cy),
            (cx, cy + radius),
            (cx - radius, cy),
        ]
        ET.SubElement(
            root,
            "polygon",
            {
                "points": _points(points),
                "fill": "#f97316",
                "stroke": "#9a3412",
                "stroke-width": "2",
                "opacity": "0.86",
            },
        )
        label = _safe_text(hazard.kind or hazard.severity or "hazard", 10)
        _text(root, x=cx, y=cy + 4, text=label[:1].upper(), size=12, weight="700", fill="#431407", anchor="middle")


def _draw_obstacles(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for obstacle in sorted((item for item in grid.obstacles if item.visible), key=lambda item: (item.y, item.x, item.id)):
        _validate_point("obstacle", grid, obstacle.x, obstacle.y)
        px, py = _cell_origin(canvas, grid.layout.cell_size, obstacle.x, obstacle.y)
        inset = max(7, grid.layout.cell_size // 5)
        fill = "#475569" if obstacle.blocks_los else "#64748b"
        ET.SubElement(
            root,
            "rect",
            {
                "x": str(px + inset),
                "y": str(py + inset),
                "width": str(grid.layout.cell_size - inset * 2),
                "height": str(grid.layout.cell_size - inset * 2),
                "fill": fill,
                "stroke": "#1e293b",
                "stroke-width": "2",
                "opacity": "0.82",
            },
        )


def _draw_entities(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for entity in sorted((item for item in grid.entities if item.visible), key=lambda item: (item.y, item.x, item.id)):
        _validate_point("entity", grid, entity.x, entity.y)
        cx, cy = _cell_center(canvas, grid.layout.cell_size, entity.x, entity.y)
        radius = max(10, grid.layout.cell_size // 3)
        fill = FACTION_FILLS.get(entity.faction, FACTION_FILLS["neutral"])
        ET.SubElement(
            root,
            "circle",
            {
                "cx": str(cx),
                "cy": str(cy),
                "r": str(radius),
                "fill": fill,
                "stroke": "#ffffff",
                "stroke-width": "3",
            },
        )
        label = _token_label(entity)
        _text(root, x=cx, y=cy + 4, text=label, size=12, weight="700", fill="#ffffff", anchor="middle")


def _draw_labels(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for label in sorted((item for item in grid.labels if item.visible), key=lambda item: (item.y, item.x, item.id)):
        _validate_point("label", grid, label.x, label.y)
        px, py = _cell_origin(canvas, grid.layout.cell_size, label.x, label.y)
        _text(
            root,
            x=px + 5,
            y=py + grid.layout.cell_size - 6,
            text=_safe_text(label.text, 18),
            size=10,
            fill="#0f172a",
        )


def _draw_legend(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    y = canvas.legend_y
    ET.SubElement(
        root,
        "rect",
        {
            "x": str(grid.layout.margin),
            "y": str(y - 14),
            "width": str(canvas.grid_width_px),
            "height": str(grid.layout.legend_height - 12),
            "fill": "#ffffff",
            "stroke": "#cbd5e1",
            "stroke-width": "1",
        },
    )
    _text(
        root,
        x=grid.layout.margin + 12,
        y=y + 8,
        text=f"Scale: {grid.rule_scale.legend_label()}",
        size=13,
        weight="700",
        fill="#0f172a",
    )
    legend_items = [
        ("terrain", "#e2e8f0"),
        ("movement block", "#334155"),
        ("LOS block", "#0f172a"),
        ("hazard", "#f97316"),
        ("token", "#2563eb"),
    ]
    x = grid.layout.margin + 12
    item_y = y + 28
    for label, fill in legend_items:
        ET.SubElement(
            root,
            "rect",
            {
                "x": str(x),
                "y": str(item_y - 10),
                "width": "12",
                "height": "12",
                "fill": fill,
                "opacity": "0.86",
            },
        )
        _text(root, x=x + 16, y=item_y, text=label, size=10, fill="#334155")
        x += 104
    _draw_entity_roster(root, grid, canvas, y + 52)


def _draw_coordinate_labels(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas) -> None:
    for x in range(grid.width):
        cx = canvas.grid_x + x * grid.layout.cell_size + grid.layout.cell_size // 2
        label = str(x)
        _text(root, x=cx, y=canvas.grid_y - 6, text=label, size=10, fill="#475569", anchor="middle")
        _text(
            root,
            x=cx,
            y=canvas.grid_y + canvas.grid_height_px + 14,
            text=label,
            size=10,
            fill="#475569",
            anchor="middle",
        )
    for y in range(grid.height):
        cy = canvas.grid_y + y * grid.layout.cell_size + grid.layout.cell_size // 2 + 4
        label = str(y)
        _text(root, x=canvas.grid_x - 8, y=cy, text=label, size=10, fill="#475569", anchor="end")
        _text(
            root,
            x=canvas.grid_x + canvas.grid_width_px + 8,
            y=cy,
            text=label,
            size=10,
            fill="#475569",
        )


def _draw_entity_roster(root: ET.Element, grid: StrictGridRenderInput, canvas: StrictGridCanvas, y: int) -> None:
    entities = tuple(item for item in grid.entities if item.visible)
    if not entities:
        _text(root, x=grid.layout.margin + 12, y=y, text="Entities: none visible", size=11, fill="#334155")
        return
    _text(root, x=grid.layout.margin + 12, y=y, text="Entities", size=11, weight="700", fill="#0f172a")
    x = grid.layout.margin + 78
    line_y = y
    max_x = grid.layout.margin + canvas.grid_width_px - 120
    for entity in sorted(entities, key=lambda item: (item.faction, item.y, item.x, item.id))[:10]:
        fill = FACTION_FILLS.get(entity.faction, FACTION_FILLS["neutral"])
        label = f"{_token_label(entity)}={_safe_text(entity.name or entity.id, 18)}({entity.x},{entity.y})"
        width = max(96, min(184, 8 * len(label) + 18))
        if x + width > max_x and x > grid.layout.margin + 78:
            x = grid.layout.margin + 78
            line_y += 18
        ET.SubElement(
            root,
            "rect",
            {
                "x": str(x),
                "y": str(line_y - 11),
                "width": "9",
                "height": "9",
                "rx": "2",
                "fill": fill,
            },
        )
        _text(root, x=x + 13, y=line_y - 2, text=label, size=10, fill="#334155")
        x += width


def _draw_cell_cross(root: ET.Element, px: int, py: int, size: int, *, stroke: str, width: int) -> None:
    padding = max(5, size // 6)
    for x1, y1, x2, y2 in (
        (px + padding, py + padding, px + size - padding, py + size - padding),
        (px + size - padding, py + padding, px + padding, py + size - padding),
    ):
        ET.SubElement(
            root,
            "line",
            {
                "x1": str(x1),
                "y1": str(y1),
                "x2": str(x2),
                "y2": str(y2),
                "stroke": stroke,
                "stroke-width": str(width),
                "opacity": "0.78",
            },
        )


def _draw_cover_marker(root: ET.Element, px: int, py: int, size: int, cover: int | str) -> None:
    marker_size = max(12, size // 4)
    x = px + size - marker_size - 3
    y = py + size - marker_size - 3
    ET.SubElement(
        root,
        "rect",
        {
            "x": str(x),
            "y": str(y),
            "width": str(marker_size),
            "height": str(marker_size),
            "fill": "#facc15",
            "stroke": "#a16207",
            "stroke-width": "1",
        },
    )
    _text(
        root,
        x=x + marker_size // 2,
        y=y + marker_size // 2 + 4,
        text=_safe_text(str(cover), 3),
        size=9,
        weight="700",
        fill="#422006",
        anchor="middle",
    )


def _text(
    root: ET.Element,
    *,
    x: int,
    y: int,
    text: str,
    size: int,
    fill: str,
    weight: str = "400",
    anchor: str = "start",
) -> None:
    element = ET.SubElement(
        root,
        "text",
        {
            "x": str(x),
            "y": str(y),
            "font-family": FONT_FAMILY,
            "font-size": str(size),
            "font-weight": weight,
            "fill": fill,
            "text-anchor": anchor,
        },
    )
    element.text = _safe_text(text, 80)


def _cell_origin(canvas: StrictGridCanvas, cell_size: int, x: int, y: int) -> tuple[int, int]:
    return canvas.grid_x + x * cell_size, canvas.grid_y + y * cell_size


def _cell_center(canvas: StrictGridCanvas, cell_size: int, x: int, y: int) -> tuple[int, int]:
    px, py = _cell_origin(canvas, cell_size, x, y)
    return px + cell_size // 2, py + cell_size // 2


def _door_line(canvas: StrictGridCanvas, cell_size: int, x: int, y: int, side: str) -> tuple[int, int, int, int]:
    px, py = _cell_origin(canvas, cell_size, x, y)
    pad = max(7, cell_size // 5)
    if side == "north":
        return px + pad, py, px + cell_size - pad, py
    if side == "south":
        return px + pad, py + cell_size, px + cell_size - pad, py + cell_size
    if side == "west":
        return px, py + pad, px, py + cell_size - pad
    return px + cell_size, py + pad, px + cell_size, py + cell_size - pad


def _points(points: list[tuple[int, int]]) -> str:
    return " ".join(f"{x},{y}" for x, y in points)


def _token_label(entity: GridEntityRender) -> str:
    name = _safe_text(entity.name or entity.id or "?", 16)
    words = [part for part in name.replace("_", " ").split(" ") if part]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:3].upper()
    return "".join(part[0] for part in words[:3]).upper()


def _safe_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "..."
    return text


def _format_number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _validate_grid_input(grid: StrictGridRenderInput) -> None:
    if grid.width <= 0 or grid.height <= 0:
        raise ValueError("strict_grid_dimensions_invalid")
    if grid.layout.cell_size <= 0:
        raise ValueError("strict_grid_cell_size_invalid")
    if grid.layout.margin < 0 or grid.layout.header_height < 0 or grid.layout.legend_height < 0:
        raise ValueError("strict_grid_layout_invalid")
    for cell in grid.cells:
        _validate_point("cell", grid, cell.x, cell.y)
    for entity in grid.entities:
        _validate_point("entity", grid, entity.x, entity.y)
    for hazard in grid.hazards:
        _validate_point("hazard", grid, hazard.x, hazard.y)
    for obstacle in grid.obstacles:
        _validate_point("obstacle", grid, obstacle.x, obstacle.y)
    for label in grid.labels:
        _validate_point("label", grid, label.x, label.y)


def _validate_point(kind: str, grid: StrictGridRenderInput, x: int, y: int) -> None:
    if not isinstance(x, int) or not isinstance(y, int):
        raise ValueError(f"{kind}_coordinate_not_integer")
    if x < 0 or y < 0 or x >= grid.width or y >= grid.height:
        raise ValueError(f"{kind}_coordinate_out_of_bounds")
