from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from .map_tool_routing import (
    looks_overview_map_request,
    looks_strict_grid_map_request,
    looks_visual_map_request,
)


DETERMINISTIC_MAP_RENDERER_TOOLS = (
    "render_strict_grid_svg",
    "render_overview_topology_svg",
)
LEGACY_MAP_RENDERER_TOOLS = ("generate_map_svg",)

MISSING_MAP_DATA_ERRORS = (
    "strict_grid_not_found",
    "active_strict_grid_missing",
    "strict_grid_not_migrated",
    "overview_map_not_found",
    "overview_topology_missing",
    "overview_nodes_required",
)

TEXT_ONLY_MAP_TERMS = (
    "text-only",
    "text only",
    "ascii",
    "纯文字",
    "文字地图",
    "文本地图",
    "文字版地图",
    "文本版地图",
    "文字草图",
    "文本草图",
    "不要生成图片",
    "不要图片",
    "不要渲染",
    "不要 svg",
    "不需要 svg",
    "不用 svg",
    "不生成 svg",
)

TEXT_MAP_SELF_DESCRIPTION_TERMS = (
    "地图如下",
    "地图：",
    "战场示意",
    "示意图",
    "布局图",
    "站位图",
    "俯视图",
    "路线图",
    "格子图",
    "grid map",
    "battle map",
)

NON_MAP_TABLE_TERMS = (
    "检定摘要",
    "骰子检定",
    "玩家名单",
    "角色名单",
    "日志",
    "诊断",
    "diagnostic",
    "token",
    "initiative",
    "turn order",
    "规则列表",
    "状态列表",
)

MISSING_MAP_RESPONSE_TERMS = (
    "缺少可渲染的结构化地图数据",
    "缺少结构化地图数据",
    "不能生成可靠的可视化地图",
    "无法生成可靠的可视化地图",
    "map data missing",
    "structured map data",
    "renderer cannot run",
)

MISSING_MAP_DATA_RESPONSE = (
    "现在还不能生成可靠的可视化地图：当前缺少可渲染的结构化地图数据。"
    "请先建立地图、放置关键实体或补齐区域拓扑后再请求生成地图。"
)
MAP_DELIVERY_ACK = "地图已生成，已附上。"

GENERIC_MAP_COMPLETION_TERMS = (
    "好",
    "好的",
    "ok",
    "done",
    "完成",
    "已完成",
    "生成完成",
    "地图完成",
    "地图已生成",
    "已生成",
)


@dataclass(frozen=True)
class MapRequestGuardContext:
    visual_map_request: bool
    text_only_map_request: bool
    preferred_renderer_tools: tuple[str, ...] = ()

    @property
    def renderer_attempt_required(self) -> bool:
        return self.visual_map_request and not self.text_only_map_request and bool(self.preferred_renderer_tools)


@dataclass(frozen=True)
class MapRendererResultSummary:
    attempted: bool = False
    succeeded: bool = False
    missing_data: bool = False
    legacy_attempted: bool = False
    attempted_tools: tuple[str, ...] = ()
    successful_tools: tuple[str, ...] = ()
    missing_errors: tuple[str, ...] = ()


def build_map_request_guard(
    message: str,
    available_tool_names: list[str] | tuple[str, ...] | set[str] | None = None,
) -> MapRequestGuardContext:
    available = set(available_tool_names or ())
    visual = looks_visual_map_request(message)
    text_only = looks_text_only_map_request(message)
    preferred: list[str] = []
    if visual and not text_only:
        if looks_overview_map_request(message) and "render_overview_topology_svg" in available:
            preferred.append("render_overview_topology_svg")
        elif looks_strict_grid_map_request(message) and "render_strict_grid_svg" in available:
            preferred.append("render_strict_grid_svg")
        else:
            preferred.extend(name for name in DETERMINISTIC_MAP_RENDERER_TOOLS if name in available)
    return MapRequestGuardContext(
        visual_map_request=visual,
        text_only_map_request=text_only,
        preferred_renderer_tools=tuple(dict.fromkeys(preferred)),
    )


def looks_text_only_map_request(message: str) -> bool:
    text = _normalized(message)
    return bool(text and looks_visual_map_request(text) and _contains_any(text, TEXT_ONLY_MAP_TERMS))


