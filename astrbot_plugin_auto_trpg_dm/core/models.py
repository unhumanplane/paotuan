from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any

from .control_authority import normalize_control_authority_store
from .map_core import default_map_store, load_active_strict_grid, normalize_map_store
from .timeline import default_timeline, normalize_timeline, timeline_view
from .turn_labels import public_turn_entity_label, turn_entity_owner_id


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
        layer = str(data.get("layer") or infer_tag_layer(key))
        value = data.get("value")
        if layer == "relations":
            value = normalize_relation_state(value)
        return cls(
            key=key,
            value=value,
            type=str(data.get("type", "text")),
            source=str(data.get("source", "llm")),
            layer=layer,
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
            key = (tag.layer or infer_tag_layer(tag.key), tag.key)
            if key in by_key:
                by_key.pop(key)
            by_key[key] = tag
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
    maps: dict[str, Any] = field(default_factory=default_map_store)
    timeline: dict[str, Any] = field(default_factory=default_timeline)
    control_authority: dict[str, Any] = field(default_factory=dict)
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
            scene=normalize_scene_anchors(data.get("scene", {})),
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
            maps=normalize_map_store(data.get("maps", {})),
            timeline=normalize_timeline(data.get("timeline", {})),
            control_authority=normalize_control_authority_store(data.get("control_authority", {})),
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
            "timeline": timeline_view(self.timeline),
            "cycle_state": self.cycle_state.value,
            "current_cycle_id": self.current_cycle_id,
            "environment_summaries": self.environment_summaries[-3:],
            "battle": self._compact_battle(),
            "control_authority": self._compact_control_authority(),
        }

    def _compact_battle(self) -> dict[str, Any]:
        battle = self.battle or {"active": False}
        loaded_grid = load_active_strict_grid(self.maps, battle)
        grid = loaded_grid.get("grid") if loaded_grid.get("ok") else {}
        if not isinstance(grid, dict):
            grid = {}
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
                "sequence_mode": _turn_sequence_mode(turn),
                "strict_sequence": _turn_sequence_mode(turn) == "strict",
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
        return public_turn_entity_label(self, entity_id, entities)

    def _battle_entity_owner(self, entity_id: str, entities: dict[str, Any]) -> str:
        return turn_entity_owner_id(self, entity_id, entities)

    def _compact_control_authority(self) -> dict[str, Any]:
        from .control_authority import project_control_authority

        return project_control_authority(self, "dm_narration_view")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _turn_sequence_mode(turn: dict[str, Any]) -> str:
    return "strict" if str(turn.get("sequence_mode") or "").strip().lower() == "strict" else "flexible"


SCENE_LOCATION_ANCHOR_KEYS = (
    "location",
    "current_location",
    "current_vehicle_status",
    "current_access_state",
)

_LOCATION_MARKERS = (
    "站",
    "台",
    "车厢",
    "驾驶室",
    "走廊",
    "门",
    "路基",
    "废墟",
    "桥",
    "房间",
    "楼层",
    "入口",
    "出口",
    "大厅",
    "街",
    "酒馆",
    "山",
    "森林",
    "船",
    "港",
    "基地",
    "营地",
    "城市",
)
_LOCATION_PREFIX_RE = re.compile(r"(?:位于|身处|当前位置[：:为是]?|地点[：:为是]?|在)([^。！？；;，,]{2,80})")


def normalize_scene_anchors(value: Any) -> dict[str, Any]:
    """Backfill conservative structured scene anchors from legacy natural-language saves.

    This does not invent hidden facts.  It only copies visible, already-written scene text into
    explicit anchor fields when all anchor fields are missing, so prompts can see that location /
    vehicle / access state must be verified and maintained in structured form.
    """
    scene = dict(value) if isinstance(value, dict) else {}
    if not scene or _has_scene_anchor(scene):
        return scene
    source_text = _scene_anchor_source_text(scene)
    if not source_text.strip():
        return scene
    location = _infer_current_location_anchor(scene, source_text)
    vehicle_status = _infer_vehicle_status_anchor(source_text)
    access_state = _infer_access_state_anchor(source_text)
    inferred: dict[str, Any] = {}
    if location:
        inferred["current_location"] = location
        inferred["location"] = location
    if vehicle_status:
        inferred["current_vehicle_status"] = vehicle_status
    if access_state:
        inferred["current_access_state"] = access_state
    if not inferred:
        return scene
    scene.update(inferred)
    scene.setdefault(
        "scene_anchor_note",
        "legacy_backfill_from_visible_scene_text; verify/update with update_scene on next location, vehicle, or access change",
    )
    return scene


