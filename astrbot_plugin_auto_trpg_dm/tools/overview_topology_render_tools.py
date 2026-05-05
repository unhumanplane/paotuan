from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field

from ..core.map_core import (
    MAP_VIEW_PLAYER,
    add_map_fact,
    add_render_ref,
    project_active_map_record,
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

        payload = dict(topology_fact.get("payload") or {})
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
            "map_id": _short_text(projected_record.get("id"), 120),
            "title": render_title,
            "map_revision": _short_text(projected_record.get("record_version") or "", 80),
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
        _persist_generated_layout_positions(
            latest_session.maps,
            render_input.map_id,
            topology_fact,
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
            "file_path": str(path),
            "file_name": path.name,
            "svg_chars": len(svg),
            "send_to_chat": bool(send_to_chat),
            "visual_only": True,
            "render_ref": _json_safe(render_ref),
            "pending_output": _json_safe(pending_output) if send_to_chat else {},
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
    fact: dict[str, Any],
    payload: dict[str, Any],
    layout: Any,
) -> None:
    generated_node_ids = tuple(getattr(layout, "generated_node_ids", ()) or ())
    if not generated_node_ids:
        return
    updated_payload = _json_safe(payload)
    layout_payload = updated_payload.get("layout")
    if not isinstance(layout_payload, dict):
        layout_payload = {}
    positions = layout_payload.get("positions")
    if not isinstance(positions, dict):
        positions = {}
    for node_id in generated_node_ids:
        point = layout.positions.get(node_id)
        if point is None or node_id in positions:
            continue
        positions[node_id] = {"x": _round_coord(point.x), "y": _round_coord(point.y)}
    layout_payload["positions"] = positions
    updated_payload["layout"] = layout_payload
    add_map_fact(
        store,
        map_id,
        fact_id=_short_text(fact.get("id") or "overview-topology", 120),
        kind=_short_text(fact.get("kind") or "overview_topology", 80),
        text=_short_text(fact.get("text") or "", 1000),
        payload=updated_payload,
        authority=_short_text(fact.get("authority") or "code", 40),
        visibility=_short_text(fact.get("visibility") or "player", 40),
        source=_short_text(fact.get("source") or "overview_topology_svg", 120),
    )


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