def classify_map_renderer_results(tool_results: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> MapRendererResultSummary:
    attempted_tools: list[str] = []
    successful_tools: list[str] = []
    missing_errors: list[str] = []
    legacy_attempted = False
    for item in tool_results or []:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool") or "")
        if tool_name in LEGACY_MAP_RENDERER_TOOLS:
            legacy_attempted = True
            continue
        if tool_name not in DETERMINISTIC_MAP_RENDERER_TOOLS:
            continue
        attempted_tools.append(tool_name)
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("ok", True):
            successful_tools.append(tool_name)
            continue
        error = str(result.get("error") or result.get("error_code") or "")
        if error in MISSING_MAP_DATA_ERRORS:
            missing_errors.append(error)
    return MapRendererResultSummary(
        attempted=bool(attempted_tools),
        succeeded=bool(successful_tools),
        missing_data=bool(missing_errors),
        legacy_attempted=legacy_attempted,
        attempted_tools=tuple(dict.fromkeys(attempted_tools)),
        successful_tools=tuple(dict.fromkeys(successful_tools)),
        missing_errors=tuple(dict.fromkeys(missing_errors)),
    )


def build_renderer_required_retry_prompt(guard: MapRequestGuardContext) -> str:
    tool_list = "、".join(guard.preferred_renderer_tools) or "deterministic map renderer"
    return (
        "玩家明确请求可视化地图。本轮不能用 ASCII、Markdown 表格、emoji tile、"
        "空格排版或文字示意图冒充地图。请至少调用一次可用的确定性地图渲染工具："
        f"{tool_list}。如果工具返回缺少结构化地图数据，请基于工具返回向玩家说明需要先建立地图、"
        "放置关键实体或补齐区域拓扑。"
    )


def build_missing_map_data_response() -> str:
    return MISSING_MAP_DATA_RESPONSE


def build_map_delivery_ack() -> str:
    return MAP_DELIVERY_ACK


def completion_needs_map_delivery_ack(completion: str) -> bool:
    text = str(completion or "").strip()
    if not text:
        return True
    normalized = re.sub(r"[\s。.!！,，、~～]+", "", text.lower())
    if not normalized:
        return True
    if normalized in GENERIC_MAP_COMPLETION_TERMS:
        return True
    return len(normalized) <= 8 and any(term == normalized for term in GENERIC_MAP_COMPLETION_TERMS)


def completion_looks_like_text_map(completion: str) -> bool:
    text = str(completion or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if _contains_any(lowered, MISSING_MAP_RESPONSE_TERMS):
        return False
    if _contains_any(lowered, NON_MAP_TABLE_TERMS) and not _contains_any(lowered, TEXT_MAP_SELF_DESCRIPTION_TERMS):
        return False
    signals = detect_text_map_signals(text)
    return bool(signals)


def detect_text_map_signals(completion: str) -> tuple[str, ...]:
    text = str(completion or "").strip()
    if not text:
        return ()
    lowered = text.lower()
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    self_described = _contains_any(lowered, TEXT_MAP_SELF_DESCRIPTION_TERMS)
    border_rows = sum(1 for line in lines if _looks_like_border_row(line))
    pipe_rows = sum(1 for line in lines if _looks_like_pipe_grid_row(line))
    box_rows = sum(1 for line in lines if _looks_like_box_row(line))
    emoji_rows = sum(1 for line in lines if _looks_like_emoji_tile_row(line))
    tab_rows = sum(1 for line in lines if _looks_like_tabular_grid_row(line))
    compact_rows = sum(1 for line in lines if _looks_like_compact_cell_row(line))

    signals: list[str] = []
    if border_rows >= 2 and pipe_rows >= 1:
        signals.append("ascii_box_grid")
    if box_rows >= 2:
        signals.append("box_drawing_grid")
    if emoji_rows >= 2:
        signals.append("emoji_tile_grid")
    if self_described and pipe_rows >= 2:
        signals.append("self_described_pipe_grid")
    if self_described and tab_rows >= 2:
        signals.append("self_described_tabular_grid")
    if self_described and compact_rows >= 2:
        signals.append("self_described_compact_cell_grid")
    return tuple(signals)


def build_guard_audit_record(
    *,
    action: str,
    reason: str,
    guard: MapRequestGuardContext,
    renderer_summary: MapRendererResultSummary | None = None,
    completion: str = "",
    text_map_signals: tuple[str, ...] = (),
) -> dict[str, Any]:
    summary = renderer_summary or MapRendererResultSummary()
    record: dict[str, Any] = {
        "type": "visual_map_request_guard",
        "action": action,
        "reason": reason,
        "visual_map_request": guard.visual_map_request,
        "text_only_map_request": guard.text_only_map_request,
        "renderer_attempt_required": guard.renderer_attempt_required,
        "preferred_renderer_tools": list(guard.preferred_renderer_tools),
        "renderer_attempted": summary.attempted,
        "renderer_succeeded": summary.succeeded,
        "renderer_missing_data": summary.missing_data,
        "attempted_tools": list(summary.attempted_tools),
        "successful_tools": list(summary.successful_tools),
        "missing_errors": list(summary.missing_errors),
        "text_map_signals": list(text_map_signals),
    }
    if completion:
        record["completion_chars"] = len(completion)
        record["completion_hash"] = hashlib.sha256(completion.encode("utf-8")).hexdigest()[:12]
    return record


def _looks_like_border_row(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    return bool(re.fullmatch(r"[+\-=\s]+", stripped) and ("+" in stripped or "-" in stripped))


def _looks_like_pipe_grid_row(line: str) -> bool:
    stripped = line.strip()
    if stripped.count("|") < 2:
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return len(cells) >= 2 and any(cell for cell in cells)


def _looks_like_box_row(line: str) -> bool:
    box_chars = "┌┬┐├┼┤└┴┘─━═╔╦╗╠╬╣╚╩╝│┃"
    return sum(1 for ch in line if ch in box_chars) >= 3


def _looks_like_emoji_tile_row(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    tile_chars = "⬜⬛🟦🟩🟨🟧🟥🟪🟫🔲🔳"
    emoji_count = sum(1 for ch in stripped if ord(ch) >= 0x1F000 or ch in tile_chars)
    text_count = sum(1 for ch in stripped if ch.isalpha())
    return emoji_count >= 3 and text_count <= 2


def _looks_like_tabular_grid_row(line: str) -> bool:
    return line.count("\t") >= 2


def _looks_like_compact_cell_row(line: str) -> bool:
    stripped = line.strip()
    if not stripped or "|" in stripped:
        return False
    tokens = re.split(r"\s{2,}", stripped)
    if len(tokens) < 3:
        return False
    compact = [token for token in tokens if 1 <= len(token) <= 4]
    return len(compact) >= 3


def _normalized(message: str) -> str:
    return str(message or "").strip().lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
