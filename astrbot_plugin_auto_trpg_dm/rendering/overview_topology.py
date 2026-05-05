from __future__ import annotations

from dataclasses import dataclass, field
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
        raw_node_ids = raw_area.get("node_ids") or []
        safe_node_ids = tuple(_short_text(item, 120) for item in raw_node_ids if _short_text(item, 120) in node_ids)
        areas.append(
            OverviewArea(
                id=area_id,
                label=_short_text(raw_area.get("label") or area_id, 80),
                kind=_short_text(raw_area.get("kind") or "area", 40),
                node_ids=safe_node_ids,
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
