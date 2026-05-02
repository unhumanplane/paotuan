from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GameMode(str, Enum):
    NARRATIVE = "narrative"
    CHARACTER_CREATION = "character_creation"
    RULE_AUTHORING = "rule_authoring"
    TACTICAL = "tactical"
    RESOLUTION = "resolution"


class CycleState(str, Enum):
    CYCLE_ACTIVE = "cycle_active"
    CYCLE_RESOLVING = "cycle_resolving"
    CYCLE_TRANSITION = "cycle_transition"


@dataclass
class TagValue:
    key: str
    value: Any
    type: str = "text"
    source: str = "llm"
    layer: str = "notes"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TagValue":
        key = str(data.get("key", ""))
        return cls(
            key=key,
            value=data.get("value"),
            type=str(data.get("type", "text")),
            source=str(data.get("source", "llm")),
            layer=str(data.get("layer") or infer_tag_layer(key)),
        )


@dataclass
class Character:
    id: str
    name: str
    player_id: str = ""
    summary: str = ""
    tags: list[TagValue] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Character":
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            player_id=str(data.get("player_id", "")),
            summary=str(data.get("summary", "")),
            tags=[TagValue.from_dict(item) for item in data.get("tags", [])],
        )

    def upsert_tags(self, tags: list[dict[str, Any]]) -> None:
        by_key = {(tag.layer or infer_tag_layer(tag.key), tag.key): tag for tag in self.tags}
        for item in tags:
            tag = TagValue.from_dict(item)
            if not tag.key:
                continue
            by_key[(tag.layer or infer_tag_layer(tag.key), tag.key)] = tag
        self.tags = list(by_key.values())


@dataclass
class RuleRef:
    name: str
    version: int
    description: str = ""
    language: str = "python_subset"
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    code_hash: str = ""
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuleRef":
        return cls(
            name=str(data.get("name", "")),
            version=int(data.get("version", 1)),
            description=str(data.get("description", "")),
            language=str(data.get("language", "python_subset")),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            tags=list(data.get("tags", [])),
            code_hash=str(data.get("code_hash", "")),
            updated_at=str(data.get("updated_at", utc_now_iso())),
        )


@dataclass
class CycleAction:
    player_id: str = ""
    character_id: str = ""
    player_message: str = ""
    dm_narrative: str = ""
    tools_called: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CycleAction":
        return cls(
            player_id=str(data.get("player_id", "")),
            character_id=str(data.get("character_id", "")),
            player_message=str(data.get("player_message", "")),
            dm_narrative=str(data.get("dm_narrative", "")),
            tools_called=_list_of_dicts(data.get("tools_called", [])),
            timestamp=str(data.get("timestamp", utc_now_iso())),
        )


@dataclass
class AuditBuffer:
    cycle_id: int = 0
    actions: list[CycleAction] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now_iso)
    ended_at: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditBuffer":
        return cls(
            cycle_id=_safe_int(data.get("cycle_id", 0)),
            actions=[
                CycleAction.from_dict(item)
                for item in _list_of_dicts(data.get("actions", []))
                if isinstance(item, dict)
            ],
            started_at=str(data.get("started_at", utc_now_iso())),
            ended_at=str(data.get("ended_at", "")),
        )


@dataclass
class RACycleInput:
    cycle_id: int = 0
    actions: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RACycleInput":
        return cls(
            cycle_id=_safe_int(data.get("cycle_id", 0)),
            actions=_list_of_dicts(data.get("actions", [])),
        )


