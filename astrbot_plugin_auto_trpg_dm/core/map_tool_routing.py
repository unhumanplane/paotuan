from __future__ import annotations


VISUAL_REQUEST_ACTION_TERMS = (
    "画",
    "绘制",
    "生成",
    "渲染",
    "显示",
    "展示",
    "打开",
    "调出",
    "可视化",
    "标出来",
    "附上",
    "发一张",
    "给我一张",
    "来一张",
    "show",
    "display",
    "render",
    "generate",
    "draw",
)

VISUAL_REQUEST_ARTIFACT_TERMS = (
    "地图",
    "示意",
    "示意图",
    "站位图",
    "俯视",
    "布局",
    "路线",
    "路径",
    "svg",
    "map",
    "grid",
    "route",
    "path",
)

EXPLICIT_VISUAL_MAP_ARTIFACT_TERMS = (
    "示意图",
    "站位图",
    "布局图",
    "俯视图",
    "路线图",
    "格子图",
    "grid map",
    "battle map",
)

OVERVIEW_TOPOLOGY_REQUEST_TERMS = (
    "overview",
    "topology",
    "拓扑",
    "概览",
    "总览",
    "大地图",
    "路线",
    "路径",
    "route",
    "path",
    "关系",
    "区域",
    "地标",
    "地点",
    "当前在哪",
    "我在哪里",
)

STRICT_GRID_REQUEST_TERMS = (
    "地图",
    "战场",
    "战棋",
    "站位",
    "位置",
    "地形",
    "布局",
    "格子",
    "网格",
    "敌我",
    "障碍",
    "掩体",
    "入口",
    "出口",
    "strict",
    "tactical",
    "battle map",
    "grid",
    "map",
)

LEGACY_SVG_FALLBACK_TERMS = (
    "generate_map_svg",
    "legacy",
    "llm svg",
    "llm-svg",
    "旧版",
    "旧 svg",
    "旧svg",
    "兜底",
    "降级",
    "fallback",
    "风格实验",
    "风格草图",
    "style experiment",
    "migration",
    "迁移",
)


def add_map_renderer_tools(names: list[str], message: str = "") -> list[str]:
    selected = list(names)
    if looks_overview_map_request(message):
        selected.append("render_overview_topology_svg")
    elif looks_strict_grid_map_request(message):
        selected.append("render_strict_grid_svg")
    if looks_legacy_svg_fallback_request(message):
        selected.append("generate_map_svg")
    return list(dict.fromkeys(selected))


def looks_visual_map_request(message: str) -> bool:
    text = _normalized(message)
    if not text:
        return False
    if _contains_any(text, EXPLICIT_VISUAL_MAP_ARTIFACT_TERMS):
        return True
    return _contains_any(text, VISUAL_REQUEST_ACTION_TERMS) and _contains_any(text, VISUAL_REQUEST_ARTIFACT_TERMS)


def looks_overview_map_request(message: str) -> bool:
    text = _normalized(message)
    if not looks_visual_map_request(text):
        return False
    return _contains_any(text, OVERVIEW_TOPOLOGY_REQUEST_TERMS)


def looks_strict_grid_map_request(message: str) -> bool:
    text = _normalized(message)
    if not text or looks_overview_map_request(text):
        return False
    if not looks_visual_map_request(text):
        return False
    return _contains_any(text, STRICT_GRID_REQUEST_TERMS)


def looks_legacy_svg_fallback_request(message: str) -> bool:
    text = _normalized(message)
    if not text or not _contains_any(text, LEGACY_SVG_FALLBACK_TERMS):
        return False
    return looks_visual_map_request(text) or "generate_map_svg" in text


def _normalized(message: str) -> str:
    return str(message or "").strip().lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
