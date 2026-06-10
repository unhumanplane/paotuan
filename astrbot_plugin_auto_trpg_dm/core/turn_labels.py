from __future__ import annotations

import re
from typing import Any, Mapping


_NUMBERED_ENTITY_LABELS = {
    "ambusher": "伏击者",
    "attacker": "袭击者",
    "bandit": "匪徒",
    "enemy": "敌人",
    "guard": "守卫",
    "hostile": "敌方单位",
    "monster": "怪物",
    "raider": "袭击者",
}

_ENEMY_PREFIXES = {
    "ambusher",
    "attacker",
    "bandit",
    "enemy",
    "guard",
    "hostile",
    "monster",
    "raider",
}

_NUMBERED_ID_RE = re.compile(r"^([a-z][a-z0-9]*)(?:[_-])(\d+)$", re.IGNORECASE)
_TRAILING_NUMBER_RE = re.compile(r"(?:^|[_-])(\d+)$", re.IGNORECASE)
_INTERNAL_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)+$", re.IGNORECASE)


def public_turn_entity_label(
    session: Any,
    entity_id: str,
    entities: Mapping[str, Any] | None = None,
    label_map: Mapping[str, Any] | None = None,
) -> str:
    """Return a player-facing label for a turn entity without exposing raw slugs."""
    raw_id = str(entity_id or "").strip()
    if not raw_id:
        return "未指定"

    explicit = _clean_label((label_map or {}).get(raw_id), raw_id)
    if explicit:
        return explicit

    entity = dict((entities or {}).get(raw_id, {}) or {})
    for key in ("name", "display_name", "label"):
        explicit = _clean_label(entity.get(key), raw_id)
        if explicit:
            return explicit

    tags = dict(entity.get("tags") or {})
    character_id = str(tags.get("character_id") or raw_id)
    character = getattr(session, "characters", {}).get(character_id)
    if character:
        return str(getattr(character, "name", "") or getattr(character, "id", "") or raw_id)
    character = getattr(session, "characters", {}).get(raw_id)
    if character:
        return str(getattr(character, "name", "") or getattr(character, "id", "") or raw_id)
    for candidate in getattr(session, "characters", {}).values():
        if str(getattr(candidate, "id", "") or "") == raw_id:
            return str(getattr(candidate, "name", "") or getattr(candidate, "id", "") or raw_id)

    return fallback_turn_entity_label(raw_id)


def fallback_turn_entity_label(entity_id: str) -> str:
    raw_id = str(entity_id or "").strip()
    if not raw_id:
        return "未指定"
    lowered = raw_id.lower()
    numbered = _NUMBERED_ID_RE.match(lowered)
    if numbered:
        prefix, number = numbered.groups()
        if prefix in _NUMBERED_ENTITY_LABELS:
            return f"{_NUMBERED_ENTITY_LABELS[prefix]} {int(number)}"
        if prefix == "pc":
            return f"玩家角色 {int(number)}"
        if prefix == "npc":
            return f"NPC {int(number)}"
    trailing_number = _TRAILING_NUMBER_RE.search(lowered)
    if trailing_number:
        number = int(trailing_number.group(1))
        for prefix, label in _NUMBERED_ENTITY_LABELS.items():
            if lowered.startswith(prefix + "_") or lowered.startswith(prefix + "-"):
                return f"{label} {number}"
        if lowered.startswith("pc_") or lowered.startswith("pc-"):
            return f"玩家角色 {number}"
        if lowered.startswith("npc_") or lowered.startswith("npc-"):
            return f"NPC {number}"
    if lowered.startswith("pc_") or lowered.startswith("pc-"):
        return "玩家角色"
    if lowered.startswith("npc_") or lowered.startswith("npc-"):
        return "NPC"
    if _INTERNAL_SLUG_RE.match(lowered):
        return "行动者"
    return raw_id


def turn_entity_owner_id(
    session: Any,
    entity_id: str,
    entities: Mapping[str, Any] | None = None,
) -> str:
    raw_id = str(entity_id or "").strip()
    if not raw_id:
        return ""
    entity = dict((entities or {}).get(raw_id, {}) or {})
    tags = dict(entity.get("tags") or {})
    if tags.get("player_id"):
        return str(tags["player_id"])
    character_id = str(tags.get("character_id", "") or raw_id)
    character = getattr(session, "characters", {}).get(character_id)
    if character and getattr(character, "player_id", ""):
        return str(character.player_id)
    for player_id, bound_id in (getattr(session, "player_character_map", {}) or {}).items():
        if str(bound_id) == character_id or str(bound_id) == raw_id:
            return str(player_id)
    return ""


def turn_actor_kind(
    session: Any,
    entity_id: str,
    entities: Mapping[str, Any] | None = None,
) -> str:
    """Classify a turn actor for player-facing timeout wording."""
    raw_id = str(entity_id or "").strip()
    entity = dict((entities or {}).get(raw_id, {}) or {})
    faction = str(entity.get("faction") or "").strip().lower()
    if faction in {"enemy", "hostile", "monster", "foe", "opponent"}:
        return "enemy"
    if faction in {"player", "ally", "pc", "party", "heroes"}:
        return "player"
    if turn_entity_owner_id(session, raw_id, entities):
        return "player"
    prefix = raw_id.lower().split("_", 1)[0].split("-", 1)[0]
    if prefix in _ENEMY_PREFIXES:
        return "enemy"
    return "npc"


def sanitize_turn_text(
    session: Any,
    text: Any,
    entities: Mapping[str, Any] | None = None,
    extra_entity_ids: list[str] | tuple[str, ...] | set[str] | None = None,
    label_map: Mapping[str, Any] | None = None,
) -> str:
    value = str(text or "")
    if not value:
        return value
    ids: set[str] = set(str(item or "").strip() for item in (extra_entity_ids or []) if str(item or "").strip())
    ids.update(str(item or "").strip() for item in (entities or {}).keys() if str(item or "").strip())
    ids.update(str(item or "").strip() for item in getattr(session, "characters", {}).keys() if str(item or "").strip())
    ids.update(
        str(item or "").strip()
        for item in (getattr(session, "player_character_map", {}) or {}).values()
        if str(item or "").strip()
    )
    for raw_id in sorted(ids, key=len, reverse=True):
        label = public_turn_entity_label(session, raw_id, entities, label_map)
        if not label or label == raw_id:
            continue
        value = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(raw_id)}(?![A-Za-z0-9_-])", label, value)
    return value


def _clean_label(value: Any, raw_id: str) -> str:
    label = str(value or "").strip()
    if not label:
        return ""
    if label == raw_id or _INTERNAL_SLUG_RE.match(label.lower()):
        return ""
    return label
