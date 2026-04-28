from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..core.plugin_log import get_plugin_logger
from ..storage.json_repository import JsonGameRepository


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
        map_prompt = _build_map_prompt(
            title=title,
            prompt=prompt or str(session.scene.get("summary") or "根据当前跑团场景绘制战术地图。"),
            style=style,
            width=width,
            height=height,
            grid_width=grid_width,
            grid_height=grid_height,
            battle=battle,
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
            svg = sanitize_svg(svg, width=width, height=height, title=title)
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


MAP_SYSTEM_PROMPT = """你是 TRPG 地图绘制子 agent。你的唯一任务是输出一张完整、可保存的 SVG 地图。

硬性规则：
1. 只输出 <svg ...>...</svg>，不要 Markdown、解释、JSON 或代码围栏。
2. SVG 必须自包含，禁止 script、foreignObject、image、use、a、iframe、animation、外部 URL、base64、data URI。
3. 只画视觉示意，不得改变游戏事实；坐标、移动、视线、攻击范围仍以主系统 Spatial Engine 为准。
4. 若收到战棋快照，优先按快照画网格、障碍、友方、敌方和关键位置；不要发明实体坐标。
5. 使用清晰俯视战术图：网格、墙体/障碍、掩体、入口出口、友敌标记、简短标签、图例。
6. 文本标签要短，避免长段说明。"""


def _build_map_prompt(
    title: str,
    prompt: str,
    style: str,
    width: int,
    height: int,
    grid_width: int,
    grid_height: int,
    battle: dict[str, Any],
) -> str:
    import json

    battle_text = json.dumps(battle, ensure_ascii=False, separators=(",", ":"))
    if len(battle_text) > 18_000:
        battle_text = battle_text[:18_000] + "...(truncated)"
    return f"""请生成 SVG 地图。

标题：{title}
画布：{width}x{height}
建议网格：{grid_width or "自定"} x {grid_height or "自定"}
风格：{style}

绘图需求：
{_short_text(prompt, 2000)}

当前战棋快照：
{battle_text or "无"}

输出要求：
- 根元素必须是 <svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
- 使用 rect/line/path/circle/polygon/text 等基础元素。
- 友方用冷色，敌方用红/橙色，障碍用深灰，掩体用棕灰，出口入口用绿色或黄色。
- 左上角写短标题，右下角放 3-5 项图例。
- 只输出 SVG。"""


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


def sanitize_svg(svg: str, width: int, height: int, title: str) -> str:
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
    serialized = ET.tostring(clean_root, encoding="unicode", short_empty_elements=True)
    if len(serialized) > 120_000:
        raise ValueError("清洗后的 SVG 仍然过大，已拒绝。")
    return serialized


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
