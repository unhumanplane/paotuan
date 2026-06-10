from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.map_core import default_map_store, load_active_strict_grid, load_active_strict_grid_entities
from ..core.memory import MemoryCompressor
from ..core.models import (
    Character,
    GameMode,
    GameSession,
    TagValue,
    compact_tag_layers,
    infer_tag_layer,
    normalize_relationship_collections,
    normalize_relation_state,
    project_public_relation_state,
    utc_now_iso,
)
from ..core.timeline import (
    apply_timeline_patch,
    extract_timeline_patch,
    patch_has_per_player_timeline,
    patch_mentions_implicit_timeline_advance,
    timeline_view,
    validate_global_timeline_advance,
)
from ..core.scene_hooks import (
    format_scene_tracking_status,
    normalize_scene_tracking_patch,
    opening_has_initial_hook,
    project_visible_scene_value,
)
from ..core.session_titles import ensure_session_title
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
    patch: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "场景状态补丁，例如 summary,current_conflict,location,current_location,current_vehicle_status,"
            "current_access_state,npcs,current_objective,open_hooks,clues,mysteries,stakes,"
            "pressure_clock。涉及移动载具/站台/门锁/队伍分离时，必须用结构化字段明确"
            "停稳、即将启动、正在行驶、已驶离、门已锁/可通行等当前事实。clues/open_hooks/mysteries "
            "建议使用可见小对象：{id,text,status,visibility}; status 可为 open, discovered, "
            "suspected, resolved, false_lead, blocked。不要写入未被角色确认的幕后真相。"
        ),
    )


class RecordTimelineEventArgs(BaseModel):
    event_id: str = Field(default="", description="Stable event id. Leave empty to derive one from event_type/entities.")
    event_type: str = Field(default="event", description="Event category, for example npc_status_confirmed or item_used.")
    summary: str = Field(..., description="Short authoritative event summary; do not add hidden truth.")
    entities: List[str] = Field(default_factory=list, description="Related character/NPC/item/location ids.")
    status: str = Field(default="confirmed", description="confirmed, suspected, retracted, or superseded.")
    visibility: str = Field(default="observed_or_confirmed", description="observed, confirmed, suspected, or observed_or_confirmed.")
    source: Dict[str, Any] = Field(default_factory=dict, description="Optional structured source such as tool result, DC, total, success.")
    evidence: List[str] = Field(default_factory=list, description="Existing tool/audit/state evidence; no invented evidence.")
    unknowns: List[str] = Field(default_factory=list, description="Known unknowns produced by this event.")
    order: Optional[int] = Field(default=None, description="Optional relative order. If empty, appended after existing events.")
    supersedes: List[str] = Field(default_factory=list, description="Older event ids this event corrects or supersedes.")
    retracted_by: str = Field(default="", description="Event id or repair id that retracts this event, when status=retracted.")


class ClarifyEntityTimelineArgs(BaseModel):
    entity_id: str = Field(..., description="Entity id, for example npc_shidong.")
    entity_type: str = Field(default="npc", description="npc, character, item, location, faction, or other.")
    name: str = Field(default="", description="Human-readable entity name.")
    current_status: str = Field(default="", description="Current authoritative status. Keep unknowns explicit.")
    historical_facts: List[str] = Field(default_factory=list, description="Time-qualified past facts, e.g. '曾在...'.")
    unknowns: List[str] = Field(default_factory=list, description="Facts that remain unknown and must not be guessed.")
    authoritative_events: List[str] = Field(default_factory=list, description="Timeline event ids supporting this clarification.")
    evidence: List[str] = Field(default_factory=list, description="Existing tool/audit/state evidence; no invented evidence.")
    scene_thread_id: str = Field(default="", description="Optional scene thread to update, e.g. character:pc_yaka.")
    replace_conflicting_current_fact: bool = Field(default=False, description="Replace stale NPC known_facts/status in the thread.")
    open_hook_id: str = Field(default="", description="Optional hook id to upsert for unresolved unknowns.")
    open_hook_text: str = Field(default="", description="Optional hook text. Must preserve confirmed facts and only ask about unknowns.")


class EventCardArgs(BaseModel):
    event_id: str = Field(default="", description="Stable event id. Leave empty to derive one from event_type/entities.")
    event_type: str = Field(default="event", description="Event category, for example scene_shift or npc_status_confirmed.")
    summary: str = Field(..., description="Short authoritative event summary; do not add hidden truth.")
    entities: List[str] = Field(default_factory=list, description="Related character/NPC/item/location ids.")
    status: str = Field(default="confirmed", description="confirmed, suspected, retracted, or superseded.")
    visibility: str = Field(default="observed_or_confirmed", description="observed, confirmed, suspected, or observed_or_confirmed.")
    source: Dict[str, Any] = Field(default_factory=dict, description="Optional structured source such as tool result, DC, total, success.")
    evidence: List[str] = Field(default_factory=list, description="Existing tool/audit/state evidence; no invented evidence.")
    unknowns: List[str] = Field(default_factory=list, description="Known unknowns produced by this event.")
    order: Optional[int] = Field(default=None, description="Optional relative order. If empty, appended after existing events.")
    supersedes: List[str] = Field(default_factory=list, description="Older event ids this event corrects or supersedes.")
    retracted_by: str = Field(default="", description="Event id or repair id that retracts this event, when status=retracted.")
    scene_patch: Dict[str, Any] = Field(default_factory=dict, description="同一事件后的场景补丁；会和事件一起记录")
    character_patches: List[Dict[str, Any]] = Field(default_factory=list, description="同一事件后需要批量更新的角色卡补丁")
    entity_clarifications: List[Dict[str, Any]] = Field(default_factory=list, description="同一事件后需要批量更新的实体时间线说明")


SCENE_THREAD_CONTROL_KEYS = {"scene_thread_id", "thread_id", "_scene_thread_id"}
SCENE_THREAD_METADATA_KEYS = {
    "scene_thread_id",
    "thread_id",
    "_scene_thread_id",
    "active_scene_thread_id",
    "scene_threads",
}
SCENE_THREAD_MIRROR_KEYS = {
    "summary",
    "location",
    "_location",
    "scene_time_label",
    "scene_time_of_day",
    "current_conflict",
    "current_objective",
    "open_hooks",
    "clues",
    "mysteries",
    "stakes",
    "pressure_clock",
    "npcs",
    "factions",
    "relations",
}
SCENE_THREAD_CLOSED_STATUSES = {"archived", "closed", "resolved", "retired"}
class UpdateWorldTagsArgs(BaseModel):
    patch: Dict[str, Any] = Field(default_factory=dict, description="世界设定 Tag 补丁，例如 genre,tone,factions,mysteries")


class StartGameArgs(BaseModel):
    title: str = Field(
        default="",
        description="可选团名/剧本名；若为空，系统会根据开场、当前目标或剧本标题自动生成短团名。",
    )
    opening_intro: str = Field(
        ...,
        description="给玩家看的简短开场介绍，必须有氛围、当前处境和第一个压力点，建议 120-400 中文字。",
    )
    player_guidance: str = Field(
        default="",
        description="给玩家的简短行动引导，说明当前可感知目标、线索或风险；不要写封闭式编号行动菜单。",
    )
    initial_hook: str = Field(
        default="",
        description="开场第一个可行动钩子，只写角色已经能感知到的目标、压力、线索或异常，不泄露幕后真相。",
    )
    campaign_outline: Dict[str, Any] = Field(
        default_factory=dict,
        description="开场前预备的跌宕剧情骨架，至少包含三段：导火索、升级/反转、高潮或重大抉择。",
    )
    scene_patch: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "开场场景状态补丁，例如 summary,current_conflict,location,npcs,current_objective,"
            "open_hooks,clues,stakes,pressure_clock。开场后至少应落盘 current_objective、"
            "两个 open_hooks、stakes 或 pressure_clock。"
        ),
    )


