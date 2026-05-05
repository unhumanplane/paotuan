from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from math import isfinite
from typing import Any

OVERVIEW_TOPOLOGY_RENDER_TYPE = "overview_topology_svg"
PENDING_SVG_MAP_TYPE = "svg_map"

PLAYER_VIEW_PROJECTION = "player_view"
PLAYER_SAFE_VISIBILITIES = {"", "public", "player"}
HIDDEN_STATUSES = {"hidden", "diagnostic", "dm"}
EDGE_STATUSES = {"", "known", "seen", "explored", "suspected", "known_but_unseen"}
BLOCKED_INPUT_KEYS = {
    "file_path",
    "grid",
    "local_path",
    "map_store",
    "path",
    "raw_map_store",
    "raw_svg",
    "svg",
    "url",
}


@dataclass(frozen=True)
class OverviewPoint:
    x: float
    y: float


@dataclass(frozen=True)
class OverviewDisplayProfile:
    width: int = 900
    height: int = 700
    padding: int = 48
    show_numeric_coordinates: bool = False


@dataclass(frozen=True)
class OverviewNode:
    id: str
    label: str
    kind: str = "place"
    area_id: str = ""
    status: str = "known"
    layout_pos: OverviewPoint | None = None
    anchor: str = ""
    order: int = 0


@dataclass(frozen=True)
class OverviewEdge:
    id: str
    source_id: str
    target_id: str
    relationship: str = "route"
    direction: str = ""
    distance_band: str = ""
    route_group: str = ""
    status: str = "known"
    landmark_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverviewArea:
    id: str
    label: str
    kind: str = "area"
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OverviewLandmark:
    id: str
    label: str
    kind: str = "landmark"
    node_id: str = ""
    edge_id: str = ""


@dataclass(frozen=True)
class OverviewTopologyRenderInput:
    render_type: str
    map_id: str
    title: str
    map_revision: str
    layout_revision: str = ""
    display: OverviewDisplayProfile = field(default_factory=OverviewDisplayProfile)
    nodes: tuple[OverviewNode, ...] = ()
    edges: tuple[OverviewEdge, ...] = ()
    areas: tuple[OverviewArea, ...] = ()
    landmarks: tuple[OverviewLandmark, ...] = ()
    current_node_id: str = ""


@dataclass(frozen=True)
class OverviewLayoutResult:
    positions: dict[str, OverviewPoint]
    reused_node_ids: tuple[str, ...]
    generated_node_ids: tuple[str, ...]
    bounds: tuple[float, float, float, float]


def build_overview_topology_render_input(envelope: dict[str, Any]) -> OverviewTopologyRenderInput:
    """Build a player-safe overview render input from a projected envelope."""
    if not isinstance(envelope, dict):
        raise ValueError("overview_envelope_invalid")
    _reject_blocked_keys(envelope)
    render_type = _short_text(envelope.get("render_type") or "", 80)
    if render_type != OVERVIEW_TOPOLOGY_RENDER_TYPE:
        raise ValueError("overview_render_type_invalid")
    projection = _short_text(envelope.get("projection") or PLAYER_VIEW_PROJECTION, 80)
    if projection != PLAYER_VIEW_PROJECTION:
        raise ValueError("overview_projection_not_player_view")
    map_id = _short_text(envelope.get("map_id") or "", 120)
    if not map_id:
        raise ValueError("overview_map_id_required")

    display = _parse_display_profile(envelope.get("display_profile") or {})
    layout_positions = _parse_layout_positions(envelope.get("layout") or {})
    nodes = _parse_nodes(envelope.get("nodes"), layout_positions)
    node_ids = {node.id for node in nodes}
    unknown_layout_nodes = sorted(set(layout_positions) - node_ids)
    if unknown_layout_nodes:
        raise ValueError(f"overview_layout_node_missing:{unknown_layout_nodes[0]}")
    edges = _parse_edges(envelope.get("edges") or [], node_ids)
    areas = _parse_areas(envelope.get("areas") or [], node_ids)
    landmarks = _parse_landmarks(envelope.get("landmarks") or [], node_ids, {edge.id for edge in edges})
    current_node_id = _short_text(envelope.get("current_node_id") or envelope.get("current_location_id") or "", 120)
    if current_node_id and current_node_id not in node_ids:
        raise ValueError(f"overview_current_node_missing:{current_node_id}")

    return OverviewTopologyRenderInput(
        render_type=render_type,
        map_id=map_id,
        title=_short_text(envelope.get("title") or map_id, 160),
        map_revision=_short_text(envelope.get("map_revision") or "", 80),
        layout_revision=_short_text(envelope.get("layout_revision") or "", 80),
        display=display,
        nodes=nodes,
        edges=edges,
        areas=areas,
        landmarks=landmarks,
        current_node_id=current_node_id,
    )


