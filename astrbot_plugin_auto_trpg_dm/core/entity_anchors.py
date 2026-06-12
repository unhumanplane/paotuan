from __future__ import annotations

import json
import re
from typing import Any


ANCHOR_CRITICALITY_VALUES = {
    "anchor",
    "critical",
    "key",
    "major",
    "locked",
    "核心",
    "关键",
    "重要",
    "锚点",
    "锁定",
}

ANCHOR_ENTITY_TYPES = {
    "character",
    "clue",
    "door",
    "entity",
    "evidence",
    "faction",
    "gate",
    "item",
    "location",
    "npc",
    "objective",
    "object",
    "pc",
    "treasure",
    "vehicle",
    "角色",
    "线索",
    "证据",
    "阵营",
    "物品",
    "宝物",
    "地点",
    "门",
    "载具",
}

ANCHOR_STATUS_TERMS = (
    "已确认",
    "确认",
    "已救出",
    "救出",
    "解绑",
    "随队",
    "跟随",
    "护送",
    "同行",
    "with_party",
    "escorted",
    "rescued",
    "alive",
    "生还",
    "活着",
    "死亡",
    "已死",
    "被俘",
    "被捕",
    "已获得",
    "获得",
    "拾取",
    "拿到",
    "持有",
    "携带",
    "交付",
    "移交",
    "消耗",
    "用掉",
    "已开启",
    "已打开",
    "已关闭",
    "已锁",
    "已解锁",
    "可通行",
    "不可通行",
    "摧毁",
    "破坏",
    "修复",
    "激活",
    "关闭",
    "开启",
    "转移",
    "安置",
    "藏好",
)

ANCHOR_EVENT_TYPE_TERMS = (
    "status",
    "state",
    "fact",
    "npc",
    "item",
    "treasure",
    "clue",
    "evidence",
    "location",
    "door",
    "gate",
    "custody",
    "possession",
    "rescue",
    "escort",
    "transfer",
    "consumed",
    "used",
    "opened",
    "closed",
    "destroyed",
    "状态",
    "事实",
    "救出",
    "护送",
    "持有",
    "获得",
    "交付",
    "消耗",
    "开启",
    "关闭",
    "摧毁",
)

FACT_DURABLE_KEYS = (
    "criticality",
    "continuity_tags",
    "current_location",
    "location",
    "custody",
    "holder",
    "owner",
    "protected_by",
    "with_party",
    "last_confirmed_at",
    "aliases",
    "alias",
)

FACT_PRESERVE_IF_OMITTED_KEYS = (
    "created_at",
    "current_status",
    "historical_facts",
    "unknowns",
    "authoritative_events",
    "evidence",
    *FACT_DURABLE_KEYS,
)

LIFE_CONFIRMED_TERMS = ("已确认生还", "确认生还", "生还", "活着", "alive")
RESCUE_OR_ESCORT_TERMS = ("已救出", "救出", "解绑", "随队", "同行", "跟随", "护送", "with_party", "rescued", "escorted")
POSSESSION_TERMS = ("已获得", "获得", "拾取", "拿到", "持有", "携带", "交付", "移交", "holder", "owner")
STATE_LOCK_TERMS = ("已开启", "已打开", "已关闭", "已锁", "已解锁", "可通行", "不可通行", "摧毁", "破坏", "修复", "激活")
TERMINAL_TERMS = ("死亡", "已死", "阵亡", "被俘", "被捕", "失能")

LIFE_UNCERTAINTY_TERMS = ("是否还活", "还活", "生死", "是否死亡", "死了吗", "遇难", "沉没", "是否随船")
LOCATION_UNCERTAINTY_TERMS = (
    "下落不明",
    "下落悬",
    "不见",
    "不在",
    "未找到",
    "没找到",
    "可能被转移",
    "可能转移",
    "转移走",
    "被转移走",
    "是否被转移",
    "还在门后",
    "可能在门后",
    "仍在门后",
    "还在原地",
    "可能还在",
)
POSSESSION_UNCERTAINTY_TERMS = (
    "未获得",
    "尚未获得",
    "没获得",
    "没有获得",
    "没拿到",
    "未拿到",
    "是否拿到",
    "是否获得",
    "是否还在",
    "还在祭坛",
    "仍在祭坛",
    "还在原处",
    "还在箱",
    "还在柜",
    "还在原地",
    "不见",
    "丢了",
    "遗失",
)
STATE_UNCERTAINTY_TERMS = (
    "还没打开",
    "尚未打开",
    "未开启",
    "没有开启",
    "还关着",
    "仍然关闭",
    "是否开启",
    "是否打开",
    "还完好",
    "未摧毁",
    "没有摧毁",
)


