from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..core.cycle_state_machine import CycleStateMachine
from ..core.timeline import (
    apply_timeline_patch,
    timeline_advance_requires_sync,
    timeline_view,
    validate_global_timeline_advance,
)
from ..core.models import CycleState, utc_now_iso
from ..core.scene_threads import thread_is_closed
from ..storage.json_repository import JsonGameRepository


class CycleControlArgs(BaseModel):
    action: str = Field(description='周期控制动作。MVP 仅支持 "end_cycle"。')
    reason: str = Field(default="", description="DM 结束当前叙事周期的简短原因。")
    sync_policy: str = Field(
        default="strict",
        description=(
            "时间推进同步策略：strict=仍需真正阻塞者确认；timeout/quorum=安全 AFK 角色可默认托管；"
            "dm_override=明确人工覆盖并写审计。"
        ),
    )
    timeline_patch: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "可选的全团时间线推进补丁，只允许全局同步时间，例如 "
            '{"day":2,"time_of_day":"morning","label":"第 2 天清晨"}。'
            "不能按玩家或角色分叉。"
        ),
    )


class CycleTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: dict[str, str] | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}
        self.state_machine = CycleStateMachine()

    async def cycle_control(
        self,
        action: str,
        reason: str = "",
        timeline_patch: Optional[Dict[str, Any]] = None,
        sync_policy: str = "strict",
    ) -> dict[str, Any]:
        normalized = str(action or "").strip().lower().replace("-", "_")
        if normalized not in {"end_cycle", "end"}:
            return {
                "ok": False,
                "error": "unsupported_cycle_action",
                "action": action,
                "supported_actions": ["end_cycle"],
            }

        session = self.repository.load_session(self.session_id)
        requested_timeline_patch = dict(timeline_patch or {})
        normalized_sync_policy = _normalize_sync_policy(sync_policy)
        timeline_result: dict[str, Any] = {"timeline_advanced": False}
        if requested_timeline_patch or timeline_advance_requires_sync(reason):
            if not requested_timeline_patch:
                result = {
                    "ok": False,
                    "error": "timeline_patch_required",
                    "message": "这次周期结束理由包含跨日、入夜、天亮或长时间跳转；必须提供全局 timeline_patch，不能只靠叙事文本推进时间。",
                }
                self.repository.append_audit(
                    self.session_id,
                    {
                        "type": "cycle_control",
                        "action": "end_cycle",
                        "actor": self.actor,
                        "reason": reason,
                        "result": result,
                    },
                )
                return {
                    **result,
                    "action": "end_cycle",
                    "cycle_id": session.current_cycle_id,
                    "timeline": timeline_view(session.timeline),
                }
            actor_player_id = str(self.actor.get("player_id") or "").strip()
            additional_player_ids = {actor_player_id} if actor_player_id else set()
            validation = validate_global_timeline_advance(
                session,
                requested_timeline_patch,
                additional_player_ids=additional_player_ids,
            )
            if not validation.get("ok"):
                validation = _apply_soft_sync_policy(
                    session,
                    validation,
                    normalized_sync_policy,
                    actor_player_id=actor_player_id,
                )
            if not validation.get("ok"):
                self.repository.append_audit(
                    self.session_id,
                    {
                        "type": "cycle_control",
                        "action": "end_cycle",
                        "actor": self.actor,
                        "reason": reason,
                        "sync_policy": normalized_sync_policy,
                        "timeline_patch": requested_timeline_patch,
                        "result": validation,
                    },
                )
                return {
                    **validation,
                    "action": "end_cycle",
                    "cycle_id": session.current_cycle_id,
                    "timeline": timeline_view(session.timeline),
                }
            if requested_timeline_patch:
                previous_timeline = timeline_view(session.timeline)
                session.timeline = apply_timeline_patch(
                    session.timeline,
                    requested_timeline_patch,
                    reason=reason,
                    cycle_id=session.current_cycle_id,
                )
                timeline_result = {
                    "timeline_advanced": True,
                    "previous_timeline": previous_timeline,
                    "timeline": timeline_view(session.timeline),
                    **validation,
                }
        previous_state = session.cycle_state
        if previous_state != CycleState.CYCLE_RESOLVING:
            try:
                self.state_machine.begin_resolving(session)
            except ValueError as exc:
                return {
                    "ok": False,
                    "error": "invalid_cycle_transition",
                    "from_state": previous_state.value,
                    "to_state": CycleState.CYCLE_RESOLVING.value,
                    "reason": str(exc),
                }
        session.audit_buffer.cycle_id = session.current_cycle_id
        session.audit_buffer.ended_at = utc_now_iso()
        self.repository.save_session(session)
        self.repository.append_audit(
            self.session_id,
            {
                "type": "cycle_control",
                "action": "end_cycle",
                "actor": self.actor,
                "reason": reason,
                "sync_policy": normalized_sync_policy,
                "from_state": previous_state.value,
                "to_state": session.cycle_state.value,
                "cycle_id": session.current_cycle_id,
                "timeline": timeline_view(session.timeline),
                "timeline_result": timeline_result,
            },
        )
        return {
            "ok": True,
            "action": "end_cycle",
            "cycle_id": session.current_cycle_id,
            "from_state": previous_state.value,
            "to_state": session.cycle_state.value,
            "timeline": timeline_view(session.timeline),
            "timeline_result": timeline_result,
            "message": "当前叙事周期已标记为结束，等待周期结算。",
        }