def _has_scene_anchor(scene: dict[str, Any]) -> bool:
    return any(_anchor_present(scene.get(key)) for key in SCENE_LOCATION_ANCHOR_KEYS)


def _anchor_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _scene_anchor_source_text(scene: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("summary", "current_objective", "current_conflict", "stakes", "active_thread_summary", "active_thread_current_objective"):
        value = scene.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    pressure_clock = scene.get("pressure_clock")
    if isinstance(pressure_clock, dict):
        for key in ("description", "status", "name"):
            value = pressure_clock.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "。".join(parts)


def _infer_current_location_anchor(scene: dict[str, Any], text: str) -> str:
    title = str(scene.get("title") or "").strip()
    first_sentence = _first_relevant_sentence(text, _LOCATION_MARKERS)
    if first_sentence:
        return _clean_anchor_text(first_sentence, limit=120)
    match = _LOCATION_PREFIX_RE.search(text)
    if match:
        return _clean_anchor_text(match.group(1), limit=96)
    if title:
        return _clean_anchor_text(title, limit=96)
    return ""


def _infer_vehicle_status_anchor(text: str) -> str:
    patterns = [
        ("已驶离", ("已驶离", "离站", "开走")),
        ("正在行驶", ("正在行驶", "行驶中", "驶向", "移动中", "飞行中", "航行中")),
        ("即将启动", ("即将启动", "即将发车", "准备启动", "准备发车")),
        ("已停稳", ("已停稳", "停稳", "抵达", "终点站", "站台")),
        ("受损/无法高机动", ("应力严重", "无法高机动", "瘫痪", "失控", "受损")),
    ]
    return _first_status_match(text, patterns)


def _infer_access_state_anchor(text: str) -> str:
    patterns = [
        ("门已锁/不可通行", ("已落锁", "落锁", "锁死", "锁住", "推不开", "不可通行", "切断沟通")),
        ("门可通行/已打开", ("可通行", "已打开", "打开", "跨出", "跨过", "入口", "出口")),
        ("需要钥匙/检修入口", ("钥匙", "检修层入口", "手动解锁", "门闩")),
    ]
    return _first_status_match(text, patterns)


def _first_status_match(text: str, patterns: list[tuple[str, tuple[str, ...]]]) -> str:
    for label, markers in patterns:
        for marker in markers:
            index = text.find(marker)
            if index >= 0:
                sentence = _sentence_around(text, index)
                detail = _clean_anchor_text(sentence, limit=120)
                return f"{label}：{detail}" if detail else label
    return ""


def _first_relevant_sentence(text: str, markers: tuple[str, ...]) -> str:
    for sentence in re.split(r"[。！？；;\n]+", text):
        cleaned = sentence.strip()
        if len(cleaned) < 2:
            continue
        if any(marker in cleaned for marker in markers):
            return cleaned
    return ""


def _sentence_around(text: str, index: int) -> str:
    start = max(text.rfind(sep, 0, index) for sep in "。！？；;\n") + 1
    end_candidates = [pos for sep in "。！？；;\n" if (pos := text.find(sep, index)) >= 0]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _clean_anchor_text(value: str, limit: int) -> str:
    cleaned = " ".join(str(value).strip().split())
    cleaned = cleaned.strip("：:，,。！？；; ")
    return _short_snapshot(cleaned, limit) if cleaned else ""


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
    "relations": (
        "关系",
        "态度",
        "信任",
        "恐惧",
        "债",
        "把柄",
        "盟友",
        "敌人",
        "组织",
        "所属",
        "联系人",
        "羁绊",
        "npc",
        "faction",
        "attitude",
        "trust",
        "fear",
        "debt",
        "leverage",
        "known_facts",
        "last_interaction",
    ),
}


RELATION_ATTITUDE_ALIASES = {
    "hostile": "hostile",
    "敌对": "hostile",
    "敌意": "hostile",
    "仇视": "hostile",
    "suspicious": "suspicious",
    "怀疑": "suspicious",
    "警惕": "suspicious",
    "不信任": "suspicious",
    "neutral": "neutral",
    "中立": "neutral",
    "普通": "neutral",
    "friendly": "friendly",
    "友好": "friendly",
    "亲近": "friendly",
    "loyal": "loyal",
    "忠诚": "loyal",
    "效忠": "loyal",
}

RELATION_INTENSITY_ALIASES = {
    "none": "none",
    "无": "none",
    "低": "low",
    "low": "low",
    "少量": "low",
    "moderate": "moderate",
    "medium": "moderate",
    "中": "moderate",
    "中等": "moderate",
    "high": "high",
    "高": "high",
    "强": "high",
    "critical": "critical",
    "极高": "critical",
    "重大": "critical",
}

