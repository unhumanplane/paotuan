from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field

from ..core.map_core import (
    MAP_VIEW_PLAYER,
    add_render_ref,
    get_map_record,
    project_active_map_record,
    update_map_record,
)
from ..rendering.overview_topology import (
    OVERVIEW_TOPOLOGY_RENDER_TYPE,
    PENDING_SVG_MAP_TYPE,
    build_overview_topology_render_input,
    layout_overview_topology,
    render_overview_topology_svg,
)
from ..storage.json_repository import JsonGameRepository


OVERVIEW_TOPOLOGY_FACT_KINDS = {"overview_topology", "topology"}


class RenderOverviewTopologySvgArgs(BaseModel):
    map_id: str = Field(default="", description="可选 overview map id；留空时使用当前 active overview map。")
    title: str = Field(default="", description="可选 SVG 标题；留空时使用地图或 topology fact 标题。")
    send_to_chat: bool = Field(default=True, description="是否把生成的 overview SVG 排队随本轮回复发送。")
    width: int = Field(default=900, ge=320, le=1600, description="SVG 像素宽度。")
    height: int = Field(default=700, ge=320, le=1600, description="SVG 像素高度。")


class OverviewTopologyRenderTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: dict[str, str] | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def render_overview_topology_svg(
        self,
        map_id: str = "",
        title: str = "",
        send_to_chat: bool = True,
        width: int = 900,
        height: int = 700,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        projected_record = project_active_map_record(
            session.maps,
            MAP_VIEW_PLAYER,
            map_id=_short_text(map_id, 120),
            strict=False,
        )
        if not projected_record:
            result = {
                "ok": False,
                "error": "overview_map_not_found",
                "map_id": _short_text(map_id, 120),
            }
            self._audit("render_overview_topology_svg", locals_without_self(locals()), result)
            return result

        topology_fact = _select_topology_fact(projected_record.get("facts") or [])
        if not topology_fact:
            result = {
                "ok": False,
                "error": "overview_topology_missing",
                "map_id": _short_text(projected_record.get("id"), 120),
            }
            self._audit("render_overview_topology_svg", locals_without_self(locals()), result)
            return result

        source_map_id = _short_text(projected_record.get("id"), 120)
        payload = dict(topology_fact.get("payload") or {})
        cached_layout_revision = _merge_cached_layout_positions(
            session.maps,
            source_map_id,
            payload,
        )
        render_title = _render_title(
            explicit=title,
            fact_payload=payload,
            fact=topology_fact,
            record=projected_record,
        )
        envelope = {
            **payload,
            "render_type": OVERVIEW_TOPOLOGY_RENDER_TYPE,
            "projection": MAP_VIEW_PLAYER,
            "map_id": source_map_id,
            "title": render_title,
            "map_revision": _record_revision(session.maps, source_map_id),
            "layout_revision": _short_text(payload.get("layout_revision") or cached_layout_revision, 80),
            "display_profile": {
                **dict(payload.get("display_profile") or {}),
                "width": _bounded_int(width, default=900, minimum=320, maximum=1600),
                "height": _bounded_int(height, default=700, minimum=320, maximum=1600),
                "show_numeric_coordinates": False,
            },
        }
        try:
            render_input = build_overview_topology_render_input(envelope)
            layout = layout_overview_topology(render_input)
            svg = render_overview_topology_svg(render_input)
        except ValueError as exc:
            result = {
                "ok": False,
                "error": "overview_render_input_invalid",
                "map_id": _short_text(projected_record.get("id"), 120),
                "reason": str(exc),
            }
            self._audit("render_overview_topology_svg", locals_without_self(locals()), result)
            return result

        path = self._write_svg(render_title, svg)
        latest_session = self.repository.load_session(self.session_id)
        layout_updates = _persist_generated_layout_positions(
            latest_session.maps,
            render_input.map_id,
            payload,
            layout,
        )
        render_ref = add_render_ref(
            latest_session.maps,
            render_input.map_id,
            ref_type=OVERVIEW_TOPOLOGY_RENDER_TYPE,
            title=render_input.title,
            name=path.name,
            path=str(path),
            visual_only=True,
        )
        pending_output = {
            "type": PENDING_SVG_MAP_TYPE,
            "render_type": OVERVIEW_TOPOLOGY_RENDER_TYPE,
            "title": render_input.title,
            "name": path.name,
            "path": str(path),
            "width": render_input.display.width,
            "height": render_input.display.height,
            "visual_only": True,
        }
        if send_to_chat:
            pending = list(latest_session.scene.get("_pending_outputs") or [])
            pending.append(pending_output)
            latest_session.scene["_pending_outputs"] = pending[-3:]
        self.repository.save_session(latest_session)

        result = {
            "ok": True,
            "render_type": OVERVIEW_TOPOLOGY_RENDER_TYPE,
            "map_id": render_input.map_id,
            "title": render_input.title,
            "map_revision": render_input.map_revision,
            "file_path": str(path),
            "file_name": path.name,
            "svg_chars": len(svg),
            "width": render_input.display.width,
            "height": render_input.display.height,
            "send_to_chat": bool(send_to_chat),
            "visual_only": True,
            "render_ref": _json_safe(render_ref),
            "pending_output": _json_safe(pending_output) if send_to_chat else {},
            "layout_revision": layout_updates.get("layout_revision", "") or render_input.layout_revision,
            "layout_updates": _json_safe(layout_updates),
        }
        self._audit("render_overview_topology_svg", locals_without_self(locals()), result)
        return result

    def _write_svg(self, title: str, svg: str) -> Path:
        maps_dir = self.repository.maps_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = maps_dir / f"{stamp}_{_safe_file_stem(title)}.svg"
        path.write_text(svg, encoding="utf-8")
        return path

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        try:
            self.repository.append_audit(
                self.session_id,
                {
                    "type": "tool",
                    "tool": tool,
                    "actor": _json_safe(self.actor),
                    "input": _audit_input(input_payload),
                    "result": _json_safe(result),
                },
            )
        except Exception:
            return


def _select_topology_fact(facts: Any) -> dict[str, Any]:
    if not isinstance(facts, list):
        return {}
    for fact in facts:
        if isinstance(fact, dict) and str(fact.get("kind") or "").strip().lower() in OVERVIEW_TOPOLOGY_FACT_KINDS:
            payload = fact.get("payload")
            if isinstance(payload, dict):
                return fact
    return {}


def _persist_generated_layout_positions(
    store: dict[str, Any],
    map_id: str,
    payload: dict[str, Any],
    layout: Any,
) -> dict[str, Any]:
    generated_node_ids = tuple(getattr(layout, "generated_node_ids", ()) or ())
    if not generated_node_ids:
        return {}
    record = get_map_record(store, map_id)
    if not isinstance(record, dict):
        return {}
    archive_identity = dict(record.get("archive_identity") or {})
    layout_cache = dict(archive_identity.get("overview_topology_layout") or {})
    positions = _cached_positions_from_payload(payload)
    cached_positions = layout_cache.get("positions")
    if isinstance(cached_positions, dict):
        positions.update(_json_safe(cached_positions))
    updated_node_ids: list[str] = []
    for node_id in generated_node_ids:
        point = layout.positions.get(node_id)
        if point is None or node_id in positions:
            continue
        positions[node_id] = {"x": _round_coord(point.x), "y": _round_coord(point.y)}
        updated_node_ids.append(node_id)
    if not updated_node_ids:
        return {}
    layout_revision = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    layout_cache.update(
        {
            "render_type": OVERVIEW_TOPOLOGY_RENDER_TYPE,
            "layout_revision": layout_revision,
            "positions": positions,
        }
    )
    archive_identity["overview_topology_layout"] = layout_cache
    update_map_record(store, map_id, archive_identity=archive_identity)
    return {
        "cached": True,
        "layout_revision": layout_revision,
        "generated_node_ids": updated_node_ids,
    }


def _merge_cached_layout_positions(store: dict[str, Any], map_id: str, payload: dict[str, Any]) -> str:
    record = get_map_record(store, map_id)
    if not isinstance(record, dict):
        return ""
    archive_identity = record.get("archive_identity")
    if not isinstance(archive_identity, dict):
        return ""
    layout_cache = archive_identity.get("overview_topology_layout")
    if not isinstance(layout_cache, dict):
        return ""
    layout_revision = _short_text(layout_cache.get("layout_revision") or "", 80)
    cached_positions = layout_cache.get("positions")
    if not isinstance(cached_positions, dict):
        return layout_revision
    node_ids = {
        _short_text(node.get("id") or "", 120)
        for node in payload.get("nodes") or []
        if isinstance(node, dict)
    }
    if not node_ids:
        return layout_revision
    layout_payload = payload.get("layout")
    if not isinstance(layout_payload, dict):
        layout_payload = {}
    positions = layout_payload.get("positions")
    if not isinstance(positions, dict):
        positions = {}
    for node_id, point in cached_positions.items():
        safe_node_id = _short_text(node_id, 120)
        if safe_node_id in node_ids and safe_node_id not in positions and isinstance(point, dict):
            positions[safe_node_id] = _json_safe(point)
    if positions:
        layout_payload["positions"] = positions
        payload["layout"] = layout_payload
    return layout_revision


def _cached_positions_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    layout_payload = payload.get("layout")
    if not isinstance(layout_payload, dict):
        return {}
    positions = layout_payload.get("positions")
    if not isinstance(positions, dict):
        return {}
    return _json_safe(positions)


def _record_revision(store: dict[str, Any], map_id: str) -> str:
    record = get_map_record(store, map_id)
    if not isinstance(record, dict):
        return ""
    return _short_text(record.get("record_version") or "", 80)


def _render_title(
    *,
    explicit: str,
    fact_payload: dict[str, Any],
    fact: dict[str, Any],
    record: dict[str, Any],
) -> str:
    return _short_text(
        explicit
        or fact_payload.get("title")
        or fact.get("text")
        or record.get("title")
        or record.get("id")
        or "概览拓扑图",
        160,
    )


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _round_coord(value: float) -> float | int:
    rounded = round(float(value), 2)
    return int(rounded) if rounded.is_integer() else rounded


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _safe_file_stem(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_.-]+", "_", value.strip())
    safe = safe.strip("._-")
    return safe[:60] or "overview_topology"


def _audit_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("session", None)
    cleaned.pop("latest_session", None)
    cleaned.pop("svg", None)
    if "payload" in cleaned:
        cleaned["payload"] = "<topology_payload>"
    if "envelope" in cleaned:
        cleaned["envelope"] = "<overview_render_envelope>"
    if "topology_fact" in cleaned:
        fact = cleaned["topology_fact"]
        cleaned["topology_fact"] = {
            "id": fact.get("id", ""),
            "kind": fact.get("kind", ""),
            "visibility": fact.get("visibility", ""),
        } if isinstance(fact, dict) else {}
    return _json_safe(cleaned)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def locals_without_self(values: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key not in {"self", "session", "latest_session", "svg", "result"}
    }
