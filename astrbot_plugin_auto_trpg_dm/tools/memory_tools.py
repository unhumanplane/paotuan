from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.memory import MemoryCompressor
from ..core.models import Character, GameMode, GameSession, TagValue, compact_tag_layers, infer_tag_layer, utc_now_iso
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


class StartGameArgs(BaseModel):
    opening_intro: str = Field(
        ...,
        description="给玩家看的简短开场介绍，必须有氛围、当前处境和第一个压力点，建议 120-400 中文字。",
    )
    player_guidance: str = Field(
        default="",
        description="给玩家的简短行动引导，说明现在可以做什么，建议 1-3 条。",
    )
    campaign_outline: Dict[str, Any] = Field(
        default_factory=dict,
        description="开场前预备的跌宕剧情骨架，至少包含三段：导火索、升级/反转、高潮或重大抉择。",
    )
    scene_patch: Dict[str, Any] = Field(
        default_factory=dict,
        description="开场场景状态补丁，例如 summary,current_conflict,location,npcs,immediate_hooks。",
    )


class SessionControlArgs(BaseModel):
    action: str = Field(..., description="会话控制动作：status, reset, restore_latest_backup, list_backups, create_backup, compress_memory, debug_last")
    reason: str = Field(default="", description="执行该动作的自然语言原因")
    confirm_token: str = Field(default="", description="重开/清空存档的二次确认 token；没有 token 时只会发起确认，不会删除存档")


class MemoryTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: Optional[Dict[str, str]] = None,
        message: str = "",
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}
        self.message = message

    async def create_character(
        self,
        character_id: str,
        name: str,
        summary: str = "",
        player_id: str = "",
        tags: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """创建或覆盖一个 Tag 型角色卡。"""
        session = self.repository.load_session(self.session_id)
        owner_id = str(player_id or self.actor.get("player_id", "") or "").strip()
        gate = background_required_result(session, "create_character")
        if gate:
            self._audit(
                "create_character",
                {"character_id": character_id, "name": name, "summary": summary, "player_id": owner_id, "tags": tags or []},
                gate,
            )
            return gate
        safe_id = self._resolve_write_character_id(session, character_id, owner_id)
        if not safe_id:
            return {"ok": False, "error": "invalid_character_id"}
        normalized_tags = normalize_tags(tags)
        post_start_guard = post_start_create_character_guard(session, safe_id, owner_id)
        if post_start_guard:
            self._audit(
                "create_character",
                {"character_id": character_id, "resolved_character_id": safe_id, "name": name, "summary": summary, "player_id": owner_id, "tags": tags or []},
                post_start_guard,
            )
            return post_start_guard
        owner_guard = self._character_owner_guard(session, safe_id, owner_id)
        if owner_guard:
            self._audit(
                "create_character",
                {"character_id": character_id, "name": name, "summary": summary, "player_id": owner_id, "tags": tags or []},
                owner_guard,
            )
            return owner_guard
        validation = validate_character_card_payload(name=name, summary=summary, tags=normalized_tags, require_name=True)
        if validation:
            self._audit(
                "create_character",
                {"character_id": character_id, "resolved_character_id": safe_id, "name": name, "summary": summary, "player_id": owner_id, "tags": tags or []},
                validation,
            )
            return validation
        balance = validate_character_card_party_balance(
            session,
            safe_id,
            name=name,
            summary=summary,
            tags=normalized_tags,
        )
        if balance:
            self._audit(
                "create_character",
                {"character_id": character_id, "resolved_character_id": safe_id, "name": name, "summary": summary, "player_id": owner_id, "tags": tags or []},
                balance,
            )
            return balance
        previous_character_id = _terminal_rejoin_previous_character_id(session, owner_id, safe_id)
        character = Character(
            id=safe_id,
            name=name,
            player_id=owner_id,
            summary=summary,
            tags=[TagValue.from_dict(item) for item in normalized_tags],
        )
        session.characters[safe_id] = character
        if owner_id:
            session.player_character_map[owner_id] = safe_id
            self._touch_participant(session, owner_id)
        if owner_id or not session.active_character_id:
            session.active_character_id = safe_id
        if not _campaign_game_started(session):
            session.mode = GameMode.CHARACTER_CREATION
        rejoin_record = record_terminal_character_rejoin(
            session,
            owner_id=owner_id,
            previous_character_id=previous_character_id,
            new_character_id=safe_id,
            source="create_character",
        )
        self.repository.save_session(session)
        result = {
            "ok": True,
            "character": character_as_dict(character),
            "bound_player_id": owner_id,
            "player_character_map": session.player_character_map,
        }
        if rejoin_record:
            result["rejoin_replacement"] = rejoin_record
        self._audit(
            "create_character",
            {
                "character_id": character_id,
                "resolved_character_id": safe_id,
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
        owner_id = str(player_id or self.actor.get("player_id", "") or "").strip()
        if not owner_id:
            return {"ok": False, "error": "missing_player_id"}
        session = self.repository.load_session(self.session_id)
        safe_id = self._resolve_write_character_id(session, character_id, owner_id)
        if not safe_id:
            return {"ok": False, "error": "invalid_character_id"}
        character = session.characters.get(safe_id)
        normalized_tags = normalize_tags(tags)
        if not character:
            gate = background_required_result(session, "bind_player_character")
            if gate:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    gate,
                )
                return gate
            post_start_guard = post_start_create_character_guard(session, safe_id, owner_id)
            if post_start_guard:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    post_start_guard,
                )
                return post_start_guard
            validation = validate_character_card_payload(name=name or safe_id, summary=summary, tags=normalized_tags, require_name=True)
            if validation:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    validation,
                )
                return validation
            balance = validate_character_card_party_balance(
                session,
                safe_id,
                name=name or safe_id,
                summary=summary,
                tags=normalized_tags,
            )
            if balance:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    balance,
                )
                return balance
        elif _campaign_game_started(session):
            bound_id = str(session.player_character_map.get(owner_id, "") or "")
            same_binding = bool(owner_id and (bound_id == safe_id or character.player_id == owner_id))
            wants_card_change = bool(
                normalized_tags
                or summary
                or (name and name.strip() and name.strip() != character.name)
            )
            replacement_allowed = _post_start_terminal_rebind_allowed(
                session,
                owner_id=owner_id,
                current_bound_id=bound_id,
                target_character_id=safe_id,
                target_character=character,
            )
            if wants_card_change:
                result = character_card_locked_after_start_result(
                    "bind_player_character",
                    safe_id,
                    owner_id,
                    message="游戏已经开场，既有角色卡锁定；重新加入只能绑定合理的新角色，不能顺手改名、改摘要、补能力或补装备。",
                )
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    result,
                )
                return result
            if not same_binding and not replacement_allowed:
                result = character_card_locked_after_start_result(
                    "bind_player_character",
                    safe_id,
                    owner_id,
                    message="游戏已经开场，既有角色卡锁定；老玩家只有在原绑定角色已死亡、退休或永久退场时，才能绑定新的合理后继角色。",
                )
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    result,
                )
                return result
        elif character:
            validation = validate_character_card_payload(name=name, summary=summary, tags=normalized_tags, require_name=False)
            if validation:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    validation,
                )
                return validation
            balance = validate_character_card_party_balance(
                session,
                safe_id,
                name=name or character.name,
                summary=summary or character.summary,
                tags=_merged_character_tags(character, normalized_tags),
            )
            if balance:
                self._audit(
                    "bind_player_character",
                    {"character_id": character_id, "resolved_character_id": safe_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                    balance,
                )
                return balance
        owner_guard = self._character_owner_guard(session, safe_id, owner_id)
        if owner_guard:
            self._audit(
                "bind_player_character",
                {"character_id": character_id, "player_id": owner_id, "name": name, "summary": summary, "tags": tags or []},
                owner_guard,
            )
            return owner_guard
        created = False
        previous_character_id = _terminal_rejoin_previous_character_id(session, owner_id, safe_id)
        if not character:
            character = Character(
                id=safe_id,
                name=name or safe_id,
                player_id=owner_id,
                summary=summary,
                tags=[TagValue.from_dict(item) for item in normalized_tags],
            )
            session.characters[safe_id] = character
            created = True
        else:
            character.player_id = owner_id
            if name and not character.name:
                character.name = name
            if summary:
                character.summary = summary
            if normalized_tags:
                character.upsert_tags(normalized_tags)
        session.player_character_map[owner_id] = safe_id
        session.active_character_id = safe_id
        self._touch_participant(session, owner_id)
        rejoin_record = record_terminal_character_rejoin(
            session,
            owner_id=owner_id,
            previous_character_id=previous_character_id,
            new_character_id=safe_id,
            source="bind_player_character",
        )
        self.repository.save_session(session)
        result = {
            "ok": True,
            "created": created,
            "bound_player_id": owner_id,
            "character": character_as_dict(character),
            "player_character_map": session.player_character_map,
        }
        if rejoin_record:
            result["rejoin_replacement"] = rejoin_record
        self._audit(
            "bind_player_character",
            {
                "character_id": character_id,
                "resolved_character_id": safe_id,
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
        gate = background_required_result(session, "update_character_tags")
        if gate:
            self._audit("update_character_tags", {"character_id": character_id, "tags": tags, "raw_text": raw_text}, gate)
            return gate
        owner_id = str(self.actor.get("player_id", "") or "").strip()
        safe_id = self._resolve_existing_character_id(session, character_id, owner_id)
        character = session.characters.get(safe_id)
        if not character and (tags or raw_text):
            character = self._maybe_create_battle_character_stub(session, safe_id, owner_id)
        if not character:
            result = {"ok": False, "error": "character_not_found", "character_id": safe_id}
            self._audit(
                "update_character_tags",
                {"character_id": character_id, "resolved_character_id": safe_id, "tags": tags, "raw_text": raw_text},
                result,
            )
            return result
        owner_guard = self._character_owner_guard(session, safe_id, owner_id)
        if owner_guard:
            self._audit(
                "update_character_tags",
                {"character_id": character_id, "resolved_character_id": safe_id, "tags": tags, "raw_text": raw_text},
                owner_guard,
            )
            return owner_guard
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
        blocked_tags: List[Dict[str, Any]] = []
        if _campaign_game_started(session):
            normalized_tags, blocked_tags = filter_runtime_character_tags_after_start(normalized_tags)
            if not normalized_tags:
                result = character_card_locked_after_start_result(
                    "update_character_tags",
                    safe_id,
                    owner_id,
                    message="游戏已经开场，既有角色卡锁定；不能补写职业、能力、装备、默认战斗行为、背景或关系。只能记录伤势、生命/资源消耗、临时状态和最近行动结果。",
                )
                result["blocked_tags"] = blocked_tags
                self._audit(
                    "update_character_tags",
                    {"character_id": character_id, "resolved_character_id": safe_id, "tags": tags, "raw_text": raw_text},
                    result,
                )
                return result
        else:
            validation = validate_character_card_payload(name="", summary="", tags=normalized_tags, require_name=False)
            if validation:
                self._audit(
                    "update_character_tags",
                    {"character_id": character_id, "resolved_character_id": safe_id, "tags": normalized_tags, "raw_text": raw_text},
                    validation,
                )
                return validation
            balance = validate_character_card_party_balance(
                session,
                safe_id,
                name=character.name,
                summary=character.summary,
                tags=_merged_character_tags(character, normalized_tags),
            )
            if balance:
                self._audit(
                    "update_character_tags",
                    {"character_id": character_id, "resolved_character_id": safe_id, "tags": normalized_tags, "raw_text": raw_text},
                    balance,
                )
                return balance
        character.upsert_tags(normalized_tags)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "character": character_as_dict(character),
            "inferred_from_raw_text": inferred_from_raw_text,
            "updated_tags": normalized_tags,
        }
        if blocked_tags:
            result["character_card_locked_after_start"] = True
            result["blocked_tags"] = blocked_tags
            result["message"] = "已只记录场内状态变化；开场后的既有角色卡字段未改动。"
        self._audit(
            "update_character_tags",
            {"character_id": character_id, "resolved_character_id": safe_id, "tags": normalized_tags, "raw_text": raw_text},
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
        locked = plot_locked_result(session, self.message, "update_scene")
        if locked and patch_touches_plot_state(patch):
            self._audit("update_scene", {"patch": patch}, locked)
            return locked
        overreach = post_start_world_fact_overreach_result(session, self.message, patch, "update_scene")
        if overreach:
            self._audit("update_scene", {"patch": patch}, overreach)
            return overreach
        gate = background_required_result(session, "update_scene")
        if gate:
            self._audit("update_scene", {"patch": patch}, gate)
            return gate
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
        locked = plot_locked_result(session, self.message, "update_world_tags")
        if locked and patch_touches_plot_state(patch):
            self._audit("update_world_tags", {"patch": patch}, locked)
            return locked
        overreach = post_start_world_fact_overreach_result(session, self.message, patch, "update_world_tags")
        if overreach:
            self._audit("update_world_tags", {"patch": patch}, overreach)
            return overreach
        session.world_tags.update(patch)
        if has_campaign_background(session):
            session.world_tags["_background_ready"] = True
        if "title" in patch:
            session.title = str(patch["title"])
        self.repository.save_session(session)
        result = {"ok": True, "world_tags": session.world_tags, "title": session.title}
        self._audit("update_world_tags", {"patch": patch}, result)
        return result

    async def start_game(
        self,
        opening_intro: str,
        player_guidance: str = "",
        campaign_outline: Optional[Dict[str, Any]] = None,
        scene_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """检查内容是否足够，足够时写入开场、锁定剧情主干，并正式开始游戏。"""
        session = self.repository.load_session(self.session_id)
        opening_intro = _coerce_prompt_text(opening_intro)
        player_guidance = _coerce_prompt_text(player_guidance)
        if _campaign_plot_locked(session):
            result = {
                "ok": False,
                "error": "game_already_started",
                "message": "游戏已经开场，不能重复开场或重写开场；可以继续角色行动，或让新玩家加入。",
                "allow_late_join_after_start": True,
            }
            self._audit("start_game", {"opening_intro_chars": len(str(opening_intro or ""))}, result)
            return result
        campaign_outline = _coerce_campaign_outline_input(campaign_outline)
        scene_patch = _coerce_scene_patch_input(scene_patch)
        missing = campaign_start_missing_requirements(session, opening_intro, campaign_outline, scene_patch)
        if missing:
            result = {
                "ok": False,
                "error": "campaign_not_ready",
                "missing_requirements": missing,
                "message": "当前内容还不足以开场；请先补齐缺失项，再开始游戏。",
                "allow_late_join_after_start": True,
            }
            self._audit(
                "start_game",
                {
                    "opening_intro_chars": len(str(opening_intro or "")),
                    "campaign_outline": campaign_outline,
                    "scene_patch": scene_patch,
                },
                result,
            )
            return result

        session.scene.update(scene_patch)
        session.scene["_game_started"] = True
        session.scene["_game_started_at"] = utc_now_iso()
        session.scene["_plot_locked"] = True
        session.scene["_allow_late_join"] = True
        session.scene["_opening_intro"] = _short_tag_value(opening_intro, 700)
        session.scene["_player_guidance"] = _short_tag_value(player_guidance, 360)
        session.world_tags["_plot_locked"] = True
        session.world_tags["_late_join_allowed"] = True
        session.world_tags["campaign_outline"] = compact_campaign_outline(campaign_outline)
        if scene_patch.get("title"):
            session.title = str(scene_patch["title"])
        session.mode = GameMode.NARRATIVE
        self.repository.save_session(session)
        opening_message = opening_intro.strip()
        if player_guidance.strip():
            opening_message = f"{opening_message}\n\n{player_guidance.strip()}"
        result = {
            "ok": True,
            "game_started": True,
            "plot_locked": True,
            "allow_late_join": True,
            "opening_message": opening_message,
            "campaign_outline": session.world_tags["campaign_outline"],
            "scene": session.scene,
        }
        self._audit(
            "start_game",
            {
                "opening_intro_chars": len(str(opening_intro or "")),
                "player_guidance_chars": len(str(player_guidance or "")),
                "campaign_outline": campaign_outline,
                "scene_patch": scene_patch,
            },
            result,
        )
        return result

    async def session_control(self, action: str, reason: str = "", confirm_token: str = "") -> Dict[str, Any]:
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
            session = self.repository.load_session(self.session_id)
            if _looks_like_rollback_not_reset(reason or self.message):
                result = {
                    "ok": False,
                    "action": "rollback_not_supported",
                    "error": "rollback_not_supported",
                    "message": "当前没有安全回档/undo 工具，存档未改动。若确实要清空并开新团，请明确说“重开当前团”，系统仍会要求二次确认。",
                }
            else:
                result = self._request_or_confirm_reset(session, reason=reason, confirm_token=confirm_token)
        elif normalized in {"confirm_reset", "confirm reset", "确认重开", "确认清空", "确认重置"}:
            session = self.repository.load_session(self.session_id)
            result = self._request_or_confirm_reset(
                session,
                reason=reason,
                confirm_token=confirm_token,
                force_confirm=True,
            )
        elif normalized in {"create_backup", "backup", "备份", "备份存档", "保存备份"}:
            result = self._create_manual_backup(reason=reason)
        elif normalized in {"list_backups", "backup_list", "backups", "备份列表", "查看备份"}:
            result = self._list_backups()
        elif normalized in {"restore_latest_backup", "restore_backup", "restore", "恢复上一个存档", "恢复存档", "恢复之前的跑团"}:
            result = self._restore_latest_backup(reason=reason)
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
                "allowed": [
                    "status",
                    "reset",
                    "confirm_reset",
                    "create_backup",
                    "list_backups",
                    "restore_latest_backup",
                    "compress_memory",
                    "debug_last",
                ],
            }
        self._audit(
            "session_control",
            {"action": action, "reason": reason, "confirm_token": _mask_token(confirm_token)},
            result,
        )
        return result

    def _create_manual_backup(self, reason: str = "") -> Dict[str, Any]:
        backup_path = self.repository.backup_session(
            self.session_id,
            reason=f"manual_backup:{_short_reset_text(reason or self.message, 160)}",
        )
        session = self.repository.load_session(self.session_id)
        return {
            "ok": bool(backup_path),
            "action": "create_backup",
            "backup_path": str(backup_path) if backup_path else "",
            "message": "当前存档已写入备份。" if backup_path else "当前没有可备份的存档文件。",
            "characters": len(session.characters),
            "participants": len(session.participants),
            "battle_active": bool((session.battle or {}).get("active")),
        }

    def _list_backups(self, limit: int = 8) -> Dict[str, Any]:
        backups = self.repository.list_session_backups(self.session_id, limit=limit)
        return {
            "ok": True,
            "action": "list_backups",
            "count": len(backups),
            "backups": [
                {
                    "name": item.get("name", ""),
                    "size": item.get("size", 0),
                    "mtime": item.get("mtime", ""),
                    "reason": item.get("reason", ""),
                }
                for item in backups
            ],
            "message": "已列出最近备份。" if backups else "当前还没有自动备份。",
        }

    def _restore_latest_backup(self, reason: str = "") -> Dict[str, Any]:
        current = self.repository.load_session(self.session_id)
        if not _session_looks_empty_or_reset(current):
            return {
                "ok": False,
                "action": "restore_latest_backup",
                "error": "current_save_not_empty",
                "message": "当前存档不是空档，不能直接覆盖恢复。若要重开请走二次确认；若要人工回档，请先由管理员在服务器备份目录手动选择。",
                "current": {
                    "characters": len(current.characters),
                    "participants": len(current.participants),
                    "rules": len(current.rules),
                    "battle_active": bool((current.battle or {}).get("active")),
                },
            }
        backups = self.repository.list_session_backups(self.session_id, limit=20)
        usable = [item for item in backups if _backup_item_looks_useful(item)]
        if not usable:
            return {
                "ok": False,
                "action": "restore_latest_backup",
                "error": "no_usable_backup",
                "message": "没有找到可恢复的非空备份。",
                "backups_seen": len(backups),
            }
        selected = usable[0]
        before_restore = self.repository.backup_session(
            self.session_id,
            reason=f"before_restore_latest:{_short_reset_text(reason or self.message, 160)}",
        )
        self.repository.restore_session_backup(self.session_id, str(selected["path"]))
        restored = self.repository.load_session(self.session_id)
        if isinstance(restored.scene, dict):
            restored.scene["_dm_paused"] = True
            restored.scene["_dm_pause_reason"] = "已从上一个非空备份恢复；为避免心跳立即推进，请确认状态后再 /dm resume。"
            restored.scene["_dm_paused_by"] = {"player_id": "__system__", "display_name": "restore_latest_backup"}
            restored.scene["_dm_paused_at"] = utc_now_iso()
            restored.scene.pop("_pending_reset_confirmation", None)
            self.repository.save_session(restored)
        return {
            "ok": True,
            "action": "restore_latest_backup",
            "restored_backup": {
                "name": selected.get("name", ""),
                "size": selected.get("size", 0),
                "mtime": selected.get("mtime", ""),
                "reason": selected.get("reason", ""),
            },
            "pre_restore_backup_path": str(before_restore) if before_restore else "",
            "message": "已恢复上一个非空备份，并已自动暂停流程。确认状态后用 /dm resume 继续。",
            "current": {
                "characters": len(restored.characters),
                "participants": len(restored.participants),
                "rules": len(restored.rules),
                "battle_active": bool((restored.battle or {}).get("active")),
            },
        }

    def _request_or_confirm_reset(
        self,
        session: GameSession,
        reason: str = "",
        confirm_token: str = "",
        force_confirm: bool = False,
    ) -> Dict[str, Any]:
        scene = session.scene if isinstance(session.scene, dict) else {}
        session.scene = scene
        requester_id = str(self.actor.get("player_id", "") or "").strip()
        requester_name = str(self.actor.get("display_name", "") or "").strip()
        pending = dict(scene.get("_pending_reset_confirmation") or {})
        supplied_token = str(confirm_token or "").strip().upper()

        if supplied_token:
            if _looks_like_rollback_not_reset(reason or pending.get("reason") or self.message):
                scene.pop("_pending_reset_confirmation", None)
                self.repository.save_session(session)
                return {
                    "ok": False,
                    "action": "confirm_reset",
                    "error": "rollback_confirmation_rejected",
                    "message": "这是回档/undo 请求，不是明确重开当前团；确认码已作废，存档未改动。",
                }
            expected_token = str(pending.get("token", "") or "").strip().upper()
            if not expected_token or supplied_token != expected_token:
                return {
                    "ok": False,
                    "action": "confirm_reset",
                    "error": "reset_confirmation_invalid",
                    "message": "重开确认码不匹配，存档未改动。请重新发起重开请求获取新的确认码。",
                }
            expires_at = _parse_utc_datetime(pending.get("expires_at"))
            if expires_at and datetime.now(timezone.utc) > expires_at:
                scene.pop("_pending_reset_confirmation", None)
                self.repository.save_session(session)
                return {
                    "ok": False,
                    "action": "confirm_reset",
                    "error": "reset_confirmation_expired",
                    "message": "重开确认码已过期，存档未改动。请重新发起重开请求。",
                }
            pending_requester = str(pending.get("requester_player_id", "") or "").strip()
            if pending_requester and requester_id and requester_id != pending_requester:
                return {
                    "ok": False,
                    "action": "confirm_reset",
                    "error": "reset_confirmation_wrong_actor",
                    "message": "只有发起重开请求的人可以用该确认码清空存档；存档未改动。",
                    "requester_player_id": pending_requester,
                    "current_player_id": requester_id,
                }
            backup_path = self.repository.backup_session(
                self.session_id,
                reason=f"confirmed_reset:{_short_reset_text(reason or pending.get('reason', ''), 160)}",
            )
            self.repository.save_session(GameSession.new(self.session_id))
            return {
                "ok": True,
                "action": "reset",
                "backup_path": str(backup_path) if backup_path else "",
                "message": "二次确认已通过，当前跑团存档已重置；旧存档已先写入备份。",
            }

        if force_confirm:
            return {
                "ok": False,
                "action": "confirm_reset",
                "error": "missing_reset_confirmation_token",
                "message": "需要带上确认码才会重开存档；存档未改动。",
            }

        now = datetime.now(timezone.utc)
        existing_expires = _parse_utc_datetime(pending.get("expires_at"))
        if pending.get("token") and (not existing_expires or now <= existing_expires):
            token = str(pending["token"])
            expires_at = str(pending.get("expires_at") or "")
        else:
            token = f"RESET-{secrets.token_hex(3).upper()}"
            expires_at = (now + timedelta(minutes=5)).isoformat()
        scene["_pending_reset_confirmation"] = {
            "token": token,
            "requester_player_id": requester_id,
            "requester_display_name": requester_name,
            "reason": _short_reset_text(reason, 240),
            "created_at": utc_now_iso(),
            "expires_at": expires_at,
        }
        self.repository.save_session(session)
        return {
            "ok": False,
            "action": "reset_confirmation_required",
            "confirm_token": token,
            "expires_at": expires_at,
            "message": f"这会清空当前跑团存档。若确实要重开，请在 5 分钟内发送：/dm 确认重开 {token}",
        }

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

    def _resolve_write_character_id(self, session: GameSession, character_id: str, owner_id: str) -> str:
        safe_id = self._safe_id(character_id or "", prefix="pc")
        if self._is_generic_character_id(safe_id):
            bound_id = str(session.player_character_map.get(owner_id, "") or "")
            if owner_id and bound_id and bound_id in session.characters:
                if (
                    _campaign_game_started(session)
                    and _character_is_terminal_for_rejoin(session, bound_id)
                    and _looks_like_rejoin_request(self.message)
                ):
                    return _next_rejoin_character_id(session, owner_id)
                return bound_id
            if owner_id:
                return self._player_default_character_id(owner_id)
            return ""
        return safe_id

    def _resolve_existing_character_id(self, session: GameSession, character_id: str, owner_id: str) -> str:
        safe_id = self._safe_id(character_id or "", prefix="pc")
        bound_id = str(session.player_character_map.get(owner_id, "") or "")
        if self._is_generic_character_id(safe_id):
            if owner_id and bound_id and bound_id in session.characters:
                return bound_id
            return safe_id
        if safe_id in session.characters:
            return safe_id
        if owner_id and bound_id and bound_id in session.characters:
            return bound_id
        return safe_id

    def _maybe_create_battle_character_stub(
        self,
        session: GameSession,
        character_id: str,
        owner_id: str,
    ) -> Character | None:
        if not owner_id or not character_id or not character_id.startswith("pc_"):
            return None
        existing_owner = self._character_owner_id(session, character_id)
        if existing_owner and existing_owner != owner_id:
            return None
        bound_id = str(session.player_character_map.get(owner_id, "") or "")
        if bound_id and bound_id in session.characters and bound_id != character_id:
            return None
        if not self._battle_has_player_unit(session, character_id):
            return None

        name = self._battle_character_label(session, character_id)
        character = Character(
            id=character_id,
            name=name,
            player_id=owner_id,
            summary="战棋轮次中出现的玩家单位；由状态写入自动补档。",
            tags=[],
        )
        session.characters[character_id] = character
        session.player_character_map[owner_id] = character_id
        session.active_character_id = character_id
        self._touch_participant(session, owner_id)
        return character

    @staticmethod
    def _battle_has_player_unit(session: GameSession, character_id: str) -> bool:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        ids: set[str] = set()
        ids.update(str(item) for item in turn.get("turn_order", []) if str(item).strip())
        ids.update(str(item) for item in turn.get("pending_entity_ids", []) if str(item).strip())
        ids.update(str(item) for item in turn.get("acted_entity_ids", []) if str(item).strip())
        ids.update(str(key) for key in dict(turn.get("actions_this_round") or {}).keys())
        grid_entities = dict(((battle.get("grid") or {}).get("entities") or {}))
        ids.update(str(key) for key in grid_entities.keys())
        return character_id in ids

    @staticmethod
    def _battle_character_label(session: GameSession, character_id: str) -> str:
        battle = session.battle or {}
        grid_entity = dict((((battle.get("grid") or {}).get("entities") or {}).get(character_id)) or {})
        tags = dict(grid_entity.get("tags") or {})
        for key in ("name", "label", "display_name"):
            value = grid_entity.get(key) or tags.get(key)
            if str(value or "").strip():
                return str(value).strip()
        turn = dict(battle.get("turn") or {})
        if str(turn.get("current_entity_id", "") or "") == character_id and str(turn.get("current_label", "") or "").strip():
            return str(turn["current_label"]).strip()
        suffix = character_id[3:] if character_id.startswith("pc_") else character_id
        return suffix or character_id

    @staticmethod
    def _is_generic_character_id(character_id: str) -> bool:
        normalized = str(character_id or "").strip().lower()
        return normalized in {
            "",
            "pc",
            "player",
            "character",
            "hero",
            "role",
            "user",
            "pc_player",
            "pc_character",
            "pc_hero",
            "pc_role",
            "pc_user",
        }

    @staticmethod
    def _player_default_character_id(player_id: str) -> str:
        safe_player_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(player_id or "").strip()).strip("._-")
        return f"pc_{safe_player_id}" if safe_player_id else ""

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

    def _character_owner_guard(
        self,
        session: GameSession,
        character_id: str,
        requested_owner_id: str,
    ) -> Dict[str, Any] | None:
        existing_owner = self._character_owner_id(session, character_id)
        if not existing_owner or not requested_owner_id or existing_owner == requested_owner_id:
            return None
        return {
            "ok": False,
            "error": "character_owner_conflict",
            "message": "该角色已经绑定到其他玩家，不能通过昵称或自然语言声明抢占控制权。",
            "character_id": character_id,
            "owner_player_id": existing_owner,
            "requester_player_id": requested_owner_id,
        }

    @staticmethod
    def _character_owner_id(session: GameSession, character_id: str) -> str:
        character = session.characters.get(character_id)
        if character and character.player_id:
            return str(character.player_id)
        for player_id, bound_id in session.player_character_map.items():
            if bound_id == character_id:
                return str(player_id)
        return ""


def _parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mask_token(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


def _short_reset_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)] + "..."


def _looks_like_rollback_not_reset(text: Any) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    rollback_terms = (
        "undo",
        "rollback",
        "roll back",
        "回档",
        "回退",
        "退回",
        "撤销",
        "上一回合",
        "上个回合",
        "上一轮",
        "上个轮次",
        "重试一次",
        "重新判",
    )
    explicit_reset_terms = (
        "清空存档",
        "删除存档",
        "重开当前团",
        "重开跑团",
        "重置存档",
        "开新团",
        "reset save",
        "reset campaign",
        "new campaign",
    )
    return any(term in normalized for term in rollback_terms) and not any(term in normalized for term in explicit_reset_terms)


def _session_looks_empty_or_reset(session: GameSession) -> bool:
    if session.characters or session.player_character_map or session.rules:
        return False
    if bool((session.battle or {}).get("active")):
        return False
    world_tags = dict(session.world_tags or {})
    if any(not str(key).startswith("_") for key in world_tags.keys()):
        return False
    participants = dict(session.participants or {})
    if len(participants) > 3:
        return False
    scene = dict(session.scene or {})
    summary = str(scene.get("summary", "") or "")
    reset_like = (
        "尚未开局" in summary
        or "等待玩家" in summary
        or not summary.strip()
        or summary.startswith("灏氭湭寮€灞")
    )
    return reset_like


def _backup_item_looks_useful(item: Dict[str, Any]) -> bool:
    path = Path(str(item.get("path") or ""))
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        session = GameSession.from_dict(data)
    except Exception:
        return False
    return not _session_looks_empty_or_reset(session)


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


CARD_STATIC_LAYERS = {"identity", "abilities", "equipment", "combat", "relations", "notes"}

RUNTIME_STATUS_KEY_TERMS = (
    "状态",
    "伤势",
    "伤口",
    "生命",
    "血量",
    "hp",
    "体力",
    "资源",
    "法术位",
    "弹药",
    "消耗",
    "剩余",
    "冷却",
    "临时",
    "buff",
    "debuff",
    "增益",
    "减益",
    "中毒",
    "倒地",
    "眩晕",
    "流血",
    "隐藏",
    "潜行",
    "暴露",
    "警戒",
    "专注",
    "集中",
    "最近行动",
    "本轮行动",
    "行动结果",
)

STATIC_CARD_KEY_TERMS = (
    "姓名",
    "职业",
    "种族",
    "身份",
    "背景",
    "出身",
    "阵营",
    "能力",
    "技能",
    "专长",
    "法术",
    "属性",
    "力量",
    "敏捷",
    "体质",
    "智力",
    "感知",
    "魅力",
    "等级",
    "level",
    "装备",
    "武器",
    "护甲",
    "ac",
    "战术",
    "默认战斗行为",
    "战斗习惯",
    "传奇能力",
    "传奇壮举",
    "魔网权限",
    "存在形式",
    "荣誉称号",
    "性格备注",
    "虚空印记",
    "关系",
    "盟友",
    "敌人",
    "组织",
)

CARD_INJECTION_TERMS = (
    "系统:",
    "system:",
    "developer:",
    "assistant:",
    "tool:",
    "忽略以上",
    "忽略规则",
    "忽略系统",
    "切换 auto",
    "auto accept",
    "自动接受",
    "绕过检定",
    "不要投骰",
)

CARD_OVERPOWERED_TERMS = (
    "无敌",
    "全能",
    "全知",
    "不死不灭",
    "无法被伤害",
    "免疫所有",
    "必定成功",
    "永远成功",
    "自动成功",
    "判定成功",
    "检定成功",
    "一击必杀",
    "秒杀",
    "无限生命",
    "无限体力",
    "无限资源",
    "无限法术",
    "无限法术位",
    "无限金币",
    "无限行动",
    "无限攻击",
    "无限反应",
    "无限附赠动作",
    "任意法术",
    "任意魔法",
    "所有法术",
    "所有魔法",
    "随意使用超魔",
    "随意使用超魔技巧",
    "随意施法",
    "随时施法",
    "魔法能量转化为任意法术",
    "不受任何debuff",
    "不受任何 debuff",
    "免疫debuff",
    "免疫所有debuff",
    "清除所有debuff",
    "所有传奇赐福",
    "魔网化身",
    "众生愿力",
    "全球神迹",
    "全世界",
    "全球",
    "召唤陨石",
    "虚空陨石",
    "帝皇",
    "神皇",
    "原体",
    "十三个原体",
    "十三名原体",
    "13个原体",
    "13名原体",
    "禁军",
    "创世神",
    "造物主",
    "世界意志",
    "裂变反应堆",
    "裸露的裂变反应堆",
    "稳定裂变反应",
    "可控临界",
    "可控裂变",
)

CARD_FACT_INJECTION_TERMS = (
    "敌人已经死",
    "敌人全死",
    "boss已经死",
    "最终boss已经死",
    "我已经赢",
    "我们已经赢",
    "剧情真相是",
    "幕后黑手是",
    "结局是",
)


def _campaign_game_started(session: GameSession) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    return bool(
        scene.get("_game_started")
        or scene.get("_legacy_live_campaign")
        or world_tags.get("_plot_locked") is True
    )


def _late_join_allowed(session: GameSession) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    return bool(scene.get("_allow_late_join", True) or world_tags.get("_late_join_allowed", True))


REJOIN_REQUEST_TERMS = (
    "重新加入",
    "重新进团",
    "重新入团",
    "再加入",
    "重建角色",
    "换新角色",
    "换角色",
    "新角色",
    "新号",
    "补位",
    "替补",
    "后继角色",
    "角色死了",
    "角色死亡",
    "阵亡",
    "已死",
    "退场",
    "退休",
    "revive as new",
    "new character",
    "rejoin",
)

TERMINAL_STATUS_EXACT = {
    "死亡",
    "已死亡",
    "阵亡",
    "已死",
    "死了",
    "身亡",
    "牺牲",
    "永久退场",
    "退场",
    "退休",
    "离队",
    "dead",
    "deceased",
    "killed",
    "retired",
    "out_of_play",
    "out of play",
}

TERMINAL_STATUS_TERMS = (
    "已死亡",
    "已经死亡",
    "确认死亡",
    "死亡确认",
    "当场死亡",
    "彻底死亡",
    "永久死亡",
    "不可复活",
    "无法复活",
    "阵亡",
    "已死",
    "死了",
    "身亡",
    "牺牲",
    "遗体",
    "尸体",
    "永久退场",
    "确认退场",
    "退场",
    "退休",
    "永久离队",
    "无法继续扮演",
    "不再可扮演",
    "dead",
    "deceased",
    "killed",
    "retired",
    "permanently removed",
    "out of play",
)

TERMINAL_KEY_TERMS = (
    "状态",
    "生命状态",
    "退场",
    "死亡",
    "结局",
    "当前状态",
    "status",
    "state",
    "condition",
)

NON_TERMINAL_DEATH_CONTEXT_TERMS = (
    "死亡豁免",
    "死亡豁免失败",
    "死亡豁免成功",
    "death save",
    "death saving",
    "濒死",
    "昏迷",
    "倒地但未死",
    "稳定伤势",
)

TERMINAL_OVERRIDE_TERMS = (
    "三次失败",
    "失败三次",
    "失败3次",
    "3次失败",
    "确认死亡",
    "死亡确认",
    "已经死亡",
    "已死亡",
    "永久退场",
)


def post_start_create_character_guard(session: GameSession, character_id: str, owner_id: str) -> Dict[str, Any] | None:
    if not _campaign_game_started(session):
        return None
    if character_id in session.characters:
        return character_card_locked_after_start_result(
            "create_character",
            character_id,
            owner_id,
            message="游戏已经开场，既有角色卡锁定，不能覆盖或重写。新玩家请使用新的角色 ID 建卡。",
        )
    if not _late_join_allowed(session):
        return {
            "ok": False,
            "error": "late_join_not_allowed",
            "phase": "post_opening",
            "character_id": character_id,
            "message": "游戏已经开场，当前团未允许新玩家补入角色；存档未改动。",
        }
    if not owner_id:
        return {
            "ok": False,
            "error": "late_join_requires_player_id",
            "phase": "post_opening",
            "character_id": character_id,
            "message": "开场后只允许新玩家为自己创建新角色；缺少当前发言人 player_id，未写入角色卡。",
        }
    bound_id = str(session.player_character_map.get(owner_id, "") or "")
    if bound_id and bound_id in session.characters:
        if bound_id != character_id and _character_is_terminal_for_rejoin(session, bound_id):
            return None
        return character_card_locked_after_start_result(
            "create_character",
            character_id,
            owner_id,
            message="游戏已经开场，该玩家已有绑定角色；只有原角色已被状态或战棋事实确认死亡、退休或永久退场时，才允许创建新的合理后继角色。",
        )
    return None


def _looks_like_rejoin_request(message: str) -> bool:
    return _contains_any_text(str(message or ""), REJOIN_REQUEST_TERMS)


def _terminal_rejoin_previous_character_id(session: GameSession, owner_id: str, new_character_id: str) -> str:
    if not owner_id:
        return ""
    bound_id = str(session.player_character_map.get(owner_id, "") or "")
    if not bound_id or bound_id == new_character_id or bound_id not in session.characters:
        return ""
    if _character_is_terminal_for_rejoin(session, bound_id):
        return bound_id
    return ""


def _post_start_terminal_rebind_allowed(
    session: GameSession,
    *,
    owner_id: str,
    current_bound_id: str,
    target_character_id: str,
    target_character: Character,
) -> bool:
    if not owner_id or not current_bound_id or current_bound_id == target_character_id:
        return False
    if current_bound_id not in session.characters:
        return False
    if not _character_is_terminal_for_rejoin(session, current_bound_id):
        return False
    target_owner = str(target_character.player_id or "").strip()
    return not target_owner or target_owner == owner_id


def _character_is_terminal_for_rejoin(session: GameSession, character_id: str) -> bool:
    character = session.characters.get(character_id)
    if not character:
        return False
    for tag in character.tags or []:
        key_text = str(tag.key or "")
        value_text = _flatten_text(tag.value)
        layer = str(tag.layer or infer_tag_layer(key_text)).lower()
        if layer == "status" or _contains_any_text(key_text, TERMINAL_KEY_TERMS):
            if _terminal_status_text_match(f"{key_text} {value_text}") or _terminal_status_text_match(value_text):
                return True
    return _battle_entity_is_terminal_for_rejoin(session, character_id)


def _battle_entity_is_terminal_for_rejoin(session: GameSession, character_id: str) -> bool:
    battle = session.battle or {}
    grid = dict(battle.get("grid") or {})
    raw_entities = grid.get("entities") or {}
    if isinstance(raw_entities, dict):
        entities = [{"id": str(entity_id), **dict(entity)} for entity_id, entity in raw_entities.items() if isinstance(entity, dict)]
    elif isinstance(raw_entities, list):
        entities = [dict(item) for item in raw_entities if isinstance(item, dict)]
    else:
        entities = []
    for entity in entities:
        tags = dict(entity.get("tags") or {})
        entity_id = str(entity.get("id") or "")
        tagged_character_id = str(tags.get("character_id") or "")
        if character_id not in {entity_id, tagged_character_id}:
            continue
        if _terminal_status_text_match(_flatten_text([entity, tags])):
            return True
        for key in ("dead", "deceased", "retired", "removed", "out_of_play", "permanently_removed"):
            if entity.get(key) is True or tags.get(key) is True:
                return True
    return False


def _terminal_status_text_match(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    if not lowered:
        return False
    stripped = lowered.strip(" ，,。.!！?？:：;；[]{}()（）\"'`")
    if stripped in TERMINAL_STATUS_EXACT:
        return True
    if _contains_any_text(lowered, NON_TERMINAL_DEATH_CONTEXT_TERMS) and not _contains_any_text(lowered, TERMINAL_OVERRIDE_TERMS):
        return False
    if re.search(r"\b(dead|deceased|killed|retired)\b", lowered):
        return True
    return _contains_any_text(lowered, TERMINAL_STATUS_TERMS)


def _next_rejoin_character_id(session: GameSession, owner_id: str) -> str:
    base = f"{MemoryTools._player_default_character_id(owner_id)}_rejoin"
    for index in range(1, 100):
        candidate = f"{base}_{index}"
        if candidate not in session.characters:
            return candidate
    return f"{base}_{secrets.token_hex(3)}"


def record_terminal_character_rejoin(
    session: GameSession,
    *,
    owner_id: str,
    previous_character_id: str,
    new_character_id: str,
    source: str,
) -> Dict[str, Any] | None:
    if not owner_id or not previous_character_id or not new_character_id or previous_character_id == new_character_id:
        return None
    previous = session.characters.get(previous_character_id)
    new = session.characters.get(new_character_id)
    if not previous or not new:
        return None
    if not _character_is_terminal_for_rejoin(session, previous_character_id):
        return None
    now = utc_now_iso()
    previous_label = previous.name or previous_character_id
    new_label = new.name or new_character_id
    previous.upsert_tags(
        [
            {
                "key": "后继角色",
                "value": new_character_id,
                "type": "text",
                "source": "system",
                "layer": "relations",
            },
            {
                "key": "退场绑定状态",
                "value": f"玩家已以 {new_label} 重新加入；旧角色保持死亡/退场状态，不得覆盖、复活式重绑或改写结局。",
                "type": "text",
                "source": "system",
                "layer": "status",
            },
        ]
    )
    new.upsert_tags(
        [
            {
                "key": "前任角色",
                "value": previous_character_id,
                "type": "text",
                "source": "system",
                "layer": "relations",
            },
            {
                "key": "入场原因",
                "value": f"{previous_label} 死亡/退场后的后继角色；入场方式必须贴合当前场景、阵营、地点与时间压力。",
                "type": "text",
                "source": "system",
                "layer": "notes",
            },
        ]
    )
    replacements = session.scene.get("_character_replacements")
    if not isinstance(replacements, list):
        replacements = []
    record = {
        "player_id": owner_id,
        "previous_character_id": previous_character_id,
        "new_character_id": new_character_id,
        "source": source,
        "created_at": now,
        "policy": "terminal_character_rejoin",
    }
    if not any(
        isinstance(item, dict)
        and item.get("player_id") == owner_id
        and item.get("previous_character_id") == previous_character_id
        and item.get("new_character_id") == new_character_id
        for item in replacements
    ):
        replacements.append(record)
    session.scene["_character_replacements"] = replacements[-20:]
    return record


def character_card_locked_after_start_result(
    tool_name: str,
    character_id: str,
    owner_id: str,
    *,
    message: str,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": "character_card_locked_after_start",
        "tool": tool_name,
        "phase": "post_opening",
        "character_id": character_id,
        "requester_player_id": owner_id,
        "message": message,
        "allowed_after_start": [
            "新玩家创建新的合理角色卡",
            "记录伤势、生命/资源消耗、临时状态",
            "记录最近行动结果",
            "通过场景或战斗工具推进事实",
        ],
        "blocked_after_start": [
            "覆盖既有角色卡",
            "老玩家补能力/装备/职业/默认战斗行为",
            "重绑或抢占已有角色",
            "把玩家主张直接写成成功事实",
        ],
    }


def validate_character_card_payload(
    *,
    name: str = "",
    summary: str = "",
    tags: Optional[List[Dict[str, Any]]] = None,
    require_name: bool = False,
) -> Dict[str, Any] | None:
    normalized_tags = tags or []
    reasons: List[str] = []
    name_text = str(name or "").strip()
    if require_name and not name_text:
        reasons.append("角色名不能为空。")
    if len(name_text) > 40:
        reasons.append("角色名过长，请压缩到 40 字以内。")
    if len(str(summary or "")) > 800:
        reasons.append("角色摘要过长，请保留身份、动机、核心能力和限制。")
    if len(normalized_tags) > 32:
        reasons.append("角色 Tag 过多，请先保留核心身份、能力、装备、弱点和状态。")

    combined = _flatten_text([name, summary, normalized_tags]).lower()
    if any(term.lower() in combined for term in CARD_INJECTION_TERMS):
        reasons.append("角色卡包含系统/工具越权或跳过规则的话术。")
    if any(term.lower() in combined for term in CARD_OVERPOWERED_TERMS):
        reasons.append("角色卡包含自动成功、无敌、无限资源或秒杀类主张。")
    if _looks_like_strategic_asset_claim(combined):
        reasons.append("角色卡不能直接携带、调用或控制核弹、战略导弹、轨道炮、舰队/军团等战略级资源；这类资源只能作为剧情目标或由 DM 在场内授予。")
    if _looks_like_force_multiplier_claim(combined):
        reasons.append("角色卡不能直接自带或指挥军团、舰队、亲卫队、重型载具编队等队伍外战力；这类资源需要场内获得、消耗或由 DM 授予。")
    if _looks_like_mythic_power_claim(combined):
        reasons.append("角色卡不能直接写成半神、神格、创世神、全知全能或传奇权能；高阶身份和超凡权能必须先符合队伍层级并由 DM 裁定。")
    if _looks_like_late_join_power_bundle(combined):
        reasons.append("开场后新角色不能自带军队、传奇随从、跨作品神级身份或路过式解决当前冲突。")
    if _looks_like_world_law_rewrite(combined):
        reasons.append("角色卡或行动描述不能把“世界意志/规则修正/清除不合世界观事物”写成可执行能力。")
    if any(term.lower() in combined for term in CARD_FACT_INJECTION_TERMS):
        reasons.append("角色卡把剧情结果、敌人死亡或真相直接写成既成事实。")
    if _looks_like_action_economy_abuse(combined):
        reasons.append("角色卡包含不合理的动作经济循环，例如反复刷新/触发动作如潮。")
    reasons.extend(_absurd_stat_reasons(combined))

    if not reasons:
        return None
    return {
        "ok": False,
        "error": "character_card_unreasonable",
        "message": "角色卡合理性校验未通过；可以保留概念，但必须移除自动成功、无敌/无限资源、越权改剧情或明显超规格数值。",
        "reasons": list(dict.fromkeys(reasons))[:8],
        "suggestion": "请改成有边界的能力、装备、弱点和资源消耗；强效果需要规则、检定或开场后场内获得。",
    }


BALANCE_STRATEGIC_ASSET_TERMS = (
    "核弹",
    "核武",
    "核武器",
    "原子弹",
    "氢弹",
    "战术核",
    "战略导弹",
    "洲际导弹",
    "导弹发射井",
    "轨道炮",
    "卫星炮",
    "天基武器",
    "反物质炸弹",
    "歼星",
    "灭星",
    "行星毁灭",
    "铀",
    "铀235",
    "铀-235",
    "u235",
    "u-235",
    "浓缩铀",
    "贫铀",
    "核燃料",
    "核材料",
    "放射性同位素",
    "裂变",
    "核裂变",
    "链式反应",
    "反应堆",
    "核反应堆",
    "裂变反应堆",
    "临界态",
    "可控临界",
    "可控裂变",
    "criticality",
    "critical mass",
    "fissile",
    "uranium",
    "nuke",
    "nuclear",
    "icbm",
    "orbital cannon",
)

BALANCE_NUCLEAR_BODY_TERMS = (
    "身体含铀",
    "矿物元素含铀",
    "由铀组成",
    "铀元素结晶",
    "铀矿",
    "u235石头人",
    "u-235石头人",
    "铀235石头人",
    "铀-235石头人",
    "浓缩铀石头人",
    "身体是铀",
    "体内有铀",
    "体内含铀",
    "核燃料身体",
    "核材料身体",
    "裂变反应堆",
    "裸露的裂变反应堆",
)

BALANCE_STRATEGIC_CONTROL_TERMS = (
    "拥有",
    "携带",
    "自带",
    "带着",
    "装备",
    "背包",
    "库存",
    "仓库",
    "掏出",
    "发射",
    "部署",
    "调用",
    "调动",
    "持有",
    "使用",
    "能用",
    "可用",
    "有",
    "控制",
    "掌控",
    "指挥",
    "按钮",
    "遥控",
    "发射器",
    "发射井",
    "armed with",
    "carry",
    "carries",
    "has a",
    "owns",
)

BALANCE_FORCE_MULTIPLIER_TERMS = (
    "军团长",
    "舰队司令",
    "禁军统领",
    "亲卫队",
    "禁军",
    "私人军队",
    "雇佣兵团",
    "机器人军团",
    "舰队",
    "军团",
    "军队",
    "武装部队",
    "战舰",
    "航母",
    "坦克连",
    "机甲部队",
    "army",
    "fleet",
    "legion",
    "battleship",
)

BALANCE_FORCE_CONTROL_TERMS = (
    "拥有",
    "自带",
    "带着",
    "有",
    "率领",
    "统领",
    "指挥",
    "控制",
    "调动",
    "掌握",
    "召唤",
    "随叫随到",
    "听命于我",
    "听我命令",
    "under my command",
    "command",
    "controls",
)

BALANCE_MYTHIC_TERMS = (
    "半神",
    "神格",
    "神明",
    "神皇",
    "帝皇",
    "原体",
    "创世神",
    "造物主",
    "世界意志",
    "全知",
    "全能",
    "不死不灭",
    "demigod",
    "godlike",
)

BALANCE_LEGENDARY_POWER_TERMS = (
    "传奇权能",
    "传奇赐福",
    "传奇动作",
    "传奇抗性",
    "史诗权能",
    "神话权能",
    "神话赐福",
    "legendary action",
    "legendary resistance",
    "mythic power",
    "mythic trait",
)

BALANCE_HIGH_TECH_TERMS = (
    "动力甲",
    "机甲",
    "高达",
    "高斯步枪",
    "激光炮",
    "等离子炮",
    "火箭筒",
    "反坦克",
    "装甲车",
    "战斗无人机",
    "power armor",
    "mecha",
    "railgun",
    "plasma cannon",
    "rocket launcher",
)


def validate_character_card_party_balance(
    session: GameSession,
    character_id: str,
    *,
    name: str = "",
    summary: str = "",
    tags: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any] | None:
    existing_profiles: List[Dict[str, Any]] = []
    for existing_id, character in (session.characters or {}).items():
        if str(existing_id) == str(character_id):
            continue
        if not character:
            continue
        if not str(character.name or character.summary or character.tags).strip():
            continue
        existing_profiles.append(
            _character_power_profile(
                name=character.name,
                summary=character.summary,
                tags=_character_tags_as_dicts(character),
            )
        )
    if not existing_profiles:
        return None

    candidate = _character_power_profile(name=name, summary=summary, tags=tags or [])
    baseline_scores = [int(profile.get("score", 0)) for profile in existing_profiles]
    baseline_max_score = max(baseline_scores, default=0)
    baseline_avg_score = sum(baseline_scores) / max(1, len(baseline_scores))
    baseline_levels = [int(profile.get("level", 0)) for profile in existing_profiles if int(profile.get("level", 0)) > 0]
    baseline_max_level = max(baseline_levels, default=0)
    baseline_min_level = min(baseline_levels, default=0)
    candidate_level = int(candidate.get("level", 0))

    reasons: List[str] = []
    if candidate_level and baseline_levels and candidate_level > baseline_max_level + 2:
        reasons.append(f"新角色等级 {candidate_level} 明显高于现有角色最高等级 {baseline_max_level}；请控制在同级或最多高 1-2 级。")
    if candidate_level and baseline_levels and baseline_min_level and candidate_level < baseline_min_level - 5:
        reasons.append(f"新角色等级 {candidate_level} 明显低于现有角色最低等级 {baseline_min_level}；请与队伍等级接近，避免战力差异过大。")
    if candidate_level >= 8 and not baseline_levels and baseline_max_score < 6:
        reasons.append("现有角色卡没有中高等级基准；新角色不能直接写成 8 级以上或高阶角色。")

    if candidate.get("strategic_terms") and not any(profile.get("strategic_terms") for profile in existing_profiles):
        terms = "、".join(candidate.get("strategic_terms", [])[:4])
        reasons.append(f"新角色携带或控制了战略级资源（{terms}），但现有角色没有同级资源。")
    if candidate.get("force_terms") and not any(profile.get("force_terms") for profile in existing_profiles):
        terms = "、".join(candidate.get("force_terms", [])[:4])
        reasons.append(f"新角色自带的随从/军团/载具资源（{terms}）明显高于队伍基准。")
    if candidate.get("mythic_terms") and not any(profile.get("mythic_terms") for profile in existing_profiles):
        terms = "、".join(candidate.get("mythic_terms", [])[:4])
        reasons.append(f"新角色包含神话/传奇级身份或权能（{terms}），但现有角色不是同一层级。")
    if candidate.get("high_tech_terms") and not any(profile.get("high_tech_terms") for profile in existing_profiles) and baseline_max_score < 5:
        terms = "、".join(candidate.get("high_tech_terms", [])[:4])
        reasons.append(f"新角色装备科技/火力层级（{terms}）明显高于现有角色卡。")

    candidate_score = int(candidate.get("score", 0))
    score_ceiling = max(baseline_max_score + 6, int(baseline_avg_score + 8), 8)
    if candidate_score >= 8 and candidate_score > score_ceiling:
        reasons.append(f"新角色综合战力评分 {candidate_score} 明显超过队伍基准上限 {score_ceiling}；请削弱身份、装备、随从或特殊能力。")

    if not reasons:
        return None
    return {
        "ok": False,
        "error": "character_card_power_mismatch",
        "message": "角色卡强度与同团既有角色差异过大；为了维护游戏平衡，新角色必须和现有角色保持同一级别水平。",
        "reasons": list(dict.fromkeys(reasons))[:8],
        "party_baseline": {
            "existing_character_count": len(existing_profiles),
            "max_level": baseline_max_level,
            "max_power_score": baseline_max_score,
            "avg_power_score": round(baseline_avg_score, 1),
        },
        "candidate_profile": {
            "level": candidate_level,
            "power_score": candidate_score,
            "matched_terms": candidate.get("matched_terms", [])[:8],
        },
        "suggestion": "请把新角色改成和队伍同级：保留概念和弱点，移除核弹/军团/神格/超规格装备，把强力资源改成需要场内寻找、检定、消耗或 DM 授予。",
    }


def _character_power_profile(*, name: str, summary: str, tags: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = _flatten_text([name, summary, tags]).lower()
    levels = _extract_character_levels(text)
    level = max(levels, default=0)
    strategic_terms = _matched_terms(text, BALANCE_STRATEGIC_ASSET_TERMS) if _looks_like_strategic_asset_claim(text) else []
    force_candidates = _matched_terms(text, BALANCE_FORCE_MULTIPLIER_TERMS)
    force_terms = force_candidates if force_candidates and _contains_any_text(text, BALANCE_FORCE_CONTROL_TERMS) else []
    mythic_terms = list(
        dict.fromkeys(
            [
                *_matched_terms(text, BALANCE_MYTHIC_TERMS),
                *_matched_terms(text, BALANCE_LEGENDARY_POWER_TERMS),
            ]
        )
    )
    high_tech_terms = _matched_terms(text, BALANCE_HIGH_TECH_TERMS)
    absurd_numeric = _has_extreme_card_number(text)

    score = 0
    if level:
        score += max(1, min(level, 20) // 2)
        if level >= 15:
            score += 6
        elif level >= 10:
            score += 3
        elif level >= 5:
            score += 1
    score += 12 if strategic_terms else 0
    score += 8 if force_terms else 0
    score += 9 if mythic_terms else 0
    score += 4 if high_tech_terms else 0
    score += 5 if absurd_numeric else 0
    matched_terms = list(dict.fromkeys([*strategic_terms, *force_terms, *mythic_terms, *high_tech_terms]))
    return {
        "level": level,
        "score": score,
        "strategic_terms": strategic_terms,
        "force_terms": force_terms,
        "mythic_terms": mythic_terms,
        "high_tech_terms": high_tech_terms,
        "matched_terms": matched_terms,
        "absurd_numeric": absurd_numeric,
    }


def _extract_character_levels(text: str) -> List[int]:
    levels: List[int] = []
    patterns = (
        re.compile(r"(?:level|lvl|lv\.?|等级|角色等级)\s*[:：]?\s*(\d{1,2})", re.IGNORECASE),
        re.compile(r"(\d{1,2})\s*级"),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = int(match.group(1))
            if 1 <= value <= 30:
                levels.append(value)
    return levels


def _looks_like_strategic_asset_claim(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any_text(lowered, BALANCE_NUCLEAR_BODY_TERMS):
        return True
    return _contains_any_text(lowered, BALANCE_STRATEGIC_ASSET_TERMS) and _contains_any_text(lowered, BALANCE_STRATEGIC_CONTROL_TERMS)


def _looks_like_force_multiplier_claim(text: str) -> bool:
    lowered = str(text or "").lower()
    leadership_terms = ("军团长", "舰队司令", "禁军统领")
    if _contains_any_text(lowered, leadership_terms):
        return True
    return _contains_any_text(lowered, BALANCE_FORCE_MULTIPLIER_TERMS) and _contains_any_text(lowered, BALANCE_FORCE_CONTROL_TERMS)


def _looks_like_mythic_power_claim(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any_text(lowered, BALANCE_LEGENDARY_POWER_TERMS):
        return True
    strong_terms = (
        "半神",
        "神格",
        "神皇",
        "帝皇",
        "原体",
        "创世神",
        "造物主",
        "世界意志",
        "全知",
        "全能",
        "不死不灭",
        "demigod",
        "godlike",
    )
    if _contains_any_text(lowered, strong_terms):
        return True
    return _contains_any_text(lowered, ("神明", "mythic", "legendary")) and _contains_any_text(
        lowered,
        ("我是", "身为", "作为", "成为", "拥有", "持有", "掌握", "权能", "赐福", "神力", "神性", "神级"),
    )


def _matched_terms(text: str, terms: tuple[str, ...]) -> List[str]:
    lowered = str(text or "").lower()
    return list(dict.fromkeys(term for term in terms if str(term).lower() in lowered))


def _has_extreme_card_number(text: str) -> bool:
    return bool(
        re.search(r"(?:dc|难度|豁免)\D{0,8}(?:3[0-9]|[4-9]\d)", text, flags=re.IGNORECASE)
        or re.search(r"\+(?:1[0-9]|[2-9]\d)\D{0,8}(?:加值|修正|bonus|攻击|豁免|检定)", text, flags=re.IGNORECASE)
    )


def _character_tags_as_dicts(character: Character) -> List[Dict[str, Any]]:
    return [
        {
            "key": tag.key,
            "value": tag.value,
            "type": tag.type,
            "source": tag.source,
            "layer": tag.layer or infer_tag_layer(tag.key),
        }
        for tag in (character.tags or [])
    ]


def _merged_character_tags(character: Character, new_tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[tuple[str, str], Dict[str, Any]] = {}
    for item in _character_tags_as_dicts(character):
        key = str(item.get("key", ""))
        layer = str(item.get("layer") or infer_tag_layer(key))
        merged[(layer, key)] = dict(item)
    for item in new_tags or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        if not key:
            continue
        layer = str(item.get("layer") or infer_tag_layer(key))
        normalized = dict(item)
        normalized["layer"] = layer
        merged[(layer, key)] = normalized
    return list(merged.values())


def filter_runtime_character_tags_after_start(tags: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    allowed: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for item in tags:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        key = str(normalized.get("key", "")).strip()
        layer = str(normalized.get("layer") or infer_tag_layer(key)).strip().lower()
        value_text = _flatten_text(normalized.get("value", "")).lower()
        key_text = key.lower()
        if _is_runtime_status_tag(layer, key_text, value_text):
            normalized["layer"] = "status"
            allowed.append(normalized)
        else:
            blocked.append(
                {
                    "key": key,
                    "layer": layer or infer_tag_layer(key),
                    "reason": "开场后既有角色卡锁定；该 tag 属于角色卡静态字段或包含不合理成功主张。",
                }
            )
    return allowed, blocked


def _is_runtime_status_tag(layer: str, key_text: str, value_text: str) -> bool:
    combined = f"{key_text} {value_text}"
    if _contains_any_text(combined, CARD_OVERPOWERED_TERMS) or _contains_any_text(combined, CARD_FACT_INJECTION_TERMS):
        return False
    if _looks_like_post_start_power_grant(combined):
        return False
    if _looks_like_post_start_resource_escalation(combined):
        return False
    if "动作如潮" in combined and _contains_any_text(combined, ("刷新", "重置", "每次", "无限", "反复触发", "连续触发")):
        return False
    runtime_like = layer == "status" or _contains_any_text(combined, RUNTIME_STATUS_KEY_TERMS)
    if not runtime_like:
        return False
    if _contains_any_text(key_text, STATIC_CARD_KEY_TERMS):
        if not _contains_any_text(combined, ("已使用", "已消耗", "剩余", "冷却", "临时", "伤势", "状态")):
            return False
    return True


def _looks_like_post_start_power_grant(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any_text(lowered, ("失败", "未能", "没有成功", "尝试", "代价", "临时", "消耗", "伤势")) and not _contains_any_text(
        lowered,
        ("已获得", "获得了", "从此拥有", "现在可以", "已激活", "永久", "核心资源", "愿力"),
    ):
        return False
    nuclear_or_mutation_upgrade = (
        _contains_any_text(
            lowered,
            (
                "已获得",
                "获得了",
                "从此拥有",
                "现在可以",
                "已激活",
                "稳定掌握",
                "永久",
                "进化成",
                "突变成",
                "转化为",
            ),
        )
        and _contains_any_text(
            lowered,
            (
                "裂变",
                "核裂变",
                "链式反应",
                "反应堆",
                "可控临界",
                "可控裂变",
                "辐射合成",
                "辐射能量",
                "辐照微生物",
                "大量有用基因",
                "更强更有效的有益进化",
                "远超哺乳类",
                "神经传导速度",
                "智力",
                "感知进化",
                "分离氦气",
                "储存氦气",
                "飞起来",
                "释放脉冲",
                "脉冲能力",
                "形态跃迁",
                "细胞层级重组",
                "能量代谢转换",
                "感知边界",
            ),
        )
    )
    return (
        nuclear_or_mutation_upgrade
        or _contains_any_text(lowered, ("愿力", "核心资源", "传奇壮举", "传奇能力", "魔网权限", "虚空印记", "虚空德鲁伊", "提夫林"))
        or (
            _contains_any_text(lowered, ("全世界", "全球", "所有时空", "全位面"))
            and _contains_any_text(lowered, ("流星雨", "神迹", "投影", "感激", "愿力"))
        )
        or (
            _contains_any_text(lowered, ("职业等级", "传奇等级", "兼职", "传奇赐福"))
            and _contains_any_text(lowered, ("记录", "拥有", "获得", "权限", "等级"))
        )
    )


def _looks_like_post_start_resource_escalation(text: str) -> bool:
    lowered = str(text or "").lower()
    if not _contains_any_text(
        lowered,
        (
            "资源",
            "储备",
            "养分",
            "营养",
            "能量",
            "魔力",
            "法力",
            "弹药",
            "金币",
            "材料",
            "补给",
            "燃料",
            "生物质",
            "矿物质",
        ),
    ):
        return False
    if _contains_any_text(
        lowered,
        ("失败", "未能", "没有成功", "尝试", "需要", "不足", "短缺", "消耗", "代价", "损失"),
    ) and not _contains_any_text(
        lowered,
        ("过剩", "海量", "巨量", "数周", "数月", "长期", "源源不断", "持续供给", "可快速补充", "自给自足", "不再依赖"),
    ):
        return False
    high_scale = _contains_any_text(
        lowered,
        (
            "过剩",
            "充沛",
            "大量",
            "海量",
            "巨量",
            "爆满",
            "满额",
            "数周",
            "数月",
            "长期",
            "高耗能",
            "源源不断",
            "持续供给",
            "稳定供给",
            "可快速补充",
            "自给自足",
            "不再依赖",
            "完全定殖",
            "大幅提升",
        ),
    )
    acquisition = _contains_any_text(
        lowered,
        (
            "获得",
            "补充",
            "储备",
            "转化",
            "转化为",
            "采集",
            "吸收",
            "吞噬",
            "分解",
            "生产",
            "生成",
            "建立",
            "供给",
            "支撑",
            "接入",
            "定殖",
        ),
    )
    return high_scale and acquisition


def _looks_like_late_join_power_bundle(text: str) -> bool:
    lowered = str(text or "").lower()
    mythic_identity = (
        "帝皇",
        "神皇",
        "原体",
        "禁军",
        "星际战士",
        "阿斯塔特",
        "战锤",
        "emperor",
        "primarch",
        "创世神",
        "造物主",
        "世界意志",
    )
    force_terms = (
        "十三个原体",
        "十三名原体",
        "13个原体",
        "13名原体",
        "带着原体",
        "带着军队",
        "带着军团",
        "随从",
        "护卫",
    )
    takeover_terms = ("路过", "降临", "收走", "清除", "指挥", "砍卫兵", "税收", "征税官", "税务官")
    return (
        _contains_any_text(lowered, mythic_identity)
        and (_contains_any_text(lowered, force_terms) or _contains_any_text(lowered, takeover_terms))
    ) or _contains_any_text(lowered, force_terms)


def _looks_like_world_law_rewrite(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        _contains_any_text(lowered, ("世界意志", "世界观", "现实", "法则", "底层逻辑", "位面基石", "宇宙规则", "dnd2024"))
        and _contains_any_text(lowered, ("修正", "清除", "清理", "抹除", "排除", "踢出", "移除", "重塑", "改写", "纠正"))
        and _contains_any_text(lowered, ("不符合", "不合理", "异界", "跨作品", "所有", "一切", "事物", "存在"))
    )


def _looks_like_action_economy_abuse(text: str) -> bool:
    if "动作如潮" not in text:
        return False
    return _contains_any_text(text, ("每次", "无限", "刷新", "重置", "反复触发", "连续触发", "命中后继续触发"))


def _absurd_stat_reasons(text: str) -> List[str]:
    reasons: List[str] = []
    ability_pattern = re.compile(r"(力量|敏捷|体质|智力|感知|魅力|str|dex|con|int|wis|cha)\D{0,6}(\d{2,4})", re.IGNORECASE)
    for match in ability_pattern.finditer(text):
        if int(match.group(2)) > 20:
            reasons.append("一级/普通建卡阶段的核心属性不能超过 20，除非规则和 DM 明确允许。")
            break
    ac_pattern = re.compile(r"(护甲等级|护甲|ac)\D{0,6}(\d{2,4})", re.IGNORECASE)
    for match in ac_pattern.finditer(text):
        if int(match.group(2)) > 25:
            reasons.append("护甲/AC 数值明显超规格。")
            break
    hp_pattern = re.compile(r"(生命值|生命|血量|hp)\D{0,6}(\d{2,4})", re.IGNORECASE)
    for match in hp_pattern.finditer(text):
        if int(match.group(2)) > 80:
            reasons.append("初始生命/血量明显超规格。")
            break
    resource_pattern = re.compile(r"(伤害|金币|资源|法术位|体力)\D{0,6}(\d{3,5})", re.IGNORECASE)
    if resource_pattern.search(text):
        reasons.append("角色卡包含明显超规格的大额伤害、资源或财富数值。")
    high_bonus_patterns = (
        re.compile(r"\+[4-9]\d*\D{0,8}(武器|法杖|剑|斧|弓|护甲|装备)", re.IGNORECASE),
        re.compile(r"(武器|法杖|剑|斧|弓|护甲|装备)\D{0,8}\+[4-9]\d*", re.IGNORECASE),
    )
    if any(pattern.search(text) for pattern in high_bonus_patterns):
        reasons.append("初始装备不能直接携带 +4 或更高强化物品；强力魔法物品需要场内获得或 DM 明确发放。")
    return reasons


def _contains_any_text(text: str, terms: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(str(term).lower() in lowered for term in terms)


BACKGROUND_KEYS = {
    "background",
    "campaign_background",
    "setting",
    "world",
    "world_premise",
    "premise",
    "starting_premise",
    "genre",
    "tone",
    "era",
    "location",
    "factions",
    "conflict",
    "theme",
    "ruleset",
}


def has_campaign_background(session: GameSession) -> bool:
    if bool((session.battle or {}).get("active")):
        return True
    world_tags = dict(session.world_tags or {})
    if world_tags.get("_background_ready") is True:
        return True
    matched: list[str] = []
    text_chars = 0
    for key, value in world_tags.items():
        key_text = str(key)
        key_lower = key_text.lower()
        if key_lower.startswith("_"):
            continue
        if key_lower in BACKGROUND_KEYS or key_text in {"背景", "世界观", "时代", "地点", "势力", "主题", "开场前提"}:
            value_text = str(value).strip()
            if value_text and value_text not in {"{}", "[]", "None"}:
                matched.append(key_lower)
                text_chars += len(value_text)
    if len(set(matched)) >= 2 and text_chars >= 12:
        return True
    if text_chars >= 40 and matched:
        return True
    contract = world_tags.get("campaign_contract")
    if isinstance(contract, dict):
        required = ("genre", "premise", "tone")
        if sum(1 for key in required if str(contract.get(key, "")).strip()) >= 2:
            return True
    return False


def _coerce_campaign_outline_input(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        acts = [str(item).strip() for item in value if str(item).strip()]
        return {"acts": acts} if acts else {}
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, list):
        acts = [str(item).strip() for item in decoded if str(item).strip()]
        return {"acts": acts} if acts else {}
    parts = [
        part.strip(" \n\t-0123456789.、:：")
        for part in re.split(r"[\n；;]+", text)
        if part.strip(" \n\t-0123456789.、:：")
    ]
    if len(parts) >= 3:
        return {"acts": parts[:6], "outline": _short_tag_value(text, 500)}
    return {"outline": _short_tag_value(text, 500)}


def _coerce_scene_patch_input(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        return decoded
    return {"summary": _short_tag_value(text, 500)}


def background_required_result(session: GameSession, tool_name: str) -> Dict[str, Any] | None:
    if has_campaign_background(session):
        return None
    return {
        "ok": False,
        "error": "background_required",
        "tool": tool_name,
        "message": (
            "当前团还没有明确背景设定，不能先写入剧本、战场或角色卡。"
            "请先用 update_world_tags 写入至少两项背景要素，例如 genre/tone/starting_premise/location/factions/ruleset；"
            "如果玩家授权，也可以由 DM 主动生成或补全这些背景要素。"
        ),
        "required_before": ["script", "character_sheet", "battle_grid"],
        "suggested_patch": {
            "genre": "例如：废土科幻 / 黑暗奇幻 / 现代悬疑",
            "tone": "例如：严肃求生 / 轻松荒诞 / 调查恐怖",
            "starting_premise": "一句话说明玩家为什么聚在这里、第一幕要面对什么",
        },
    }


PLOT_LOCKED_KEYS = {
    "background",
    "campaign_background",
    "campaign_contract",
    "campaign_outline",
    "central_conflict",
    "conflict",
    "current_conflict",
    "era",
    "factions",
    "genre",
    "location",
    "main_plot",
    "mystery",
    "npcs",
    "opening",
    "plot",
    "premise",
    "ruleset",
    "scene",
    "setting",
    "starting_premise",
    "summary",
    "theme",
    "title",
    "tone",
    "world",
    "world_premise",
    "背景",
    "世界观",
    "剧情",
    "剧本",
    "主线",
    "题材",
    "设定",
}


def campaign_start_missing_requirements(
    session: GameSession,
    opening_intro: str,
    campaign_outline: Dict[str, Any],
    scene_patch: Dict[str, Any],
) -> List[str]:
    missing: List[str] = []
    if not has_campaign_background(session):
        missing.append("background: 先写入背景设定，至少包含题材/基调/开场前提/地点/势力/规则中的两类。")
    intro_text = " ".join(str(opening_intro or "").split())
    if len(intro_text) < 40:
        missing.append("opening_intro: 需要一段简短开场介绍，包含氛围、当前处境和第一个压力点。")
    if not _outline_has_dramatic_structure(campaign_outline):
        missing.append("campaign_outline: 需要预备跌宕剧情骨架，至少有导火索、升级/反转、高潮或重大抉择三段。")
    scene_text = _flatten_text([scene_patch, session.scene])
    if not any(token in scene_text for token in ("冲突", "危机", "目标", "任务", "压力", "敌", "抉择", "hook", "conflict")):
        missing.append("initial_hook: 开场场景需要明确眼前目标、危机或第一个可行动钩子。")
    return missing


def compact_campaign_outline(outline: Dict[str, Any]) -> Dict[str, Any]:
    compacted = _compact_structured(outline, depth=3)
    if isinstance(compacted, dict):
        compacted.setdefault("_locked_after_opening", True)
        compacted.setdefault("_dynamic_adjustment_policy", "只允许 DM 根据玩家行动结果微调推进；不接受玩家开场后直接改背景、题材或主线。")
        return compacted
    return {
        "outline": _short_tag_value(str(compacted), 500),
        "_locked_after_opening": True,
        "_dynamic_adjustment_policy": "只允许 DM 根据玩家行动结果微调推进；不接受玩家开场后直接改背景、题材或主线。",
    }


def plot_locked_result(session: GameSession, player_message: str, tool_name: str) -> Dict[str, Any] | None:
    if not _campaign_plot_locked(session):
        return None
    if not looks_like_player_plot_rewrite_request(player_message):
        return None
    return {
        "ok": False,
        "error": "plot_locked_after_start",
        "tool": tool_name,
        "message": "游戏已经开场，背景、题材、主线和核心剧本已锁定；玩家不能在开场后直接改剧情。可以声明角色行动，或作为新玩家加入。",
        "allowed_after_start": ["角色行动", "调查与选择", "战斗行动", "新玩家创建新角色", "伤势/资源/临时状态记录"],
    }


def post_start_world_fact_overreach_result(
    session: GameSession,
    player_message: str,
    patch: Dict[str, Any],
    tool_name: str,
) -> Dict[str, Any] | None:
    if not _campaign_plot_locked(session):
        return None
    combined = _flatten_text([player_message, patch]).lower()
    global_rewrite = _contains_any_text(combined, ("全世界", "全球", "所有时空", "全位面")) and _contains_any_text(
        combined,
        ("流星雨", "神迹", "投影", "感激", "愿力", "陨石"),
    )
    power_grant = _contains_any_text(
        combined,
        (
            "众生愿力",
            "核心资源",
            "传奇壮举",
            "传奇能力",
            "魔网权限",
            "所有传奇赐福",
            "职业等级",
            "传奇等级",
            "魔网化身",
            "半神",
            "神格",
            "裂变反应堆",
            "可控临界",
            "可控裂变",
            "辐射合成代谢",
            "辐射能量",
            "大量有用基因",
            "更强更有效的有益进化",
            "形态跃迁",
            "细胞层级重组",
            "能量代谢转换",
            "感知边界的消融",
        ),
    )
    resource_overgrant = _looks_like_post_start_resource_escalation(combined)
    faction_takeover = _contains_any_text(
        combined,
        ("补充设定", "现在演绎", "现在刚刚到场", "刚刚到场", "派出"),
    ) and _contains_any_text(
        combined,
        (
            "传奇战士",
            "传奇牧师",
            "传奇法师",
            "神眷者",
            "大量补给",
            "税务官",
            "收人头税",
            "呼吸税",
            "睡眠税",
        ),
    )
    mass_control = _contains_any_text(
        combined,
        ("无数古树", "所有出路", "每一个人", "所有人", "整个小镇"),
    ) and _contains_any_text(combined, ("堵死", "缠绕", "控制", "必须", "归还", "占据"))
    guaranteed_summon = _contains_any_text(
        combined,
        ("不存在失败", "不存在失败的可能", "一定可以", "必定可以", "自动成功", "不会失败"),
    ) and _contains_any_text(combined, ("召唤", "虫群", "泰伦", "撕裂空间", "传送门", "法术", "检定", "判定"))
    monster_arrival = _contains_any_text(combined, ("一打刀虫", "虫巢暴君", "泰伦虫族")) and _contains_any_text(
        combined,
        ("撕破虚空", "来到我的身边", "召唤", "现在刚刚到场"),
    )
    late_join_power_bundle = _looks_like_late_join_power_bundle(combined)
    world_law_rewrite = _looks_like_world_law_rewrite(combined)
    if not (
        global_rewrite
        or power_grant
        or resource_overgrant
        or faction_takeover
        or mass_control
        or guaranteed_summon
        or monster_arrival
        or late_join_power_bundle
        or world_law_rewrite
    ):
        return None
    return {
        "ok": False,
        "error": "post_start_world_fact_overreach",
        "tool": tool_name,
        "phase": "post_opening",
        "message": "游戏开场后不能把玩家单方面主张写成全世界事实、永久能力、等级提升或新资源。可以作为愿望、传闻、未来伏笔，或改成一次有限场内行动再裁定。",
        "blocked_after_start": [
            "全世界/全球级事实改写",
            "新增愿力/神格/传奇权限",
            "口头追加职业或等级",
            "场外补写传奇援军、军团资源或大规模场景封锁",
            "跨作品神级身份或自带传奇随从/军队",
            "世界意志、规则修正或清除不合世界观事物",
            "把召唤、控制或到场说成必定成功",
            "把自我升格直接落盘",
            "一次行动直接获得过剩、长期或源源不断的资源储备",
        ],
    }


def patch_touches_plot_state(patch: Dict[str, Any]) -> bool:
    for key in patch.keys():
        key_text = str(key)
        key_lower = key_text.lower()
        if key_lower in PLOT_LOCKED_KEYS or key_text in PLOT_LOCKED_KEYS:
            return True
    return False


def looks_like_player_plot_rewrite_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(token in normalized for token in ("加入", "建卡", "角色", "我的名字", "我是")) and not any(
        token in normalized for token in ("剧情", "剧本", "背景", "主线", "世界观")
    ):
        return False
    plot_terms = ("剧情", "剧本", "背景", "世界观", "题材", "类型", "风格", "主线", "设定", "幕后黑手", "真相", "结局")
    rewrite_terms = ("改成", "换成", "变成", "调整", "修改", "重写", "换一个", "改一下", "不能", "可不可以", "能不能")
    direct_rewrite = any(term in normalized for term in plot_terms) and any(term in normalized for term in rewrite_terms)
    fact_injection = any(term in normalized for term in ("其实", "真相是", "原来", "幕后黑手是", "结局是")) and any(
        term in normalized for term in plot_terms
    )
    return direct_rewrite or fact_injection


def _campaign_plot_locked(session: GameSession) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    return bool(
        (scene.get("_game_started") and scene.get("_plot_locked", True))
        or world_tags.get("_plot_locked") is True
    )


def _bound_player_characters(session: GameSession) -> List[Character]:
    characters: List[Character] = []
    for player_id, character_id in (session.player_character_map or {}).items():
        if not str(player_id).strip():
            continue
        character = session.characters.get(str(character_id))
        if character and character.player_id:
            characters.append(character)
    return characters


def _outline_has_dramatic_structure(outline: Dict[str, Any]) -> bool:
    if not isinstance(outline, dict) or not outline:
        return False
    acts = outline.get("acts") or outline.get("beats") or outline.get("chapters") or outline.get("stages")
    if isinstance(acts, list) and len([item for item in acts if str(item).strip()]) >= 3:
        return True
    meaningful_keys = {
        "inciting_incident",
        "inciting",
        "incident",
        "trigger",
        "setup",
        "opening_pressure",
        "hook",
        "act1",
        "act_1",
        "act_1_inciting",
        "act2",
        "act_2",
        "act_2_escalation",
        "act3",
        "act_3",
        "act_3_climax",
        "escalation",
        "rising_action",
        "rising",
        "complication",
        "twist",
        "reversal",
        "turning_point",
        "climax",
        "high_point",
        "highpoint",
        "finale",
        "payoff",
        "culmination",
        "final_choice",
        "major_choice",
        "decision",
        "dilemma",
        "choice",
        "choice_point",
        "resolution",
        "stakes",
        "导火索",
        "升级",
        "反转",
        "高潮",
        "抉择",
    }
    count = 0
    for key, value in outline.items():
        if (str(key) in meaningful_keys or str(key).lower() in meaningful_keys) and str(value).strip():
            count += 1
    if count >= 3:
        return True
    text = _flatten_text(outline)
    return len(text) >= 120 and sum(1 for token in ("导火索", "升级", "反转", "高潮", "抉择", "危机") if token in text) >= 2


def _flatten_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(value)


def _coerce_prompt_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_coerce_prompt_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        preferred: List[str] = []
        for key in ("text", "content", "intro", "opening_intro", "guidance", "summary", "description"):
            if key in value:
                text = _coerce_prompt_text(value.get(key))
                if text:
                    preferred.append(text)
        if preferred:
            return "\n".join(preferred)
        return _flatten_text(value)
    return str(value).strip()


def _compact_structured(value: Any, depth: int = 3) -> Any:
    if depth <= 0:
        return _short_tag_value(value, 260)
    if isinstance(value, dict):
        compacted: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 16:
                compacted["_truncated"] = True
                break
            compacted[str(key)[:80]] = _compact_structured(item, depth - 1)
        return compacted
    if isinstance(value, list):
        return [_compact_structured(item, depth - 1) for item in value[:10]]
    if isinstance(value, str):
        return _short_tag_value(value, 360)
    return value


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