def layout_overview_topology(render_input: OverviewTopologyRenderInput) -> OverviewLayoutResult:
    """Return stable player-visible positions for overview nodes."""
    if not render_input.nodes:
        raise ValueError("overview_nodes_required")
    node_by_id = {node.id: node for node in render_input.nodes}
    adjacency: dict[str, list[str]] = {node.id: [] for node in render_input.nodes}
    for edge in render_input.edges:
        adjacency[edge.source_id].append(edge.target_id)
        adjacency[edge.target_id].append(edge.source_id)
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda node_id: _node_sort_key(node_by_id[node_id]))

    root_id = render_input.current_node_id if render_input.current_node_id in node_by_id else _stable_root(render_input.nodes)
    depths = _bfs_depths(root_id, adjacency)
    max_depth = max(depths.values(), default=0)
    layers: dict[int, list[OverviewNode]] = {}
    for node in sorted(render_input.nodes, key=_node_sort_key):
        depth = depths.get(node.id, max_depth + 1)
        layers.setdefault(depth, []).append(node)

    positions: dict[str, OverviewPoint] = {}
    reused: list[str] = []
    generated: list[str] = []
    fallback_width = max(1, render_input.display.width - render_input.display.padding * 2)
    fallback_height = max(1, render_input.display.height - render_input.display.padding * 2)
    layer_count = max(1, len(layers))
    for depth, nodes in sorted(layers.items()):
        x = render_input.display.padding + (fallback_width * depth / max(1, layer_count - 1))
        slot_count = len(nodes)
        for slot, node in enumerate(nodes):
            if node.layout_pos is not None:
                positions[node.id] = node.layout_pos
                reused.append(node.id)
                continue
            y = render_input.display.padding + (fallback_height * (slot + 1) / (slot_count + 1))
            if depth > 0:
                parent_pos = _first_parent_position(node.id, positions, adjacency, depths)
                if parent_pos is not None:
                    y = parent_pos.y + (slot - (slot_count - 1) / 2) * 82
            positions[node.id] = OverviewPoint(x=x, y=_clamp(y, render_input.display.padding, render_input.display.height - render_input.display.padding))
            generated.append(node.id)

    return OverviewLayoutResult(
        positions=positions,
        reused_node_ids=tuple(reused),
        generated_node_ids=tuple(generated),
        bounds=_visible_bounds(positions),
    )


