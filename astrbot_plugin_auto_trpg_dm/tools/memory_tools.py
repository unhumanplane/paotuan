from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.memory import MemoryCompressor
from ..core.models import Character, GameMode, GameSession, TagValue, compact_tag_layers, infer_tag_layer
from ..storage.json_repository import JsonGameRepository


class CreateCharacterArgs(BaseModel):
    character_id: str = Field(..., description="角色 ID，建议使用 pc_ 开头")
    name: str = Field(..., description="角色名")
    summary: str = Field(default="", description="角色一句话摘要")
    player_id: str = Field(default="", description="角色所属玩家 ID；为空时默认当前发言人")
    tags: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Tag 列表，每项包含 key,value,type,source,layer；layer 可为 identity/abilities/equipment/combat/status/relations/notes",
    )


class BindPlayerCharacterArgs(BaseModel):
    character_id: str = Field(..., description="要绑定给玩家的角色 ID")
    player_id: str = Field(default="", description="玩家 ID；为空时默认当前发言人")
    name: str = Field(default="", description="角色名；角色不存在时用于创建")
    summary: str = Field(default="", description="角色摘要；角色不存在时用于创建")
    tags: List[Dict[str, Any]] = Field(default_factory=list, description="角色不存在时写入的初始 Tag")


class UpdateCharacterTagsArgs(BaseModel):
    character_id: str = Field(..., description="要更新的角色 ID")
    tags: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="要新增或覆盖的 Tag 列表。每项建议包含 key,value,layer；layer 可为 identity/abilities/equipment/combat/status/relations/notes",
    )
    raw_text: str = Field(default="", description="当 tags 为空时，可传入玩家原始自然语言补充文本，由本地兜底解析为 Tag")


class UpdateSceneArgs(BaseModel):
    patch: Dict[str, Any] = Field(default_factory=dict, description="场景状态补丁，例如 summary,current_conflict,location,npcs")


class UpdateWorldTagsArgs(BaseModel):
    patch: Dict[str, Any] = Field(default_factory=dict, description="世界设定 Tag 补丁，例如 genre,tone,factions,mysteries")


class SessionControlArgs(BaseModel):
    action: str = Field(..., description="会话控制动作：status, reset, compress_memory, debug_last")
    reason: str = Field(default="", description="执行该动作的自然语言原因")


class MemoryTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: Optional[Dict[str, str]] = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def create_character(
        self,
        character_id: str,
        name: str,
        summary: str = "",
        player_id: str = "",
        tags: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """创建或覆盖一个 Tag 型角色卡。"""
        safe_id = self._safe_id(character_id, prefix="pc")
        if not safe_id:
            return {"ok": False, "error": "invalid_character_id"}
        session = self.repository.load_session(self.session_id)
        owner_id = str(player_id or self.actor.get("player_id", "") or "").strip()
        character = Character(
            id=safe_id,
            name=name,
            player_id=owner_id,
            summary=summary,
            tags=[TagValue.from_dict(item) for item in normalize_tags(tags)],
        )
        session.characters[safe_id] = character
        if owner_id:
            session.player_character_map[owner_id] = safe_id
            self._touch_participant(session, owner_id)
        if owner_id or not session.active_character_id:
            session.active_character_id = safe_id
        session.mode = GameMode.CHARACTER_CREATION
        self.repository.save_session(session)
        result = {
            "ok": True,
            "character": character_as_dict(character),
            "bound_player_id": owner_id,
            "player_character_map": session.player_character_map,
        }
        self._audit(
            "create_character",
            {
                "character_id": character_id,
                "name": name,
                "summary": summary,
                "player_id": owner_id,
                "tags": tags or [],
            },
            result,
        )
        return result

    async def bind_player_character(
        self,
        character_id: str,
        player_id: str = "",
        name: str = "",
        summary: str = "",
        tags: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """绑定当前玩家与角色。角色不存在时创建一个轻量角色卡。"""
        safe_id = self._safe_id(character_id, prefix="pc")
        if not safe_id:
            return {"ok": False, "error": "invalid_character_id"}
        owner_id = str(player_id or self.actor.get("player_id", "") or "").strip()
        if not owner_id:
            return {"ok": False, "error": "missing_player_id"}
        session = self.repository.load_session(self.session_id)
        character = session.characters.get(safe_id)
        created = False
        if not character:
            character = Character(
                id=safe_id,
                name=name or safe_id,
                player_id=owner_id,
                summary=summary,
                tags=[TagValue.from_dict(item) for item in normalize_tags(tags)],
            )
            session.characters[safe_id] = character
            created = True
        else:
            character.player_id = owner_id
            if name and not character.name:
                character.name = name
            if summary:
                character.summary = summary
            normalized_tags = normalize_tags(tags)
            if normalized_tags:
                character.upsert_tags(normalized_tags)
        session.player_character_map[owner_id] = safe_id
        session.active_character_id = safe_id
        self._touch_participant(session, owner_id)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "created": created,
            "bound_player_id": owner_id,
            "character": character_as_dict(character),
            "player_character_map": session.player_character_map,
        }
        self._audit(
            "bind_player_character",
            {
                "character_id": character_id,
                "player_id": owner_id,
                "name": name,
                "summary": summary,
                "tags": tags or [],
            },
            result,
        )
        return result

    async def update_character_tags(
        self,
        character_id: str,
        tags: Optional[Any] = None,
        raw_text: str = "",
    ) -> Dict[str, Any]:
        """新增或覆盖角色卡 Tag。"""
        session = self.repository.load_session(self.session_id)
        safe_id = self._safe_id(character_id, prefix="pc")
        character = session.characters.get(safe_id)
        if not character:
            return {"ok": False, "error": "character_not_found", "character_id": safe_id}
        source_text = raw_text or (tags if isinstance(tags, str) else "")
        normalized_tags = normalize_tags(tags)
        inferred_from_raw_text = False
        if not normalized_tags and source_text:
            normalized_tags = infer_tags_from_text(str(source_text))
            inferred_from_raw_text = bool(normalized_tags)
        if not normalized_tags and source_text:
            normalized_tags = [
                {
                    "key": "待裁定补充",
                    "value": _short_tag_value(str(source_text), 240),
                    "type": "text",
                    "source": "local_fallback",
                    "layer": "notes",
                }
            ]
            inferred_from_raw_text = True
        if not normalized_tags:
            result = {
                "ok": False,
                "error": "empty_tags",
                "message": "update_character_tags 需要至少一个 tag；未改动角色。",
                "character_id": safe_id,
            }
            self._audit("update_character_tags", {"character_id": character_id, "tags": tags, "raw_text": raw_text}, result)
            return result
        character.upsert_tags(normalized_tags)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "character": character_as_dict(character),
            "inferred_from_raw_text": inferred_from_raw_text,
            "updated_tags": normalized_tags,
        }
        self._audit(
            "update_character_tags",
            {"character_id": character_id, "tags": normalized_tags, "raw_text": raw_text},
            result,
        )
        return result

    async def update_scene(self, patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """更新当前场景、冲突、地点、NPC 摘要等叙事状态。"""
        if not isinstance(patch, dict) or not patch:
            result = {
                "ok": False,
                "error": "empty_patch",
                "message": "update_scene 需要非空 patch；未改动场景。",
            }
            self._audit("update_scene", {"patch": patch}, result)
            return result
        session = self.repository.load_session(self.session_id)
        session.scene.update(patch)
        self.repository.save_session(session)
        result = {"ok": True, "scene": session.scene}
        self._audit("update_scene", {"patch": patch}, result)
        return result

    async def update_world_tags(self, patch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """更新世界设定 Tag，用于动态生成剧本、势力、地点和风格。"""
        if not isinstance(patch, dict) or not patch:
            result = {
                "ok": False,
                "error": "empty_patch",
                "message": "update_world_tags 需要非空 patch；未改动世界设定。",
            }
            self._audit("update_world_tags", {"patch": patch}, result)
            return result
        session = self.repository.load_session(self.session_id)
        session.world_tags.update(patch)
        if "title" in patch:
            session.title = str(patch["title"])
        self.repository.save_session(session)
        result = {"ok": True, "world_tags": session.world_tags, "title": session.title}
        self._audit("update_world_tags", {"patch": patch}, result)
        return result

    async def session_control(self, action: str, reason: str = "") -> Dict[str, Any]:
        """读取状态、重开当前会话、压缩记忆或查看最近调试记录。"""
        normalized = action.strip().lower()
        if normalized in {"status", "状态"}:
            session = self.repository.load_session(self.session_id)
            snapshot = session.compact_snapshot()
            result = {
                "ok": True,
                "action": "status",
                "session_id": self.session_id,
                "mode": session.mode.value,
                "title": session.title,
                "characters": len(session.characters),
                "participants": len(session.participants),
                "player_character_map": session.player_character_map,
                "rules": len(session.rules),
                "memory_summary_chars": len(session.memory_summary),
                "battle_active": bool(session.battle.get("active", False)),
                "save_path": str(self.repository.save_path(self.session_id)),
                "audit_path": str(self.repository.audit_path(self.session_id)),
                "plugin_log_path": str(self.repository.plugin_log_path()),
                "snapshot": snapshot,
            }
        elif normalized in {"reset", "new", "new_game", "重开", "新团", "开新团"}:
            self.repository.save_session(GameSession.new(self.session_id))
            result = {
                "ok": True,
                "action": "reset",
                "message": "当前会话的唯一跑团存档已重置。",
            }
        elif normalized in {"compress_memory", "compress", "压缩记忆", "记忆压缩"}:
            session = self.repository.load_session(self.session_id)
            compressor = MemoryCompressor()
            compressed = compressor.maybe_compress(session)
            if not compressed:
                session.memory_summary = compressor.build_summary(session)
                compressed = True
            self.repository.save_session(session)
            result = {
                "ok": True,
                "action": "compress_memory",
                "compressed": compressed,
                "summary_chars": len(session.memory_summary),
                "memory_summary": session.memory_summary,
            }
        elif normalized in {"debug_last", "debug", "最近调试", "调试"}:
            result = {
                "ok": True,
                "action": "debug_last",
                "records": self.repository.last_audit_records(self.session_id, limit=20),
                "audit_path": str(self.repository.audit_path(self.session_id)),
                "plugin_log_path": str(self.repository.plugin_log_path()),
            }
        else:
            result = {
                "ok": False,
                "error": "unsupported_session_control_action",
                "allowed": ["status", "reset", "compress_memory", "debug_last"],
            }
        self._audit(
            "session_control",
            {"action": action, "reason": reason},
            result,
        )
        return result

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": tool, "input": input_payload, "result": result},
        )

    @staticmethod
    def _safe_id(value: str, prefix: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value.strip())
        safe = safe.strip("._-")
        if not safe:
            return ""
        if "_" not in safe and not safe.startswith(prefix):
            safe = f"{prefix}_{safe}"
        return safe

    def _touch_participant(self, session: GameSession, player_id: str) -> None:
        participant = dict(session.participants.get(player_id, {}))
        participant.update(
            {
                "player_id": player_id,
                "display_name": self.actor.get("display_name", "") or participant.get("display_name", ""),
                "platform": self.actor.get("platform", "") or participant.get("platform", ""),
                "last_seen_at": self.actor.get("seen_at", "") or participant.get("last_seen_at", ""),
            }
        )
        session.participants[player_id] = participant


def character_as_dict(character: Character) -> Dict[str, Any]:
    return {
        "id": character.id,
        "name": character.name,
        "player_id": character.player_id,
        "summary": character.summary,
            "tags": [
                {
                    "key": tag.key,
                    "value": tag.value,
                    "type": tag.type,
                    "source": tag.source,
                    "layer": tag.layer or infer_tag_layer(tag.key),
                }
                for tag in character.tags
            ],
            "tag_layers": compact_tag_layers(character.tags),
        }


def normalize_tags(tags: Any) -> List[Dict[str, Any]]:
    if not tags:
        return []
    if isinstance(tags, dict):
        if "key" in tags and "value" in tags:
            tags = [tags]
        else:
            tags = [
                {
                    "key": str(key),
                    "value": value,
                    "type": _infer_tag_type(value),
                    "source": "llm",
                }
                for key, value in tags.items()
                if str(key).strip()
            ]
    if not isinstance(tags, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in tags:
        if not isinstance(item, dict):
            continue
        if "key" in item:
            key = str(item.get("key", "")).strip()
            if not key:
                continue
            normalized.append(
                {
                    "key": key,
                    "value": item.get("value"),
                    "type": str(item.get("type") or _infer_tag_type(item.get("value"))),
                    "source": str(item.get("source") or "llm"),
                    "layer": str(item.get("layer") or infer_tag_layer(key)),
                }
            )
            continue
        for key, value in item.items():
            if str(key).strip():
                normalized_key = str(key)
                normalized.append(
                    {
                        "key": normalized_key,
                        "value": value,
                        "type": _infer_tag_type(value),
                        "source": "llm",
                        "layer": infer_tag_layer(normalized_key),
                    }
                )
    return normalized


TAG_KEYS = (
    "默认战斗行为",
    "默认战斗习惯",
    "补充风格",
    "核心专长",
    "常用装备",
    "常用法术",
    "角色能力",
    "主武器",
    "次级法术",
    "次要法术",
    "战斗习惯",
    "战斗行为",
    "弱点",
    "缺陷",
    "限制",
    "背景",
    "身份",
    "职业",
    "种族",
    "专长",
    "法术",
    "武器",
    "装备",
    "风格",
    "次要",
    "次级",
    "能力",
)


TAG_KEY_ALIASES = {
    "补充风格": "风格",
    "角色能力": "能力",
    "主武器": "武器",
    "常用法术": "常用法术",
    "默认战斗习惯": "默认战斗行为",
    "战斗习惯": "默认战斗行为",
    "战斗行为": "默认战斗行为",
    "缺陷": "弱点",
    "限制": "弱点",
    "次要": "次要能力",
    "次级": "次要能力",
    "次要法术": "次要法术",
    "次级法术": "次要法术",
}


def infer_tags_from_text(text: str) -> List[Dict[str, Any]]:
    """Best-effort parser for common Chinese character-card supplements."""
    cleaned = _clean_tag_text(text)
    if not cleaned:
        return []

    ordered_keys = sorted(TAG_KEYS, key=len, reverse=True)
    key_pattern = r"(^|[\s，,；;、])(" + "|".join(re.escape(key) for key in ordered_keys) + r")"
    matches = list(re.finditer(key_pattern, cleaned))
    collected: Dict[str, Any] = {}
    if matches:
        for index, match in enumerate(matches):
            key = match.group(2)
            end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
            segment = cleaned[match.end() : end]
            _add_inferred_tag(collected, key, segment)
    else:
        for segment in re.split(r"[，,；;\n]+", cleaned):
            lowered = segment.strip()
            if not lowered:
                continue
            if any(token in lowered for token in ("默认", "持续", "优先", "直到", "战斗行为", "战斗习惯")):
                _merge_tag_value(collected, "默认战斗行为", _clean_tag_value(lowered))
            elif any(token in lowered for token in ("火球", "法术", "攻击", "点燃", "治疗", "圣光", "潜行", "射击", "连珠", "快速")):
                _merge_tag_value(collected, "能力", _split_tag_values(lowered))
            elif any(token in lowered for token in ("主武器", "武器", "装备", "斧", "剑", "刀", "弓", "枪", "披风", "护甲", "猎装")):
                _merge_tag_value(collected, "装备", _split_tag_values(lowered))
            elif any(token in lowered for token in ("弱点", "克制", "降低", "削弱", "缺陷", "代价", "限制")):
                _merge_tag_value(collected, "弱点", _clean_tag_value(lowered))
            elif any(token in lowered for token in ("职业", "种族", "身份", "背景", "风格", "法师", "游侠", "战士", "术士")):
                _merge_tag_value(collected, "身份", _clean_tag_value(lowered))

    return [
        {
            "key": key,
            "value": value,
            "type": _infer_tag_type(value),
            "source": "local_infer",
            "layer": infer_tag_layer(key),
        }
        for key, value in collected.items()
        if str(key).strip() and value not in ("", [], None)
    ]


def _clean_tag_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("“", "").replace("”", "").replace("\"", "").replace("'", "")
    cleaned = cleaned.replace("：", ":")
    return cleaned


def _add_inferred_tag(collected: Dict[str, Any], raw_key: str, raw_value: str) -> None:
    key = TAG_KEY_ALIASES.get(raw_key, raw_key)
    value = _clean_tag_value(raw_value)
    if not value:
        return
    if key in {"职业", "种族", "风格"}:
        _merge_tag_value(collected, key, value)
    else:
        _merge_tag_value(collected, key, _split_tag_values(value))


def _clean_tag_value(value: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = re.sub(r"^[\s:：,，;；、]+", "", cleaned)
    cleaned = re.sub(r"^(加入|补充|新增|设为|设置为|定为|作为|成为|是|为|使用|常备)+", "", cleaned)
    cleaned = cleaned.strip(" :：,，;；、。")
    return cleaned


def _split_tag_values(value: str) -> Any:
    parts = [
        item.strip(" :：,，;；、。")
        for item in re.split(r"、|以及|和|/|，|,|；|;", value)
        if item.strip(" :：,，;；、。")
    ]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return parts


def _merge_tag_value(collected: Dict[str, Any], key: str, value: Any) -> None:
    if value in ("", [], None):
        return
    if key not in collected:
        collected[key] = value
        return
    existing = collected[key]
    existing_items = existing if isinstance(existing, list) else [existing]
    new_items = value if isinstance(value, list) else [value]
    merged: List[Any] = []
    for item in existing_items + new_items:
        if item not in merged:
            merged.append(item)
    collected[key] = merged if len(merged) > 1 else merged[0]


def _short_tag_value(value: str, limit: int) -> str:
    cleaned = _clean_tag_text(value)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _infer_tag_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (list, dict)):
        return "json"
    return "text"
