#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot_plugin_auto_trpg_dm.rendering import (
    build_strict_grid_render_input,
    render_strict_grid_svg,
)


DEFAULT_OUTPUT_DIR = ".story-forge-runs"


def render_story_grid_map(
    payload: dict[str, Any],
    *,
    output_dir: Path,
    title: str = "",
) -> dict[str, Any]:
    envelope = build_story_grid_render_envelope(payload, title=title)
    render_input = build_strict_grid_render_input(envelope)
    svg = render_strict_grid_svg(render_input)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_stem = _safe_file_stem(render_input.title or render_input.map_id or "story-grid")
    svg_path = output_dir / f"{stamp}-{file_stem}.svg"
    svg_path.write_text(svg, encoding="utf-8")
    metadata = {
        "ok": True,
        "type": "svg_map",
        "render_type": "strict_grid_svg",
        "visual_only": True,
        "map_id": render_input.map_id,
        "title": render_input.title,
        "file_name": svg_path.name,
        "file_path": str(svg_path),
        "width": render_input.width,
        "height": render_input.height,
        "svg_chars": len(svg),
        "safe_projection": _safe_render_metadata(svg_path, render_input),
    }
    metadata_path = output_dir / f"{stamp}-{file_stem}.metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def build_story_grid_render_envelope(payload: dict[str, Any], *, title: str = "") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("story_grid_payload_not_object")
    source = _grid_source(payload)
    grid = _grid_payload(source)
    width = _positive_int(grid.get("width", source.get("width")), "grid_width")
    height = _positive_int(grid.get("height", source.get("height")), "grid_height")
    envelope: dict[str, Any] = {
        "projection": "player_view",
        "map_id": str(source.get("map_id") or payload.get("map_id") or "story-grid"),
        "title": title or str(source.get("title") or payload.get("title") or source.get("map_title") or "Story grid"),
        "grid": {
            "width": width,
            "height": height,
            "cells": _normalize_cells(grid.get("cells", source.get("cells")), width=width, height=height),
            "entities": _normalize_entities(grid.get("entities", source.get("entities")), width=width, height=height),
            "rule_scale": source.get("rule_scale") or grid.get("rule_scale") or {"distance_per_cell": 5, "unit": "ft"},
        },
    }
    for key in ("visible_bounds", "layout", "rule_scale", "discovered_areas"):
        value = source.get(key, payload.get(key))
        if value not in (None, "", [], {}):
            envelope[key] = value
    for key in ("doors", "hazards", "obstacles", "labels"):
        value = _derived_overlay_values(
            key,
            grid.get(key, source.get(key)),
            cells=envelope["grid"]["cells"],
        )
        normalized = _normalize_overlay(value, width=width, height=height)
        if normalized:
            envelope[key] = normalized
    return envelope


def _grid_source(payload: dict[str, Any]) -> dict[str, Any]:
    for value in (
        payload.get("map_grid_seed"),
        _nested_map_grid_seed(payload.get("scene_goal")),
        _nested_map_grid_seed(payload.get("convergence_action")),
    ):
        if isinstance(value, dict):
            return _with_inherited_map_fields(
                dict(value),
                payload,
                title_candidates=(
                    payload.get("title"),
                    payload.get("map_title"),
                    payload.get("next_scene_entry"),
                    payload.get("scene_goal"),
                    payload.get("available_action"),
                ),
            )
    for key in ("strict_grid", "tactical_grid", "map_grid", "grid"):
        value = payload.get(key)
        if isinstance(value, dict):
            return _with_inherited_map_fields(dict(value), payload)
    return dict(payload)


