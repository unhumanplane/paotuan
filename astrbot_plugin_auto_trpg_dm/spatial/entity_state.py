from __future__ import annotations

from typing import Any, Mapping


CORPSE_STATE = "corpse"
INCAPACITATED_STATE = "incapacitated"
PRONE_STATE = "prone"
ACTIVE_STATE = "active"

_CORPSE_TERMS = {
    "dead",
    "death",
    "corpse",
    "killed",
    "slain",
    "fatal",
    "defeated_dead",
    "阵亡",
    "死亡",
    "已死",
    "尸体",
    "遗体",
    "毙命",
    "击毙",
    "头部中弹阵亡",
}

_INCAPACITATED_TERMS = {
    "down",
    "downed",
    "unconscious",
    "incapacitated",
    "disabled",
    "dying",
    "bleeding_out",
    "昏迷",
    "失能",
    "无法行动",
    "重伤倒地",
    "濒死",
}

_PRONE_TERMS = {
    "prone",
    "crouch",
    "crouched",
    "pinned",
    "suppressed",
    "倒地",
    "卧倒",
    "趴下",
    "蹲伏",
    "压制",
}


def entity_life_state(entity: Mapping[str, Any] | Any) -> str:
    tags = _entity_tags(entity)
    explicit = str(_entity_get(entity, "life_state") or tags.get("life_state") or tags.get("state") or "").strip().lower()
    if explicit in {ACTIVE_STATE, "alive", "normal", "ok"}:
        return ACTIVE_STATE
    if explicit in {CORPSE_STATE, "dead", "death", "killed", "slain"}:
        return CORPSE_STATE
    if explicit in {INCAPACITATED_STATE, "down", "downed", "unconscious", "disabled", "dying"}:
        return INCAPACITATED_STATE
    if explicit in {PRONE_STATE, "crouch", "crouched", "pinned", "suppressed"}:
        return PRONE_STATE

    combined = _combined_state_text(entity)
    if _contains_any(combined, _CORPSE_TERMS):
        return CORPSE_STATE
    if _contains_any(combined, _INCAPACITATED_TERMS):
        return INCAPACITATED_STATE
    if _contains_any(combined, _PRONE_TERMS):
        return PRONE_STATE
    return ACTIVE_STATE


def is_entity_corpse(entity: Mapping[str, Any] | Any) -> bool:
    return entity_life_state(entity) == CORPSE_STATE


def is_entity_incapacitated(entity: Mapping[str, Any] | Any) -> bool:
    return entity_life_state(entity) in {CORPSE_STATE, INCAPACITATED_STATE}


def entity_can_take_turn(entity: Mapping[str, Any] | Any) -> bool:
    return entity_life_state(entity) not in {CORPSE_STATE, INCAPACITATED_STATE}


def entity_blocks_movement(entity: Mapping[str, Any] | Any) -> bool:
    if is_entity_corpse(entity):
        return False
    return bool(_entity_get(entity, "blocks_move", True))


def _combined_state_text(entity: Mapping[str, Any] | Any) -> str:
    values: list[str] = []
    for key in ("status", "state", "condition", "life_state", "name", "label"):
        value = _entity_get(entity, key, None)
        if value not in (None, "", [], {}):
            values.append(str(value))
    tags = _entity_tags(entity)
    for key, value in tags.items():
        values.append(str(key))
        if value not in (None, "", [], {}):
            values.append(str(value))
    return " ".join(values).strip().lower()


def _entity_tags(entity: Mapping[str, Any] | Any) -> dict[str, Any]:
    tags = _entity_get(entity, "tags", {})
    return dict(tags) if isinstance(tags, Mapping) else {}


def _entity_get(entity: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(entity, Mapping):
        return entity.get(key, default)
    return getattr(entity, key, default)


def _contains_any(text: str, terms: set[str]) -> bool:
    if not text:
        return False
    return any(term.lower() in text for term in terms)