RELATION_STRUCTURED_FIELDS = {
    "id",
    "target_id",
    "target",
    "name",
    "type",
    "kind",
    "scope",
    "attitude",
    "trust",
    "fear",
    "debt",
    "leverage",
    "known_facts",
    "last_interaction",
    "flags",
    "relations",
    "relationship",
    "relationships",
    "secret_allegiance",
    "hidden_motive",
    "hidden_motives",
    "true_motive",
    "future_betrayal",
    "evidence",
    "source",
    "updated_at",
    "visibility",
    "notes",
    "public_notes",
}

RELATION_PUBLIC_FIELDS = {
    "id",
    "target_id",
    "target",
    "name",
    "type",
    "kind",
    "scope",
    "attitude",
    "trust",
    "fear",
    "debt",
    "leverage",
    "known_facts",
    "last_interaction",
    "flags",
    "relations",
    "relationship",
    "relationships",
    "evidence",
    "updated_at",
    "public_notes",
}

RELATION_HIDDEN_FIELDS = {
    "hidden",
    "hidden_motive",
    "hidden_motives",
    "secret",
    "secret_allegiance",
    "secret_loyalty",
    "true_allegiance",
    "true_motive",
    "private_notes",
    "dm_notes",
    "gm_notes",
    "future_betrayal",
    "planned_betrayal",
    "unrevealed",
    "unrevealed_facts",
    "agenda",
}

RELATION_COLLECTION_KEYS = {
    "relations",
    "relationship",
    "relationships",
    "npc_relations",
    "faction_relations",
    "npcs",
    "factions",
    "关系",
    "阵营关系",
    "npc关系",
}


def infer_tag_layer(key: str) -> str:
    text = str(key or "").strip().lower()
    if not text:
        return "notes"
    alias = TAG_LAYER_ALIASES.get(text)
    if alias:
        return alias
    if any(
        token in text
        for token in (
            "关系",
            "attitude",
            "trust",
            "fear",
            "debt",
            "leverage",
            "known_facts",
            "last_interaction",
            "npc",
            "faction",
        )
    ):
        return "relations"
    for layer, keywords in TAG_LAYER_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            return layer
    return "notes"


