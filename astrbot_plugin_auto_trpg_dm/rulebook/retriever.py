from __future__ import annotations

import json
import re
from typing import Any

from .models import RuleCard


TACTICAL_CATEGORY_BOOSTS = {
    "action_economy": 18,
    "combat": 16,
    "conditions": 14,
    "damage_healing": 12,
    "equipment_core": 8,
    "spellcasting_core": 8,
}

DM_GUIDANCE_TERMS = (
    "dm",
    "城主",
    "地下城主",
    "裁定",
    "即兴",
    "后果",
    "失败",
    "代价",
    "叙事",
    "描写",
    "共同故事",
    "不是竞争",
    "公平",
    "灵活",
    "尊重",
    "边界",
    "不舒服",
    "越界",
    "玩家偏好",
    "游戏性",
    "娱乐性",
    "称职",
)


class CoreRuleRetriever:
    def __init__(self, cards: list[RuleCard], aliases: dict[str, list[str]] | None = None):
        self.cards = cards
        self.aliases = aliases or {}
        self._card_text = {card.id: _card_search_text(card) for card in cards}
        self._card_ngrams = {card.id: _ngrams(self._card_text[card.id]) for card in cards}

    def query(
        self,
        query: str,
        *,
        categories: list[str] | None = None,
        limit: int = 4,
        max_chars: int = 1600,
        mode_hint: str = "",
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        categories = [item.strip() for item in (categories or []) if str(item).strip()]
        limit = max(1, min(int(limit or 4), 8))
        max_chars = max(400, min(int(max_chars or 1600), 3000))
        if not query:
            return {
                "ok": False,
                "error": "empty_query",
                "query": query,
                "matches": [],
                "hints": ["请提供要查询的规则问题或玩家行动。"],
            }

        query_terms = _query_terms(query, self.aliases)
        query_ngrams = _ngrams(_normalize_text(query))
        scored: list[tuple[float, RuleCard]] = []
        for card in self.cards:
            if categories and not _matches_categories(card, categories):
                continue
            score = self._score_card(card, query, query_terms, query_ngrams, mode_hint)
            if score > 0:
                scored.append((score, card))
        scored.sort(key=lambda item: (item[0], item[1].title), reverse=True)
        matches = [
            self._trim_match(card.to_public_dict(score=_normal_score(score)))
            for score, card in scored[:limit]
        ]
        payload = {
            "ok": True,
            "query": query,
            "matches": matches,
            "hints": _hints_for_matches(matches),
        }
        return _fit_payload(payload, max_chars)

    def _score_card(
        self,
        card: RuleCard,
        query: str,
        query_terms: list[str],
        query_ngrams: set[str],
        mode_hint: str,
    ) -> float:
        score = 0.0
        query_norm = _normalize_text(query)
        title_norm = _normalize_text(card.title)
        aliases_norm = [_normalize_text(alias) for alias in card.aliases]
        text = self._card_text.get(card.id, "")

        if query_norm and query_norm == title_norm:
            score += 120
        elif title_norm and (title_norm in query_norm or query_norm in title_norm):
            score += 70
        for alias in aliases_norm:
            if not alias:
                continue
            if alias == query_norm:
                score += 95
            elif alias in query_norm or query_norm in alias:
                score += 55

        for term in query_terms:
            term_norm = _normalize_text(term)
            if not term_norm:
                continue
            if term_norm == title_norm:
                score += 65
            elif term_norm in title_norm:
                score += 36
            if term_norm in aliases_norm:
                score += 48
            elif any(term_norm in alias for alias in aliases_norm):
                score += 28
            if term_norm in [_normalize_text(tag) for tag in card.tags]:
                score += 26
            elif term_norm in text:
                score += 8

        if query_ngrams:
            shared = len(query_ngrams.intersection(self._card_ngrams.get(card.id, set())))
            if shared:
                score += min(35, shared * 1.6)
        mode = mode_hint.lower()
        if score > 0 and mode in {"tactical", "resolution"}:
            score += TACTICAL_CATEGORY_BOOSTS.get(card.category, 0)
        if score > 0 and card.category == "dm_guidance":
            if mode in {"narrative", "resolution", "character_creation"} or _contains_dm_guidance_term(query_norm):
                score += 18
        return score

    @staticmethod
    def _trim_match(match: dict[str, Any]) -> dict[str, Any]:
        match["summary"] = _limit_text(match.get("summary", ""), 320)
        match["procedure"] = [_limit_text(item, 130) for item in list(match.get("procedure") or [])[:5]]
        match["exceptions"] = [_limit_text(item, 130) for item in list(match.get("exceptions") or [])[:4]]
        match["related_rule_ids"] = list(match.get("related_rule_ids") or [])[:6]
        match["source_refs"] = list(match.get("source_refs") or [])[:3]
        return match


def _query_terms(query: str, aliases: dict[str, list[str]]) -> list[str]:
    normalized = _normalize_text(query)
    terms: list[str] = []
    for chunk in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized):
        if len(chunk) >= 2 or chunk.isdigit():
            terms.append(chunk)
    for canonical, names in aliases.items():
        all_names = [canonical, *names]
        if any(_normalize_text(name) and _normalize_text(name) in normalized for name in all_names):
            terms.extend(all_names)
    terms.extend(sorted(_ngrams(normalized)))
    return _dedupe(terms)


def _matches_categories(card: RuleCard, categories: list[str]) -> bool:
    wanted = {_normalize_text(item) for item in categories}
    values = {_normalize_text(card.category), *(_normalize_text(tag) for tag in card.tags)}
    return bool(wanted.intersection(values))


def _card_search_text(card: RuleCard) -> str:
    parts = [
        card.id,
        card.title,
        card.category,
        " ".join(card.aliases),
        " ".join(card.tags),
        card.summary,
        " ".join(card.procedure),
        " ".join(card.exceptions),
    ]
    return _normalize_text(" ".join(parts))


def _normalize_text(value: str) -> str:
    text = str(value or "").lower()
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ngrams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    grams: set[str] = set()
    for size in (2, 3):
        for index in range(0, max(0, len(compact) - size + 1)):
            grams.add(compact[index : index + size])
    return grams


def _normal_score(score: float) -> float:
    return min(0.99, score / 140.0)


def _fit_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    while payload.get("matches") and len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        payload["matches"] = payload["matches"][:-1]
    if payload.get("matches") and len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        for match in payload["matches"]:
            match["summary"] = _limit_text(match.get("summary", ""), 180)
            match["procedure"] = [_limit_text(item, 80) for item in list(match.get("procedure") or [])[:3]]
            match["exceptions"] = [_limit_text(item, 80) for item in list(match.get("exceptions") or [])[:2]]
    return payload


def _hints_for_matches(matches: list[dict[str, Any]]) -> list[str]:
    if not matches:
        return ["规则库没有命中；不要凭空编造具体书面规则，可按当前团风格给出临时裁定并标注。"]
    categories = {str(item.get("category") or "") for item in matches}
    hints = ["query_core_rules 只提供规则摘要；数值检定、命中、豁免、伤害、治疗仍应调用 execute_rule。"]
    if "dm_guidance" in categories:
        hints.append("DM 指引用于稳定裁定风格：以玩家引导和共同故事为主，规则限制作为公平护栏。")
    if categories.intersection({"combat", "action_economy"}):
        hints.append("战斗中还要结合 get_battle_snapshot、move_entity、check_attack_vector 和 turn_control 的客观事实。")
    return hints[:2]


def _limit_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = _normalize_text(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _contains_dm_guidance_term(text: str) -> bool:
    return any(_normalize_text(term) in text for term in DM_GUIDANCE_TERMS)
