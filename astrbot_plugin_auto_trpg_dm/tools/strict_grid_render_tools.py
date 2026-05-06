from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict

from pydantic import BaseModel, Field

from ..core.map_delivery_cadence import (
    MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
    MAP_RENDER_STRICT_GRID,
    MapDeliveryRequest,
    enqueue_map_pending_output,
)
from ..core.map_core import (
    MAP_TYPE_STRICT_LOCAL,
    MAP_VIEW_PLAYER,
    add_render_ref,
    load_active_strict_grid,
    migrate_legacy_battle_grid,
    project_active_map_record,
)
from ..rendering import build_strict_grid_render_input, render_strict_grid_svg
from ..storage.json_repository import JsonGameRepository


class RenderStrictGridSvgArgs(BaseModel):
    title: str = Field(default="", description="可选标题；为空时使用当前 strict map 标题。")
    send_to_chat: bool = Field(default=True, description="是否把渲染结果排队随本轮回复发送。")


class StrictGridRenderTools:
    def __init__(self, repository: JsonGameRepository, session_id: str, actor: dict[str, str] | None = None):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def render_strict_grid_svg(
        self,
        title: str = "",
        send_to_chat: bool = True,
        delivery_trigger: str = MAP_DELIVERY_TRIGGER_PLAYER_REQUEST,
        trigger_id: str = "",
        combat_id: str = "",
        round_number: int = 0,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        loaded = load_active_strict_grid(session.maps, session.battle or {})
        if loaded.get("source") == "legacy_battle_grid":
            migrated = migrate_legacy_battle_grid(session.maps, session.battle or {})
            if migrated.get("ok") and migrated.get("map_id"):
                battle = dict(session.battle or {})
                battle["map_id"] = migrated["map_id"]
                battle["grid"] = migrated["grid"]
                session.battle = battle
                loaded = load_active_strict_grid(session.maps, session.battle)
        if not loaded.get("ok"):
            result = {
                "ok": False,
                "error": loaded.get("reason") or "strict_grid_not_found",
                "source": loaded.get("source", "none"),
            }
            self._audit("render_strict_grid_svg", {"title": title, "send_to_chat": send_to_chat}, result)
            return result

        map_id = str(loaded.get("map_id") or session.maps.get("active_strict_map_id") or "")
        player_record = project_active_map_record(session.maps, MAP_VIEW_PLAYER, map_id=map_id, strict=True)
        record = dict(loaded.get("record") or {})
        envelope = _render_envelope(
            record,
            player_record,
            grid=dict(loaded.get("grid") or {}),
            map_id=map_id,
            title=title,
        )
        try:
            render_input = build_strict_grid_render_input(envelope)
            svg = render_strict_grid_svg(render_input)
        except ValueError as exc:
            result = {"ok": False, "error": str(exc), "map_id": map_id}
            self._audit("render_strict_grid_svg", {"title": title, "send_to_chat": send_to_chat}, result)
            return result

        path = self._write_svg(envelope["title"], svg)
        ref = add_render_ref(
            session.maps,
            map_id,
            ref_type="strict_grid_svg",
            title=envelope["title"],
            name=path.name,
            path=str(path),
            visual_only=True,
        )
        map_record = {
            "type": "svg_map",
            "render_type": "strict_grid_svg",
            "title": envelope["title"],
            "name": path.name,
            "path": str(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "map_id": map_id,
            "projection": MAP_VIEW_PLAYER,
            "width": render_input.width,
            "height": render_input.height,
            "visual_only": True,
        }
        delivery_decision = None
        if send_to_chat:
            delivery_decision, _state = enqueue_map_pending_output(
                session.scene,
                map_record,
                MapDeliveryRequest(
                    trigger=delivery_trigger,
                    render_type=MAP_RENDER_STRICT_GRID,
                    map_id=map_id,
                    map_revision=str(record.get("record_version") or ""),
                    trigger_id=trigger_id,
                    combat_id=combat_id,
                    round_number=round_number,
                ),
            )
        self.repository.save_session(session)

        result = {
            "ok": True,
            "map_id": map_id,
            "render_type": MAP_RENDER_STRICT_GRID,
            "title": envelope["title"],
            "map_revision": str(record.get("record_version") or ""),
            "file_path": str(path),
            "file_name": path.name,
            "svg_chars": len(svg),
            "send_to_chat": bool(send_to_chat),
            "visual_only": True,
            "render_ref": {
                "type": ref.get("type"),
                "title": ref.get("title"),
                "name": ref.get("name"),
                "visual_only": ref.get("visual_only", True),
            },
            "message": "Strict grid SVG rendered from player-view structured coordinates.",
        }
        if delivery_decision is not None:
            result["delivery"] = _json_safe(delivery_decision.__dict__)
        self._audit("render_strict_grid_svg", {"title": title, "send_to_chat": send_to_chat}, result)
        return result

    def _write_svg(self, title: str, svg: str) -> Path:
        maps_dir = self.repository.maps_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        path = maps_dir / f"{stamp}_{_safe_file_stem(title or 'strict-grid')}.svg"
        path.write_text(svg, encoding="utf-8")
        return path

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": tool, "input": input_payload, "result": _json_safe(result)},
        )


def _render_envelope(
    record: dict[str, Any],
    player_record: dict[str, Any] | None,
    *,
    grid: dict[str, Any],
    map_id: str,
    title: str,
) -> dict[str, Any]:
    safe_record = dict(player_record or {})
    envelope = {
        "projection": MAP_VIEW_PLAYER,
        "visibility": safe_record.get("visibility", "player"),
        "map_id": map_id,
        "title": title or safe_record.get("title") or record.get("title") or map_id or "Strict grid map",
        "grid": grid,
    }
    for key in ("visible_bounds", "rule_scale", "layout", "cells", "entities", "doors", "hazards", "obstacles", "labels", "discovered_areas"):
        if key in safe_record:
            envelope[key] = safe_record[key]
        elif key in record and key not in {"cells", "entities"}:
            envelope[key] = record[key]
    if record.get("type") != MAP_TYPE_STRICT_LOCAL and safe_record.get("type") != MAP_TYPE_STRICT_LOCAL:
        raise ValueError("strict_map_record_type_required")
    return envelope


def _safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", str(value or "").strip())
    return stem.strip("_")[:60] or "strict-grid"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
