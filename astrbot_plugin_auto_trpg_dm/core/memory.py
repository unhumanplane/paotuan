from __future__ import annotations

import json
from typing import Any

from .models import GameSession, compact_rules, compact_tag_layers


class MemoryCompressor:
    def __init__(self, max_snapshot_chars: int = 200_000, max_summary_chars: int = 12_000):
        self.max_snapshot_chars = max_snapshot_chars
        self.max_summary_chars = max_summary_chars

    def snapshot_chars(self, session: GameSession) -> int:
        return len(json.dumps(session.compact_snapshot(), ensure_ascii=False))

    def maybe_compress(self, session: GameSession) -> bool:
        if (
            self.snapshot_chars(session) <= self.max_snapshot_chars
            and len(session.memory_summary) < self.max_summary_chars
        ):
            return False
        session.memory_summary = self.build_summary(session)
        self._trim_verbose_state(session)
        self._enforce_hard_budget(session)
        return True

    def build_summary(self, session: GameSession) -> str:
        parts: list[str] = []
        parts.append(f"团名：{session.title}")
        scene_summary = session.scene.get("summary") or "暂无场景摘要"
        parts.append(f"当前场景：{scene_summary}")
        if session.scene.get("current_conflict"):
            parts.append(f"当前冲突：{session.scene.get('current_conflict')}")
        if session.world_tags:
            parts.append("世界设定：" + _compact_json(session.world_tags, 700))
        if session.characters:
            character_bits = []
            for character in session.characters.values():
                tag_bits = _compact_json(compact_tag_layers(character.tags, max_tags_per_layer=5), 700)
                character_bits.append(f"{character.name}({character.id})：{character.summary}; {tag_bits}")
            parts.append("角色：" + " | ".join(character_bits))
        if session.rules:
            parts.append("已注册规则摘要：" + _compact_json(compact_rules(session.rules, detail_limit=8), 1200))
        battle = session.compact_snapshot().get("battle", {})
        if battle.get("active"):
            parts.append("战棋状态：" + _compact_json(battle, 900))
        summary = "\n".join(part for part in parts if part)
        if len(summary) > self.max_summary_chars:
            head_chars = self.max_summary_chars // 2
            tail_chars = self.max_summary_chars - head_chars - 20
            summary = summary[:head_chars] + "\n...\n" + summary[-tail_chars:]
        return summary

    def _trim_verbose_state(self, session: GameSession) -> None:
        # Keep deterministic state, but shorten verbose narrative blobs after summary is refreshed.
        for key in ("history", "transcript", "raw_events"):
            session.scene.pop(key, None)

    def _enforce_hard_budget(self, session: GameSession) -> None:
        if self.snapshot_chars(session) <= self.max_snapshot_chars:
            return
        self._trim_mapping(session.scene, keep={"summary", "current_conflict", "location"}, limit=2000)
        self._trim_mapping(session.world_tags, keep={"adjudication", "response_style"}, limit=2000)
        if self.snapshot_chars(session) <= self.max_snapshot_chars:
            return
        for character in session.characters.values():
            character.summary = _short(character.summary, 500)
            character.tags = character.tags[:16]
            for tag in character.tags:
                tag.value = _short_json_like(tag.value, 500)
        if self.snapshot_chars(session) <= self.max_snapshot_chars:
            return
        for character in session.characters.values():
            character.tags = character.tags[:8]
            for tag in character.tags:
                tag.value = _short_json_like(tag.value, 240)

    @staticmethod
    def _trim_mapping(mapping: dict[str, Any], keep: set[str], limit: int) -> None:
        for key, value in list(mapping.items()):
            if key in keep:
                mapping[key] = _short_json_like(value, limit)
            else:
                mapping[key] = _short_json_like(value, limit)


def _compact_json(value: Any, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _short(text, limit)


def _short(value: Any, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _short_json_like(value: Any, limit: int) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _short(value, limit) if isinstance(value, str) else value
    return _compact_json(value, limit)