def render_overview_topology_svg(render_input: OverviewTopologyRenderInput) -> str:
    """Render a deterministic, player-safe overview topology SVG."""
    layout = layout_overview_topology(render_input)
    width = render_input.display.width
    height = render_input.display.height
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f"<title>{escape(render_input.title)}</title>",
        '<rect width="100%" height="100%" fill="#f7f4ed"/>',
        '<g data-layer="edges">',
    ]
    for edge in sorted(render_input.edges, key=lambda item: item.id):
        source = layout.positions[edge.source_id]
        target = layout.positions[edge.target_id]
        classes = "edge"
        dash = ' stroke-dasharray="8 7"' if edge.status in {"suspected", "known_but_unseen"} else ""
        opacity = "0.48" if edge.status == "known_but_unseen" else "0.78"
        parts.append(
            f'<line class="{classes}" data-edge-id="{escape(edge.id)}" x1="{_svg_num(source.x)}" y1="{_svg_num(source.y)}" '
            f'x2="{_svg_num(target.x)}" y2="{_svg_num(target.y)}" stroke="#58616a" stroke-width="3" '
            f'stroke-linecap="round" opacity="{opacity}"{dash}/>'
        )
        label = _edge_label(edge)
        if label:
            mid_x = (source.x + target.x) / 2
            mid_y = (source.y + target.y) / 2 - 8
            parts.append(
                f'<text class="edge-label" x="{_svg_num(mid_x)}" y="{_svg_num(mid_y)}" '
                f'text-anchor="middle" font-size="13" fill="#4b535c">{escape(label)}</text>'
            )
    parts.append("</g>")
    parts.append('<g data-layer="areas">')
    for area in sorted(render_input.areas, key=lambda item: item.id):
        area_points = [layout.positions[node_id] for node_id in area.node_ids if node_id in layout.positions]
        if not area_points:
            continue
        min_x, min_y, max_x, max_y = _visible_bounds({str(index): point for index, point in enumerate(area_points)})
        parts.append(
            f'<rect class="area" data-area-id="{escape(area.id)}" x="{_svg_num(min_x - 34)}" y="{_svg_num(min_y - 28)}" '
            f'width="{_svg_num(max_x - min_x + 68)}" height="{_svg_num(max_y - min_y + 56)}" '
            f'rx="14" fill="#d7e6de" stroke="#8aa595" stroke-width="1.5" opacity="0.42"/>'
        )
        parts.append(
            f'<text class="area-label" x="{_svg_num(min_x - 24)}" y="{_svg_num(min_y - 34)}" '
            f'font-size="12" fill="#4d6659">{escape(area.label)}</text>'
        )
    parts.append("</g>")
    parts.append('<g data-layer="landmarks">')
    for landmark in sorted(render_input.landmarks, key=lambda item: item.id):
        point = _landmark_position(landmark, render_input.edges, layout.positions)
        if point is None:
            continue
        parts.append(
            f'<path class="landmark" data-landmark-id="{escape(landmark.id)}" d="M {_svg_num(point.x)} {_svg_num(point.y - 16)} '
            f'l 6 12 h -12 z" fill="#b7791f" opacity="0.9"/>'
        )
        parts.append(
            f'<text class="landmark-label" x="{_svg_num(point.x + 10)}" y="{_svg_num(point.y - 8)}" '
            f'font-size="12" fill="#79520f">{escape(landmark.label)}</text>'
        )
    parts.append("</g>")
    parts.append('<g data-layer="nodes">')
    for node in sorted(render_input.nodes, key=_node_sort_key):
        point = layout.positions[node.id]
        is_current = node.id == render_input.current_node_id
        fill = "#2f6f73" if is_current else "#ffffff"
        stroke = "#184d52" if is_current else "#52606d"
        text_fill = "#ffffff" if is_current else "#25313b"
        parts.append(
            f'<circle class="node" data-node-id="{escape(node.id)}" cx="{_svg_num(point.x)}" cy="{_svg_num(point.y)}" '
            f'r="18" fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>'
        )
        parts.append(
            f'<text class="node-label" x="{_svg_num(point.x)}" y="{_svg_num(point.y + 5)}" '
            f'text-anchor="middle" font-size="13" font-weight="600" fill="{text_fill}">{escape(_fit_label(node.label, 10))}</text>'
        )
        if node.label and len(node.label) > 10:
            parts.append(
                f'<text class="node-caption" x="{_svg_num(point.x)}" y="{_svg_num(point.y + 36)}" '
                f'text-anchor="middle" font-size="12" fill="#25313b">{escape(_fit_label(node.label, 18))}</text>'
            )
    parts.append("</g>")
    parts.append('<g data-layer="labels">')
    parts.append(
        f'<text x="{width - render_input.display.padding}" y="{height - 24}" text-anchor="end" '
        f'font-size="12" fill="#65717d">overview topology - visual only</text>'
    )
    parts.append("</g>")
    parts.append('<g data-layer="current-marker">')
    if render_input.current_node_id and render_input.current_node_id in layout.positions:
        point = layout.positions[render_input.current_node_id]
        parts.append(
            f'<circle class="current-marker" data-current-node-id="{escape(render_input.current_node_id)}" '
            f'cx="{_svg_num(point.x + 18)}" cy="{_svg_num(point.y - 18)}" r="6" '
            f'fill="#f6c453" stroke="#7a4b00" stroke-width="2"/>'
        )
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def _parse_display_profile(value: Any) -> OverviewDisplayProfile:
    data = value if isinstance(value, dict) else {}
    return OverviewDisplayProfile(
        width=_bounded_int(data.get("width"), default=900, minimum=320, maximum=1600),
        height=_bounded_int(data.get("height"), default=700, minimum=320, maximum=1600),
        padding=_bounded_int(data.get("padding"), default=48, minimum=16, maximum=160),
        show_numeric_coordinates=bool(data.get("show_numeric_coordinates", False)),
    )