def normalize_relation_state(value: Any) -> Any:
    """Normalize lightweight NPC/faction relationship JSON without hiding audit fields."""
    if isinstance(value, list):
        return [normalize_relation_state(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    normalized: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        key = str(raw_key)
        if not key:
            continue
        if key == "attitude":
            normalized[key] = _normalize_relation_attitude(raw_item)
        elif key in {"trust", "fear", "debt", "leverage"}:
            normalized[key] = _normalize_relation_intensity_or_note(raw_item)
        elif key in {"known_facts", "flags"}:
            normalized[key] = _compact_relation_list(raw_item, 12)
        elif key == "last_interaction":
            normalized[key] = _short_snapshot(raw_item, 240)
        elif _looks_like_relation_key(key) and isinstance(raw_item, (dict, list)):
            normalized[key] = normalize_relation_state(raw_item)
        else:
            normalized[key] = raw_item
    if _looks_structured_relation(normalized):
        normalized.setdefault("attitude", "neutral")
    return normalized


def normalize_relationship_collections(value: Any, key: str = "") -> Any:
    """Normalize relationship-shaped scene/world patches while preserving legacy saves."""
    if isinstance(value, list):
        if _looks_like_relation_key(key):
            return [normalize_relationship_collections(item, key) for item in value]
        return [
            normalize_relationship_collections(item, key)
            if isinstance(item, (dict, list))
            else item
            for item in value
        ]
    if not isinstance(value, Mapping):
        return value
    if _looks_like_relation_key(key):
        if _looks_structured_relation(value):
            return normalize_relation_state(value)
        return {
            str(raw_key): normalize_relation_state(raw_item) if isinstance(raw_item, (dict, list)) else raw_item
            for raw_key, raw_item in value.items()
            if str(raw_key)
        }
    normalized: dict[str, Any] = {}
    for raw_key, raw_item in value.items():
        item_key = str(raw_key)
        if _looks_like_relation_key(item_key):
            normalized[item_key] = normalize_relationship_collections(raw_item, item_key)
        elif _looks_like_relation_key(key):
            normalized[item_key] = normalize_relation_state(raw_item)
        elif isinstance(raw_item, (dict, list)):
            normalized[item_key] = normalize_relationship_collections(raw_item, item_key)
        else:
            normalized[item_key] = raw_item
    if _looks_like_relation_key(key) or _looks_structured_relation(normalized):
        return normalize_relation_state(normalized)
    return normalized


def project_public_relation_state(value: Any) -> Any:
    """Return the player-knowable part of relationship state."""
    if isinstance(value, list):
        return [
            item
            for item in (project_public_relation_state(item) for item in value)
            if item not in ({}, [], "", None)
        ]
    if not isinstance(value, Mapping):
        return value
    visibility = str(value.get("visibility") or "").strip().lower()
    if visibility in {"hidden", "secret", "dm", "gm", "diagnostic"}:
        return {}
    projected: dict[str, Any] = {}
    relation_like = _looks_structured_relation(value)
    for raw_key, raw_item in value.items():
        key = str(raw_key)
        if _relation_key_is_hidden(key):
            continue
        if relation_like and key not in RELATION_PUBLIC_FIELDS:
            continue
        if isinstance(raw_item, (dict, list)):
            projected_item = project_public_relation_state(raw_item)
        else:
            projected_item = raw_item
        if projected_item not in ({}, [], "", None):
            projected[key] = projected_item
    return projected


def _looks_like_relation_key(key: str) -> bool:
    text = str(key or "").strip().lower()
    return text in RELATION_COLLECTION_KEYS or infer_tag_layer(text) == "relations"


def _looks_structured_relation(value: Mapping[str, Any]) -> bool:
    keys = {str(key) for key in value.keys()}
    return bool(keys.intersection(RELATION_STRUCTURED_FIELDS))


def _relation_key_is_hidden(key: str) -> bool:
    text = str(key or "").strip().lower()
    if text in RELATION_HIDDEN_FIELDS:
        return True
    return (
        text.startswith("_")
        or "hidden" in text
        or "secret" in text
        or "betrayal" in text
        or "private" in text
        or "dm_only" in text
        or "gm_only" in text
    )


def _normalize_relation_attitude(value: Any) -> str:
    text = str(value or "").strip().lower()
    return RELATION_ATTITUDE_ALIASES.get(text, text or "neutral")


def _normalize_relation_intensity_or_note(value: Any) -> Any:
    if isinstance(value, (int, float)):
        number = max(-2, min(2, int(value)))
        return {-2: "none", -1: "low", 0: "moderate", 1: "high", 2: "critical"}[number]
    if isinstance(value, list):
        return _compact_relation_list(value, 8)
    if isinstance(value, Mapping):
        return normalize_relation_state(value)
    text = str(value or "").strip()
    if not text:
        return ""
    return RELATION_INTENSITY_ALIASES.get(text.lower(), _short_snapshot(text, 160))


def _compact_relation_list(value: Any, limit: int) -> list[Any]:
    items = value if isinstance(value, list) else [value]
    compacted: list[Any] = []
    for item in items[:limit]:
        if isinstance(item, str):
            text = _short_snapshot(item, 180)
            if text:
                compacted.append(text)
        elif isinstance(item, Mapping):
            projected = project_public_relation_state(normalize_relation_state(item))
            if projected:
                compacted.append(projected)
        elif item not in (None, "", [], {}):
            compacted.append(item)
    return compacted


def compact_tag_layers(tags: list[TagValue], max_layers: int = 6, max_tags_per_layer: int = 8) -> dict[str, Any]:
    layered: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        layer = TAG_LAYER_ALIASES.get(str(tag.layer or "").lower(), "") or infer_tag_layer(tag.key)
        items = layered.setdefault(layer, [])
        value = project_public_relation_state(tag.value) if layer == "relations" else tag.value
        if layer == "relations" and value in ({}, [], "", None):
            continue
        items.append(
            {
                "key": tag.key,
                "value": _short_snapshot(value, 120),
                "type": tag.type,
            }
        )
    ordered: dict[str, Any] = {}
    for layer in ("identity", "abilities", "equipment", "combat", "status", "relations", "notes"):
        if layer in layered:
            ordered[layer] = _compact_tag_layer_items(layered[layer], layer, max_tags_per_layer)
        if len(ordered) >= max_layers:
            break
    return ordered


def _compact_tag_layer_items(
    items: list[dict[str, Any]],
    layer: str,
    max_tags_per_layer: int,
) -> list[dict[str, Any]]:
    limit = max(1, max_tags_per_layer)
    if len(items) <= limit:
        return items
    if layer in {"status", "relations", "notes"}:
        return list(reversed(items[-limit:]))
    return items[:limit]


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