def normalized_anchor_text(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        raw = value
    else:
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            raw = str(value)
    return re.sub(r"\s+", "", raw).lower()


def contains_any_text(text: str, terms: tuple[str, ...] | set[str]) -> bool:
    if not text:
        return False
    return any(str(term).lower() in text for term in terms if str(term))


def entity_aliases(entity_id: str, fact: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for value in (entity_id, fact.get("entity_id"), fact.get("name")):
        if value not in (None, "", [], {}):
            aliases.append(str(value))
    raw_aliases = fact.get("aliases") or fact.get("alias") or []
    if isinstance(raw_aliases, str):
        aliases.append(raw_aliases)
    elif isinstance(raw_aliases, list):
        aliases.extend(str(item) for item in raw_aliases if str(item).strip())
    return list(dict.fromkeys(alias.strip() for alias in aliases if alias and alias.strip()))


def entity_fact_relevance_score(entity_id: str, fact: dict[str, Any], query_text: str = "") -> int:
    query = normalized_anchor_text(query_text)
    score = 0
    aliases = entity_aliases(entity_id, fact)
    if query:
        for alias in aliases:
            normalized_alias = normalized_anchor_text(alias)
            if normalized_alias and normalized_alias in query:
                score += 120
                break
    criticality = normalized_anchor_text(fact.get("criticality"))
    if criticality and (criticality in ANCHOR_CRITICALITY_VALUES or contains_any_text(criticality, ANCHOR_CRITICALITY_VALUES)):
        score += 90
    fact_text = normalized_anchor_text(
        {
            "entity_type": fact.get("entity_type"),
            "current_status": fact.get("current_status"),
            "current_location": fact.get("current_location") or fact.get("location"),
            "custody": fact.get("custody"),
            "holder": fact.get("holder"),
            "owner": fact.get("owner"),
            "continuity_tags": fact.get("continuity_tags"),
            "historical_facts": fact.get("historical_facts"),
            "unknowns": fact.get("unknowns"),
        }
    )
    entity_type = normalized_anchor_text(fact.get("entity_type"))
    if entity_type in ANCHOR_ENTITY_TYPES:
        score += 8
    if contains_any_text(fact_text, ANCHOR_STATUS_TERMS):
        score += 45
    for key in ("current_location", "location", "custody", "holder", "owner", "with_party", "protected_by"):
        if fact.get(key) not in (None, "", [], {}):
            score += 18
            break
    if fact.get("authoritative_events"):
        score += 8
    if query and score < 120:
        query_fragments = [part for part in re.split(r"[，。；、,\s]+", query) if len(part) >= 2]
        if query_fragments and any(fragment in fact_text for fragment in query_fragments[:8]):
            score += 16
    return score


def is_entity_anchor_fact(entity_id: str, fact: dict[str, Any], query_text: str = "") -> bool:
    score = entity_fact_relevance_score(entity_id, fact, query_text)
    if query_text and score >= 120:
        return True
    return score >= 45


def select_anchor_entity_facts(
    facts: Any,
    *,
    message: str = "",
    limit: int = 6,
) -> list[tuple[str, dict[str, Any], int]]:
    if not isinstance(facts, dict):
        return []
    scored: list[tuple[str, dict[str, Any], int]] = []
    query_text = normalized_anchor_text(message)
    for entity_id, fact in facts.items():
        if not isinstance(fact, dict):
            continue
        score = entity_fact_relevance_score(str(entity_id), fact, query_text)
        if score <= 0:
            continue
        if query_text or is_entity_anchor_fact(str(entity_id), fact):
            scored.append((str(entity_id), fact, score))
    scored.sort(
        key=lambda item: (
            item[2],
            str(item[1].get("last_confirmed_at") or item[1].get("updated_at") or ""),
            str(item[1].get("created_at") or ""),
            item[0],
        ),
        reverse=True,
    )
    return scored[: max(0, limit)]


def authoritative_event_ids_for_facts(anchor_facts: list[tuple[str, dict[str, Any], int]]) -> set[str]:
    event_ids: set[str] = set()
    for _entity_id, fact, _score in anchor_facts:
        for event_id in fact.get("authoritative_events") or []:
            safe = str(event_id or "").strip()
            if safe:
                event_ids.add(safe)
    return event_ids


def timeline_event_anchor_score(
    event: dict[str, Any],
    *,
    anchor_entity_ids: set[str],
    authoritative_event_ids: set[str],
) -> int:
    event_id = str(event.get("id") or "").strip()
    score = 0
    if event_id and event_id in authoritative_event_ids:
        score += 120
    event_entities = {str(item or "").strip() for item in event.get("entities") or [] if str(item or "").strip()}
    if anchor_entity_ids and event_entities & anchor_entity_ids:
        score += 90
    event_text = normalized_anchor_text(
        {
            "event_type": event.get("event_type"),
            "status": event.get("status"),
            "summary": event.get("summary"),
            "unknowns": event.get("unknowns"),
        }
    )
    if contains_any_text(event_text, ANCHOR_EVENT_TYPE_TERMS) or contains_any_text(event_text, ANCHOR_STATUS_TERMS):
        score += 24
    if normalized_anchor_text(event.get("status")) == "confirmed":
        score += 5
    return score


def merge_entity_fact_preserving_anchors(previous: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(previous, dict) or not previous:
        return fact
    merged = dict(fact)
    for key in FACT_PRESERVE_IF_OMITTED_KEYS:
        if merged.get(key) in (None, "", [], {}) and previous.get(key) not in (None, "", [], {}):
            merged[key] = previous.get(key)
    if previous.get("created_at") and not merged.get("created_at"):
        merged["created_at"] = previous.get("created_at")
    return merged


def entity_fact_resolution_kinds(fact: dict[str, Any]) -> set[str]:
    text = normalized_anchor_text(
        {
            "current_status": fact.get("current_status"),
            "current_location": fact.get("current_location") or fact.get("location"),
            "custody": fact.get("custody"),
            "holder": fact.get("holder"),
            "owner": fact.get("owner"),
            "with_party": fact.get("with_party"),
        }
    )
    kinds: set[str] = set()
    if contains_any_text(text, LIFE_CONFIRMED_TERMS):
        kinds.add("life")
    if contains_any_text(text, RESCUE_OR_ESCORT_TERMS) or fact.get("custody") or fact.get("current_location") or fact.get("with_party"):
        kinds.add("location")
    if contains_any_text(text, POSSESSION_TERMS) or fact.get("holder") or fact.get("owner"):
        kinds.add("possession")
    if contains_any_text(text, STATE_LOCK_TERMS):
        kinds.add("state")
    if contains_any_text(text, TERMINAL_TERMS):
        kinds.add("terminal")
    return kinds


def text_mentions_entity_anchor(text: str, entity_id: str, fact: dict[str, Any]) -> bool:
    normalized_text = normalized_anchor_text(text)
    if not normalized_text:
        return False
    for alias in entity_aliases(entity_id, fact):
        normalized_alias = normalized_anchor_text(alias)
        if normalized_alias and normalized_alias in normalized_text:
            return True
    return False


def text_contradicts_entity_anchor(text: Any, entity_id: str, fact: dict[str, Any]) -> bool:
    normalized_text = normalized_anchor_text(text)
    if not normalized_text or not isinstance(fact, dict):
        return False
    if not text_mentions_entity_anchor(normalized_text, entity_id, fact):
        return False
    kinds = entity_fact_resolution_kinds(fact)
    if not kinds:
        return False
    if "life" in kinds and contains_any_text(normalized_text, LIFE_UNCERTAINTY_TERMS):
        return True
    if "location" in kinds and contains_any_text(normalized_text, LOCATION_UNCERTAINTY_TERMS):
        return True
    if "possession" in kinds and contains_any_text(normalized_text, POSSESSION_UNCERTAINTY_TERMS):
        return True
    if "state" in kinds and contains_any_text(normalized_text, STATE_UNCERTAINTY_TERMS):
        return True
    return False


def entity_anchor_replacement(entity_id: str, fact: dict[str, Any]) -> str:
    name = str(fact.get("name") or entity_id).strip() or entity_id
    status = str(fact.get("current_status") or "").strip()
    location = str(fact.get("current_location") or fact.get("location") or "").strip()
    holder = str(fact.get("holder") or fact.get("owner") or "").strip()
    unknowns = fact.get("unknowns") or []
    chunks = []
    if status:
        chunks.append(status)
    if location:
        chunks.append(f"当前位置：{location}")
    if holder:
        chunks.append(f"持有/归属：{holder}")
    if unknowns:
        unknown_text = "；".join(str(item) for item in unknowns[:3] if str(item).strip())
        if unknown_text:
            chunks.append(f"未解项仅限：{unknown_text}")
    detail = "；".join(chunks) if chunks else "已有较新权威事实记录"
    return f"【已过期】旧实体钩子已被较新存档覆盖：{name}{detail}。"