class SessionControlArgs(BaseModel):
    action: str = Field(
        ...,
        description=(
            "会话控制动作：status, reset, restore_latest_backup, preview_latest_backup, "
            "restart_latest_backup_story, list_backups, create_backup, compress_memory, debug_last"
        ),
    )
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
                    message="游戏已经开场，既有角色卡锁定；不能补写职业、能力、装备、默认战斗行为或背景。只能记录伤势、生命/资源消耗、临时状态、最近行动结果，以及已有场内依据的关系后果。",
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
        if patch_has_per_player_timeline(patch):
            result = {
                "ok": False,
                "error": "per_player_timeline_forbidden",
                "message": "时间线是全团共享权威状态，不能按玩家或角色分别写入不同日期/时段。",
            }
            self._audit("update_scene", {"patch": patch}, result)
            return result
        timeline_patch, scene_patch = extract_timeline_patch(patch)
        if timeline_patch:
            validation = validate_global_timeline_advance(session, timeline_patch)
            if not validation.get("ok"):
                self._audit("update_scene", {"patch": patch}, validation)
                return validation
            session.timeline = apply_timeline_patch(
                session.timeline,
                timeline_patch,
                reason=self.message or "update_scene",
                cycle_id=session.current_cycle_id,
            )
        patch = scene_patch
        if not patch:
            self.repository.save_session(session)
            result = {"ok": True, "scene": session.scene, "timeline": timeline_view(session.timeline)}
            self._audit("update_scene", {"patch": timeline_patch}, result)
            return result
        if not timeline_patch and patch_mentions_implicit_timeline_advance(patch):
            result = {
                "ok": False,
                "error": "timeline_patch_required",
                "message": (
                    "场景补丁里包含跨日、天亮、入夜、长休或等待到下一时段的推进；"
                    "必须通过全团同步 timeline_patch/cycle_control 处理，不能只写入某条 scene thread。"
                ),
                "timeline": timeline_view(session.timeline),
            }
            self._audit("update_scene", {"patch": patch}, result)
            return result
        normalized_patch = normalize_relationship_collections(
            normalize_scene_tracking_patch(patch)
        )
        thread_id = _resolve_scene_thread_id(session, self.actor, self.message, normalized_patch)
        normalized_patch = {
            key: value
            for key, value in normalized_patch.items()
            if key not in SCENE_THREAD_METADATA_KEYS
        }
        scene_threads = _scene_threads(session.scene)
        _coalesce_character_thread_alias(session.scene, scene_threads, thread_id)
        scene_thread = _merge_scene_thread(
            dict(scene_threads.get(thread_id) or {}),
            normalized_patch,
            actor=self.actor,
            character_id=_actor_character_id(session, self.actor),
        )
        scene_threads[thread_id] = scene_thread
        _write_scene_mirror(session.scene, thread_id, scene_thread, normalized_patch)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "scene": session.scene,
            "timeline": timeline_view(session.timeline),
            "scene_thread_id": thread_id,
            "scene_thread": scene_thread,
            "scene_threads_isolated": True,
        }
        self._audit("update_scene", {"patch": normalized_patch, "scene_thread_id": thread_id}, result)
        return result

    async def record_timeline_event(
        self,
        event_id: str = "",
        event_type: str = "event",
        summary: str = "",
        entities: Optional[List[str]] = None,
        status: str = "confirmed",
        visibility: str = "observed_or_confirmed",
        source: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
        unknowns: Optional[List[str]] = None,
        order: Optional[int] = None,
        supersedes: Optional[List[str]] = None,
        retracted_by: str = "",
    ) -> Dict[str, Any]:
        summary_text = _short_tag_value(summary, 500)
        if not summary_text:
            result = {
                "ok": False,
                "error": "empty_summary",
                "message": "record_timeline_event needs a concise authoritative summary.",
            }
            self._audit("record_timeline_event", {"event_id": event_id, "summary": summary}, result)
            return result
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "record_timeline_event")
        if gate:
            self._audit("record_timeline_event", {"event_id": event_id, "summary": summary_text}, gate)
            return gate
        event = _build_timeline_event(
            session.scene,
            event_id=event_id,
            event_type=event_type,
            summary=summary_text,
            entities=entities or [],
            status=status,
            visibility=visibility,
            source=source or {},
            evidence=evidence or [],
            unknowns=unknowns or [],
            order=order,
            supersedes=supersedes or [],
            retracted_by=retracted_by,
        )
        timeline = _event_timeline(session.scene)
        existing = _timeline_event_by_id(timeline, str(event["id"]))
        action = "updated" if existing is not None else "created"
        if existing is not None:
            existing.update(event)
            event = existing
        else:
            timeline.append(event)
        _sort_event_timeline(timeline)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "action": action,
            "event": event,
            "event_timeline": _project_event_timeline(session.scene, entities=event.get("entities") or []),
        }
        self._audit(
            "record_timeline_event",
            {
                "event_id": event_id,
                "event_type": event_type,
                "summary": summary_text,
                "entities": entities or [],
                "status": status,
                "visibility": visibility,
                "source": source or {},
                "evidence": evidence or [],
                "unknowns": unknowns or [],
                "order": order,
                "supersedes": supersedes or [],
                "retracted_by": retracted_by,
            },
            result,
        )
        return result

    async def clarify_entity_timeline(
        self,
        entity_id: str,
        entity_type: str = "npc",
        name: str = "",
        current_status: str = "",
        historical_facts: Optional[List[str]] = None,
        unknowns: Optional[List[str]] = None,
        authoritative_events: Optional[List[str]] = None,
        evidence: Optional[List[str]] = None,
        scene_thread_id: str = "",
        replace_conflicting_current_fact: bool = False,
        open_hook_id: str = "",
        open_hook_text: str = "",
    ) -> Dict[str, Any]:
        safe_entity_id = _safe_entity_id(entity_id)
        if not safe_entity_id:
            result = {"ok": False, "error": "invalid_entity_id"}
            self._audit("clarify_entity_timeline", {"entity_id": entity_id}, result)
            return result
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "clarify_entity_timeline")
        if gate:
            self._audit("clarify_entity_timeline", {"entity_id": safe_entity_id}, gate)
            return gate
        fact = _build_entity_fact(
            entity_id=safe_entity_id,
            entity_type=entity_type,
            name=name,
            current_status=current_status,
            historical_facts=historical_facts or [],
            unknowns=unknowns or [],
            authoritative_events=authoritative_events or [],
            evidence=evidence or [],
        )
        entity_facts = _entity_facts(session.scene)
        previous = dict(entity_facts.get(safe_entity_id) or {})
        entity_facts[safe_entity_id] = fact
        applied = [{"type": "entity_fact", "entity_id": safe_entity_id, "previous": previous, "current": fact}]

        resolved_thread_id = ""
        if scene_thread_id or replace_conflicting_current_fact or open_hook_text:
            resolved_thread_id = _resolve_scene_thread_id(
                session,
                self.actor,
                self.message,
                {"scene_thread_id": scene_thread_id} if scene_thread_id else {},
            )
            threads = _scene_threads(session.scene)
            _coalesce_character_thread_alias(session.scene, threads, resolved_thread_id)
            thread = dict(threads.get(resolved_thread_id) or {})
            if replace_conflicting_current_fact:
                _clarify_entity_in_scene_thread(thread, fact)
                applied.append({"type": "scene_thread_entity_fact", "thread_id": resolved_thread_id, "entity_id": safe_entity_id})
            if open_hook_text:
                hook_id = _safe_hook_id(open_hook_id, safe_entity_id)
                _upsert_open_hook(thread, hook_id=hook_id, text=_short_tag_value(open_hook_text, 360))
                applied.append({"type": "scene_thread_open_hook", "thread_id": resolved_thread_id, "hook_id": hook_id})
            threads[resolved_thread_id] = thread
            _write_scene_mirror(session.scene, resolved_thread_id, thread, thread)
        self.repository.save_session(session)
        result = {
            "ok": True,
            "entity_id": safe_entity_id,
            "entity_fact": fact,
            "applied": applied,
        }
        if resolved_thread_id:
            result["scene_thread_id"] = resolved_thread_id
            result["scene_thread"] = session.scene.get("scene_threads", {}).get(resolved_thread_id, {})
        self._audit(
            "clarify_entity_timeline",
            {
                "entity_id": entity_id,
                "resolved_entity_id": safe_entity_id,
                "entity_type": entity_type,
                "name": name,
                "current_status": current_status,
                "historical_facts": historical_facts or [],
                "unknowns": unknowns or [],
                "authoritative_events": authoritative_events or [],
                "evidence": evidence or [],
                "scene_thread_id": scene_thread_id,
                "replace_conflicting_current_fact": replace_conflicting_current_fact,
                "open_hook_id": open_hook_id,
                "open_hook_text": open_hook_text,
            },
            result,
        )
        return result

    async def record_event_card(
        self,
        event_id: str = "",
        event_type: str = "event",
        summary: str = "",
        entities: Optional[List[str]] = None,
        status: str = "confirmed",
        visibility: str = "observed_or_confirmed",
        source: Optional[Dict[str, Any]] = None,
        evidence: Optional[List[str]] = None,
        unknowns: Optional[List[str]] = None,
        order: Optional[int] = None,
        supersedes: Optional[List[str]] = None,
        retracted_by: str = "",
        scene_patch: Optional[Dict[str, Any]] = None,
        character_patches: Optional[List[Dict[str, Any]]] = None,
        entity_clarifications: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        summary_text = _short_tag_value(summary, 500)
        if not summary_text:
            result = {
                "ok": False,
                "error": "empty_summary",
                "message": "record_event_card needs a concise authoritative summary.",
            }
            self._audit("record_event_card", {"event_id": event_id, "summary": summary}, result)
            return result
        session = self.repository.load_session(self.session_id)
        gate = background_required_result(session, "record_event_card")
        if gate:
            self._audit("record_event_card", {"event_id": event_id, "summary": summary_text}, gate)
            return gate
        event = _build_timeline_event(
            session.scene,
            event_id=event_id,
            event_type=event_type,
            summary=summary_text,
            entities=entities or [],
            status=status,
            visibility=visibility,
            source=source or {},
            evidence=evidence or [],
            unknowns=unknowns or [],
            order=order,
            supersedes=supersedes or [],
            retracted_by=retracted_by,
        )
        timeline = _event_timeline(session.scene)
        existing = _timeline_event_by_id(timeline, str(event["id"]))
        action = "updated" if existing is not None else "created"
        if existing is not None:
            existing.update(event)
            event = existing
        else:
            timeline.append(event)
        _sort_event_timeline(timeline)

        applied: list[dict[str, Any]] = [{"type": "timeline_event", "event_id": event["id"], "action": action}]
        character_results: list[dict[str, Any]] = []
        entity_results: list[dict[str, Any]] = []

        if scene_patch:
            if patch_has_per_player_timeline(scene_patch):
                result = {
                    "ok": False,
                    "error": "per_player_timeline_forbidden",
                    "message": "时间线是全团共享权威状态，不能按玩家或角色分别写入不同日期/时段。",
                }
                self._audit("record_event_card", {"event_id": event_id, "scene_patch": scene_patch}, result)
                return result
            timeline_patch, remaining_scene_patch = extract_timeline_patch(scene_patch)
            if timeline_patch:
                validation = validate_global_timeline_advance(session, timeline_patch)
                if not validation.get("ok"):
                    self._audit("record_event_card", {"event_id": event_id, "scene_patch": scene_patch}, validation)
                    return validation
                session.timeline = apply_timeline_patch(
                    session.timeline,
                    timeline_patch,
                    reason=self.message or "record_event_card",
                    cycle_id=session.current_cycle_id,
                )
                applied.append({"type": "timeline_patch", "patch": timeline_patch})
            scene_patch = remaining_scene_patch
            if scene_patch and not timeline_patch and patch_mentions_implicit_timeline_advance(scene_patch):
                result = {
                    "ok": False,
                    "error": "timeline_patch_required",
                    "message": (
                        "场景补丁里包含跨日、天亮、入夜、长休或等待到下一时段的推进；"
                        "必须通过全团同步 timeline_patch/cycle_control 处理，不能只写入某条 scene thread。"
                    ),
                    "timeline": timeline_view(session.timeline),
                }
                self._audit("record_event_card", {"event_id": event_id, "scene_patch": scene_patch}, result)
                return result
            if scene_patch:
                normalized_patch = normalize_relationship_collections(normalize_scene_tracking_patch(scene_patch))
                thread_id = _resolve_scene_thread_id(session, self.actor, self.message, normalized_patch)
                normalized_patch = {
                    key: value
                    for key, value in normalized_patch.items()
                    if key not in SCENE_THREAD_METADATA_KEYS
                }
                scene_threads = _scene_threads(session.scene)
                _coalesce_character_thread_alias(session.scene, scene_threads, thread_id)
                scene_thread = _merge_scene_thread(
                    dict(scene_threads.get(thread_id) or {}),
                    normalized_patch,
                    actor=self.actor,
                    character_id=_actor_character_id(session, self.actor),
                )
                scene_threads[thread_id] = scene_thread
                _write_scene_mirror(session.scene, thread_id, scene_thread, normalized_patch)
                applied.append({"type": "scene_patch", "scene_thread_id": thread_id})

        for patch in character_patches or []:
            if not isinstance(patch, dict):
                result = {"ok": False, "error": "invalid_character_patch", "patch": patch}
                self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, result)
                return result
            character_id = str(patch.get("character_id") or "").strip()
            if not character_id:
                result = {"ok": False, "error": "missing_character_id", "patch": patch}
                self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, result)
                return result
            tags = patch.get("tags")
            raw_text = str(patch.get("raw_text") or "")
            allow_stub_creation = bool(patch.get("allow_stub_creation", False))
            owner_id = str(self.actor.get("player_id", "") or "").strip()
            safe_id = self._resolve_existing_character_id(session, character_id, owner_id)
            character = session.characters.get(safe_id)
            if not character and allow_stub_creation and (tags or raw_text):
                character = self._maybe_create_battle_character_stub(session, safe_id, owner_id)
            if not character:
                result = {"ok": False, "error": "character_not_found", "character_id": safe_id}
                self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, result)
                return result
            owner_guard = self._character_owner_guard(session, safe_id, owner_id)
            if owner_guard:
                self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, owner_guard)
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
                    "message": "record_event_card 里的角色补丁需要至少一个 tag；未改动角色。",
                    "character_id": safe_id,
                }
                self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, result)
                return result
            blocked_tags: List[Dict[str, Any]] = []
            if _campaign_game_started(session):
                normalized_tags, blocked_tags = filter_runtime_character_tags_after_start(normalized_tags)
                if not normalized_tags:
                    result = character_card_locked_after_start_result(
                        "record_event_card",
                        safe_id,
                        owner_id,
                        message="游戏已经开场，既有角色卡锁定；不能补写职业、能力、装备、默认战斗行为或背景。只能记录伤势、生命/资源消耗、临时状态、最近行动结果，以及已有场内依据的关系后果。",
                    )
                    result["blocked_tags"] = blocked_tags
                    self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, result)
                    return result
            else:
                validation = validate_character_card_payload(name=character.name, summary=character.summary, tags=normalized_tags, require_name=False)
                if validation:
                    self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, validation)
                    return validation
                balance = validate_character_card_party_balance(
                    session,
                    safe_id,
                    name=character.name,
                    summary=character.summary,
                    tags=_merged_character_tags(character, normalized_tags),
                )
                if balance:
                    self._audit("record_event_card", {"event_id": event_id, "character_patch": patch}, balance)
                    return balance
            character.upsert_tags(normalized_tags)
            patch_result = {
                "character_id": safe_id,
                "character": character_as_dict(character),
                "inferred_from_raw_text": inferred_from_raw_text,
                "updated_tags": normalized_tags,
            }
            if blocked_tags:
                patch_result["character_card_locked_after_start"] = True
                patch_result["blocked_tags"] = blocked_tags
            character_results.append(patch_result)
            applied.append({"type": "character_patch", "character_id": safe_id, "tag_count": len(normalized_tags)})

        for item in entity_clarifications or []:
            if not isinstance(item, dict):
                result = {"ok": False, "error": "invalid_entity_clarification", "patch": item}
                self._audit("record_event_card", {"event_id": event_id, "entity_clarification": item}, result)
                return result
            safe_entity_id = _safe_entity_id(item.get("entity_id") or "")
            if not safe_entity_id:
                result = {"ok": False, "error": "invalid_entity_id"}
                self._audit("record_event_card", {"event_id": event_id, "entity_clarification": item}, result)
                return result
            fact = _build_entity_fact(
                entity_id=safe_entity_id,
                entity_type=str(item.get("entity_type") or "npc"),
                name=str(item.get("name") or ""),
                current_status=str(item.get("current_status") or ""),
                historical_facts=list(item.get("historical_facts") or []),
                unknowns=list(item.get("unknowns") or []),
                authoritative_events=list(item.get("authoritative_events") or []),
                evidence=list(item.get("evidence") or []),
            )
            entity_facts = _entity_facts(session.scene)
            previous = dict(entity_facts.get(safe_entity_id) or {})
            entity_facts[safe_entity_id] = fact
            entity_result: dict[str, Any] = {"entity_id": safe_entity_id, "entity_fact": fact, "previous": previous}
            thread_id = str(item.get("scene_thread_id") or "").strip()
            if thread_id or item.get("replace_conflicting_current_fact") or item.get("open_hook_text"):
                resolved_thread_id = _resolve_scene_thread_id(
                    session,
                    self.actor,
                    self.message,
                    {"scene_thread_id": thread_id} if thread_id else {},
                )
                threads = _scene_threads(session.scene)
                _coalesce_character_thread_alias(session.scene, threads, resolved_thread_id)
                thread = dict(threads.get(resolved_thread_id) or {})
                if item.get("replace_conflicting_current_fact"):
                    _clarify_entity_in_scene_thread(thread, fact)
                if item.get("open_hook_text"):
                    hook_id = _safe_hook_id(item.get("open_hook_id") or "", safe_entity_id)
                    _upsert_open_hook(thread, hook_id=hook_id, text=_short_tag_value(str(item.get("open_hook_text") or ""), 360))
                threads[resolved_thread_id] = thread
                _write_scene_mirror(session.scene, resolved_thread_id, thread, thread)
                entity_result["scene_thread_id"] = resolved_thread_id
            entity_results.append(entity_result)
            applied.append({"type": "entity_fact", "entity_id": safe_entity_id})

        self.repository.save_session(session)
        result = {
            "ok": True,
            "action": action,
            "event": event,
            "scene": session.scene,
            "timeline": timeline_view(session.timeline),
            "applied": applied,
        }
        if character_results:
            result["character_patches"] = character_results
        if entity_results:
            result["entity_clarifications"] = entity_results
        self._audit(
            "record_event_card",
            {
                "event_id": event_id,
                "event_type": event_type,
                "summary": summary_text,
                "entities": entities or [],
                "status": status,
                "visibility": visibility,
                "source": source or {},
                "evidence": evidence or [],
                "unknowns": unknowns or [],
                "order": order,
                "supersedes": supersedes or [],
                "retracted_by": retracted_by,
                "scene_patch": scene_patch or {},
                "character_patches": character_patches or [],
                "entity_clarifications": entity_clarifications or [],
            },
            result,
        )
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
        locked_world = plot_locked_world_tags_result(session, patch, "update_world_tags")
        if locked_world:
            self._audit("update_world_tags", {"patch": patch}, locked_world)
            return locked_world
        locked = plot_locked_result(session, self.message, "update_world_tags")
        if locked and patch_touches_plot_state(patch):
            self._audit("update_world_tags", {"patch": patch}, locked)
            return locked
        overreach = post_start_world_fact_overreach_result(session, self.message, patch, "update_world_tags")
        if overreach:
            self._audit("update_world_tags", {"patch": patch}, overreach)
            return overreach
        normalized_patch = normalize_relationship_collections(patch)
        session.world_tags.update(normalized_patch)
        if has_campaign_background(session):
            session.world_tags["_background_ready"] = True
        if "title" in normalized_patch:
            session.title = str(normalized_patch["title"])
        self.repository.save_session(session)
        result = {"ok": True, "world_tags": session.world_tags, "title": session.title}
        self._audit("update_world_tags", {"patch": normalized_patch}, result)
        return result

    async def start_game(
        self,
        opening_intro: str,
        title: str = "",
        player_guidance: str = "",
        initial_hook: str = "",
        campaign_outline: Optional[Dict[str, Any]] = None,
        scene_patch: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """检查内容是否足够，足够时写入开场、锁定剧情主干，并正式开始游戏。"""
        session = self.repository.load_session(self.session_id)
        opening_intro = _coerce_prompt_text(opening_intro)
        player_guidance = _coerce_prompt_text(player_guidance)
        initial_hook = _coerce_prompt_text(initial_hook)
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
        scene_patch = normalize_scene_tracking_patch(scene_patch)
        missing = campaign_start_missing_requirements(
            session,
            opening_intro,
            campaign_outline,
            scene_patch,
            initial_hook=initial_hook,
        )
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
                    "initial_hook_chars": len(str(initial_hook or "")),
                    "campaign_outline": campaign_outline,
                    "scene_patch": scene_patch,
                },
                result,
            )
            return result

        scene_patch = normalize_scene_tracking_patch(
            scene_patch,
            fill_opening=True,
            opening_intro=opening_intro,
            player_guidance=player_guidance,
            initial_hook=initial_hook,
        )
        session.scene.update(scene_patch)
        session.scene["_game_started"] = True
        session.scene["_game_started_at"] = utc_now_iso()
        session.scene["_plot_locked"] = True
        session.scene["_allow_late_join"] = True
        session.scene["_opening_intro"] = _short_tag_value(opening_intro, 700)
        session.scene["_player_guidance"] = _short_tag_value(player_guidance, 360)
        if initial_hook:
            session.scene["_initial_hook"] = _short_tag_value(initial_hook, 360)
        session.world_tags["_plot_locked"] = True
        session.world_tags["_late_join_allowed"] = True
        session.world_tags["campaign_outline"] = compact_campaign_outline(campaign_outline)
        if title:
            session.title = _short_title_value(title, 64)
        elif scene_patch.get("title"):
            session.title = _short_title_value(scene_patch["title"], 64)
        ensure_session_title(
            session,
            scene_patch,
            initial_hook,
            opening_intro,
            session.world_tags.get("campaign_generation"),
            session.world_tags.get("campaign_contract"),
            session.world_tags.get("starting_premise"),
        )
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
                "initial_hook_chars": len(str(initial_hook or "")),
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
        elif normalized in {"preview_latest_backup", "preview_backup", "view_latest_backup", "查看上一个存档", "预览上一个存档"}:
            result = self._preview_latest_backup(reason=reason)
        elif normalized in {
            "restart_latest_backup_story",
            "restart_story_start",
            "reset_to_latest_story_start",
            "重新开上一个存档的故事",
            "重置到上一个故事开头",
        }:
            result = self._restart_latest_backup_story(reason=reason)
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
                    "preview_latest_backup",
                    "restart_latest_backup_story",
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

    def _preview_latest_backup(self, reason: str = "") -> Dict[str, Any]:
        backups = self.repository.list_session_backups(self.session_id, limit=20)
        usable = [item for item in backups if _backup_item_looks_useful(item)]
        if not usable:
            return {
                "ok": False,
                "action": "preview_latest_backup",
                "error": "no_usable_backup",
                "message": "没有找到可查看的非空备份；当前存档未改动。",
                "backups_seen": len(backups),
            }
        selected = usable[0]
        try:
            backup_session = _load_backup_session(selected)
        except Exception as exc:
            return {
                "ok": False,
                "action": "preview_latest_backup",
                "error": "backup_preview_load_failed",
                "message": "找到了备份，但读取预览失败；当前存档未改动。",
                "detail": str(exc)[:160],
            }
        preview = _build_backup_story_preview(backup_session, selected)
        return {
            "ok": True,
            "action": "preview_latest_backup",
            "selected_backup": {
                "name": selected.get("name", ""),
                "size": selected.get("size", 0),
                "mtime": selected.get("mtime", ""),
                "reason": selected.get("reason", ""),
            },
            "preview": preview,
            "message": _format_backup_story_preview(preview),
        }

    def _restart_latest_backup_story(self, reason: str = "") -> Dict[str, Any]:
        backups = self.repository.list_session_backups(self.session_id, limit=20)
        usable = [item for item in backups if _backup_item_looks_useful(item)]
        if not usable:
            return {
                "ok": False,
                "action": "restart_latest_backup_story",
                "error": "no_usable_backup",
                "message": "没有找到可重开的非空备份；当前存档未改动。",
                "backups_seen": len(backups),
            }
        selected = usable[0]
        try:
            backup_session = _load_backup_session(selected)
            story_start = _build_story_start_session_from_backup(self.session_id, backup_session, selected)
        except Exception as exc:
            return {
                "ok": False,
                "action": "restart_latest_backup_story",
                "error": "backup_story_start_build_failed",
                "message": "找到了备份，但无法整理成故事开头；当前存档未改动。",
                "detail": str(exc)[:160],
            }
        if story_start is None:
            return {
                "ok": False,
                "action": "restart_latest_backup_story",
                "error": "backup_story_start_not_found",
                "message": "找到了备份，但里面没有足够的背景或开场信息；当前存档未改动。",
            }
        before_restart = self.repository.backup_session(
            self.session_id,
            reason=f"before_restart_latest_story:{_short_reset_text(reason or self.message, 160)}",
        )
        self.repository.save_session(story_start)
        return {
            "ok": True,
            "action": "restart_latest_backup_story",
            "selected_backup": {
                "name": selected.get("name", ""),
                "size": selected.get("size", 0),
                "mtime": selected.get("mtime", ""),
                "reason": selected.get("reason", ""),
            },
            "pre_restart_backup_path": str(before_restart) if before_restart else "",
            "message": (
                "已重置到上一个故事的开头；当前档已先备份，旧角色卡、玩家绑定、战斗、地图和进度都没有带入。"
                "可以重新建卡/绑定角色后从这个开场继续。"
            ),
            "current": {
                "title": story_start.title,
                "characters": len(story_start.characters),
                "participants": len(story_start.participants),
                "battle_active": bool((story_start.battle or {}).get("active")),
                "game_started": bool((story_start.scene or {}).get("_game_started")),
            },
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
        # An explicit, non-generic character id that does not exist must not silently
        # fall back to the actor's current binding. In successor/rejoin flows the LLM
        # may try to update the new id before creating/binding it; falling back here
        # contaminates the retired old card with the new character's equipment/status.
        # Returning safe_id lets update_character_tags fail with character_not_found so
        # the model can call create_character/bind_player_character instead.
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
        grid_entities = load_active_strict_grid_entities(session.maps, battle)
        ids.update(str(key) for key in grid_entities.keys())
        return character_id in ids

    @staticmethod
    def _battle_character_label(session: GameSession, character_id: str) -> str:
        battle = session.battle or {}
        grid_entities = load_active_strict_grid_entities(session.maps, battle)
        grid_entity = dict(grid_entities.get(character_id) or {})
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


def _load_backup_session(item: Dict[str, Any]) -> GameSession:
    path = Path(str(item.get("path") or ""))
    data = json.loads(path.read_text(encoding="utf-8"))
    return GameSession.from_dict(data)


def _build_backup_story_preview(session: GameSession, item: Dict[str, Any]) -> Dict[str, Any]:
    scene = dict(session.scene or {})
    world_tags = dict(session.world_tags or {})
    background: List[str] = []
    for label, key in (
        ("类型", "genre"),
        ("调性", "tone"),
        ("地点", "location"),
        ("规则", "ruleset"),
        ("开场方向", "starting_premise"),
        ("背景", "campaign_background"),
    ):
        text = _preview_value_text(world_tags.get(key), 240)
        if not text and key == "location":
            text = _preview_value_text(scene.get("location"), 160)
        if text:
            background.append(f"{label}：{text}")

    tracking_status = format_scene_tracking_status(scene)
    if tracking_status.startswith("当前还没有记录可见目标"):
        tracking_status = ""

    characters: List[str] = []
    for character in session.characters.values():
        name = character.name or character.id
        summary = _preview_value_text(character.summary, 90)
        characters.append(f"{name}（{summary}）" if summary else name)
        if len(characters) >= 6:
            break

    scene_summary = _preview_value_text(scene.get("summary"), 360)
    current_conflict = _preview_value_text(scene.get("current_conflict"), 220)
    return {
        "title": session.title,
        "mode": session.mode.value,
        "updated_at": session.updated_at,
        "backup_mtime": str(item.get("mtime", "")),
        "backup_reason": str(item.get("reason", "")),
        "background": background[:6],
        "scene_summary": scene_summary,
        "current_conflict": current_conflict,
        "tracking_status": tracking_status,
        "characters": characters,
        "character_count": len(session.characters),
        "participant_count": len(session.participants),
        "battle_active": bool((session.battle or {}).get("active")),
        "game_started": bool(scene.get("_game_started") or scene.get("_legacy_live_campaign")),
    }


def _build_story_start_session_from_backup(
    session_id: str,
    backup_session: GameSession,
    item: Dict[str, Any],
) -> GameSession | None:
    world_tags = _story_start_world_tags(backup_session.world_tags or {})
    scene = _story_start_scene(backup_session.scene or {})
    if not world_tags and not scene:
        return None

    story_start = GameSession.new(session_id)
    story_start.title = backup_session.title or "未命名团"
    story_start.mode = GameMode.NARRATIVE
    story_start.world_tags.update(world_tags)
    story_start.world_tags["_background_ready"] = True
    story_start.world_tags["_plot_locked"] = True
    story_start.world_tags["_late_join_allowed"] = True
    story_start.world_tags["_restarted_from_backup"] = {
        "name": item.get("name", ""),
        "mtime": item.get("mtime", ""),
        "reason": item.get("reason", ""),
        "source_title": backup_session.title,
        "without_character_cards": True,
    }
    story_start.scene.clear()
    story_start.scene.update(scene)
    _ensure_story_start_scene_defaults(story_start)
    story_start.scene["_game_started"] = True
    story_start.scene["_game_started_at"] = utc_now_iso()
    story_start.scene["_plot_locked"] = True
    story_start.scene["_allow_late_join"] = True
    story_start.scene["_restarted_story_start"] = True
    story_start.scene["_restart_policy"] = "从上一个备份提取故事开头；不复制旧角色卡、绑定、战斗、地图或进度。"
    story_start.characters = {}
    story_start.participants = {}
    story_start.player_character_map = {}
    story_start.active_character_id = ""
    story_start.rules = dict(backup_session.rules or {})
    story_start.rule_sets = dict(backup_session.rule_sets or {})
    story_start.memory_summary = ""
    story_start.battle = {"active": False}
    story_start.maps = default_map_store()
    story_start.environment_summaries = []
    return story_start


def _story_start_world_tags(world_tags: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
        "background",
        "campaign_background",
        "campaign_contract",
        "campaign_outline",
        "central_conflict",
        "conflict",
        "era",
        "factions",
        "genre",
        "location",
        "premise",
        "ruleset",
        "setting",
        "starting_premise",
        "theme",
        "title",
        "tone",
        "world",
        "world_premise",
        "背景",
        "世界观",
        "时代",
        "地点",
        "势力",
        "主题",
        "开场前提",
    }
    result: Dict[str, Any] = {}
    for key, value in dict(world_tags or {}).items():
        key_text = str(key)
        key_lower = key_text.lower()
        if key_lower.startswith("_"):
            continue
        if key_lower not in allowed and key_text not in allowed:
            continue
        projected = project_visible_scene_value(value, key=key_text, depth=4, text_limit=800, item_limit=12)
        if projected not in ({}, [], "", None):
            result[key_text] = projected
    return result


def _story_start_scene(scene: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(scene or {})
    has_opening_metadata = bool(source.get("_opening_intro") or source.get("_initial_hook"))
    if has_opening_metadata:
        allowed = {
            "_opening_intro",
            "_player_guidance",
            "_initial_hook",
            "initial_hook",
            "location",
            "title",
        }
    else:
        allowed = {
            "summary",
            "current_conflict",
            "location",
            "npcs",
            "current_objective",
            "open_hooks",
            "clues",
            "mysteries",
            "stakes",
            "pressure_clock",
            "initial_hook",
            "title",
        }
    result: Dict[str, Any] = {}
    for key in allowed:
        if key not in source:
            continue
        value = source.get(key)
        if key.startswith("_"):
            if key in {"_opening_intro", "_player_guidance", "_initial_hook"}:
                text = _preview_value_text(value, 700 if key == "_opening_intro" else 360)
                if text:
                    result[key] = text
            continue
        projected = project_visible_scene_value(value, key=key, depth=4, text_limit=700, item_limit=12)
        if projected not in ({}, [], "", None):
            result[key] = projected
    if has_opening_metadata and result.get("_opening_intro"):
        result["summary"] = result["_opening_intro"]
    if has_opening_metadata and result.get("_initial_hook"):
        result["current_objective"] = result["_initial_hook"]
    result = normalize_scene_tracking_patch(result, fill_opening=True)
    return result


def _ensure_story_start_scene_defaults(session: GameSession) -> None:
    scene = session.scene
    opening_intro = str(scene.get("_opening_intro") or scene.get("summary") or "").strip()
    initial_hook = str(scene.get("_initial_hook") or scene.get("initial_hook") or scene.get("current_objective") or "").strip()
    if not opening_intro:
        opening_intro = _preview_value_text((session.world_tags or {}).get("starting_premise"), 420)
    if not opening_intro:
        opening_intro = "故事重新回到最初的开场；旧档角色卡没有带入，新的角色可以从这里进入。"
    scene["_opening_intro"] = _short_tag_value(opening_intro, 700)
    scene["summary"] = _short_tag_value(opening_intro, 700)
    if not initial_hook:
        initial_hook = _preview_value_text(scene.get("pressure_clock"), 240) or _preview_value_text(scene.get("open_hooks"), 240)
    if initial_hook:
        scene["_initial_hook"] = _short_tag_value(initial_hook, 360)
        scene.setdefault("current_objective", _short_tag_value(initial_hook, 220))
    scene.setdefault("_player_guidance", "请重新建卡或绑定新角色；旧角色卡没有带入这个重开开头。")
    scene.setdefault("summary", opening_intro)
    scene.setdefault("current_conflict", scene.get("current_objective") or initial_hook or opening_intro)


def _format_backup_story_preview(preview: Dict[str, Any]) -> str:
    lines = ["上一个存档预览（只读，当前存档未改动）："]
    title = str(preview.get("title") or "未命名团")
    backup_mtime = str(preview.get("backup_mtime") or "未知时间")
    started = "已开场" if preview.get("game_started") else "未正式开场"
    lines.append(f"团名：{title}；状态：{started}；备份时间：{backup_mtime}。")
    background = list(preview.get("background") or [])
    if background:
        lines.append("背景：" + "；".join(str(item) for item in background[:4]))
    if preview.get("scene_summary"):
        lines.append(f"场景：{preview['scene_summary']}")
    if preview.get("current_conflict"):
        lines.append(f"当前冲突：{preview['current_conflict']}")
    if preview.get("tracking_status"):
        lines.append(str(preview["tracking_status"]))
    characters = list(preview.get("characters") or [])
    if characters:
        extra = int(preview.get("character_count") or 0) - len(characters)
        suffix = f"；另有 {extra} 名角色" if extra > 0 else ""
        lines.append("角色：" + "；".join(str(item) for item in characters) + suffix)
    else:
        lines.append("角色：暂无角色记录。")
    if preview.get("battle_active"):
        lines.append("战斗：备份中有进行中的战斗状态。")
    lines.append("若要恢复这个备份，请在当前档为空或刚清空后发送 `/dm 恢复上一个存档`。")
    return "\n".join(lines)


def _preview_value_text(value: Any, limit: int) -> str:
    projected = project_visible_scene_value(value, depth=3, text_limit=limit, item_limit=6)
    if projected in (None, "", [], {}):
        return ""
    if isinstance(projected, str):
        text = projected
    elif isinstance(projected, list):
        text = "、".join(_short_reset_text(item, 80) for item in projected if str(item).strip())
    else:
        text = json.dumps(projected, ensure_ascii=False, separators=(",", ":"), default=str)
    return _short_reset_text(text, limit)


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
                    "value": project_public_relation_state(tag.value) if (tag.layer or infer_tag_layer(tag.key)) == "relations" else tag.value,
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
            layer = str(item.get("layer") or infer_tag_layer(key))
            value = item.get("value")
            if layer == "relations":
                value = normalize_relation_state(value)
            normalized.append(
                {
                    "key": key,
                    "value": value,
                    "type": str(item.get("type") or _infer_tag_type(value)),
                    "source": str(item.get("source") or "llm"),
                    "layer": layer,
                }
            )
            continue
        for key, value in item.items():
            if str(key).strip():
                normalized_key = str(key)
                layer = infer_tag_layer(normalized_key)
                normalized_value = normalize_relation_state(value) if layer == "relations" else value
                normalized.append(
                    {
                        "key": normalized_key,
                        "value": normalized_value,
                        "type": _infer_tag_type(normalized_value),
                        "source": "llm",
                        "layer": layer,
                    }
                )
    return normalized


CARD_STATIC_LAYERS = {"identity", "abilities", "equipment", "combat", "notes"}

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
    "被驱逐",
    "驱逐离船",
    "被捕且无法继续参与",
    "无法继续参与",
    "不可继续参与",
    "已离开当前故事",
    "不再参与当前故事",
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
    "被驱逐",
    "驱逐离船",
    "被捕且无法继续参与",
    "无法继续参与",
    "不可继续参与",
    "已离开当前故事",
    "不再参与当前故事",
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

TERMINAL_REJOIN_KEY_TERMS = (
    "生命状态",
    "退场状态",
    "退场确认",
    "死亡状态",
    "死亡确认",
    "死亡",
    "退场",
    "结局",
    "当前状态",
    "角色状态",
    "status",
    "state",
    "condition",
)

TERMINAL_REJOIN_EXACT_KEYS = {
    "状态",
    "当前状态",
    "status",
    "state",
    "condition",
}

TERMINAL_REJOIN_AMBIGUOUS_TERMS = (
    "遗体",
    "尸体",
    "killed",
)

TERMINAL_REJOIN_STRONG_TERMS = tuple(
    term for term in TERMINAL_STATUS_TERMS if term not in TERMINAL_REJOIN_AMBIGUOUS_TERMS
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
        if _character_terminal_rejoin_key_matches(key_text, layer):
            if _terminal_character_rejoin_text_match(key_text, value_text):
                return True
    return _battle_entity_is_terminal_for_rejoin(session, character_id)


def _character_terminal_rejoin_key_matches(key_text: str, layer: str) -> bool:
    normalized = str(key_text or "").strip().lower()
    if normalized in TERMINAL_REJOIN_EXACT_KEYS:
        return True
    if _contains_any_text(key_text, TERMINAL_REJOIN_KEY_TERMS):
        return True
    return layer == "status" and normalized in TERMINAL_REJOIN_EXACT_KEYS


def _terminal_character_rejoin_text_match(key_text: str, value_text: str) -> bool:
    combined = f"{key_text} {value_text}"
    if not (_terminal_status_text_match(combined) or _terminal_status_text_match(value_text)):
        return False
    key_is_explicit_terminal = _contains_any_text(
        key_text,
        (
            "生命状态",
            "死亡",
            "退场",
            "结局",
            "death",
            "dead",
            "deceased",
            "retired",
        ),
    )
    if key_is_explicit_terminal:
        return True
    lowered = " ".join(str(combined or "").lower().split())
    if re.search(r"\b(dead|deceased|retired)\b", lowered):
        return True
    return _contains_any_text(lowered, TERMINAL_REJOIN_STRONG_TERMS)


def _battle_entity_is_terminal_for_rejoin(session: GameSession, character_id: str) -> bool:
    battle = session.battle or {}
    loaded_grid = load_active_strict_grid(session.maps, battle)
    grid = loaded_grid.get("grid") if loaded_grid.get("ok") else {}
    if not isinstance(grid, dict):
        grid = {}
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
    if _looks_like_unearned_entry_success_fact(combined):
        reasons.append("新角色卡不能把潜入、登船、绕过安保、已在隐蔽位置或已成功潜伏写成既成事实；入场位置和潜入结果需要场内裁定。")
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

BALANCE_ELITE_OPERATIVE_TERMS = (
    "顶级",
    "专业训练",
    "受过专业训练",
    "特种训练",
    "特种潜水",
    "特工",
    "刺客",
    "杀手",
    "渗透",
    "潜伏",
    "潜入",
    "格斗专家",
    "格斗",
    "轻武器",
    "隐藏的轻武器",
    "潜艇投放",
    "干式潜水服",
    "ks-23",
    "霰弹枪",
    "鹿弹",
    "assassin",
    "operative",
    "special forces",
    "commando",
    "jason bourne",
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
    if candidate.get("elite_operative_terms") and not any(profile.get("elite_operative_terms") for profile in existing_profiles) and baseline_max_score < 5:
        terms = "、".join(candidate.get("elite_operative_terms", [])[:4])
        reasons.append(f"新角色自带顶级渗透、特种作战或隐藏武装能力（{terms}），明显高于队伍基准；应降级为普通训练、有限装备或需要场内检定的资源。")

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
    elite_operative_terms = _matched_terms(text, BALANCE_ELITE_OPERATIVE_TERMS) if _looks_like_elite_operative_bundle(text) else []
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
    score += 8 if elite_operative_terms else 0
    score += 9 if mythic_terms else 0
    score += 4 if high_tech_terms else 0
    score += 5 if absurd_numeric else 0
    matched_terms = list(dict.fromkeys([*strategic_terms, *force_terms, *elite_operative_terms, *mythic_terms, *high_tech_terms]))
    return {
        "level": level,
        "score": score,
        "strategic_terms": strategic_terms,
        "force_terms": force_terms,
        "elite_operative_terms": elite_operative_terms,
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


def _looks_like_elite_operative_bundle(text: str) -> bool:
    lowered = str(text or "").lower()
    if _contains_any_text(lowered, ("jason bourne", "杰森伯恩", "杰森·伯恩")):
        return True
    identity_terms = ("特工", "刺客", "杀手", "间谍", "突击队", "特战", "commando", "operative", "assassin")
    capability_terms = (
        "顶级",
        "专业训练",
        "受过专业训练",
        "特种训练",
        "特种潜水",
        "渗透",
        "潜伏",
        "潜入",
        "格斗",
        "轻武器",
        "隐藏的轻武器",
        "潜艇投放",
        "干式潜水服",
        "ks-23",
        "霰弹枪",
        "鹿弹",
    )
    hits = sum(1 for term in capability_terms if term in lowered)
    if _contains_any_text(lowered, identity_terms) and hits >= 2:
        return True
    return "潜艇" in lowered and "特种潜水" in lowered and hits >= 2


def _looks_like_unearned_entry_success_fact(text: str) -> bool:
    lowered = str(text or "").lower()
    success_terms = ("已成功", "已经成功", "成功登", "成功潜", "已潜伏", "已经潜伏", "已在", "当前在隐蔽")
    entry_terms = ("登船", "上船", "潜入", "潜伏", "绕过安保", "避开船员", "隐蔽位置", "藏身", "藏在")
    return _contains_any_text(lowered, success_terms) and _contains_any_text(lowered, entry_terms)


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
        elif _is_runtime_relation_tag(layer, key_text, value_text):
            normalized["layer"] = "relations"
            normalized["value"] = normalize_relation_state(normalized.get("value"))
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


def _is_runtime_relation_tag(layer: str, key_text: str, value_text: str) -> bool:
    if layer != "relations":
        return False
    combined = f"{key_text} {value_text}"
    if _contains_any_text(combined, CARD_OVERPOWERED_TERMS) or _contains_any_text(combined, CARD_FACT_INJECTION_TERMS):
        return False
    if _looks_like_unearned_social_control(combined):
        return False
    evidence_terms = (
        "检定",
        "成功",
        "失败",
        "部分成功",
        "威胁",
        "恐吓",
        "说服",
        "欺骗",
        "交易",
        "付",
        "支付",
        "交换",
        "救",
        "帮助",
        "攻击",
        "暴力",
        "偷",
        "承诺",
        "履约",
        "背叛",
        "最近互动",
        "last_interaction",
        "known_facts",
        "attitude",
        "trust",
        "fear",
        "debt",
        "leverage",
        "玩家",
        "队伍",
    )
    return _contains_any_text(combined, evidence_terms)


def _looks_like_unearned_social_control(text: str) -> bool:
    force_terms = (
        "必定相信",
        "一定相信",
        "直接相信",
        "必须相信",
        "必定协助",
        "一定协助",
        "必须协助",
        "无条件协助",
        "立刻效忠",
        "永远效忠",
        "交出所有",
    )
    if not _contains_any_text(text, force_terms):
        return False
    return not _contains_any_text(text, ("检定", "成功", "代价", "交换", "支付", "救", "帮助", "威胁", "恐吓", "交易"))


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


POST_START_WORLD_TAG_LOCKED_KEYS = {
    "background",
    "campaign_background",
    "campaign_contract",
    "campaign_outline",
    "central_conflict",
    "conflict",
    "era",
    "genre",
    "location",
    "main_plot",
    "mystery",
    "opening",
    "plot",
    "premise",
    "ruleset",
    "setting",
    "starting_premise",
    "theme",
    "title",
    "tone",
    "world",
    "world_premise",
    "world_rules",
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
    initial_hook: str = "",
) -> List[str]:
    missing: List[str] = []
    if not has_campaign_background(session):
        missing.append("background: 先写入背景设定，至少包含题材/基调/开场前提/地点/势力/规则中的两类。")
    intro_text = " ".join(str(opening_intro or "").split())
    if len(intro_text) < 40:
        missing.append("opening_intro: 需要一段简短开场介绍，包含氛围、当前处境和第一个压力点。")
    if not _outline_has_dramatic_structure(campaign_outline):
        missing.append("campaign_outline: 需要预备跌宕剧情骨架，至少有导火索、升级/反转、高潮或重大抉择三段。")
    if not opening_has_initial_hook(session.scene, scene_patch, initial_hook=initial_hook):
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


def plot_locked_world_tags_result(session: GameSession, patch: Dict[str, Any], tool_name: str) -> Dict[str, Any] | None:
    if not _campaign_plot_locked(session):
        return None
    locked_keys = [
        str(key)
        for key in patch.keys()
        if str(key).lower() in POST_START_WORLD_TAG_LOCKED_KEYS or str(key) in POST_START_WORLD_TAG_LOCKED_KEYS
    ]
    if not locked_keys:
        return None
    return {
        "ok": False,
        "error": "world_tags_locked_after_start",
        "tool": tool_name,
        "phase": "post_opening",
        "locked_keys": locked_keys,
        "message": (
            "游戏已经开场，核心世界观、题材、基调、规则框架和开场前提不能再通过 update_world_tags 改写。"
            "如果玩家对既有设定提出异议，应先核对审计/存档；只有确认是误写或经玩家共识后，才通过人工受控修正或新团重开处理。"
        ),
        "allowed_after_start": [
            "adjudication/裁定风格等运行参数",
            "由场内行动产生且可审计的势力关系变化",
            "不改写核心世界观的公开记录整理",
        ],
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
        "message": "游戏开场后不能把玩家单方面主张写成全世界事实、永久能力、等级提升或新资源。不要重复调用 update_scene/update_world_tags；请把它改写成有限的场内尝试、传闻、愿望或伏笔，必要时先用 resolve_check/execute_rule 裁定。",
        "next_tool_hint": "停止重复写入世界事实；若玩家是在行动，先裁定一次有限行动；若只是设定改写，直接 final_response 说明不能这样改。",
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


def _safe_entity_id(value: Any) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    safe = safe.strip("._-:")
    return safe[:80]


def _safe_timeline_event_id(value: Any) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    safe = safe.strip("._-:")
    if not safe:
        safe = "event"
    if not safe.startswith("event_"):
        safe = f"event_{safe}"
    return safe[:96]


def _safe_hook_id(value: Any, entity_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    safe = safe.strip("._-:")
    if not safe:
        safe = f"hook_{_safe_entity_id(entity_id) or 'entity'}_unknowns"
    if not safe.startswith("hook_"):
        safe = f"hook_{safe}"
    return safe[:96]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _event_timeline(scene: Dict[str, Any]) -> List[Dict[str, Any]]:
    timeline = scene.get("event_timeline")
    if isinstance(timeline, list):
        normalized = [item for item in timeline if isinstance(item, dict)]
        scene["event_timeline"] = normalized
        return normalized
    timeline = []
    scene["event_timeline"] = timeline
    return timeline


def _entity_facts(scene: Dict[str, Any]) -> Dict[str, Any]:
    facts = scene.get("entity_facts")
    if isinstance(facts, dict):
        return facts
    facts = {}
    scene["entity_facts"] = facts
    return facts


def _timeline_event_by_id(timeline: List[Dict[str, Any]], event_id: str) -> Optional[Dict[str, Any]]:
    for item in timeline:
        if str(item.get("id") or "") == event_id:
            return item
    return None


def _next_timeline_order(scene: Dict[str, Any]) -> int:
    orders: List[int] = []
    for item in _event_timeline(scene):
        orders.append(_safe_int(item.get("order"), 0))
    return (max(orders) + 10) if orders else 10


def _sort_event_timeline(timeline: List[Dict[str, Any]]) -> None:
    timeline.sort(key=lambda item: (_safe_int(item.get("order"), 0), str(item.get("created_at") or ""), str(item.get("id") or "")))


def _build_timeline_event(
    scene: Dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    summary: str,
    entities: List[str],
    status: str,
    visibility: str,
    source: Dict[str, Any],
    evidence: List[str],
    unknowns: List[str],
    order: Optional[int],
    supersedes: List[str],
    retracted_by: str,
) -> Dict[str, Any]:
    safe_entities = [_safe_entity_id(item) for item in entities if _safe_entity_id(item)]
    base_id = event_id or "_".join([event_type or "event"] + safe_entities[:2])
    safe_id = _safe_timeline_event_id(base_id)
    timeline = _event_timeline(scene)
    if not event_id:
        candidate = safe_id
        suffix = 2
        while _timeline_event_by_id(timeline, candidate):
            candidate = f"{safe_id}_{suffix}"
            suffix += 1
        safe_id = candidate
    safe_status = str(status or "confirmed").strip().lower()
    if safe_status not in {"confirmed", "suspected", "retracted", "superseded"}:
        safe_status = "confirmed"
    safe_visibility = str(visibility or "observed_or_confirmed").strip().lower()
    if safe_visibility not in {"observed", "confirmed", "suspected", "observed_or_confirmed", "hidden"}:
        safe_visibility = "observed_or_confirmed"
    event: Dict[str, Any] = {
        "id": safe_id,
        "event_type": _short_tag_value(event_type or "event", 80),
        "order": int(order) if order is not None else _next_timeline_order(scene),
        "status": safe_status,
        "summary": _short_tag_value(summary, 500),
        "entities": safe_entities[:12],
        "visibility": safe_visibility,
        "evidence": [_short_tag_value(item, 240) for item in evidence if str(item).strip()][:12],
        "created_at": utc_now_iso(),
    }
    if source:
        event["source"] = _compact_structured(source, depth=3)
    if unknowns:
        event["unknowns"] = [_short_tag_value(item, 180) for item in unknowns if str(item).strip()][:8]
    if supersedes:
        event["supersedes"] = [_safe_timeline_event_id(item) for item in supersedes if str(item).strip()][:8]
    if retracted_by:
        event["retracted_by"] = _short_tag_value(retracted_by, 120)
    return event


def _project_event_timeline(scene: Dict[str, Any], *, entities: List[Any], limit: int = 12) -> List[Dict[str, Any]]:
    entity_set = {_safe_entity_id(item) for item in entities if _safe_entity_id(item)}
    selected: List[Dict[str, Any]] = []
    for event in reversed(_event_timeline(scene)):
        event_entities = {_safe_entity_id(item) for item in event.get("entities", []) if _safe_entity_id(item)}
        if entity_set and not (entity_set & event_entities):
            continue
        selected.append(dict(event))
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected


def _build_entity_fact(
    *,
    entity_id: str,
    entity_type: str,
    name: str,
    current_status: str,
    historical_facts: List[str],
    unknowns: List[str],
    authoritative_events: List[str],
    evidence: List[str],
) -> Dict[str, Any]:
    fact: Dict[str, Any] = {
        "entity_id": entity_id,
        "entity_type": _short_tag_value(entity_type or "entity", 60),
        "name": _short_tag_value(name or entity_id, 120),
        "updated_at": utc_now_iso(),
    }
    if current_status:
        fact["current_status"] = _short_tag_value(current_status, 300)
    if historical_facts:
        fact["historical_facts"] = [_short_tag_value(item, 240) for item in historical_facts if str(item).strip()][:12]
    if unknowns:
        fact["unknowns"] = [_short_tag_value(item, 180) for item in unknowns if str(item).strip()][:8]
    if authoritative_events:
        fact["authoritative_events"] = [_safe_timeline_event_id(item) for item in authoritative_events if str(item).strip()][:12]
    if evidence:
        fact["evidence"] = [_short_tag_value(item, 240) for item in evidence if str(item).strip()][:12]
    return fact


def _clarify_entity_in_scene_thread(thread: Dict[str, Any], fact: Dict[str, Any]) -> None:
    entity_type = str(fact.get("entity_type") or "").strip().lower()
    if entity_type not in {"npc", "character"}:
        return
    entity_id = _safe_entity_id(fact.get("entity_id") or "")
    name = str(fact.get("name") or "").strip()
    npcs = thread.get("npcs")
    if not isinstance(npcs, list):
        npcs = []
        thread["npcs"] = npcs
    target: Optional[Dict[str, Any]] = None
    for item in npcs:
        if not isinstance(item, dict):
            continue
        if entity_id and str(item.get("id") or "") == entity_id:
            target = item
            break
        if name and str(item.get("name") or "") == name:
            target = item
            break
    if target is None:
        target = {"id": entity_id, "name": name or entity_id, "attitude": "neutral"}
        npcs.append(target)
    if entity_id:
        target["id"] = entity_id
    if name:
        target["name"] = name
    if fact.get("current_status"):
        target["status"] = fact["current_status"]
    if fact.get("historical_facts"):
        target["known_facts"] = list(fact.get("historical_facts") or [])[:12]
    if fact.get("unknowns"):
        target["unknowns"] = list(fact.get("unknowns") or [])[:8]
    if fact.get("authoritative_events"):
        target["authoritative_events"] = list(fact.get("authoritative_events") or [])[:12]


def _upsert_open_hook(thread: Dict[str, Any], *, hook_id: str, text: str) -> None:
    if not text:
        return
    hooks = thread.get("open_hooks")
    if not isinstance(hooks, list):
        hooks = []
        thread["open_hooks"] = hooks
    for hook in hooks:
        if isinstance(hook, dict) and str(hook.get("id") or "") == hook_id:
            hook.update({"id": hook_id, "text": text, "status": "open", "visibility": "observed", "attitude": "concern"})
            return
    hooks.append({"id": hook_id, "text": text, "status": "open", "visibility": "observed", "attitude": "concern"})


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


def _scene_threads(scene: Dict[str, Any]) -> Dict[str, Any]:
    threads = scene.get("scene_threads")
    if isinstance(threads, dict):
        return threads
    threads = {}
    scene["scene_threads"] = threads
    return threads


def _resolve_scene_thread_id(
    session: GameSession,
    actor: Dict[str, str],
    message: str,
    patch: Dict[str, Any],
) -> str:
    for key in SCENE_THREAD_CONTROL_KEYS:
        value = str(patch.get(key) or "").strip()
        if value:
            return _canonical_scene_thread_id(session, value)
    location = str(patch.get("location") or patch.get("_location") or "").strip()
    character_id = _actor_character_id(session, actor)
    if location:
        base = f"{character_id or 'scene'}:{location}"
    elif character_id:
        base = f"character:{character_id}"
    else:
        base = f"session:{_short_tag_value(message or session.session_id, 40)}"
    return _canonical_scene_thread_id(session, base)


def _actor_character_id(session: GameSession, actor: Dict[str, str]) -> str:
    player_id = str((actor or {}).get("player_id") or "").strip()
    if not player_id:
        return ""
    return str((session.player_character_map or {}).get(player_id, "") or "")


def _safe_scene_thread_id(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    safe = safe.strip("._:-")
    return safe[:96] or "default"


def _canonical_scene_thread_id(session: GameSession, value: str) -> str:
    safe = _safe_scene_thread_id(value)
    if safe.startswith("character:"):
        return safe
    if safe in (session.characters or {}):
        return _safe_scene_thread_id(f"character:{safe}")
    return safe


def _coalesce_character_thread_alias(
    scene: Dict[str, Any],
    scene_threads: Dict[str, Any],
    thread_id: str,
) -> None:
    if not thread_id.startswith("character:"):
        return
    alias = thread_id.split(":", 1)[1]
    if not alias or alias == thread_id or alias not in scene_threads:
        return
    legacy = scene_threads.pop(alias)
    if not isinstance(legacy, dict):
        return
    current = scene_threads.get(thread_id)
    if isinstance(current, dict):
        merged = _merge_scene_thread_alias_records(legacy, current)
        scene_threads[thread_id] = merged
    else:
        scene_threads[thread_id] = dict(legacy)
    if scene.get("active_scene_thread_id") == alias:
        scene["active_scene_thread_id"] = thread_id


def _merge_scene_thread_alias_records(legacy: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    legacy_updated = str((legacy or {}).get("updated_at") or "")
    current_updated = str((current or {}).get("updated_at") or "")
    primary_is_open = False
    if _scene_thread_is_closed(current) and not _scene_thread_is_closed(legacy):
        primary, secondary = legacy, current
        primary_is_open = True
    elif _scene_thread_is_closed(legacy) and not _scene_thread_is_closed(current):
        primary, secondary = current, legacy
        primary_is_open = True
    elif legacy_updated > current_updated:
        primary, secondary = legacy, current
    else:
        primary, secondary = current, legacy
    merged = dict(secondary or {})
    merged.update(dict(primary or {}))
    if primary_is_open:
        merged.pop("status", None)
    return merged


def _merge_scene_thread(
    current: Dict[str, Any],
    patch: Dict[str, Any],
    *,
    actor: Dict[str, str],
    character_id: str,
) -> Dict[str, Any]:
    merged = dict(current or {})
    merged.update(patch)
    if _scene_thread_patch_is_terminal(merged, patch):
        merged["status"] = "closed"
    elif _scene_thread_is_closed(merged) and _scene_thread_patch_reopens_terminal(merged, patch, character_id):
        merged.pop("status", None)
    merged["updated_at"] = utc_now_iso()
    scene_time_label = _scene_thread_time_label(patch)
    if scene_time_label:
        merged["scene_time_label"] = scene_time_label
    scene_time_of_day = _scene_thread_time_of_day(patch)
    if scene_time_of_day:
        merged["scene_time_of_day"] = scene_time_of_day
    explicit_empty_participants = "participants" in patch and not list(patch.get("participants") or [])
    if character_id and not explicit_empty_participants:
        participants = list(merged.get("participants") or [])
        if character_id not in participants:
            participants.append(character_id)
        merged["participants"] = participants[-12:]
        merged["active_character_id"] = character_id
    player_id = str((actor or {}).get("player_id") or "").strip()
    if player_id:
        merged["last_actor_player_id"] = player_id
    return merged


def _scene_thread_time_label(patch: Dict[str, Any]) -> str:
    for key in ("scene_time_label", "time_label", "current_time_label", "time", "scene_time", "current_time"):
        value = patch.get(key)
        if isinstance(value, str) and value.strip():
            return _short_tag_value(value, 80)
    return ""


def _scene_thread_time_of_day(patch: Dict[str, Any]) -> str:
    for key in ("scene_time_of_day", "time_of_day", "period", "phase"):
        value = patch.get(key)
        if isinstance(value, str) and value.strip():
            return _short_tag_value(value, 40)
    label = _scene_thread_time_label(patch)
    for token in ("黎明", "清晨", "早上", "上午", "中午", "下午", "傍晚", "黄昏", "晚上", "夜晚", "深夜", "凌晨"):
        if token in label:
            return token
    return ""


def _write_scene_mirror(
    scene: Dict[str, Any],
    thread_id: str,
    scene_thread: Dict[str, Any],
    patch: Dict[str, Any],
) -> None:
    if _scene_thread_is_closed(scene_thread):
        if scene.get("active_scene_thread_id") == thread_id:
            replacement_id = _find_replacement_scene_thread_id(scene, exclude_thread_id=thread_id)
            if replacement_id:
                scene["active_scene_thread_id"] = replacement_id
                replacement_thread = dict(_scene_threads(scene).get(replacement_id) or {})
                _mirror_scene_thread_fields(scene, replacement_thread)
            else:
                scene.pop("active_scene_thread_id", None)
        return
    if _scene_thread_is_inactive(scene_thread):
        return
    scene["active_scene_thread_id"] = thread_id
    _mirror_scene_thread_fields(scene, scene_thread)


def _mirror_scene_thread_fields(scene: Dict[str, Any], scene_thread: Dict[str, Any]) -> None:
    for key in SCENE_THREAD_MIRROR_KEYS:
        if key in scene_thread:
            scene[key] = scene_thread[key]
        else:
            scene.pop(key, None)


def _scene_thread_is_closed(thread: Dict[str, Any]) -> bool:
    status = str((thread or {}).get("status") or "").strip().lower()
    return status in SCENE_THREAD_CLOSED_STATUSES


def _scene_thread_is_inactive(thread: Dict[str, Any]) -> bool:
    return isinstance(thread, dict) and "participants" in thread and not list(thread.get("participants") or [])


def _scene_thread_patch_is_terminal(thread: Dict[str, Any], patch: Dict[str, Any]) -> bool:
    status = str((patch or {}).get("status") or "").strip().lower()
    return status in SCENE_THREAD_CLOSED_STATUSES


def _scene_thread_patch_reopens_terminal(thread: Dict[str, Any], patch: Dict[str, Any], character_id: str) -> bool:
    if not isinstance(patch, dict) or not patch:
        return False
    status = str(patch.get("status") or "").strip().lower()
    if status in SCENE_THREAD_CLOSED_STATUSES:
        return False
    if status in {"active", "open", "reopened"}:
        return True
    if _scene_thread_patch_is_terminal(thread, patch):
        return False
    actor_matches = bool(
        character_id
        and (
            str(thread.get("active_character_id") or "") == character_id
            or character_id in {str(item) for item in thread.get("participants") or [] if str(item)}
        )
    )
    if not actor_matches:
        return False
    active_fields = (
        "summary",
        "location",
        "_location",
        "current_objective",
        "current_conflict",
        "open_hooks",
        "clues",
        "mysteries",
        "stakes",
        "pressure_clock",
        "npcs",
    )
    return any(key in patch and patch.get(key) not in (None, "", [], {}) for key in active_fields)


def _find_replacement_scene_thread_id(scene: Dict[str, Any], *, exclude_thread_id: str) -> str:
    threads = _scene_threads(scene)
    candidates = []
    for candidate_id, thread in threads.items():
        if candidate_id == exclude_thread_id or not isinstance(thread, dict):
            continue
        if _scene_thread_is_closed(thread):
            continue
        candidates.append((str(thread.get("updated_at") or ""), str(candidate_id)))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][1]


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


def _short_title_value(value: Any, limit: int) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    cleaned = cleaned.replace("“", "").replace("”", "").replace("\"", "").replace("'", "")
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