def _normalize_sync_policy(value: str) -> str:
    normalized = str(value or "strict").strip().lower().replace("-", "_")
    if normalized in {"timeout", "soft", "afk_timeout"}:
        return "timeout"
    if normalized in {"quorum", "majority"}:
        return "quorum"
    if normalized in {"dm_override", "override", "force"}:
        return "dm_override"
    return "strict"


def _apply_soft_sync_policy(
    session: Any,
    validation: dict[str, Any],
    sync_policy: str,
    *,
    actor_player_id: str,
) -> dict[str, Any]:
    if validation.get("error") != "timeline_sync_required" or sync_policy == "strict":
        return validation
    missing = [str(player_id) for player_id in validation.get("missing_player_ids") or [] if str(player_id)]
    if not missing:
        clean = dict(validation)
        clean.pop("error", None)
        clean["ok"] = True
        return clean
    defaulted: list[str] = []
    unsafe: list[str] = []
    for player_id in missing:
        if sync_policy == "dm_override" or _player_is_safe_to_afk_default(session, player_id):
            defaulted.append(player_id)
        else:
            unsafe.append(player_id)
    if unsafe and sync_policy != "dm_override":
        return {
            **validation,
            "ok": False,
            "error": "unsafe_afk_advance",
            "message": "仍有 AFK 角色处于危险、战斗、活跃冲突或关键选择中；不能跨时段推进，只能等待或托管当前回合。",
            "sync_policy": sync_policy,
            "unsafe_player_ids": sorted(unsafe),
            "afk_defaulted_player_ids": sorted(defaulted),
        }
    acted = set(str(player_id) for player_id in validation.get("acted_player_ids") or [] if str(player_id))
    if actor_player_id:
        acted.add(actor_player_id)
    acted.update(defaulted)
    active = set(str(player_id) for player_id in validation.get("active_player_ids") or [] if str(player_id))
    clean_validation = dict(validation)
    clean_validation.pop("error", None)
    return {
        **clean_validation,
        "ok": True,
        "sync_policy": sync_policy,
        "active_player_ids": sorted(active),
        "acted_player_ids": sorted(acted),
        "missing_player_ids": [],
        "afk_defaulted_player_ids": sorted(defaulted),
        "afk_default_note": "安全 AFK 角色已按随队休息/待命托管，全团时间线保持统一推进。",
    }