def _parse_layout_positions(value: Any) -> dict[str, OverviewPoint]:
    if not isinstance(value, dict):
        return {}
    raw_positions = value.get("positions", value)
    if not isinstance(raw_positions, dict):
        return {}
    positions: dict[str, OverviewPoint] = {}
    for node_id, raw_pos in raw_positions.items():
        safe_id = _short_text(node_id, 120)
        if safe_id and isinstance(raw_pos, dict):
            positions[safe_id] = _parse_point(raw_pos, field_name=f"layout_pos:{safe_id}")
    return positions


def _parse_nodes(value: Any, layout_positions: dict[str, OverviewPoint]) -> tuple[OverviewNode, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("overview_nodes_required")
    nodes: list[OverviewNode] = []
    seen: set[str] = set()
    for index, raw_node in enumerate(value):
        if not isinstance(raw_node, dict):
            raise ValueError("overview_node_invalid")
        _reject_blocked_keys(raw_node)
        node_id = _short_text(raw_node.get("id") or "", 120)
        if not node_id:
            raise ValueError("overview_node_id_required")
        if node_id in seen:
            raise ValueError(f"overview_node_duplicate:{node_id}")
        seen.add(node_id)
        _require_player_safe_visibility(raw_node, node_id)
        status = _status(raw_node.get("status") or "known")
        if status in HIDDEN_STATUSES:
            raise ValueError(f"overview_hidden_status:{node_id}")
        layout_pos = layout_positions.get(node_id)
        if isinstance(raw_node.get("layout_pos"), dict):
            layout_pos = _parse_point(raw_node["layout_pos"], field_name=f"node_layout_pos:{node_id}")
        nodes.append(
            OverviewNode(
                id=node_id,
                label=_short_text(raw_node.get("label") or raw_node.get("title") or node_id, 80),
                kind=_short_text(raw_node.get("kind") or "place", 40),
                area_id=_short_text(raw_node.get("area_id") or "", 120),
                status=status,
                layout_pos=layout_pos,
                anchor=_short_text(raw_node.get("anchor") or "", 80),
                order=_bounded_int(raw_node.get("order"), default=index, minimum=0, maximum=100000),
            )
        )
    return tuple(nodes)


def _parse_edges(value: Any, node_ids: set[str]) -> tuple[OverviewEdge, ...]:
    if not isinstance(value, list):
        raise ValueError("overview_edges_invalid")
    edges: list[OverviewEdge] = []
    seen: set[str] = set()
    for index, raw_edge in enumerate(value):
        if not isinstance(raw_edge, dict):
            raise ValueError("overview_edge_invalid")
        _reject_blocked_keys(raw_edge)
        edge_id = _short_text(raw_edge.get("id") or f"edge-{index}", 120)
        if edge_id in seen:
            raise ValueError(f"overview_edge_duplicate:{edge_id}")
        seen.add(edge_id)
        _require_player_safe_visibility(raw_edge, edge_id)
        status = _status(raw_edge.get("status") or "known")
        if status not in EDGE_STATUSES or status in HIDDEN_STATUSES:
            raise ValueError(f"overview_edge_status_invalid:{edge_id}")
        source_id = _short_text(raw_edge.get("source_id") or raw_edge.get("source") or "", 120)
        target_id = _short_text(raw_edge.get("target_id") or raw_edge.get("target") or "", 120)
        if source_id not in node_ids or target_id not in node_ids:
            raise ValueError(f"overview_edge_endpoint_missing:{edge_id}")
        edges.append(
            OverviewEdge(
                id=edge_id,
                source_id=source_id,
                target_id=target_id,
                relationship=_short_text(raw_edge.get("relationship") or "route", 80),
                direction=_short_text(raw_edge.get("direction") or "", 80),
                distance_band=_short_text(raw_edge.get("distance_band") or raw_edge.get("rough_distance") or "", 80),
                route_group=_short_text(raw_edge.get("route_group") or "", 80),
                status=status,
                landmark_ids=tuple(_short_text(item, 120) for item in (raw_edge.get("landmark_ids") or []) if item),
            )
        )
    return tuple(edges)


def _parse_areas(value: Any, node_ids: set[str]) -> tuple[OverviewArea, ...]:
    if not isinstance(value, list):
        raise ValueError("overview_areas_invalid")
    areas: list[OverviewArea] = []
    for raw_area in value:
        if not isinstance(raw_area, dict):
            raise ValueError("overview_area_invalid")
        _reject_blocked_keys(raw_area)
        area_id = _short_text(raw_area.get("id") or "", 120)
        if not area_id:
            raise ValueError("overview_area_id_required")
        _require_player_safe_visibility(raw_area, area_id)
        status = _status(raw_area.get("status") or "")
        if status in HIDDEN_STATUSES:
            raise ValueError(f"overview_area_status_invalid:{area_id}")
        raw_node_ids = raw_area.get("node_ids") or []
        safe_node_ids = []
        for item in raw_node_ids:
            node_id = _short_text(item, 120)
            if node_id not in node_ids:
                raise ValueError(f"overview_area_node_missing:{area_id}")
            safe_node_ids.append(node_id)
        areas.append(
            OverviewArea(
                id=area_id,
                label=_short_text(raw_area.get("label") or area_id, 80),
                kind=_short_text(raw_area.get("kind") or "area", 40),
                node_ids=tuple(safe_node_ids),
            )
        )
    return tuple(areas)


def _parse_landmarks(value: Any, node_ids: set[str], edge_ids: set[str]) -> tuple[OverviewLandmark, ...]:
    if not isinstance(value, list):
        raise ValueError("overview_landmarks_invalid")
    landmarks: list[OverviewLandmark] = []
    for raw_landmark in value:
        if not isinstance(raw_landmark, dict):
            raise ValueError("overview_landmark_invalid")
        _reject_blocked_keys(raw_landmark)
        landmark_id = _short_text(raw_landmark.get("id") or "", 120)
        if not landmark_id:
            raise ValueError("overview_landmark_id_required")
        _require_player_safe_visibility(raw_landmark, landmark_id)
        status = _status(raw_landmark.get("status") or "")
        if status in HIDDEN_STATUSES:
            raise ValueError(f"overview_landmark_status_invalid:{landmark_id}")
        node_id = _short_text(raw_landmark.get("node_id") or "", 120)
        edge_id = _short_text(raw_landmark.get("edge_id") or "", 120)
        if node_id and node_id not in node_ids:
            raise ValueError(f"overview_landmark_node_missing:{landmark_id}")
        if edge_id and edge_id not in edge_ids:
            raise ValueError(f"overview_landmark_edge_missing:{landmark_id}")
        landmarks.append(
            OverviewLandmark(
                id=landmark_id,
                label=_short_text(raw_landmark.get("label") or landmark_id, 80),
                kind=_short_text(raw_landmark.get("kind") or "landmark", 40),
                node_id=node_id,
                edge_id=edge_id,
            )
        )
    return tuple(landmarks)


def _require_player_safe_visibility(value: dict[str, Any], item_id: str) -> None:
    visibility = _short_text(value.get("visibility") or "", 40).lower()
    if visibility not in PLAYER_SAFE_VISIBILITIES:
        raise ValueError(f"overview_visibility_not_player_safe:{item_id}")


def _reject_blocked_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            safe_key = str(key).strip().lower()
            if safe_key in BLOCKED_INPUT_KEYS:
                raise ValueError(f"overview_blocked_field:{safe_key}")
            _reject_blocked_keys(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_blocked_keys(item)


def _parse_point(value: dict[str, Any], *, field_name: str) -> OverviewPoint:
    try:
        x = float(value.get("x"))
        y = float(value.get("y"))
    except (TypeError, ValueError):
        raise ValueError(f"overview_point_invalid:{field_name}") from None
    if not isfinite(x) or not isfinite(y):
        raise ValueError(f"overview_point_invalid:{field_name}")
    return OverviewPoint(x=x, y=y)


def _status(value: Any) -> str:
    return _short_text(value or "", 40).strip().lower()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _bfs_depths(root_id: str, adjacency: dict[str, list[str]]) -> dict[str, int]:
    depths = {root_id: 0}
    queue = [root_id]
    for node_id in queue:
        for neighbor in adjacency.get(node_id, []):
            if neighbor not in depths:
                depths[neighbor] = depths[node_id] + 1
                queue.append(neighbor)
    return depths


def _stable_root(nodes: tuple[OverviewNode, ...]) -> str:
    return sorted(nodes, key=_node_sort_key)[0].id


def _node_sort_key(node: OverviewNode) -> tuple[int, str, str]:
    return (node.order, node.label, node.id)


def _first_parent_position(
    node_id: str,
    positions: dict[str, OverviewPoint],
    adjacency: dict[str, list[str]],
    depths: dict[str, int],
) -> OverviewPoint | None:
    node_depth = depths.get(node_id, 0)
    for neighbor in adjacency.get(node_id, []):
        if depths.get(neighbor, node_depth + 1) < node_depth and neighbor in positions:
            return positions[neighbor]
    return None


def _visible_bounds(positions: dict[str, OverviewPoint]) -> tuple[float, float, float, float]:
    xs = [point.x for point in positions.values()]
    ys = [point.y for point in positions.values()]
    return (min(xs), min(ys), max(xs), max(ys))


def _landmark_position(
    landmark: OverviewLandmark,
    edges: tuple[OverviewEdge, ...],
    positions: dict[str, OverviewPoint],
) -> OverviewPoint | None:
    if landmark.node_id:
        point = positions.get(landmark.node_id)
        return OverviewPoint(point.x + 18, point.y - 18) if point else None
    if landmark.edge_id:
        edge = next((item for item in edges if item.id == landmark.edge_id), None)
        if edge and edge.source_id in positions and edge.target_id in positions:
            source = positions[edge.source_id]
            target = positions[edge.target_id]
            return OverviewPoint((source.x + target.x) / 2, (source.y + target.y) / 2)
    return None


def _edge_label(edge: OverviewEdge) -> str:
    values = [edge.relationship, edge.direction, edge.distance_band, edge.route_group]
    return " / ".join(_fit_label(value, 18) for value in values if value)


def _fit_label(value: str, limit: int) -> str:
    text = _short_text(value, limit + 1)
    return text if len(text) <= limit else text[: max(1, limit - 3)] + "..."


def _svg_num(value: float) -> str:
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.2f}"


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