@dataclass
class GameSession:
    session_id: str
    mode: GameMode = GameMode.NARRATIVE
    cycle_state: CycleState = CycleState.CYCLE_ACTIVE
    title: str = "未命名团"
    active_character_id: str = ""
    participants: dict[str, dict[str, Any]] = field(default_factory=dict)
    player_character_map: dict[str, str] = field(default_factory=dict)
    world_tags: dict[str, Any] = field(default_factory=dict)
    scene: dict[str, Any] = field(default_factory=dict)
    memory_summary: str = ""
    characters: dict[str, Character] = field(default_factory=dict)
    rules: dict[str, RuleRef] = field(default_factory=dict)
    rule_sets: dict[str, Any] = field(default_factory=dict)
    battle: dict[str, Any] = field(default_factory=dict)
    current_cycle_id: int = 0
    audit_buffer: AuditBuffer = field(default_factory=AuditBuffer)
    ra_cycle_input: RACycleInput = field(default_factory=RACycleInput)
    environment_summaries: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def new(cls, session_id: str) -> "GameSession":
        return cls(
            session_id=session_id,
            scene={
                "summary": "尚未开局。等待玩家用自然语言描述世界、角色或当前行动。",
                "current_conflict": "",
            },
            memory_summary="",
            battle={"active": False},
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameSession":
        mode_value = data.get("mode", GameMode.NARRATIVE.value)
        try:
            mode = GameMode(mode_value)
        except ValueError:
            mode = GameMode.NARRATIVE
        cycle_state_value = data.get("cycle_state", CycleState.CYCLE_ACTIVE.value)
        try:
            cycle_state = CycleState(cycle_state_value)
        except ValueError:
            cycle_state = CycleState.CYCLE_ACTIVE
        return cls(
            session_id=str(data.get("session_id", "")),
            mode=mode,
            cycle_state=cycle_state,
            title=str(data.get("title", "未命名团")),
            active_character_id=str(data.get("active_character_id", "")),
            participants=dict(data.get("participants", {})),
            player_character_map=dict(data.get("player_character_map", {})),
            world_tags=dict(data.get("world_tags", {})),
            scene=dict(data.get("scene", {})),
            memory_summary=str(data.get("memory_summary", "")),
            characters={
                key: Character.from_dict(value)
                for key, value in dict(data.get("characters", {})).items()
            },
            rules={
                key: RuleRef.from_dict(value)
                for key, value in dict(data.get("rules", {})).items()
            },
            rule_sets=_dict_or_empty(data.get("rule_sets", {})),
            battle=dict(data.get("battle", {"active": False})),
            current_cycle_id=_safe_int(data.get("current_cycle_id", 0)),
            audit_buffer=AuditBuffer.from_dict(_dict_or_empty(data.get("audit_buffer", {}))),
            ra_cycle_input=RACycleInput.from_dict(_dict_or_empty(data.get("ra_cycle_input", {}))),
            environment_summaries=_list_of_dicts(data.get("environment_summaries", [])),
            created_at=str(data.get("created_at", utc_now_iso())),
            updated_at=str(data.get("updated_at", utc_now_iso())),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        data["cycle_state"] = self.cycle_state.value
        return data

    def compact_snapshot(self) -> dict[str, Any]:
        active_character = None
        if self.active_character_id:
            active_character = self.characters.get(self.active_character_id)
        return {
            "session_id": self.session_id,
            "title": self.title,
            "mode": self.mode.value,
            "active_character": _compact_character(active_character) if active_character else None,
            "character_count": len(self.characters),
            "participants": [
                {
                    "player_id": player_id,
                    "display_name": data.get("display_name", ""),
                    "character_id": self.player_character_map.get(player_id, ""),
                }
                for player_id, data in self.participants.items()
            ],
            "player_character_map": self.player_character_map,
            "characters": [
                _compact_character(character)
                for character in self.characters.values()
            ],
            "world_tags": self.world_tags,
            "scene": self.scene,
            "memory_summary": self.memory_summary,
            "rules": compact_rules(self.rules),
            "rule_sets": self.rule_sets,
            "cycle_state": self.cycle_state.value,
            "current_cycle_id": self.current_cycle_id,
            "environment_summaries": self.environment_summaries[-3:],
            "battle": self._compact_battle(),
        }

    def _compact_battle(self) -> dict[str, Any]:
        battle = self.battle or {"active": False}
        grid = battle.get("grid", {})
        entities = dict(grid.get("entities", {}))
        turn = dict(battle.get("turn", {}))
        current_entity_id = str(turn.get("current_entity_id", "") or battle.get("turn_entity_id", ""))
        order = list(turn.get("turn_order", []))[:24]
        actions = dict(turn.get("actions_this_round", {}))
        return {
            "active": bool(battle.get("active", False)),
            "turn_entity_id": battle.get("turn_entity_id", ""),
            "turn": {
                "active": bool(turn.get("active", False)),
                "round": turn.get("round", 0),
                "phase": turn.get("phase", "idle"),
                "turn_order": order,
                "current_index": turn.get("current_index", -1),
                "current_entity_id": current_entity_id,
                "current_label": self._battle_entity_label(current_entity_id, entities),
                "current_owner_player_id": self._battle_entity_owner(current_entity_id, entities),
                "output_limit_chars": turn.get("output_limit_chars", 720),
                "timeout_seconds": turn.get("timeout_seconds", 120),
                "waiting_since_at": turn.get("waiting_since_at", ""),
                "deadline_at": turn.get("deadline_at", ""),
                "actions_this_round": actions,
                "acted_entity_ids": [entity_id for entity_id in order if entity_id in actions],
                "pending_entity_ids": [entity_id for entity_id in order if entity_id not in actions],
                "recent_turn_log": list(turn.get("turn_log", []))[-8:],
            }
            if turn
            else {},
            "grid": {
                "width": grid.get("width"),
                "height": grid.get("height"),
                "entities": list(entities.values())[:32],
                "obstacles": [
                    cell
                    for cell in grid.get("cells", [])
                    if cell.get("blocks_move") or cell.get("blocks_los")
                ][:64],
            }
            if grid
            else {},
        }

    def _battle_entity_label(self, entity_id: str, entities: dict[str, Any]) -> str:
        entity = dict(entities.get(entity_id, {}))
        if entity.get("name"):
            return str(entity["name"])
        character = self.characters.get(entity_id)
        if character:
            return character.name or character.id
        return entity_id

    def _battle_entity_owner(self, entity_id: str, entities: dict[str, Any]) -> str:
        entity = dict(entities.get(entity_id, {}))
        tags = dict(entity.get("tags", {}))
        if tags.get("player_id"):
            return str(tags["player_id"])
        character_id = str(tags.get("character_id", "") or entity_id)
        character = self.characters.get(character_id)
        if character and character.player_id:
            return character.player_id
        for player_id, bound_id in self.player_character_map.items():
            if bound_id == character_id or bound_id == entity_id:
                return player_id
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _compact_character(character: Character) -> dict[str, Any]:
    return {
        "id": character.id,
        "name": character.name,
        "player_id": character.player_id,
        "summary": _short_snapshot(character.summary, 240),
        "tag_count": len(character.tags),
        "tag_layers": compact_tag_layers(character.tags),
    }


TAG_LAYER_ALIASES = {
    "identity": "identity",
    "身份": "identity",
    "abilities": "abilities",
    "ability": "abilities",
    "能力": "abilities",
    "equipment": "equipment",
    "装备": "equipment",
    "combat": "combat",
    "战斗": "combat",
    "status": "status",
    "状态": "status",
    "relations": "relations",
    "关系": "relations",
    "notes": "notes",
    "备注": "notes",
}


TAG_LAYER_KEYWORDS = {
    "identity": ("职业", "种族", "背景", "风格", "身份", "阵营", "出身", "姓名", "年龄"),
    "abilities": ("能力", "专长", "法术", "技能", "属性", "核心专长", "次要能力", "特性", "天赋"),
    "equipment": ("装备", "武器", "主武器", "常用装备", "护甲", "道具", "物品", "弹药"),
    "combat": ("默认战斗行为", "战斗习惯", "战术", "攻击", "防御", "射击", "近战", "施法偏好"),
    "status": ("状态", "伤势", "生命", "体力", "资源", "buff", "debuff", "增益", "减益", "异常", "弱点", "缺陷", "限制", "克制"),
    "relations": ("关系", "盟友", "敌人", "组织", "所属", "联系人", "羁绊"),
}


def infer_tag_layer(key: str) -> str:
    text = str(key or "").strip().lower()
    if not text:
        return "notes"
    alias = TAG_LAYER_ALIASES.get(text)
    if alias:
        return alias
    for layer, keywords in TAG_LAYER_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return layer
    return "notes"


def compact_tag_layers(tags: list[TagValue], max_layers: int = 6, max_tags_per_layer: int = 8) -> dict[str, Any]:
    layered: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        layer = TAG_LAYER_ALIASES.get(str(tag.layer or "").lower(), "") or infer_tag_layer(tag.key)
        items = layered.setdefault(layer, [])
        if len(items) >= max_tags_per_layer:
            continue
        items.append(
            {
                "key": tag.key,
                "value": _short_snapshot(tag.value, 120),
                "type": tag.type,
            }
        )
    ordered: dict[str, Any] = {}
    for layer in ("identity", "abilities", "equipment", "combat", "status", "relations", "notes"):
        if layer in layered:
            ordered[layer] = layered[layer]
        if len(ordered) >= max_layers:
            break
    return ordered


def compact_rules(rules: dict[str, RuleRef], detail_limit: int = 10, name_limit: int = 48) -> dict[str, Any]:
    if not rules:
        return {"count": 0, "level_1": {"names": [], "by_tag": {}}, "level_2": []}
    ordered = sorted(
        rules.values(),
        key=lambda rule: (rule.updated_at, rule.name),
        reverse=True,
    )
    by_tag: dict[str, int] = {}
    for rule in ordered:
        tags = rule.tags or ["untagged"]
        for tag in tags[:4]:
            key = str(tag or "untagged")
            by_tag[key] = by_tag.get(key, 0) + 1
    names = [f"{rule.name}@v{rule.version}" for rule in ordered[:name_limit]]
    return {
        "count": len(ordered),
        "level_1": {
            "names": names,
            "names_omitted": max(0, len(ordered) - len(names)),
            "by_tag": dict(sorted(by_tag.items(), key=lambda item: item[0])[:24]),
        },
        "level_2": [
            {
                "name": rule.name,
                "version": rule.version,
                "description": _short_snapshot(rule.description, 96),
                "tags": list(rule.tags)[:5],
            }
            for rule in ordered[:detail_limit]
        ],
        "hint": "执行规则使用 level_1.names 里的规则名；需要参数时按 tag 查询 detail，不要重复同参数 list_rules。",
    }


def _short_snapshot(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        value = str(value)
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