def _nested_map_grid_seed(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and isinstance(value.get("map_grid_seed"), dict):
        return value["map_grid_seed"]
    return None


def _with_inherited_map_fields(
    source: dict[str, Any],
    payload: dict[str, Any],
    *,
    title_candidates: tuple[Any, ...] = (),
) -> dict[str, Any]:
    for inherited in ("map_id", "title", "visible_bounds", "layout", "rule_scale", "discovered_areas"):
        if inherited not in source and payload.get(inherited) not in (None, "", [], {}):
            source[inherited] = payload[inherited]
    if source.get("title") in (None, "", [], {}):
        for candidate in title_candidates:
            if isinstance(candidate, str) and candidate.strip():
                source["title"] = candidate.strip()
                break
    return source


def _grid_payload(source: dict[str, Any]) -> dict[str, Any]:
    value = source.get("grid")
    if isinstance(value, dict):
        return dict(value)
    return source


def _normalize_cells(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                cell = dict(item)
            elif isinstance(item, list) and len(item) >= 2:
                cell = {"x": item[0], "y": item[1]}
                if len(item) >= 3:
                    cell["terrain"] = item[2]
            else:
                continue
            _adapt_cell_type(cell)
            if _point_in_bounds(cell, width=width, height=height):
                cells.append(cell)
    return cells


def _normalize_entities(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    if isinstance(value, dict):
        iterable = (
            {"id": str(entity_id), **dict(item)}
            for entity_id, item in value.items()
            if isinstance(item, dict)
        )
    elif isinstance(value, list):
        iterable = (dict(item) for item in value if isinstance(item, dict))
    else:
        iterable = ()
    for item in iterable:
        _adapt_entity_type(item)
        if _point_in_bounds(item, width=width, height=height):
            entities.append(item)
    return entities


def _adapt_cell_type(cell: dict[str, Any]) -> None:
    raw_type = str(cell.get("type") or cell.get("kind") or "").strip().lower()
    if raw_type and cell.get("terrain") in (None, "", [], {}):
        if raw_type in {"wall", "floor", "stone", "dirt", "grass", "water", "mud", "normal"}:
            cell["terrain"] = raw_type
        elif raw_type == "door":
            cell["terrain"] = "floor"
    if raw_type == "wall":
        cell.setdefault("blocks_move", True)
        cell.setdefault("blocks_los", True)


def _adapt_entity_type(entity: dict[str, Any]) -> None:
    raw_type = str(entity.get("type") or entity.get("kind") or "").strip()
    if raw_type and entity.get("name") in (None, "", [], {}):
        entity["name"] = str(entity.get("label") or raw_type)
    if raw_type and entity.get("faction") in (None, "", [], {}):
        entity["faction"] = "npc" if raw_type.strip().lower() in {"npc", "character"} else raw_type.lower()


def _derived_overlay_values(key: str, value: Any, *, cells: list[dict[str, Any]]) -> Any:
    items = list(value) if isinstance(value, list) else []
    for cell in cells:
        raw_type = str(cell.get("type") or cell.get("kind") or "").strip().lower()
        if key == "labels" and cell.get("label") not in (None, "", [], {}):
            items.append(
                {
                    "id": cell.get("id") or f"cell-label-{cell.get('x')}-{cell.get('y')}",
                    "x": cell.get("x"),
                    "y": cell.get("y"),
                    "text": cell.get("label"),
                    "visibility": cell.get("visibility", ""),
                }
            )
        elif key == "doors" and raw_type == "door":
            items.append(
                {
                    "id": cell.get("id") or f"door-{cell.get('x')}-{cell.get('y')}",
                    "x": cell.get("x"),
                    "y": cell.get("y"),
                    "side": cell.get("side") or "north",
                    "state": cell.get("state") or "closed",
                    "visibility": cell.get("visibility", ""),
                }
            )
    return items


def _normalize_overlay(value: Any, *, width: int, height: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        current = dict(item)
        if _point_in_bounds(current, width=width, height=height):
            items.append(current)
    return items


def _point_in_bounds(item: dict[str, Any], *, width: int, height: int) -> bool:
    try:
        x = _coordinate(item.get("x"), "x")
        y = _coordinate(item.get("y"), "y")
    except ValueError:
        return False
    return 0 <= x < width and 0 <= y < height


def _safe_render_metadata(svg_path: Path, render_input: Any) -> dict[str, Any]:
    return {
        "type": "strict_grid_svg",
        "title": render_input.title,
        "name": svg_path.name,
        "visual_only": True,
        "map_id": render_input.map_id,
        "width": render_input.width,
        "height": render_input.height,
    }


def _read_json(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("story_grid_file_must_contain_object")
    return payload


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


def _safe_file_stem(value: str) -> str:
    stem = re.sub(r"[^\w.-]+", "_", str(value or ""), flags=re.UNICODE)
    return stem.strip("._")[:60] or "story-grid"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a standalone Story Forge/grid JSON payload through the deterministic strict-grid SVG renderer."
    )
    parser.add_argument("--grid-file", required=True, help="UTF-8 JSON file containing grid/cells/entities/overlays.")
    parser.add_argument("--title", default="", help="Optional map title override.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory for SVG and metadata.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = render_story_grid_map(
        _read_json(args.grid_file),
        output_dir=Path(args.output_dir),
        title=args.title,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
