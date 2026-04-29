from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..core.plugin_log import get_plugin_logger
from ..storage.json_repository import JsonGameRepository


MAP_FONT_FAMILY = "Noto Sans CJK SC, Noto Sans SC, Microsoft YaHei, SimHei, sans-serif"


class GenerateMapSvgArgs(BaseModel):
    prompt: str = Field(
        default="",
        description="地图绘制需求。描述场景、地形、阵营、关键地点、入口出口、战术重点。",
    )
    title: str = Field(default="战场地图", description="地图标题")
    width: int = Field(default=900, ge=320, le=1600, description="SVG 像素宽度")
    height: int = Field(default=900, ge=320, le=1600, description="SVG 像素高度")
    grid_width: int = Field(default=0, ge=0, le=64, description="可选战棋网格宽度；0 表示由子 agent 自行判断")
    grid_height: int = Field(default=0, ge=0, le=64, description="可选战棋网格高度；0 表示由子 agent 自行判断")
    style: str = Field(default="clean tactical top-down", description="视觉风格，例如废土、地牢、科幻、清晰战术俯视图")
    include_current_battle: bool = Field(default=True, description="是否把当前战棋快照交给绘图子 agent 参考")
    send_to_chat: bool = Field(default=True, description="是否把生成的 SVG 文件排队随本轮回复发送")


class MapTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        astr_context: Any | None = None,
        provider_id: str = "",
    ):
        self.repository = repository
        self.session_id = session_id
        self.astr_context = astr_context
        self.provider_id = provider_id

    async def generate_map_svg(
        self,
        prompt: str = "",
        title: str = "战场地图",
        width: int = 900,
        height: int = 900,
        grid_width: int = 0,
        grid_height: int = 0,
        style: str = "clean tactical top-down",
        include_current_battle: bool = True,
        send_to_chat: bool = True,
    ) -> Dict[str, Any]:
        """Generate a visual-only SVG map through an isolated LLM call."""
        if self.astr_context is None:
            result = {"ok": False, "error": "missing_astr_context"}
            self._audit("generate_map_svg", locals_without_self(locals()), result)
            return result

        width = max(320, min(1600, int(width or 900)))
        height = max(320, min(1600, int(height or 900)))
        grid_width = max(0, min(64, int(grid_width or 0)))
        grid_height = max(0, min(64, int(grid_height or 0)))
        title = _short_text(title or "战场地图", 80)
        style = _short_text(style or "clean tactical top-down", 120)

        session = self.repository.load_session(self.session_id)
        battle = session.compact_snapshot().get("battle", {}) if include_current_battle else {}
        if (not grid_width or not grid_height) and battle.get("grid"):
            grid = battle.get("grid") or {}
            grid_width = grid_width or int(grid.get("width") or 0)
            grid_height = grid_height or int(grid.get("height") or 0)
        player_positions = _player_position_records(session, battle)
        map_prompt = _build_map_prompt(
            title=title,
            prompt=prompt or str(session.scene.get("summary") or "根据当前跑团场景绘制战术地图。"),
            style=style,
            width=width,
            height=height,
            grid_width=grid_width,
            grid_height=grid_height,
            battle=battle,
            player_positions=player_positions,
        )

        get_plugin_logger().info(
            "map_subagent_request session=%s title=%s prompt_chars=%s battle_chars=%s",
            self.session_id,
            title,
            len(map_prompt),
            len(str(battle)),
        )
        response = await self._llm_generate(
            prompt=map_prompt,
            contexts=[],
            system_prompt=MAP_SYSTEM_PROMPT,
        )
        raw_text = getattr(response, "completion_text", "") or str(response)
        svg = _extract_svg(raw_text)
        if not svg:
            result = {
                "ok": False,
                "error": "no_svg_returned",
                "message": "地图子 agent 没有返回合法 SVG；未写入文件。",
                "raw_excerpt": _short_text(raw_text, 300),
            }
            self._audit("generate_map_svg", locals_without_self(locals()), result)
            get_plugin_logger().warning(
                "map_subagent_failed session=%s error=no_svg_returned raw_chars=%s",
                self.session_id,
                len(raw_text),
            )
            return result

        try:
            svg = sanitize_svg(
                svg,
                width=width,
                height=height,
                title=title,
                player_positions=player_positions,
                grid_width=grid_width,
                grid_height=grid_height,
            )
        except ValueError as exc:
            result = {
                "ok": False,
                "error": "invalid_svg",
                "message": str(exc),
                "raw_excerpt": _short_text(raw_text, 300),
            }
            self._audit("generate_map_svg", locals_without_self(locals()), result)
            get_plugin_logger().warning("map_subagent_failed session=%s error=invalid_svg reason=%s", self.session_id, exc)
            return result

        path = self._write_svg(title, svg)
        latest_session = self.repository.load_session(self.session_id)
        map_record = {
            "type": "svg_map",
            "title": title,
            "name": path.name,
            "path": str(path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "width": width,
            "height": height,
            "grid_width": grid_width,
            "grid_height": grid_height,
            "player_position_count": sum(1 for item in player_positions if item.get("placed")),
            "visual_only": True,
        }
        latest_session.scene["last_map_svg"] = map_record
        if send_to_chat:
            pending = list(latest_session.scene.get("_pending_outputs") or [])
            pending.append(map_record)
            latest_session.scene["_pending_outputs"] = pending[-3:]
        self.repository.save_session(latest_session)

        result = {
            "ok": True,
            "title": title,
            "file_path": str(path),
            "file_name": path.name,
            "svg_chars": len(svg),
            "send_to_chat": send_to_chat,
            "visual_only": True,
            "message": "SVG 地图已生成。注意：它只是视觉层，物理坐标仍以 Spatial Engine 为准。",
        }
        self._audit("generate_map_svg", locals_without_self(locals()), result)
        get_plugin_logger().info(
            "map_subagent_completed session=%s file=%s svg_chars=%s send_to_chat=%s",
            self.session_id,
            path,
            len(svg),
            send_to_chat,
        )
        return result

    async def _llm_generate(self, **kwargs: Any) -> Any:
        if self.provider_id:
            kwargs = {"chat_provider_id": self.provider_id, **kwargs}
        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._llm_generate_once(**kwargs)
            except Exception as exc:
                if isinstance(exc, TypeError) or not _is_retryable_llm_error(exc):
                    raise
                last_exc = exc
                if attempt >= max_attempts:
                    break
                delay = 1.5 * attempt
                get_plugin_logger().warning(
                    "map_llm_retry session=%s attempt=%s retry_left=%s delay=%.1fs error=%s",
                    self.session_id,
                    attempt,
                    max_attempts - attempt,
                    delay,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
        get_plugin_logger().error(
            "map_llm_failed_after_retries session=%s attempts=%s error=%s",
            self.session_id,
            max_attempts,
            str(last_exc)[:240] if last_exc else "",
        )
        return _LlmFailureResponse("地图子模型连续调用失败，未生成 SVG。")

    async def _llm_generate_once(self, **kwargs: Any) -> Any:
        try:
            return await self.astr_context.llm_generate(**kwargs)
        except TypeError as exc:
            if "chat_provider_id" not in kwargs:
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("chat_provider_id", None)
            try:
                return await self.astr_context.llm_generate(**retry_kwargs)
            except TypeError:
                raise exc

    def _write_svg(self, title: str, svg: str) -> Path:
        maps_dir = self.repository.maps_dir()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        file_name = f"{stamp}_{_safe_file_stem(title)}.svg"
        path = maps_dir / file_name
        path.write_text(svg, encoding="utf-8")
        return path

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        try:
            self.repository.append_audit(
                self.session_id,
                {"type": "tool", "tool": tool, "input": _audit_input(input_payload), "result": _json_safe(result)},
            )
        except Exception as exc:
            get_plugin_logger().warning(
                "map_audit_failed session=%s tool=%s error=%s",
                self.session_id,
                tool,
                exc,
            )


MAP_SYSTEM_PROMPT = """你是 TRPG 地图绘制子 agent。你的唯一任务是输出一张完整、可保存的 SVG 战术俯视图。

硬性规则：
1. 只输出 <svg ...>...</svg>，不要 Markdown、解释、JSON 或代码围栏。
2. SVG 必须自包含，禁止 script、foreignObject、image、use、a、iframe、animation、外部 URL、base64、data URI。
3. 只画视觉示意，不得改变游戏事实；坐标、移动、视线、攻击范围仍以主系统 Spatial Engine 为准。
4. 若收到战棋快照，优先按快照画网格、障碍、友方、敌方和关键位置；不要发明实体坐标。
5. 使用清晰战术俯视图，不要做粗糙草图：必须有浅色背景、标题栏、主网格区、图例区、地形/障碍层、实体层、标签层。
6. 版式必须留白：标题栏高度至少 90px；主网格不要压到标题文字；图例放在独立小框内，不要压住实体和格子编号。
7. 图层顺序固定为：背景 -> 标题栏 -> 网格底色和格线 -> 墙/障碍/掩体/入口出口 -> 友方 -> 敌方 -> 当前行动/威胁标记 -> 标签 -> 图例。
8. 配色要有层次：浅底；网格浅蓝灰；墙/障碍深灰；掩体棕灰；友方冷色蓝/青；敌方暖色红/橙；入口出口绿色/黄色；当前行动用金色描边。
9. 文本标签要短。实体标签优先放在圆形 token 中心，使用 text-anchor="middle" dominant-baseline="middle"；过长标签改成 2-6 字简称，避免压住其他实体。
10. 中文标签必须可读：font-family 使用 Noto Sans CJK SC, Noto Sans SC, Microsoft YaHei, SimHei, sans-serif；普通标签 font-size 14-18，标题 24-30，图例 16-18。
11. 如果同类单位很多，token 内只写 1-4 字简称或编号，完整含义放进右侧图例/态势面板；不要把长名字挤进 token。
12. 标题摘要写完整短句，不要用“...”省略；优先用“当前：某某行动”“北门承压”等 8-18 字短句。
13. 为了聊天 PNG 预览兼容，只使用显式坐标的 rect、line、circle、ellipse、polygon、polyline、text；不要依赖 path、transform、style、filter、defs、marker、pattern、复杂渐变或外部字体。
14. 如果信息不足，画清晰“当前态势示意图”，但不要把示意图当成新的坐标事实。"""


def _build_map_prompt(
    title: str,
    prompt: str,
    style: str,
    width: int,
    height: int,
    grid_width: int,
    grid_height: int,
    battle: dict[str, Any],
    player_positions: list[dict[str, Any]] | None = None,
) -> str:
    import json

    battle_text = json.dumps(battle, ensure_ascii=False, separators=(",", ":"))
    if len(battle_text) > 18_000:
        battle_text = battle_text[:18_000] + "...(truncated)"
    layout = _layout_hint(width, height, grid_width, grid_height)
    player_position_text = _player_positions_prompt_text(player_positions or [])
    return f"""请生成 SVG 地图。

标题：{title}
画布：{width}x{height}
建议网格：{grid_width or "自定"} x {grid_height or "自定"}
风格：{style}

绘图需求：
{_short_text(prompt, 2000)}

当前战棋快照：
{battle_text or "无"}

玩家/PC 位置事实：
{player_position_text}

推荐版式：
{layout}

输出要求：
- 根元素必须是 <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
- 只使用 rect/line/circle/ellipse/polygon/polyline/text；所有元素直接写 x/y/cx/cy/points 坐标。
- 不要使用 path、transform、style、filter、defs、marker、pattern、渐变、阴影或外部资源。
- 友方用冷色，敌方用红/橙色，障碍用深灰，掩体用棕灰，入口/出口用绿色或黄色，当前行动用金色描边。
- 左上标题栏写短标题和一行态势摘要；地图主体留出清楚边距；图例 3-5 项放入独立边框小框。
- 每个中文标签 2-8 个汉字；实体 token 内文字必须 text-anchor="middle" dominant-baseline="middle"。
- 标签不要压住实体、边框、图例或其他标签；拥挤时用短简称或编号，并把全称放在右侧图例/态势面板。
- 如果“玩家/PC 位置事实”里有坐标，必须在主地图上用醒目的紫色/蓝紫 PC 标记画出；如果没有坐标，不要伪造位置，必须在右侧态势面板写“玩家位置未登记”。
- 标题摘要必须是完整短句，不要输出省略号。
- 使用浅灰蓝背景、细网格、深色描边、少量高对比色块；不要一整张图只有单一色系。
- 只输出 SVG。"""


def _layout_hint(width: int, height: int, grid_width: int, grid_height: int) -> str:
    header_h = 96 if height >= 760 else 80
    margin = max(28, min(72, width // 14))
    side_panel_w = 190 if width >= 760 else 0
    grid_left = margin
    grid_top = header_h + 28
    grid_max_w = max(220, width - grid_left - margin - side_panel_w)
    grid_max_h = max(220, height - grid_top - margin)
    if grid_width and grid_height:
        cell = max(18, int(min(grid_max_w / grid_width, grid_max_h / grid_height)))
        grid_px_w = min(grid_max_w, cell * grid_width)
        grid_px_h = min(grid_max_h, cell * grid_height)
        grid_text = f"主网格建议 x={grid_left}, y={grid_top}, cell={cell}, size={int(grid_px_w)}x{int(grid_px_h)}。"
    else:
        grid_px_w = min(grid_max_w, max(300, width - margin * 2 - side_panel_w))
        grid_px_h = min(grid_max_h, max(300, height - grid_top - margin))
        grid_text = f"主网格建议 x={grid_left}, y={grid_top}, size={int(grid_px_w)}x{int(grid_px_h)}，按场景自定格数。"
    legend_x = grid_left + int(grid_px_w) + 22 if side_panel_w else max(margin, width - 210)
    if legend_x + 180 > width - 20:
        legend_x = max(margin, width - 210)
    legend_y = grid_top if side_panel_w else max(grid_top + 24, height - 140)
    return (
        f"标题栏占 y=0..{header_h}，标题 x={margin}, y=42，摘要 x={margin}, y=72。"
        f"\n{grid_text}"
        f"\n图例建议 x={legend_x}, y={legend_y}, width=170-190, height=105-125；"
        "图例不要放进 token 密集区域。"
        "\n实体 token 半径 18-24；友方圆形蓝/青，敌方圆形红/橙，当前行动加金色外圈；"
        "障碍/掩体先画在 token 下方。"
        "\n右侧可追加“态势”小面板，写 2-4 行短句；不要让右侧面板与主地图重叠。"
    )


def _player_position_records(session: Any, battle: dict[str, Any]) -> list[dict[str, Any]]:
    participants = dict(getattr(session, "participants", {}) or {})
    characters = dict(getattr(session, "characters", {}) or {})
    player_character_map = dict(getattr(session, "player_character_map", {}) or {})
    entities = _battle_entity_list(battle)
    records: list[dict[str, Any]] = []
    seen_characters: set[str] = set()
    for player_id, character_id in player_character_map.items():
        player_id = str(player_id or "").strip()
        character_id = str(character_id or "").strip()
        if not player_id and not character_id:
            continue
        character = characters.get(character_id)
        entity = _find_character_entity(entities, player_id, character_id)
        records.append(_player_position_record(participants, player_id, character_id, character, entity))
        if character_id:
            seen_characters.add(character_id)
    for character_id, character in characters.items():
        player_id = str(getattr(character, "player_id", "") or "").strip()
        if not player_id or character_id in seen_characters:
            continue
        entity = _find_character_entity(entities, player_id, str(character_id))
        records.append(_player_position_record(participants, player_id, str(character_id), character, entity))
    return records[:18]


def _battle_entity_list(battle: dict[str, Any]) -> list[dict[str, Any]]:
    grid = dict((battle or {}).get("grid") or {})
    raw_entities = grid.get("entities") or []
    if isinstance(raw_entities, dict):
        return [{"id": str(entity_id), **dict(entity)} for entity_id, entity in raw_entities.items()]
    if isinstance(raw_entities, list):
        return [dict(item) for item in raw_entities if isinstance(item, dict)]
    return []


def _find_character_entity(entities: list[dict[str, Any]], player_id: str, character_id: str) -> dict[str, Any] | None:
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        tags = dict(entity.get("tags") or {})
        if character_id and (entity_id == character_id or str(tags.get("character_id") or "") == character_id):
            return entity
        if player_id and str(tags.get("player_id") or "") == player_id:
            return entity
    return None


def _player_position_record(
    participants: dict[str, Any],
    player_id: str,
    character_id: str,
    character: Any,
    entity: dict[str, Any] | None,
) -> dict[str, Any]:
    participant = dict(participants.get(player_id) or {})
    character_name = str(getattr(character, "name", "") or character_id or "未绑定角色")
    entity = dict(entity or {})
    x = entity.get("x")
    y = entity.get("y")
    placed = _is_number_like(x) and _is_number_like(y)
    return {
        "player_id": player_id,
        "player_name": _safe_text(participant.get("display_name") or player_id or "玩家", 40),
        "character_id": character_id,
        "character_name": _safe_text(character_name, 40),
        "entity_id": str(entity.get("id") or character_id or ""),
        "x": int(float(x)) if placed else None,
        "y": int(float(y)) if placed else None,
        "faction": str(entity.get("faction") or entity.get("team") or "player"),
        "placed": placed,
    }


def _player_positions_prompt_text(records: list[dict[str, Any]]) -> str:
    if not records:
        return "没有玩家角色绑定或实体记录。不要在地图上伪造玩家位置；在右侧态势面板写“玩家位置未登记”。"
    placed = [record for record in records if record.get("placed")]
    unplaced = [record for record in records if not record.get("placed")]
    lines: list[str] = []
    if placed:
        lines.append("已登记坐标，必须在主地图上画出：")
        for index, record in enumerate(placed[:12], start=1):
            lines.append(
                "- "
                f"P{index} {record.get('character_name') or record.get('character_id')} / "
                f"{record.get('player_name')}：grid=({record.get('x')},{record.get('y')}), "
                f"entity_id={record.get('entity_id')}"
            )
    if unplaced:
        names = "、".join(_compact_label(str(record.get("character_name") or record.get("player_name") or "玩家"), 8) for record in unplaced[:10])
        lines.append(f"未登记坐标：{names}。不要把这些玩家放进格子；在态势面板写“待放置”。")
    if not placed:
        lines.append("当前没有任何玩家/PC 的 Spatial 坐标。必须明确标注“玩家位置未登记”，不要假造站位。")
    return "\n".join(lines)


def _is_number_like(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _extract_svg(text: str) -> str:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    start = stripped.find("<svg")
    end = stripped.rfind("</svg>")
    if start < 0 or end < 0:
        return ""
    return stripped[start : end + len("</svg>")].strip()


ALLOWED_TAGS = {
    "svg",
    "g",
    "defs",
    "title",
    "desc",
    "rect",
    "line",
    "path",
    "circle",
    "ellipse",
    "polygon",
    "polyline",
    "text",
    "tspan",
    "marker",
    "pattern",
    "linearGradient",
    "radialGradient",
    "stop",
}

ALLOWED_ATTRS = {
    "id",
    "class",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "width",
    "height",
    "viewBox",
    "d",
    "points",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-dasharray",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
    "transform",
    "font-size",
    "font-family",
    "font-weight",
    "paint-order",
    "text-anchor",
    "dominant-baseline",
    "marker-end",
    "marker-start",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "patternUnits",
    "patternTransform",
}


def sanitize_svg(
    svg: str,
    width: int,
    height: int,
    title: str,
    player_positions: list[dict[str, Any]] | None = None,
    grid_width: int = 0,
    grid_height: int = 0,
) -> str:
    if len(svg) > 120_000:
        raise ValueError("SVG 太大，已拒绝。")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError(f"SVG XML 解析失败：{exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("根元素不是 svg。")
    clean_root = _clean_element(root)
    if clean_root is None:
        raise ValueError("SVG 没有可保留内容。")
    clean_root.set("xmlns", "http://www.w3.org/2000/svg")
    clean_root.set("width", str(width))
    clean_root.set("height", str(height))
    clean_root.set("viewBox", f"0 0 {width} {height}")
    if not any(_local_name(child.tag) == "title" for child in list(clean_root)):
        title_el = ET.Element("title")
        title_el.text = _safe_text(title, 80)
        clean_root.insert(0, title_el)
    _normalize_svg_layout(
        clean_root,
        width=width,
        height=height,
        title=title,
        player_positions=player_positions or [],
        grid_width=grid_width,
        grid_height=grid_height,
    )
    serialized = ET.tostring(clean_root, encoding="unicode", short_empty_elements=True)
    if len(serialized) > 120_000:
        raise ValueError("清洗后的 SVG 仍然过大，已拒绝。")
    return serialized


def _normalize_svg_layout(
    root: ET.Element,
    width: int,
    height: int,
    title: str,
    player_positions: list[dict[str, Any]] | None = None,
    grid_width: int = 0,
    grid_height: int = 0,
) -> None:
    _ensure_canvas_background(root, width, height)
    _ensure_visible_title(root, width, title)
    _ensure_title_bar(root, width)
    _reserve_header_space(root, height)
    _ensure_legend(root, width, height)
    _normalize_text_elements(root, title)
    _center_marker_labels(root)
    _ensure_player_positions(root, width, height, player_positions or [], grid_width, grid_height)
    _add_text_halo_attributes(root)


def _ensure_canvas_background(root: ET.Element, width: int, height: int) -> None:
    for element in _iter_elements(root):
        if _local_name(element.tag) != "rect":
            continue
        x = _attr_float(element.get("x"), 0.0)
        y = _attr_float(element.get("y"), 0.0)
        w = _attr_float(element.get("width"), 0.0)
        h = _attr_float(element.get("height"), 0.0)
        fill = str(element.get("fill") or "").strip().lower()
        if x <= 2 and y <= 2 and w >= width * 0.9 and h >= height * 0.9 and fill and fill != "none":
            return
    bg = ET.Element(
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(width),
            "height": str(height),
            "fill": "#eaf1f6",
        },
    )
    root.insert(_metadata_end_index(root), bg)


def _ensure_visible_title(root: ET.Element, width: int, title: str) -> None:
    safe_title = _safe_text(title or "战场地图", 32)
    for element in _iter_elements(root):
        if _local_name(element.tag) != "text":
            continue
        text = _element_text(element)
        y = _attr_float(element.get("y"), 9999.0)
        size = _attr_float(element.get("font-size"), 16.0)
        if y <= 120 and (safe_title in text or text in safe_title or size >= 24):
            return
    title_el = ET.Element(
        "text",
        {
            "x": "36",
            "y": "50",
            "font-family": MAP_FONT_FAMILY,
            "font-size": "28",
            "font-weight": "700",
            "fill": "#17212b",
        },
    )
    title_el.text = safe_title
    insert_at = min(len(root), _metadata_end_index(root) + 1)
    root.insert(insert_at, title_el)


def _ensure_title_bar(root: ET.Element, width: int) -> None:
    for element in list(root):
        if _local_name(element.tag) != "rect":
            continue
        x = _attr_float(element.get("x"), 0.0)
        y = _attr_float(element.get("y"), 0.0)
        w = _attr_float(element.get("width"), 0.0)
        h = _attr_float(element.get("height"), 0.0)
        if x <= 2 and y <= 2 and w >= width * 0.8 and 64 <= h <= 128:
            return
    top_text_index = None
    for index, child in enumerate(list(root)):
        if _contains_top_text(child):
            top_text_index = index
            break
    if top_text_index is None:
        return
    band = ET.Element(
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": str(width),
            "height": "96",
            "fill": "#eaf1f6",
            "opacity": "0.96",
        },
    )
    divider = ET.Element(
        "line",
        {
            "x1": "0",
            "y1": "96",
            "x2": str(width),
            "y2": "96",
            "stroke": "#b8c7d3",
            "stroke-width": "2",
        },
    )
    root.insert(top_text_index, band)
    root.insert(top_text_index + 1, divider)


def _reserve_header_space(root: ET.Element, height: int) -> None:
    min_y = 999999.0
    max_y = 0.0
    for child in list(root):
        if _is_header_or_background_child(child):
            continue
        box = _element_bbox(child)
        if box is None:
            continue
        _, y1, _, y2 = box
        min_y = min(min_y, y1)
        max_y = max(max_y, y2)
    header_bottom = 112.0
    if min_y == 999999.0 or min_y >= header_bottom:
        return
    dy = min(header_bottom - min_y, max(0.0, height - 8.0 - max_y))
    if dy <= 0:
        return
    for child in list(root):
        if not _is_header_or_background_child(child):
            _shift_element(child, 0.0, dy)


def _is_header_or_background_child(element: ET.Element) -> bool:
    tag = _local_name(element.tag)
    if tag in {"title", "desc"}:
        return True
    box = _element_bbox(element)
    if box is None:
        return False
    x1, y1, x2, y2 = box
    if tag == "rect" and x1 <= 2 and y1 <= 2 and (x2 - x1) >= 300 and (y2 - y1) >= 300:
        return True
    if y1 <= 4 and y2 <= 104:
        return True
    if tag == "line" and y1 <= 104 and y2 <= 104:
        return True
    if _contains_top_text(element):
        return True
    return False


def _ensure_legend(root: ET.Element, width: int, height: int) -> None:
    for element in _iter_elements(root):
        if _local_name(element.tag) == "text" and "图例" in _element_text(element):
            return
    legend_w = 188
    legend_h = 116
    x = max(24, width - legend_w - 28)
    y = max(112, height - legend_h - 28)
    group = ET.Element("g", {"id": "auto-map-legend"})
    group.extend(
        [
            ET.Element(
                "rect",
                {
                    "x": _fmt_number(x),
                    "y": _fmt_number(y),
                    "width": str(legend_w),
                    "height": str(legend_h),
                    "fill": "#ffffff",
                    "stroke": "#2f3b46",
                    "stroke-width": "2",
                    "opacity": "0.96",
                },
            ),
            _text_el(x + 18, y + 28, "图例", 18, "#17212b", weight="700"),
            ET.Element("circle", {"cx": _fmt_number(x + 30), "cy": _fmt_number(y + 55), "r": "10", "fill": "#3f88c5", "stroke": "#18324a", "stroke-width": "2"}),
            _text_el(x + 50, y + 61, "友方", 16, "#17212b"),
            ET.Element("circle", {"cx": _fmt_number(x + 30), "cy": _fmt_number(y + 82), "r": "10", "fill": "#d94841", "stroke": "#3a1f1b", "stroke-width": "2"}),
            _text_el(x + 50, y + 88, "敌方", 16, "#17212b"),
            ET.Element("rect", {"x": _fmt_number(x + 108), "y": _fmt_number(y + 45), "width": "20", "height": "20", "fill": "#4ade80", "stroke": "#2f3b46", "stroke-width": "2"}),
            _text_el(x + 136, y + 62, "出口", 16, "#17212b"),
            ET.Element("rect", {"x": _fmt_number(x + 108), "y": _fmt_number(y + 73), "width": "20", "height": "20", "fill": "#6b7280", "stroke": "#374151", "stroke-width": "2"}),
            _text_el(x + 136, y + 90, "障碍", 16, "#17212b"),
        ]
    )
    root.append(group)


def _normalize_text_elements(root: ET.Element, title: str) -> None:
    safe_title = _safe_text(title or "", 32)
    for element in _iter_elements(root):
        if _local_name(element.tag) not in {"text", "tspan"}:
            continue
        if not element.get("font-family") or "Noto Sans" not in str(element.get("font-family")):
            element.set("font-family", MAP_FONT_FAMILY)
        text = _element_text(element)
        size = _attr_float(element.get("font-size"), 16.0)
        if safe_title and (safe_title in text or text in safe_title):
            min_size, max_size, max_chars = 22, 30, 32
        elif "图例" in text:
            min_size, max_size, max_chars = 16, 22, 8
        elif re.fullmatch(r"\d{1,2}", text):
            min_size, max_size, max_chars = 12, 18, 2
        else:
            min_size, max_size, max_chars = 12, 22, 16
        clamped_size = int(max(min_size, min(max_size, round(size))))
        element.set("font-size", str(clamped_size))
        if element.text:
            element.text = _compact_label(element.text, max_chars)


def _center_marker_labels(root: ET.Element) -> None:
    circles: list[tuple[ET.Element, float, float, float]] = []
    for element in _iter_elements(root):
        if _local_name(element.tag) != "circle":
            continue
        r = _attr_float(element.get("r"), 0.0)
        fill = str(element.get("fill") or "").strip().lower()
        if 12 <= r <= 28 and fill and fill != "none":
            circles.append((element, _attr_float(element.get("cx")), _attr_float(element.get("cy")), r))
    if not circles:
        return
    for element in _iter_elements(root):
        if _local_name(element.tag) != "text":
            continue
        text = _element_text(element)
        if not text or len(text) > 10:
            continue
        if not _is_light_label_fill(element.get("fill")):
            continue
        if str(element.get("text-anchor") or "").lower() == "middle" and str(element.get("dominant-baseline") or "").lower() in {"middle", "central"}:
            continue
        x = _attr_float(element.get("x"), 99999.0)
        y = _attr_float(element.get("y"), 99999.0)
        nearest: tuple[ET.Element, float, float, float] | None = None
        nearest_score = 999999.0
        for circle in circles:
            _, cx, cy, r = circle
            dx = abs(x - cx)
            dy = abs(y - cy)
            if dx <= max(10, r * 1.50) and dy <= max(8, r * 0.60):
                score = dx + dy
                if score < nearest_score:
                    nearest = circle
                    nearest_score = score
        if nearest is None:
            continue
        _, cx, cy, _ = nearest
        element.set("x", _fmt_number(cx))
        element.set("y", _fmt_number(cy))
        element.set("text-anchor", "middle")
        element.set("dominant-baseline", "middle")
        size = int(max(13, min(18, round(_attr_float(element.get("font-size"), 16.0)))))
        element.set("font-size", str(size))
        if element.text:
            element.text = _compact_label(element.text, 6)


def _ensure_player_positions(
    root: ET.Element,
    width: int,
    height: int,
    player_positions: list[dict[str, Any]],
    grid_width: int,
    grid_height: int,
) -> None:
    if not player_positions:
        _add_player_position_panel(root, width, height, [], "玩家位置未登记")
        return
    placed = [record for record in player_positions if record.get("placed")]
    unplaced = [record for record in player_positions if not record.get("placed")]
    if placed and grid_width > 0 and grid_height > 0:
        grid_box = _find_main_grid_rect(root, width, height)
        if grid_box is not None:
            _add_player_position_overlays(root, placed, grid_box, grid_width, grid_height)
    if placed or unplaced:
        title = "玩家位置" if placed else "玩家位置未登记"
        _add_player_position_panel(root, width, height, player_positions, title)


def _find_main_grid_rect(root: ET.Element, width: int, height: int) -> tuple[float, float, float, float] | None:
    best: tuple[float, float, float, float] | None = None
    best_area = 0.0
    for element in _iter_elements(root):
        if _local_name(element.tag) != "rect":
            continue
        box = _element_bbox(element)
        if box is None:
            continue
        x1, y1, x2, y2 = box
        w = x2 - x1
        h = y2 - y1
        if w < 220 or h < 220:
            continue
        if x1 <= 2 and y1 <= 2 and w >= width * 0.85 and h >= height * 0.85:
            continue
        if y1 < 96 or x1 > width - 260:
            continue
        area = w * h
        if area > best_area:
            best = box
            best_area = area
    return best


def _add_player_position_overlays(
    root: ET.Element,
    placed: list[dict[str, Any]],
    grid_box: tuple[float, float, float, float],
    grid_width: int,
    grid_height: int,
) -> None:
    x1, y1, x2, y2 = grid_box
    cell_w = (x2 - x1) / max(1, grid_width)
    cell_h = (y2 - y1) / max(1, grid_height)
    group = ET.Element("g", {"id": "auto-player-positions"})
    for index, record in enumerate(placed[:12], start=1):
        try:
            gx = int(record.get("x"))
            gy = int(record.get("y"))
        except (TypeError, ValueError):
            continue
        if gx < 0 or gy < 0 or gx >= grid_width or gy >= grid_height:
            continue
        cx = x1 + (gx + 0.5) * cell_w
        cy = y1 + (gy + 0.5) * cell_h
        label = f"P{index}"
        group.append(
            ET.Element(
                "circle",
                {
                    "cx": _fmt_number(cx),
                    "cy": _fmt_number(cy),
                    "r": "15",
                    "fill": "#7c3aed",
                    "stroke": "#ffffff",
                    "stroke-width": "4",
                    "opacity": "0.96",
                },
            )
        )
        group.append(
            ET.Element(
                "circle",
                {
                    "cx": _fmt_number(cx),
                    "cy": _fmt_number(cy),
                    "r": "19",
                    "fill": "none",
                    "stroke": "#312e81",
                    "stroke-width": "2",
                    "opacity": "0.96",
                },
            )
        )
        text = _text_el(cx, cy, label, 13, "#ffffff", weight="700")
        text.set("text-anchor", "middle")
        text.set("dominant-baseline", "middle")
        group.append(text)
    if list(group):
        root.append(group)


def _add_player_position_panel(
    root: ET.Element,
    width: int,
    height: int,
    records: list[dict[str, Any]],
    title: str,
) -> None:
    if any(_local_name(element.tag) == "text" and "玩家位置" in _element_text(element) for element in _iter_elements(root)):
        return
    panel_w = 212 if width >= 760 else min(190, width - 48)
    placed = [record for record in records if record.get("placed")]
    unplaced = [record for record in records if not record.get("placed")]
    line_count = max(2, min(7, len(records) + 1))
    panel_h = 46 + line_count * 22
    x = max(24, width - panel_w - 48)
    y = _next_side_panel_y(root, width, height, panel_h, x, panel_w)
    group = ET.Element("g", {"id": "auto-player-position-panel"})
    group.append(
        ET.Element(
            "rect",
            {
                "x": _fmt_number(x),
                "y": _fmt_number(y),
                "width": _fmt_number(panel_w),
                "height": _fmt_number(panel_h),
                "fill": "#fff7ed",
                "stroke": "#9a3412",
                "stroke-width": "2",
                "opacity": "0.97",
            },
        )
    )
    group.append(_text_el(x + 16, y + 28, title, 17, "#7c2d12", weight="700"))
    lines: list[str] = []
    if placed:
        for index, record in enumerate(placed[:5], start=1):
            coord = f"P{index}({record.get('x')},{record.get('y')})"
            name = str(record.get("character_name") or record.get("player_name") or "")
            remaining = 12 - len(coord)
            suffix = _compact_label(name, remaining) if remaining >= 4 else ""
            lines.append(f"{coord}{suffix}")
    if unplaced:
        names = "、".join(_compact_label(str(record.get("character_name") or record.get("player_name") or "PC"), 5) for record in unplaced[:4])
        lines.append(f"待放置：{names}")
    if not lines:
        lines = ["Spatial无坐标", "先放PC再画站位"]
    for index, line in enumerate(lines[:7]):
        group.append(_text_el(x + 16, y + 56 + index * 22, line, 14, "#431407"))
    root.append(group)


def _next_side_panel_y(
    root: ET.Element,
    width: int,
    height: int,
    panel_h: float,
    panel_x: float,
    panel_w: float,
) -> float:
    overlap_left = panel_x - 18
    overlap_right = panel_x + panel_w + 18
    max_y = 112.0
    for element in _iter_elements(root):
        tag = _local_name(element.tag)
        if tag not in {"rect", "text", "g"}:
            continue
        box = _element_bbox(element)
        if box is None:
            continue
        x1, y1, x2, y2 = box
        box_w = x2 - x1
        box_h = y2 - y1
        if x1 <= 2 and y1 <= 2 and box_w >= width * 0.85 and box_h >= height * 0.85:
            continue
        if box_w >= width * 0.75 and box_h >= height * 0.55:
            continue
        if y2 <= 100:
            continue
        if x2 < overlap_left or x1 > overlap_right:
            continue
        if box_w >= 30 and box_h >= 10:
            max_y = max(max_y, y2 + 14)
    return min(max_y, max(112.0, height - panel_h - 24))


def _add_text_halo_attributes(root: ET.Element) -> None:
    for element in _iter_elements(root):
        if _local_name(element.tag) not in {"text", "tspan"}:
            continue
        fill = str(element.get("fill") or "").strip()
        if not fill or fill.lower() == "none":
            continue
        if element.get("stroke"):
            continue
        if _is_light_label_fill(fill):
            element.set("stroke", "#0f172a")
            element.set("stroke-width", "0.65")
        else:
            element.set("stroke", "#f8fafc")
            element.set("stroke-width", "0.55")
        element.set("paint-order", "stroke fill")


def _is_light_label_fill(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if text in {"white", "#fff", "#ffffff", "rgb(255,255,255)", "rgb(255, 255, 255)"}:
        return True
    match = re.fullmatch(r"#([0-9a-f]{6})", text)
    if not match:
        return False
    raw = match.group(1)
    red = int(raw[0:2], 16)
    green = int(raw[2:4], 16)
    blue = int(raw[4:6], 16)
    return (0.299 * red + 0.587 * green + 0.114 * blue) >= 205


def _text_el(x: float, y: float, text: str, size: int, fill: str, weight: str = "") -> ET.Element:
    attrs = {
        "x": _fmt_number(x),
        "y": _fmt_number(y),
        "font-family": MAP_FONT_FAMILY,
        "font-size": str(size),
        "fill": fill,
    }
    if weight:
        attrs["font-weight"] = weight
    element = ET.Element("text", attrs)
    element.text = text
    return element


def _iter_elements(root: ET.Element):
    yield root
    for child in list(root):
        yield from _iter_elements(child)


def _metadata_end_index(root: ET.Element) -> int:
    index = 0
    children = list(root)
    while index < len(children) and _local_name(children[index].tag) in {"title", "desc"}:
        index += 1
    return index


def _contains_top_text(element: ET.Element) -> bool:
    if _local_name(element.tag) == "text" and _attr_float(element.get("y"), 9999.0) <= 110:
        return True
    return any(_contains_top_text(child) for child in list(element))


def _element_text(element: ET.Element) -> str:
    return "".join(element.itertext()).strip()


def _element_bbox(element: ET.Element) -> tuple[float, float, float, float] | None:
    tag = _local_name(element.tag)
    if tag == "rect":
        x = _attr_float(element.get("x"))
        y = _attr_float(element.get("y"))
        return (x, y, x + _attr_float(element.get("width")), y + _attr_float(element.get("height")))
    if tag == "line":
        x1 = _attr_float(element.get("x1"))
        y1 = _attr_float(element.get("y1"))
        x2 = _attr_float(element.get("x2"))
        y2 = _attr_float(element.get("y2"))
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag == "circle":
        cx = _attr_float(element.get("cx"))
        cy = _attr_float(element.get("cy"))
        r = _attr_float(element.get("r"))
        return (cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx = _attr_float(element.get("cx"))
        cy = _attr_float(element.get("cy"))
        rx = _attr_float(element.get("rx"))
        ry = _attr_float(element.get("ry"))
        return (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag in {"polygon", "polyline"}:
        numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", str(element.get("points") or ""))]
        points = [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)]
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return (min(xs), min(ys), max(xs), max(ys))
    if tag in {"text", "tspan"}:
        x = _attr_float(element.get("x"))
        y = _attr_float(element.get("y"))
        size = max(10.0, _attr_float(element.get("font-size"), 16.0))
        text_width = min(360.0, max(10.0, len(_element_text(element)) * size * 0.7))
        anchor = str(element.get("text-anchor") or "").lower()
        if anchor == "middle":
            x1 = x - text_width / 2
            x2 = x + text_width / 2
        elif anchor == "end":
            x1 = x - text_width
            x2 = x
        else:
            x1 = x
            x2 = x + text_width
        return (x1, y - size, x2, y + size * 0.35)
    boxes = [_element_bbox(child) for child in list(element)]
    boxes = [box for box in boxes if box is not None]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _shift_element(element: ET.Element, dx: float, dy: float) -> None:
    for key in ("x", "x1", "x2", "cx"):
        if key in element.attrib:
            element.set(key, _fmt_number(_attr_float(element.get(key)) + dx))
    for key in ("y", "y1", "y2", "cy"):
        if key in element.attrib:
            element.set(key, _fmt_number(_attr_float(element.get(key)) + dy))
    if "points" in element.attrib:
        numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", str(element.get("points") or ""))]
        shifted = []
        for index, number in enumerate(numbers):
            shifted.append(_fmt_number(number + (dx if index % 2 == 0 else dy)))
        element.set("points", " ".join(shifted))
    for child in list(element):
        _shift_element(child, dx, dy)


def _compact_label(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _attr_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _fmt_number(value: float | int) -> str:
    number = float(value)
    if abs(number - round(number)) < 0.001:
        return str(int(round(number)))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _clean_element(element: ET.Element) -> Optional[ET.Element]:
    tag = _local_name(element.tag)
    if tag not in ALLOWED_TAGS:
        return None
    cleaned = ET.Element(tag)
    for raw_key, raw_value in element.attrib.items():
        key = _local_name(raw_key)
        if key not in ALLOWED_ATTRS:
            continue
        value = _safe_attr_value(raw_value)
        if value is not None:
            cleaned.set(key, value)
    if tag in {"text", "tspan"} and not cleaned.get("font-family"):
        cleaned.set("font-family", MAP_FONT_FAMILY)
    if element.text and tag in {"title", "desc", "text", "tspan"}:
        cleaned.text = _safe_text(element.text, 120)
    for child in list(element):
        cleaned_child = _clean_element(child)
        if cleaned_child is not None:
            cleaned.append(cleaned_child)
            if child.tail and tag in {"text", "tspan"}:
                cleaned_child.tail = _safe_text(child.tail, 80)
    return cleaned


def _safe_attr_value(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    forbidden = ("javascript:", "data:", "http://", "https://", "file:", "<", ">", "&{")
    if any(item in lowered for item in forbidden):
        if not re.fullmatch(r"url\(\s*#[A-Za-z_][\w:.-]*\s*\)", text):
            return None
    if len(text) > 2000:
        text = text[:2000]
    return text


def _safe_text(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]+", "", str(value or ""))
    return _short_text(text, limit)


def _short_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _safe_file_stem(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff_.-]+", "_", value.strip())
    safe = safe.strip("._-")
    return safe[:60] or "trpg_map"


def _audit_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(payload)
    if "raw_text" in cleaned:
        cleaned["raw_text"] = _short_text(cleaned["raw_text"], 300)
    if "prompt" in cleaned:
        cleaned["prompt"] = _short_text(cleaned["prompt"], 500)
    if "battle" in cleaned:
        cleaned["battle"] = "<omitted>"
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
        if key not in {"self", "session", "latest_session", "response", "raw_text", "svg", "result"}
    }


class _LlmFailureResponse:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text

    def __str__(self) -> str:
        return self.completion_text


def _is_retryable_llm_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
        "connection",
        "server disconnected",
        "temporarily unavailable",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
    )
    return any(marker in name or marker in text for marker in retryable_markers)