def _player_is_safe_to_afk_default(session: Any, player_id: str) -> bool:
    if _battle_waits_for_player(session, player_id):
        return False
    character_id = str((getattr(session, "player_character_map", {}) or {}).get(player_id) or "")
    if character_id and _character_is_terminal(session, character_id):
        return True
    thread = _latest_thread_for_character(session, character_id)
    if not thread:
        return True
    if thread_is_closed(thread):
        return True
    text = _flatten_text(thread).lower()
    if thread.get("afk_default_safe") is True:
        return True
    if any(term in text for term in ("战斗", "敌人", "逼问", "关键选择", "等待选择", "危险", "濒死", "被追", "潜行暴露", "ambush", "combat", "danger")):
        return False
    return any(term in text for term in ("安全", "待命", "睡", "休息", "酒馆", "回房", "无回应", "随队", "safe", "rest"))


def _battle_waits_for_player(session: Any, player_id: str) -> bool:
    battle = getattr(session, "battle", {}) or {}
    if not isinstance(battle, dict):
        return False
    if battle.get("active") is True:
        return True
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if not turn or turn.get("active") is not True:
        return False
    if str(turn.get("phase") or "").strip().lower() in {"idle", "suspended", "ended"}:
        return False
    current_entity_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
    owner = _battle_entity_owner(session, current_entity_id)
    return bool(owner and owner == player_id)


def _battle_entity_owner(session: Any, entity_id: str) -> str:
    if not entity_id:
        return ""
    maps = getattr(session, "maps", {}) or {}
    battle = getattr(session, "battle", {}) or {}
    active_map_id = str(battle.get("active_map_id") or battle.get("map_id") or "")
    records = maps.get("records") if isinstance(maps, dict) else {}
    record = records.get(active_map_id) if isinstance(records, dict) and active_map_id else {}
    grid = record.get("grid") if isinstance(record, dict) else {}
    entities = grid.get("entities") if isinstance(grid, dict) else {}
    entity = entities.get(entity_id) if isinstance(entities, dict) else {}
    tags = entity.get("tags") if isinstance(entity, dict) else {}
    if isinstance(tags, dict) and tags.get("player_id"):
        return str(tags.get("player_id") or "")
    for player_id, character_id in (getattr(session, "player_character_map", {}) or {}).items():
        if entity_id == character_id:
            return str(player_id)
    return ""


def _latest_thread_for_character(session: Any, character_id: str) -> dict[str, Any]:
    if not character_id:
        return {}
    scene = getattr(session, "scene", {}) or {}
    threads = scene.get("scene_threads") if isinstance(scene, dict) else {}
    if not isinstance(threads, dict):
        return {}
    matches: list[tuple[str, dict[str, Any]]] = []
    for thread in threads.values():
        if not isinstance(thread, dict):
            continue
        participants = {str(item) for item in thread.get("participants") or [] if str(item)}
        if str(thread.get("active_character_id") or "") == character_id or character_id in participants:
            matches.append((str(thread.get("updated_at") or ""), thread))
    if not matches:
        return {}
    matches.sort(key=lambda item: item[0], reverse=True)
    return dict(matches[0][1])


def _character_is_terminal(session: Any, character_id: str) -> bool:
    character = (getattr(session, "characters", {}) or {}).get(character_id)
    if not character:
        return False
    for tag in getattr(character, "tags", []) or []:
        text = f"{getattr(tag, 'key', '')} {getattr(tag, 'value', '')}".lower()
        if any(
            term in text
            for term in (
                "死亡",
                "阵亡",
                "永久退场",
                "退场",
                "退休",
                "被驱逐",
                "驱逐离船",
                "被捕且无法继续参与",
                "无法继续参与",
                "不可继续参与",
                "已离开当前故事",
                "不再参与当前故事",
                "dead",
                "retired",
                "out_of_play",
            )
        ):
            return True
    return False


def _flatten_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten_text(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(_flatten_text(item) for item in value)
    return str(value or "")
