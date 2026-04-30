from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import RuleCard
from .retriever import CoreRuleRetriever


class RulebookStore:
    def __init__(self, rulebook_dir: Path, fallback_dirs: list[Path] | None = None):
        self.rulebook_dir = Path(rulebook_dir)
        self.fallback_dirs = [Path(item) for item in (fallback_dirs or [])]
        self.active_dir: Path | None = None
        self.active_dirs: list[Path] = []
        self.manifest: dict[str, Any] = {}
        self.aliases: dict[str, list[str]] = {}
        self.cards: list[RuleCard] = []
        self._retriever: CoreRuleRetriever | None = None
        self._loaded = False

    @property
    def available(self) -> bool:
        self.load()
        return bool(self.cards)

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return
        self._loaded = True
        self.active_dirs = self._select_rulebook_dirs()
        self.active_dir = self.active_dirs[0] if self.active_dirs else None
        self.manifest = {}
        self.aliases = {}
        self.cards = []
        self._retriever = None
        if not self.active_dirs:
            return

        cards_by_id: dict[str, RuleCard] = {}
        for directory in self.active_dirs:
            manifest_path = directory / "manifest.json"
            aliases_path = directory / "aliases.json"
            cards_path = directory / "rule_cards.jsonl"
            if not self.manifest and manifest_path.exists():
                self.manifest = _read_json_object(manifest_path)
            if aliases_path.exists():
                aliases_raw = _read_json_object(aliases_path)
                for key, value in aliases_raw.items():
                    if not isinstance(value, list):
                        continue
                    names = [str(item) for item in value if str(item or "").strip()]
                    existing = self.aliases.setdefault(str(key), [])
                    for name in names:
                        if name not in existing:
                            existing.append(name)
            for card in _read_cards(cards_path):
                cards_by_id.setdefault(card.id, card)
        self.cards = list(cards_by_id.values())
        self._retriever = CoreRuleRetriever(self.cards, self.aliases)

    def query(
        self,
        query: str,
        *,
        categories: list[str] | None = None,
        limit: int = 4,
        max_chars: int = 1600,
        mode_hint: str = "",
    ) -> dict[str, Any]:
        self.load()
        if not self.cards or self._retriever is None:
            return {
                "ok": False,
                "available": False,
                "error": "rulebook_not_built",
                "query": query,
                "matches": [],
                "hints": [
                    "DND 2024 核心规则库尚未构建；可运行 scripts/build_dnd2024_core_rulebook.py 生成 rule_cards.jsonl。",
                    "缺少规则卡时不要编造具体书面规则；如必须推进，请给出临时裁定并标注。",
                ],
                "searched_dirs": [str(self.rulebook_dir), *(str(item) for item in self.fallback_dirs)],
            }
        result = self._retriever.query(
            query,
            categories=categories,
            limit=limit,
            max_chars=max_chars,
            mode_hint=mode_hint,
        )
        result["available"] = True
        result["rulebook"] = {
            "id": str(self.manifest.get("rulebook_id") or "dnd2024_core"),
            "version": str(self.manifest.get("rulebook_version") or ""),
            "card_count": len(self.cards),
            "active_dir": str(self.active_dir),
            "active_dirs": [str(item) for item in self.active_dirs],
        }
        while result.get("matches") and len(json.dumps(result, ensure_ascii=False)) > max_chars:
            result["matches"] = result["matches"][:-1]
        return result

    def _select_rulebook_dirs(self) -> list[Path]:
        return [
            directory
            for directory in [self.rulebook_dir, *self.fallback_dirs]
            if (directory / "rule_cards.jsonl").exists()
        ]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_cards(path: Path) -> list[RuleCard]:
    cards: list[RuleCard] = []
    if not path.exists():
        return cards
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return cards
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            card = RuleCard.from_dict(data)
            if card.id and card.title:
                cards.append(card)
    return cards
