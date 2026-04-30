from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..core.models import GameMode, GameSession, utc_now_iso
from ..storage.json_repository import JsonGameRepository


TURN_TIMEOUT_SECONDS = 120
DEFAULT_TURN_OUTPUT_LIMIT_CHARS = 1440


class TurnControlArgs(BaseModel):
    action: str = Field(
        ...,
        description=(
            "回合状态动作：status,start_round,set_order,start_scene_resolution,"
            "finish_scene_resolution,start_character_turn,record_action,advance_turn,"
            "auto_act_current,skip_current,end_encounter"
        ),
    )
    turn_order: List[str] = Field(default_factory=list, description="行动顺序里的实体/角色 ID")
    current_entity_id: str = Field(default="", description="指定要记录行动的实体/角色 ID；角色回合中可为本轮未行动且归当前发言人所有的实体")
    summary: str = Field(default="", description="场面结算、角色行动或跳过原因的简短摘要")
    reason: str = Field(default="", description="推进状态的自然语言原因")
    output_limit_chars: int = Field(default=DEFAULT_TURN_OUTPUT_LIMIT_CHARS, ge=80, le=2000, description="本阶段建议单次回复长度上限")
    auto_policy: str = Field(default="defend_or_follow", description="无人响应时的自动行为策略")
    advance_after: bool = Field(default=True, description="record_action/auto_act_current 后是否自动推进到下一阶段")


class TurnTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        actor: Optional[Dict[str, str]] = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}

    async def turn_control(
        self,
        action: str,
        turn_order: Optional[List[str]] = None,
        current_entity_id: str = "",
        summary: str = "",
        reason: str = "",
        output_limit_chars: int = DEFAULT_TURN_OUTPUT_LIMIT_CHARS,
        auto_policy: str = "defend_or_follow",
        advance_after: bool = True,
    ) -> Dict[str, Any]:
        session = self.repository.load_session(self.session_id)
        turn = self._ensure_turn_state(session)
        normalized = action.strip().lower()
        output_limit_chars = max(
            DEFAULT_TURN_OUTPUT_LIMIT_CHARS,
            min(2000, int(output_limit_chars or DEFAULT_TURN_OUTPUT_LIMIT_CHARS)),
        )

        if normalized in {"status", "状态"}:
            result = self._status(session)
        elif normalized in {"start_round", "start", "开始轮次", "开始回合"}:
            order = self._clean_order(turn_order or []) or self._derive_turn_order(session)
            if not order:
                result = {"ok": False, "error": "empty_turn_order", "message": "没有可行动角色或实体。"}
            else:
                turn.update(
                    {
                        "active": True,
                        "round": max(1, int(turn.get("round", 0) or 0) + 1),
                        "phase": "scene_resolution",
                        "turn_order": order,
                        "current_index": -1,
                        "current_entity_id": "",
                        "output_limit_chars": output_limit_chars,
                        "auto_policy": auto_policy,
                        "actions_this_round": {},
                        "scene_resolution_done": False,
                    }
                )
                session.mode = GameMode.TACTICAL
                session.battle["turn_entity_id"] = ""
                self.repository.save_session(session)
                result = self._status(session)
        elif normalized in {"set_order", "设置顺序", "initiative", "先攻"}:
            order = self._clean_order(turn_order or [])
            if not order:
                result = {"ok": False, "error": "empty_turn_order"}
            else:
                turn["turn_order"] = order
                turn["active"] = True
                turn["output_limit_chars"] = output_limit_chars
                if turn.get("current_entity_id") not in order:
                    turn["current_index"] = -1
                    turn["current_entity_id"] = ""
                    session.battle["turn_entity_id"] = ""
                session.mode = GameMode.TACTICAL
                self.repository.save_session(session)
                result = self._status(session)
        elif normalized in {"start_scene_resolution", "scene_resolution", "场面结算"}:
            turn["active"] = True
            turn["phase"] = "scene_resolution"
            turn["current_entity_id"] = ""
            turn["current_index"] = -1
            turn["output_limit_chars"] = output_limit_chars
            turn["scene_resolution_done"] = False
            session.mode = GameMode.TACTICAL
            session.battle["turn_entity_id"] = ""
            if summary:
                self._append_turn_log(session, "scene_resolution_start", summary, reason)
            self.repository.save_session(session)
            result = self._status(session)
        elif normalized in {"finish_scene_resolution", "end_scene_resolution", "场面结算完成"}:
            result = self._start_next_character_turn(session, turn, output_limit_chars)
        elif normalized in {"start_character_turn", "character_turn", "角色回合"}:
            result = self._start_character_turn(session, turn, current_entity_id, output_limit_chars)
        elif normalized in {"record_action", "记录行动"}:
            entity_id = current_entity_id.strip() or self._default_action_entity(session, turn)
            if not entity_id:
                result = {"ok": False, "error": "missing_current_entity_id"}
            else:
                guard = self._validate_player_control_request(session, turn, entity_id)
                if guard:
                    result = guard
                else:
                    self._record_action(session, turn, entity_id, summary or "完成本回合行动", "player", reason)
                    if advance_after:
                        result = self._advance_turn(session, turn, output_limit_chars)
                    else:
                        self._ensure_turn_timer(turn, "record_action_without_advance")
                        self.repository.save_session(session)
                        result = self._status(session)
        elif normalized in {"advance_turn", "next_turn", "下一位", "推进"}:
            guard = self._validate_advance_turn_request(session, turn)
            if guard:
                result = guard
            else:
                current_id = str(turn.get("current_entity_id", "") or session.battle.get("turn_entity_id", "")).strip()
                if current_id and current_id not in dict(turn.get("actions_this_round") or {}):
                    self._record_action(session, turn, current_id, summary or "当前行动者声明跳过/结束本回合", "skipped", reason)
                result = self._advance_turn(session, turn, output_limit_chars)
        elif normalized in {"auto_act_current", "auto", "无人响应", "自动行动"}:
            result = self._auto_act_current(session, turn, summary, reason, output_limit_chars, auto_policy)
        elif normalized in {"skip_current", "skip", "跳过"}:
            entity_id = current_entity_id.strip() or self._default_action_entity(session, turn)
            if not entity_id:
                result = {"ok": False, "error": "missing_current_entity_id"}
            else:
                guard = self._validate_player_control_request(session, turn, entity_id)
                if guard:
                    result = guard
                else:
                    self._record_action(session, turn, entity_id, summary or "玩家声明跳过本回合", "skipped", reason)
                    result = self._advance_turn(session, turn, output_limit_chars)
        elif normalized in {"end_encounter", "end", "结束战斗", "结束遭遇"}:
            if (
                turn.get("active")
                and str(turn.get("phase", "")) == "character_turn"
                and not _looks_like_terminal_encounter_end(summary, reason, session)
            ):
                result = {
                    "ok": False,
                    "error": "end_encounter_requires_scene_resolution",
                    "message": "不能在角色回合中直接结束遭遇；请先完成当前轮次，进入场面结算后再根据敌方士气、撤退、增援或环境压力裁定是否结束。",
                    "phase": str(turn.get("phase", "")),
                    "current_entity_id": str(turn.get("current_entity_id", "")),
                }
            else:
                turn["active"] = False
                turn["phase"] = "ended"
                turn["current_entity_id"] = ""
                session.battle["turn_entity_id"] = ""
                session.battle["active"] = False
                session.mode = GameMode.NARRATIVE
                if summary:
                    self._append_turn_log(session, "encounter_end", summary, reason)
                self.repository.save_session(session)
                result = self._status(session)
        else:
            result = {
                "ok": False,
                "error": "unsupported_turn_control_action",
                "allowed": [
                    "status",
                    "start_round",
                    "set_order",
                    "start_scene_resolution",
                    "finish_scene_resolution",
                    "start_character_turn",
                    "record_action",
                    "advance_turn",
                    "auto_act_current",
                    "skip_current",
                    "end_encounter",
                ],
            }

        self._audit(
            "turn_control",
            {
                "action": action,
                "turn_order": turn_order or [],
                "current_entity_id": current_entity_id,
                "summary": summary,
                "reason": reason,
                "output_limit_chars": output_limit_chars,
                "auto_policy": auto_policy,
                "advance_after": advance_after,
            },
            result,
        )
        return result

    def _ensure_turn_state(self, session: GameSession) -> Dict[str, Any]:
        if not session.battle:
            session.battle = {"active": False}
        turn = session.battle.get("turn")
        if not isinstance(turn, dict):
            turn = {}
            session.battle["turn"] = turn
        turn.setdefault("active", False)
        turn.setdefault("round", 0)
        turn.setdefault("phase", "idle")
        turn.setdefault("turn_order", [])
        turn.setdefault("current_index", -1)
        turn.setdefault("current_entity_id", session.battle.get("turn_entity_id", ""))
        turn.setdefault("output_limit_chars", DEFAULT_TURN_OUTPUT_LIMIT_CHARS)
        if int(turn.get("output_limit_chars") or 0) < DEFAULT_TURN_OUTPUT_LIMIT_CHARS:
            turn["output_limit_chars"] = DEFAULT_TURN_OUTPUT_LIMIT_CHARS
        turn.setdefault("auto_policy", "defend_or_follow")
        turn.setdefault("timeout_seconds", TURN_TIMEOUT_SECONDS)
        turn.setdefault("actions_this_round", {})
        turn.setdefault("turn_log", [])
        return turn

    def apply_turn_timeout_policy(self, session: GameSession, message: str) -> List[Dict[str, Any]]:
        """Apply the deterministic 120-second waiting rule before the LLM sees a turn push."""
        turn = self._ensure_turn_state(session)
        if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
            return []
        current_id = str(turn.get("current_entity_id", "") or session.battle.get("turn_entity_id", "")).strip()
        if not current_id:
            return []
        owner_id = self._owner_player_id(session, current_id)
        actor_id = str(self.actor.get("player_id", "") or "")
        if not owner_id or not actor_id:
            turn["timeout_seconds"] = TURN_TIMEOUT_SECONDS
            return []

        turn["timeout_seconds"] = TURN_TIMEOUT_SECONDS
        actor_pending_id = self._pending_entity_for_actor(session, turn, actor_id)
        if actor_pending_id and actor_pending_id != current_id and _looks_like_own_round_action(message):
            return [
                {
                    "type": "turn_out_of_order_actor_allowed",
                    "current_entity_id": current_id,
                    "current_label": self._entity_label(session, current_id),
                    "owner_player_id": owner_id,
                    "actor_player_id": actor_id,
                    "actor_entity_id": actor_pending_id,
                    "actor_entity_label": self._entity_label(session, actor_pending_id),
                    "deadline_at": turn.get("deadline_at", ""),
                    "reason": "actor_has_unacted_entity_this_round",
                }
            ]

        if actor_id == owner_id:
            initialized = self._ensure_turn_timer(turn, "missing_turn_timer_initialized")
            deadline = _parse_datetime(turn.get("deadline_at"))
            remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds())) if deadline else 0
            return [
                {
                    "type": "turn_actor_response_no_reset",
                    "current_entity_id": current_id,
                    "current_label": self._entity_label(session, current_id),
                    "owner_player_id": owner_id,
                    "actor_player_id": actor_id,
                    "deadline_at": turn.get("deadline_at", ""),
                    "remaining_seconds": remaining,
                    "timer_initialized": initialized,
                    "reason": "current_actor_response_does_not_extend_deadline",
                }
            ]

        if not _looks_like_timeout_push(message):
            return []

        now = datetime.now(timezone.utc)
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline is None:
            self._reset_turn_timer(turn, "timeout_window_started")
            return [
                {
                    "type": "turn_wait_started",
                    "current_entity_id": current_id,
                    "current_label": self._entity_label(session, current_id),
                    "owner_player_id": owner_id,
                    "actor_player_id": actor_id,
                    "deadline_at": turn.get("deadline_at", ""),
                    "reason": "non_owner_push_started_timeout_window",
                }
            ]
        if now < deadline:
            return [
                {
                    "type": "turn_waiting",
                    "current_entity_id": current_id,
                    "current_label": self._entity_label(session, current_id),
                    "owner_player_id": owner_id,
                    "actor_player_id": actor_id,
                    "deadline_at": turn.get("deadline_at", ""),
                    "remaining_seconds": max(0, int((deadline - now).total_seconds())),
                    "reason": "non_owner_push_before_timeout",
                }
            ]

        waiting_since = _parse_datetime(turn.get("waiting_since_at"))
        elapsed = int((now - waiting_since).total_seconds()) if waiting_since else TURN_TIMEOUT_SECONDS
        label = self._entity_label(session, current_id)
        summary = f"{label} 超过 120 秒未响应，采取保守行动：防御、保持掩体、跟随队伍，不消耗稀缺资源。"
        self._record_action(
            session,
            turn,
            current_id,
            summary,
            "auto_timeout",
            f"其他玩家推动流程；当前行动者已等待约 {max(TURN_TIMEOUT_SECONDS, elapsed)} 秒。",
        )
        result = self._advance_turn(
            session,
            turn,
            int(turn.get("output_limit_chars") or DEFAULT_TURN_OUTPUT_LIMIT_CHARS),
        )
        return [
            {
                "type": "turn_timeout_auto_action",
                "current_entity_id": current_id,
                "current_label": label,
                "owner_player_id": owner_id,
                "actor_player_id": actor_id,
                "elapsed_seconds": max(TURN_TIMEOUT_SECONDS, elapsed),
                "summary": summary,
                "advance_result": result,
            }
        ]

    def _derive_turn_order(self, session: GameSession) -> List[str]:
        grid = (session.battle or {}).get("grid") or {}
        entities = dict(grid.get("entities", {}))
        if entities:
            def sort_key(item: tuple[str, Dict[str, Any]]) -> tuple[int, str]:
                entity_id, entity = item
                faction = str(entity.get("faction", "")).lower()
                priority = 0 if faction in {"player", "ally", "pc", "heroes"} else 1
                return priority, str(entity.get("name") or entity_id)

            return [entity_id for entity_id, _ in sorted(entities.items(), key=sort_key)]
        bound = [cid for cid in session.player_character_map.values() if cid in session.characters]
        if bound:
            return list(dict.fromkeys(bound))
        return list(session.characters.keys())

    @staticmethod
    def _clean_order(order: List[str]) -> List[str]:
        cleaned: List[str] = []
        seen = set()
        for item in order:
            value = str(item).strip()
            if value and value not in seen:
                cleaned.append(value)
                seen.add(value)
        return cleaned

    def _start_next_character_turn(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        output_limit_chars: int,
    ) -> Dict[str, Any]:
        order = self._clean_order(list(turn.get("turn_order") or [])) or self._derive_turn_order(session)
        if not order:
            return {"ok": False, "error": "empty_turn_order"}
        turn["turn_order"] = order
        turn["phase"] = "character_turn"
        turn["scene_resolution_done"] = True
        turn["output_limit_chars"] = output_limit_chars
        index = _int_or_default(turn.get("current_index"), -1)
        if index < 0:
            index = 0
        elif index >= len(order):
            index = 0
        return self._set_current_index(session, turn, index)

    def _start_character_turn(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        current_entity_id: str,
        output_limit_chars: int,
    ) -> Dict[str, Any]:
        order = self._clean_order(list(turn.get("turn_order") or [])) or self._derive_turn_order(session)
        entity_id = current_entity_id.strip()
        if not entity_id:
            entity_id = str(turn.get("current_entity_id", "") or (order[0] if order else ""))
        if not entity_id:
            return {"ok": False, "error": "missing_current_entity_id"}
        if entity_id not in order:
            order.append(entity_id)
        turn["turn_order"] = order
        turn["phase"] = "character_turn"
        turn["output_limit_chars"] = output_limit_chars
        return self._set_current_index(session, turn, order.index(entity_id))

    def _set_current_index(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        index: int,
    ) -> Dict[str, Any]:
        order = list(turn.get("turn_order") or [])
        if not order:
            return {"ok": False, "error": "empty_turn_order"}
        index = max(0, min(index, len(order) - 1))
        entity_id = order[index]
        turn["active"] = True
        turn["phase"] = "character_turn"
        turn["current_index"] = index
        turn["current_entity_id"] = entity_id
        self._reset_turn_timer(turn, "turn_started")
        session.battle["turn_entity_id"] = entity_id
        session.mode = GameMode.TACTICAL
        self.repository.save_session(session)
        return self._status(session)

    def _advance_turn(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        output_limit_chars: int,
    ) -> Dict[str, Any]:
        if turn.get("phase") == "scene_resolution":
            return self._start_next_character_turn(session, turn, output_limit_chars)
        order = self._clean_order(list(turn.get("turn_order") or [])) or self._derive_turn_order(session)
        if not order:
            return {"ok": False, "error": "empty_turn_order"}
        turn["turn_order"] = order
        pending = self._pending_entities(turn, order)
        if not pending:
            turn["round"] = max(1, int(turn.get("round", 1) or 1) + 1)
            turn["phase"] = "scene_resolution"
            turn["current_index"] = -1
            turn["current_entity_id"] = ""
            turn["actions_this_round"] = {}
            turn["scene_resolution_done"] = False
            turn["output_limit_chars"] = output_limit_chars
            self._reset_turn_timer(turn, "scene_resolution_started")
            session.battle["turn_entity_id"] = ""
            session.mode = GameMode.TACTICAL
            self.repository.save_session(session)
            return self._status(session)
        index = self._next_pending_index(turn, order, pending)
        return self._set_current_index(session, turn, index)

    def _pending_entities(self, turn: Dict[str, Any], order: List[str]) -> List[str]:
        actions = dict(turn.get("actions_this_round") or {})
        return [entity_id for entity_id in order if entity_id not in actions]

    def _next_pending_index(self, turn: Dict[str, Any], order: List[str], pending: List[str]) -> int:
        current_id = str(turn.get("current_entity_id", "")).strip()
        if current_id in pending:
            return order.index(current_id)
        current_index = _int_or_default(turn.get("current_index"), -1)
        if current_index < 0:
            return order.index(pending[0])
        for offset in range(1, len(order) + 1):
            candidate = order[(current_index + offset) % len(order)]
            if candidate in pending:
                return order.index(candidate)
        return order.index(pending[0])

    def _default_action_entity(self, session: GameSession, turn: Dict[str, Any]) -> str:
        current_id = str(turn.get("current_entity_id", "") or session.battle.get("turn_entity_id", "")).strip()
        requester_id = str(self.actor.get("player_id", "") or "").strip()
        actions = dict(turn.get("actions_this_round") or {})
        if current_id and current_id not in actions and self._owner_player_id(session, current_id) == requester_id:
            return current_id
        actor_entity = self._pending_entity_for_actor(session, turn, requester_id)
        if actor_entity:
            return actor_entity
        return current_id

    def _pending_entity_for_actor(self, session: GameSession, turn: Dict[str, Any], actor_id: str) -> str:
        actor_id = str(actor_id or "").strip()
        if not actor_id:
            return ""
        order = self._clean_order(list(turn.get("turn_order") or [])) or self._derive_turn_order(session)
        actions = dict(turn.get("actions_this_round") or {})
        for entity_id in order:
            if entity_id in actions:
                continue
            if self._owner_player_id(session, entity_id) == actor_id:
                return entity_id
        return ""

    def _auto_act_current(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        summary: str,
        reason: str,
        output_limit_chars: int,
        auto_policy: str,
    ) -> Dict[str, Any]:
        entity_id = str(turn.get("current_entity_id", "")).strip()
        if not entity_id:
            return {"ok": False, "error": "missing_current_entity_id"}
        auto_guard = self._validate_auto_action_request(session, turn, entity_id, summary, reason)
        if auto_guard:
            return auto_guard
        auto_summary = summary.strip() or self._default_auto_action(session, entity_id, auto_policy)
        self._record_action(session, turn, entity_id, auto_summary, "auto", reason or "玩家未响应，按保守策略自动行动")
        result = self._advance_turn(session, turn, output_limit_chars)
        result["auto_action"] = {"entity_id": entity_id, "summary": auto_summary, "policy": auto_policy}
        return result

    def _validate_auto_action_request(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        entity_id: str,
        summary: str,
        reason: str,
    ) -> Dict[str, Any] | None:
        owner_id = self._owner_player_id(session, entity_id)
        requester_id = str(self.actor.get("player_id", "") or "")
        if not owner_id:
            return None
        if not requester_id:
            return {
                "ok": False,
                "error": "auto_action_requires_requester",
                "message": "不能在没有明确发言人的情况下自动代管玩家角色。",
                "current_entity_id": entity_id,
                "owner_player_id": owner_id,
            }
        text = f"{summary}\n{reason}"
        explicit = _looks_like_auto_request(text)
        if owner_id == requester_id and explicit:
            return None
        if owner_id != requester_id and explicit:
            now = datetime.now(timezone.utc)
            deadline = _parse_datetime(turn.get("deadline_at"))
            if deadline is None:
                self._reset_turn_timer(turn, "timeout_window_started_by_tool")
                self.repository.save_session(session)
                return {
                    "ok": False,
                    "error": "turn_timeout_window_started",
                    "message": "当前行动角色属于其他玩家；已开始 120 秒等待，超时后才能自动保守代管。",
                    "current_entity_id": entity_id,
                    "owner_player_id": owner_id,
                    "requester_player_id": requester_id,
                    "deadline_at": turn.get("deadline_at", ""),
                }
            if now < deadline:
                return {
                    "ok": False,
                    "error": "turn_not_timed_out",
                    "message": "当前行动角色属于其他玩家，尚未超过 120 秒；不能替他行动或跳过。",
                    "current_entity_id": entity_id,
                    "owner_player_id": owner_id,
                    "requester_player_id": requester_id,
                    "deadline_at": turn.get("deadline_at", ""),
                    "remaining_seconds": max(0, int((deadline - now).total_seconds())),
                }
            return None
        if owner_id == requester_id:
            message = "当前发言人就是行动角色的玩家；只有明确说跳过、待机、防御或自动处理时，才会代管本回合。"
        else:
            message = "当前行动角色属于其他玩家；只有明确推动流程且已超过 120 秒未响应时，才会自动保守代管。"
        return {
            "ok": False,
            "error": "auto_action_requires_explicit_continue",
            "message": message,
            "current_entity_id": entity_id,
            "owner_player_id": owner_id,
            "requester_player_id": requester_id,
        }

    def _validate_player_control_request(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        entity_id: str,
    ) -> Dict[str, Any] | None:
        if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
            return None
        current_id = str(turn.get("current_entity_id", "") or session.battle.get("turn_entity_id", "")).strip()
        actions = dict(turn.get("actions_this_round") or {})
        if entity_id in actions:
            return {
                "ok": False,
                "error": "entity_already_acted_this_round",
                "message": "该角色本轮已经行动过；不能在同一轮内再次结算主要动作。",
                "current_entity_id": current_id,
                "requested_entity_id": entity_id,
                "phase": str(turn.get("phase", "")),
            }
        order = self._clean_order(list(turn.get("turn_order") or [])) or self._derive_turn_order(session)
        if order and entity_id not in order:
            return {
                "ok": False,
                "error": "entity_not_in_turn_order",
                "message": "该角色不在本轮行动列表里；不能在当前轮次直接结算主要动作。",
                "current_entity_id": current_id,
                "requested_entity_id": entity_id,
                "phase": str(turn.get("phase", "")),
            }
        owner_id = self._owner_player_id(session, entity_id)
        requester_id = str(self.actor.get("player_id", "") or "")
        if owner_id and requester_id != owner_id:
            return {
                "ok": False,
                "error": "character_control_denied",
                "message": "当前行动角色属于其他玩家；非持有人不能替他记录行动、跳过或防御。若其超过 120 秒未响应，只能使用 auto_act_current 进行保守自动行动。",
                "current_entity_id": entity_id,
                "owner_player_id": owner_id,
                "requester_player_id": requester_id,
                "deadline_at": str(turn.get("deadline_at", "")),
            }
        if not owner_id and current_id and entity_id != current_id:
            return {
                "ok": False,
                "error": "wrong_turn_actor",
                "message": "无持有人的单位仍按当前指针行动；不能乱序操作 NPC 或敌方单位。",
                "current_entity_id": current_id,
                "requested_entity_id": entity_id,
                "phase": str(turn.get("phase", "")),
            }
        return None

    def _validate_advance_turn_request(
        self,
        session: GameSession,
        turn: Dict[str, Any],
    ) -> Dict[str, Any] | None:
        if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
            return None
        current_id = str(turn.get("current_entity_id", "") or session.battle.get("turn_entity_id", "")).strip()
        if not current_id:
            return None
        owner_id = self._owner_player_id(session, current_id)
        requester_id = str(self.actor.get("player_id", "") or "")
        if owner_id and requester_id != owner_id:
            return {
                "ok": False,
                "error": "turn_advance_requires_owner_or_timeout",
                "message": "不能直接推进其他玩家的回合；若当前玩家超过 120 秒未响应，请改用 auto_act_current。",
                "current_entity_id": current_id,
                "owner_player_id": owner_id,
                "requester_player_id": requester_id,
                "deadline_at": str(turn.get("deadline_at", "")),
            }
        return None

    def _record_action(
        self,
        session: GameSession,
        turn: Dict[str, Any],
        entity_id: str,
        summary: str,
        source: str,
        reason: str = "",
    ) -> None:
        actions = dict(turn.get("actions_this_round") or {})
        actions[entity_id] = {
            "source": source,
            "summary": summary,
            "reason": reason,
        }
        turn["actions_this_round"] = actions
        self._append_turn_log(session, source, f"{entity_id}: {summary}", reason)

    def _reset_turn_timer(self, turn: Dict[str, Any], reason: str) -> None:
        now = utc_now_iso()
        timeout_seconds = TURN_TIMEOUT_SECONDS
        turn["timeout_seconds"] = timeout_seconds
        turn["waiting_since_at"] = now
        turn["deadline_at"] = (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()
        turn["wait_reset_reason"] = reason

    def _ensure_turn_timer(self, turn: Dict[str, Any], reason: str) -> bool:
        turn["timeout_seconds"] = TURN_TIMEOUT_SECONDS
        if _parse_datetime(turn.get("deadline_at")) is not None:
            return False
        self._reset_turn_timer(turn, reason)
        return True

    def _append_turn_log(self, session: GameSession, event_type: str, summary: str, reason: str = "") -> None:
        turn = self._ensure_turn_state(session)
        log = list(turn.get("turn_log") or [])
        log.append(
            {
                "at": utc_now_iso(),
                "round": turn.get("round", 0),
                "phase": turn.get("phase", ""),
                "type": event_type,
                "summary": summary[:240],
                "reason": reason[:160],
            }
        )
        turn["turn_log"] = log[-24:]

    def _default_auto_action(self, session: GameSession, entity_id: str, auto_policy: str) -> str:
        label = self._entity_label(session, entity_id)
        grid_entity = self._grid_entity(session, entity_id)
        faction = str((grid_entity or {}).get("faction", "")).lower()
        tags = dict((grid_entity or {}).get("tags", {}))
        if _has_any_status(tags, {"stunned", "down", "incapacitated", "眩晕", "倒地", "无法行动"}):
            return f"{label} 状态受限，本回合无法有效行动，只能防御并稳住呼吸。"
        if faction in {"enemy", "hostile", "monster"}:
            return f"{label} 按当前威胁保守推进，优先压制最近目标，但不触发新的复杂机制。"
        if auto_policy == "hold_position":
            return f"{label} 暂无响应，保持当前位置警戒并采取防御姿态。"
        return f"{label} 暂无响应，采取保守行动：跟随队伍、保持掩护，不消耗稀缺资源。"

    def _status(self, session: GameSession) -> Dict[str, Any]:
        turn = self._ensure_turn_state(session)
        current_id = str(turn.get("current_entity_id", "") or "")
        order = self._clean_order(list(turn.get("turn_order") or []))
        actions = dict(turn.get("actions_this_round") or {})
        pending = self._pending_entities(turn, order)
        result = {
            "ok": True,
            "turn": {
                "active": bool(turn.get("active", False)),
                "round": int(turn.get("round", 0) or 0),
                "phase": str(turn.get("phase", "idle")),
                "turn_order": list(turn.get("turn_order") or []),
                "current_index": _int_or_default(turn.get("current_index"), -1),
                "current_entity_id": current_id,
                "current_label": self._entity_label(session, current_id) if current_id else "",
                "current_owner_player_id": self._owner_player_id(session, current_id) if current_id else "",
                "output_limit_chars": int(turn.get("output_limit_chars", DEFAULT_TURN_OUTPUT_LIMIT_CHARS) or DEFAULT_TURN_OUTPUT_LIMIT_CHARS),
                "auto_policy": str(turn.get("auto_policy", "defend_or_follow")),
                "timeout_seconds": int(turn.get("timeout_seconds") or TURN_TIMEOUT_SECONDS),
                "waiting_since_at": str(turn.get("waiting_since_at", "")),
                "deadline_at": str(turn.get("deadline_at", "")),
                "actions_this_round": actions,
                "acted_entity_ids": [entity_id for entity_id in order if entity_id in actions],
                "pending_entity_ids": pending,
                "recent_turn_log": list(turn.get("turn_log") or [])[-8:],
            },
            "llm_instruction": self._instruction_for_turn(turn),
        }
        return result

    def _instruction_for_turn(self, turn: Dict[str, Any]) -> str:
        if not turn.get("active"):
            return "没有启用回合轮动；如进入冲突，先调用 turn_control start_round。"
        phase = turn.get("phase")
        limit = int(turn.get("output_limit_chars", DEFAULT_TURN_OUTPUT_LIMIT_CHARS) or DEFAULT_TURN_OUTPUT_LIMIT_CHARS)
        if phase == "scene_resolution":
            return (
                f"当前是场面结算阶段：必须主动推进敌方、环境、持续效果或士气/增援压力，"
                f"不能只宣布玩家获胜或战场安静；若敌人确实溃退/撤离，也要说明原因、代价和残余威胁。"
                f"不要结算玩家个人行动。回复不超过 {limit} 字，然后推进到角色回合。"
            )
        if phase == "character_turn":
            return f"当前是角色回合：current_entity_id 是建议/超时锚点；本轮未行动且归当前发言人所有的角色可以乱序行动。记录主要动作时用该角色 ID 调用 record_action，advance_after=true。120 秒从上一位完成行动后开始计算；若没有未行动玩家响应且锚点超时，调用 auto_act_current。回复不超过 {limit} 字。"
        return f"按当前阶段裁定；回复不超过 {limit} 字。"

    def _grid_entity(self, session: GameSession, entity_id: str) -> Dict[str, Any]:
        entities = dict(((session.battle or {}).get("grid") or {}).get("entities", {}))
        return dict(entities.get(entity_id, {}))

    def _entity_label(self, session: GameSession, entity_id: str) -> str:
        grid_entity = self._grid_entity(session, entity_id)
        if grid_entity:
            return str(grid_entity.get("name") or entity_id)
        character = session.characters.get(entity_id)
        if character:
            return character.name or character.id
        for character in session.characters.values():
            if character.id == entity_id:
                return character.name or character.id
        return entity_id

    def _owner_player_id(self, session: GameSession, entity_id: str) -> str:
        grid_entity = self._grid_entity(session, entity_id)
        tags = dict(grid_entity.get("tags", {}))
        if tags.get("player_id"):
            return str(tags["player_id"])
        character_id = str(tags.get("character_id", "") or entity_id)
        character = session.characters.get(character_id)
        if character and character.player_id:
            return character.player_id
        for player_id, bound_id in session.player_character_map.items():
            if bound_id == character_id or bound_id == entity_id:
                return player_id
        return ""

    def _audit(self, tool: str, input_payload: Dict[str, Any], result: Dict[str, Any]) -> None:
        self.repository.append_audit(
            self.session_id,
            {"type": "tool", "tool": tool, "input": input_payload, "result": result},
        )


def _has_any_status(tags: Dict[str, Any], values: set[str]) -> bool:
    status_text = " ".join(str(value).lower() for value in tags.values())
    return any(value.lower() in status_text for value in values)


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _looks_like_auto_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    terms = (
        "无人响应",
        "没人响应",
        "不响应",
        "未响应",
        "超时",
        "继续",
        "下一位",
        "下一个",
        "跳过",
        "待机",
        "防御",
        "放弃行动",
        "自动",
        "代管",
        "保守行动",
        "hold",
        "skip",
        "auto",
        "continue",
        "next",
    )
    return any(term in lowered for term in terms)


def _looks_like_timeout_push(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    info_terms = (
        "状态",
        "当前",
        "有什么",
        "武器",
        "地图",
        "日志",
        "token",
        "规则",
        "谁",
        "哪里",
        "在哪",
        "上一次",
        "debug",
        "消耗",
        "压缩",
        "画",
        "示意图",
        "看看",
        "查询",
    )
    push_terms = (
        "继续",
        "推进",
        "下一位",
        "下一个",
        "跳过",
        "超时",
        "无人响应",
        "没人响应",
        "不响应",
        "防御",
        "待机",
        "行动",
        "攻击",
        "移动",
        "施法",
        "射击",
        "结算",
        "我来",
        "我要",
        "我想",
        "开始",
        "快",
        "auto",
        "continue",
        "next",
        "skip",
    )
    has_push = any(term in lowered for term in push_terms)
    has_info = any(term in lowered for term in info_terms)
    return has_push and not has_info


def _looks_like_own_round_action(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    explicit_skip_current = (
        "跳过当前",
        "跳过他",
        "跳过她",
        "跳过它",
        "让他防御",
        "让她防御",
        "让它防御",
        "当前玩家超时",
        "当前角色超时",
        "当前行动者超时",
        "没人响应",
        "无人响应",
        "不响应",
    )
    if any(term in lowered for term in explicit_skip_current):
        return False
    action_terms = (
        "我",
        "我要",
        "我想",
        "我来",
        "去",
        "走",
        "跑",
        "冲",
        "移动",
        "靠近",
        "攻击",
        "射",
        "砍",
        "刺",
        "施法",
        "治疗",
        "防御",
        "掩护",
        "侦察",
        "侦查",
        "观察",
        "查看",
        "搜索",
        "调查",
        "警戒",
        "潜行",
        "检定",
        "判定",
        "示警",
        "叫醒",
        "拿",
        "捡",
        "使用",
        "点燃",
        "装填",
        "待机",
    )
    return any(term in lowered for term in action_terms)


def _looks_like_turn_info_only(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    action_terms = (
        "我要",
        "我想",
        "我来",
        "我发动",
        "我进行",
        "攻击",
        "移动",
        "冲",
        "射",
        "砍",
        "防御",
        "侦察",
        "观察",
        "搜索",
        "调查",
        "警戒",
        "检定",
        "判定",
    )
    if any(term in lowered for term in action_terms):
        return False
    info_terms = (
        "行动顺序",
        "战斗顺序",
        "轮动顺序",
        "当前轮次",
        "当前回合",
        "谁行动",
        "轮到谁",
        "状态",
        "战况",
        "剧情",
        "汇报",
        "地图",
        "token",
        "上下文",
        "规则列表",
        "有哪些规则",
        "日志",
        "debug",
    )
    return any(term in lowered for term in info_terms)


def _looks_like_terminal_encounter_end(summary: str, reason: str, session: GameSession) -> bool:
    text = f"{summary}\n{reason}\n{(session.scene or {}).get('summary', '')}\n{(session.scene or {}).get('current_conflict', '')}"
    lowered = str(text or "").lower()
    terminal_terms = (
        "战斗已实质结束",
        "遭遇已结束",
        "危机已正式解除",
        "危机已落下帷幕",
        "最终结局",
        "全局结算",
        "圆满落幕",
        "圆满结束",
        "正式落幕",
        "进入间幕",
        "休息一会",
        "休整",
        "重建",
        "暂无冲突",
    )
    return any(term in lowered for term in terminal_terms)


def _parse_datetime(value: Any) -> datetime | None:
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
