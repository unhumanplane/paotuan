import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from time import monotonic
from typing import List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.message.components import Image as ImageComponent, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.plugin_log import configure_plugin_logging
from .core.router import IntentRouter
from .core.security import security_precheck
from .rules.python_runtime import PythonRuleRuntime
from .storage.json_repository import JsonGameRepository
from .tools.diagnostic_tools import DiagnosticTools
from .tools.memory_tools import MemoryTools, has_campaign_background
from .tools.registry import ToolRegistry
from .tools.turn_tools import TurnTools


@register(
    "auto_trpg_dm",
    "codex",
    "全自然语言 TRPG DM：动态规则、战棋物理验证、Tag 角色卡与自动剧本。",
    "0.1.39",
)
class AutoTrpgDmPlugin(Star):
    DEDUP_WINDOW_SECONDS = 18.0
    ACTION_PACING_SECONDS = 12
    HEARTBEAT_INTERVAL_SECONDS = 60

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.astr_context = context
        self.trigger_prefixes = ["/dm"]
        self._recent_dm_messages: dict[tuple[str, str, str], float] = {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_auto_trpg_dm"
        self.repository = JsonGameRepository(data_dir)
        self.plugin_logger = configure_plugin_logging(self.repository.plugin_log_path())
        rule_runtime = PythonRuleRuntime(data_dir / "rules")
        tool_registry = ToolRegistry(repository=self.repository, rule_runtime=rule_runtime, astr_context=context)
        self.router = IntentRouter(
            astr_context=context,
            repository=self.repository,
            tool_registry=tool_registry,
        )
        migrated = self._migrate_legacy_turn_fields()
        if migrated:
            self.plugin_logger.info("legacy_turn_fields_migrated saves=%s", migrated)
        character_migrations = self._migrate_character_bindings()
        if character_migrations:
            self.plugin_logger.info("character_bindings_migrated changes=%s", character_migrations)
        live_scene_migrations = self._migrate_legacy_live_scene_state()
        if live_scene_migrations:
            self.plugin_logger.info("legacy_live_scene_state_migrated saves=%s", live_scene_migrations)
        self._heartbeat_task: asyncio.Task | None = None
        self._start_heartbeat_task()
        self.plugin_logger.info("plugin_initialized version=0.1.39 data_dir=%s", data_dir)
        logger.info("Auto TRPG DM plugin initialized.")

    @filter.command("dm")
    async def on_dm_command(self, event: AstrMessageEvent, content: GreedyStr):
        """唯一显式入口：/dm 后面全部交给自然语言路由。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("DM")
    async def on_dm_command_upper(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /DM，避免请求落到 AstrBot 默认聊天。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("Dm")
    async def on_dm_command_title(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /Dm。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("dM")
    async def on_dm_command_mixed(self, event: AstrMessageEvent, content: GreedyStr):
        """兼容玩家误用 /dM。"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    async def _handle_dm_command_content(self, event: AstrMessageEvent, content: GreedyStr):
        routed_message = str(content or "").strip()
        if not routed_message:
            routed_message = "查看当前跑团状态；如果还没有开局，请询问玩家想跑什么类型的团。"
        async for result in self._handle_dm_event(event, routed_message):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """只接收显式 /dm 入口，避免普通群聊进入 LLM。"""
        message = (event.message_str or "").strip()
        if not message:
            return
        routed_message = self._extract_routed_message(event, message)
        if not routed_message:
            return
        async for result in self._handle_dm_event(event, routed_message):
            yield result

    async def _handle_dm_event(self, event: AstrMessageEvent, routed_message: str):
        session_id = IntentRouter.session_id_for_event(event)
        actor = self.router.actor_context_for_event(event)
        sender_id = actor.get("player_id", "")
        fast_reply = await self._local_fast_path(session_id, actor, routed_message)
        if fast_reply:
            self.plugin_logger.info(
                "dm_fast_path session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            yield self._quoted_result(event, fast_reply)
            event.stop_event()
            return
        duplicate_reply = self._duplicate_reply(session_id, sender_id, routed_message)
        if duplicate_reply:
            self.plugin_logger.info(
                "duplicate_dm_ignored session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            yield self._quoted_result(event, duplicate_reply)
            event.stop_event()
            return
        pacing_reply = self._action_pacing_reply(session_id, actor, routed_message)
        if pacing_reply:
            self.plugin_logger.info(
                "action_pacing_blocked session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            yield self._quoted_result(event, pacing_reply)
            event.stop_event()
            return
        security = security_precheck(routed_message)
        if security.blocked:
            self.plugin_logger.info(
                "dm_received session=%s sender=%s risk=%s text=<blocked>",
                session_id,
                sender_id,
                security.risk,
            )
            logger.info(
                "Auto TRPG DM received /dm message. session=%s sender=%s text=<blocked>",
                session_id,
                sender_id,
            )
        else:
            self.plugin_logger.info(
                "dm_received session=%s sender=%s text=%s",
                session_id,
                sender_id,
                security.redacted_message[:240].replace("\n", "\\n"),
            )
            logger.info(
                "Auto TRPG DM handling /dm message. session=%s sender=%s text=%s",
                session_id,
                sender_id,
                security.redacted_message[:120],
            )
        if security.blocked:
            self.plugin_logger.warning(
                "security_block session=%s sender=%s risk=%s categories=%s",
                session_id,
                sender_id,
                security.risk,
                ",".join(security.categories),
            )
            logger.warning(
                "Auto TRPG DM blocked unsafe /dm message. session=%s sender=%s risk=%s categories=%s",
                session_id,
                sender_id,
                security.risk,
                ",".join(security.categories),
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "security_block",
                    "actor": actor,
                    **security.to_audit_record(),
                },
            )
            yield self._quoted_result(event, security.reply)
            event.stop_event()
            return
        if security.notes:
            self.plugin_logger.info(
                "security_note session=%s sender=%s categories=%s notes=%s",
                session_id,
                sender_id,
                ",".join(security.categories),
                len(security.notes),
            )
            logger.info(
                "Auto TRPG DM marked /dm message for guarded adjudication. session=%s sender=%s categories=%s",
                session_id,
                sender_id,
                ",".join(security.categories),
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "security_note",
                    "actor": actor,
                    **security.to_audit_record(),
                },
            )
        gate_reply = self._cycle_state_gate(session_id, actor, routed_message)
        if gate_reply:
            self.plugin_logger.info(
                "cycle_state_gate_blocked session=%s sender=%s cycle_state=%s",
                session_id,
                sender_id,
                getattr(self.repository.load_session(session_id), "cycle_state", None),
            )
            yield self._quoted_result(event, gate_reply)
            event.stop_event()
            return
        try:
            completion = await self.router.handle_message(
                event,
                message_override=routed_message,
                security_notes=security.notes,
            )
        except Exception as exc:
            self.plugin_logger.exception("dm_failed session=%s sender=%s error=%s", session_id, sender_id, exc)
            logger.exception("Auto TRPG DM failed to handle message.")
            yield self._quoted_result(event, self._friendly_error_message(exc))
            event.stop_event()
            return
        pending_outputs = self._pop_pending_outputs(session_id)
        dice_outputs = [item for item in pending_outputs if item.get("type") == "dice_check"]
        other_outputs = [item for item in pending_outputs if item.get("type") != "dice_check"]
        sent_any = False
        for item in dice_outputs[:3]:
            dice_text = self._format_dice_check(item)
            if dice_text:
                yield self._quoted_result(event, dice_text)
                sent_any = True
        if completion or other_outputs:
            if not completion and other_outputs:
                completion = "地图已生成，已附上。"
            self.plugin_logger.info(
                "dm_completed session=%s sender=%s reply_chars=%s pending_outputs=%s",
                session_id,
                sender_id,
                len(completion),
                len(pending_outputs),
            )
            yield self._quoted_result(event, completion, pending_outputs=other_outputs)
            sent_any = True
        if sent_any:
            event.stop_event()

    async def _local_fast_path(self, session_id: str, actor: dict[str, str], routed_message: str) -> str:
        text = self._dedupe_text(routed_message)
        normalized = text.lower()
        session = self.repository.load_session(session_id)
        paused = bool((session.scene or {}).get("_dm_paused", False))

        if normalized in {"pause", "暂停", "暂停流程", "暂停游戏"}:
            session.scene["_dm_paused"] = True
            session.scene["_dm_pause_reason"] = text
            session.scene["_dm_paused_by"] = actor
            session.scene["_dm_paused_at"] = _utc_now_iso()
            self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "pause", "actor": actor})
            return "流程已暂停。我不会推进轮次、替人行动或调用模型；需要继续时发 `/dm resume` 或 `/dm 恢复`。"

        if normalized in {"resume", "unpause", "恢复", "继续流程", "解除暂停"} or (paused and normalized == "继续"):
            if paused:
                session.scene["_dm_paused"] = False
                session.scene["_dm_resumed_by"] = actor
                session.scene["_dm_resumed_at"] = _utc_now_iso()
                self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "resume", "actor": actor})
            return "流程已恢复。下一句 `/dm` 会按当前存档继续裁定。"

        if _looks_like_backup_list_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "list_backups",
                reason=text,
            )
            self.repository.append_audit(
                session_id,
                {"type": "local_fast_path", "action": "backup_list", "actor": actor, "result": result},
            )
            backups = result.get("backups") or []
            if not backups:
                return "当前还没有自动备份。"
            lines = ["最近备份："]
            for item in backups[:5]:
                size = int(item.get("size") or 0)
                lines.append(f"- {item.get('mtime', '')}；{size // 1024}K；{item.get('reason', '') or item.get('name', '')}")
            return "\n".join(lines)

        if _looks_like_manual_backup_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "create_backup",
                reason=text,
            )
            self.plugin_logger.info(
                "dm_backup_created session=%s sender=%s ok=%s path=%s",
                session_id,
                actor.get("player_id", ""),
                result.get("ok"),
                result.get("backup_path", ""),
            )
            return str(result.get("message") or "备份请求已处理。")

        if _looks_like_restore_latest_backup_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "restore_latest_backup",
                reason=text,
            )
            self.plugin_logger.info(
                "dm_restore_latest_backup session=%s sender=%s ok=%s error=%s",
                session_id,
                actor.get("player_id", ""),
                result.get("ok"),
                result.get("error", ""),
            )
            return str(result.get("message") or "恢复请求已处理。")

        reset_token = _extract_reset_confirmation_token(text)
        if reset_token:
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "confirm_reset",
                reason=text,
                confirm_token=reset_token,
            )
            self.plugin_logger.info(
                "dm_reset_confirmation session=%s sender=%s ok=%s action=%s",
                session_id,
                actor.get("player_id", ""),
                result.get("ok"),
                result.get("action"),
            )
            return str(result.get("message") or "存档未改动。")

        if _looks_like_reset_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "reset",
                reason=text,
            )
            self.plugin_logger.info(
                "dm_reset_requested session=%s sender=%s action=%s",
                session_id,
                actor.get("player_id", ""),
                result.get("action"),
            )
            return str(result.get("message") or "重开需要二次确认，存档暂未改动。")

        if normalized in {"status", "状态", "当前状态"}:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "status", "actor": actor})
            return self._format_local_status(session)

        if normalized in {"token", "tokens", "token消耗", "上下文", "上下文消耗"}:
            usage = await DiagnosticTools(self.repository, session_id).estimate_token_usage("summary")
            current = usage.get("current", {})
            rough = usage.get("rough_token_estimate", {})
            compression = usage.get("compression", {})
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "token", "actor": actor})
            return (
                "Token 粗算："
                f"快照 {current.get('compact_snapshot_chars', 0)} 字，约 {rough.get('heuristic', 0)} token；"
                f"完整存档 {current.get('full_save_chars', 0)} 字。"
                f"距自动压缩约 {compression.get('snapshot_chars_remaining_before_compression', 0)} 字。"
            )

        if normalized in {"当前轮次", "当前回合", "轮次", "回合", "谁行动", "轮到谁", "行动顺序", "战斗顺序", "轮动顺序"} or _looks_like_turn_status_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "turn_status", "actor": actor})
            return self._format_turn_status(session, include_order=_looks_like_turn_order_request(text))

        turn_reply = await self._local_turn_fast_path(session_id, session, actor, text)
        if turn_reply:
            return turn_reply

        if _looks_like_player_roster_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "player_roster", "actor": actor})
            return self._format_player_roster(session)

        if _campaign_plot_locked(session) and _looks_like_plot_rewrite_request(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "plot_locked_after_start",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return "游戏已经开场，背景、题材和主线已锁定；我不会把这句当成改剧本请求。你可以声明角色行动、调查目标，或作为新玩家加入。"

        if paused:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "paused_block", "actor": actor})
            return "当前流程处于暂停状态，我不会把这句送进模型。可用 `/dm status`、`/dm token`、`/dm 当前轮次` 查看信息，或 `/dm resume` 恢复。"

        if (
            not has_campaign_background(session)
            and not _looks_like_background_authoring_request(text)
            and not _looks_like_enough_background_seed(text)
        ):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "background_required",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return (
                "先定背景，再写剧本、角色卡或战场。"
                "请至少给两类要素：题材/基调/开场前提/地点/势力/规则。"
                "例：废土科幻，荒诞危险，众人被异常求救信号引到旧中继站。"
            )

        return ""

    async def _local_turn_fast_path(self, session_id: str, session, actor: dict[str, str], text: str) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
            return ""
        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "").strip()
        if not current_id:
            return ""
        entities = dict((battle.get("grid") or {}).get("entities") or {})
        current_label = _entity_label(session, current_id, entities)
        owner_id = _entity_owner(session, current_id, entities)
        actor_id = str(actor.get("player_id") or "").strip()
        actor_pending_id = _pending_entity_for_actor(session, turn, actor_id, entities)

        if _looks_like_local_turn_end(text):
            acting_id = actor_pending_id or (current_id if (not owner_id or actor_id == owner_id) else "")
            if not acting_id:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "turn_end_denied",
                        "actor": actor,
                        "current_entity_id": current_id,
                        "owner_player_id": owner_id,
                        "text": text[:160],
                    },
                )
                return self._format_turn_waiting(session, turn, current_id, current_label, owner_id)
            if acting_id:
                acting_label = _entity_label(session, acting_id, entities)
                acting_owner_id = _entity_owner(session, acting_id, entities)
                summary = _local_turn_end_summary(text, acting_label)
                result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
                    action="record_action",
                    current_entity_id=acting_id,
                    summary=summary,
                    reason=f"玩家本地直路由声明：{text[:80]}",
                    output_limit_chars=180,
                    advance_after=True,
                )
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "turn_end_advanced",
                        "actor": actor,
                        "current_entity_id": acting_id,
                        "owner_player_id": acting_owner_id,
                        "suggested_current_entity_id": current_id,
                        "summary": summary,
                        "result": result,
                    },
                )
                return self._format_turn_advance_result(result, f"{acting_label}本回合结束。")

        if _looks_like_local_turn_push(text):
            if owner_id and actor_id == owner_id:
                summary = f"{current_label}声明结束本回合，保持当前态势。"
                result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
                    action="record_action",
                    current_entity_id=current_id,
                    summary=summary,
                    reason=f"当前行动者请求推进：{text[:80]}",
                    output_limit_chars=180,
                    advance_after=True,
                )
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "owner_turn_push_advanced",
                        "actor": actor,
                        "current_entity_id": current_id,
                        "owner_player_id": owner_id,
                        "result": result,
                    },
                )
                return self._format_turn_advance_result(result, f"{current_label}本回合结束。")
            deadline = _parse_datetime(turn.get("deadline_at"))
            now = datetime.now(timezone.utc)
            if deadline is not None and now >= deadline:
                elapsed = int((now - (_parse_datetime(turn.get("waiting_since_at")) or deadline)).total_seconds())
                summary = (
                    f"{current_label}超过 120 秒未响应，采取保守行动："
                    "保持警戒、防御或跟随队伍，不消耗稀缺资源。"
                )
                result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
                    action="auto_act_current",
                    current_entity_id=current_id,
                    summary=summary,
                    reason=f"其他玩家请求推进且已超时约 {max(120, elapsed)} 秒：{text[:80]}",
                    output_limit_chars=180,
                    auto_policy="defend_or_follow",
                    advance_after=True,
                )
                timeout_pause = self._apply_turn_timeout_pause_if_needed(
                    session_id,
                    session,
                    turn,
                    current_id,
                    entities,
                    result,
                    source="local_fast_path_timeout",
                    actor=actor,
                )
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "turn_timeout_auto_advanced",
                        "actor": actor,
                        "current_entity_id": current_id,
                        "owner_player_id": owner_id,
                        "result": result,
                        "timeout_pause": timeout_pause,
                    },
                )
                reply = self._format_turn_advance_result(result, summary)
                if timeout_pause.get("auto_paused"):
                    reply += "\n" + self._format_timeout_pause_line(timeout_pause)
                return reply
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "turn_push_waiting",
                    "actor": actor,
                    "current_entity_id": current_id,
                    "owner_player_id": owner_id,
                    "text": text[:160],
                },
            )
            return self._format_turn_waiting(session, turn, current_id, current_label, owner_id)

        return ""

    def _format_turn_waiting(self, session, turn: dict, current_id: str, current_label: str, owner_id: str) -> str:
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline:
            remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
        else:
            remaining = int(turn.get("timeout_seconds") or 120)
        owner_name = str((session.participants.get(owner_id) or {}).get("display_name") or owner_id or "未绑定")
        return f"当前建议等待 {current_label}（{owner_name}），剩余约 {remaining} 秒；本轮未行动的本人角色可以直接声明行动，超时后可用 `/dm 下一位` 保守推进锚点。"

    def _format_turn_advance_result(self, result: dict, prefix: str) -> str:
        if not result.get("ok"):
            return str(result.get("message") or f"轮次推进失败：{result.get('error', 'unknown_error')}")
        turn = dict(result.get("turn") or {})
        phase = str(turn.get("phase") or "")
        if phase == "scene_resolution":
            return f"{prefix}\n进入第 {turn.get('round', '?')} 轮场面结算。"
        current_label = str(turn.get("current_label") or turn.get("current_entity_id") or "未指定")
        return f"{prefix}\n建议行动：{current_label}；本轮未行动者也可直接行动。"

    def _format_local_status(self, session) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        paused = "暂停中" if (session.scene or {}).get("_dm_paused") else "运行中"
        if turn.get("active"):
            turn_text = self._format_turn_status(session)
        else:
            turn_text = "当前没有启用轮次。"
        return (
            f"团名：{session.title}；模式：{session.mode.value}；流程：{paused}；开场：{_game_started_text(session)}。\n"
            f"玩家 {len(session.participants)}，角色 {len(session.characters)}，规则 {len(session.rules)}。\n"
            f"{turn_text}"
        )

    def _format_turn_status(self, session, include_order: bool = False) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active"):
            return "当前没有启用轮次。"
        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
        entities = dict((battle.get("grid") or {}).get("entities") or {})
        label = _entity_label(session, current_id, entities) if current_id else "未指定"
        owner_id = _entity_owner(session, current_id, entities) if current_id else ""
        owner_name = str((session.participants.get(owner_id) or {}).get("display_name") or owner_id or "未绑定")
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline:
            remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
            wait_text = f"等待剩余约 {remaining} 秒。"
        else:
            wait_text = "等待计时会在下一次相关 /dm 时补齐为 120 秒。"
        base = (
            f"第 {int(turn.get('round') or 0)} 轮，阶段：{turn.get('phase', 'idle')}。\n"
            f"建议行动/超时锚点：{label}（{current_id or '无'}），持有人：{owner_name}。\n"
            f"{wait_text}"
        )
        if not include_order:
            return base
        order = list(turn.get("turn_order") or [])
        if not order:
            return base
        actions = dict(turn.get("actions_this_round") or {})
        order_labels = []
        for index, entity_id in enumerate(order, start=1):
            entity_label = _entity_label(session, str(entity_id), entities)
            marker = "（当前）" if str(entity_id) == current_id else ""
            acted = "已行动" if str(entity_id) in actions else "未行动"
            order_labels.append(f"{index}. {entity_label}{marker}：{acted}")
        return base + "\n" + "\n".join(order_labels)

    def _format_player_roster(self, session) -> str:
        participants = session.participants or {}
        if not participants:
            return "当前还没有登记玩家。"
        lines = ["玩家登记："]
        for index, (player_id, participant) in enumerate(participants.items(), start=1):
            display_name = str(participant.get("display_name") or player_id)
            character_id = str(session.player_character_map.get(player_id, "") or "")
            character = session.characters.get(character_id) if character_id else None
            if character:
                character_name = character.name or character.id
                lines.append(f"{index}. {display_name}（{player_id}） -> {character_name} [{character.id}]")
            else:
                lines.append(f"{index}. {display_name}（{player_id}） -> 未绑定角色")
        return "\n".join(lines)

    def _action_pacing_reply(self, session_id: str, actor: dict[str, str], routed_message: str) -> str:
        text = self._dedupe_text(routed_message)
        player_id = str(actor.get("player_id") or "").strip()
        if not player_id or not _looks_like_paced_player_action(text):
            return ""
        session = self.repository.load_session(session_id)
        if not _campaign_action_pacing_enabled(session):
            return ""
        now = datetime.now(timezone.utc)
        pacing = dict((session.scene or {}).get("_action_pacing") or {})
        record = dict(pacing.get(player_id) or {})
        last_at = _parse_datetime(record.get("last_action_at"))
        if last_at:
            elapsed = int((now - last_at).total_seconds())
            remaining = self.ACTION_PACING_SECONDS - elapsed
            if remaining > 0:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "action_pacing_throttled",
                        "actor": actor,
                        "text": text[:240],
                        "last_action": str(record.get("last_action") or "")[:160],
                        "elapsed_seconds": elapsed,
                        "remaining_seconds": remaining,
                    },
                )
                return (
                    f"你的上一段行动还在场内时间里结算，先等约 {remaining} 秒。"
                    "如果是补充说明，请把它合并到上一动作；如果只是查状态，可以用 `/dm status` 或 `/dm 当前轮次`。"
                )
        pacing[player_id] = {
            "last_action_at": now.isoformat(),
            "last_action": text[:160],
        }
        session.scene["_action_pacing"] = pacing
        self.repository.save_session(session)
        self.plugin_logger.info(
            "action_pacing_recorded session=%s sender=%s cooldown=%s text=%s",
            session_id,
            player_id,
            self.ACTION_PACING_SECONDS,
            text[:160],
        )
        self.repository.append_audit(
            session_id,
            {
                "type": "action_pacing_recorded",
                "actor": actor,
                "text": text[:240],
                "cooldown_seconds": self.ACTION_PACING_SECONDS,
            },
        )
        return ""

    def _cycle_state_gate(self, session_id: str, actor: dict[str, str], message: str) -> str:
        session = self.repository.load_session(session_id)
        cycle_state = getattr(session, "cycle_state", None)
        if cycle_state is None:
            return ""
        from astrbot_plugin_auto_trpg_dm.core.models import CycleState
        from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
        if cycle_state == CycleState.CYCLE_RESOLVING:
            CycleStateMachine.transition(session, CycleState.CYCLE_ACTIVE)
            self.repository.save_session(session)
            self.repository.append_audit(
                session_id,
                {
                    "type": "cycle_state_gate_short_circuit",
                    "from_state": "CYCLE_RESOLVING",
                    "to_state": "CYCLE_ACTIVE",
                    "reason": "RA not yet implemented (PR 3)",
                    "actor": actor,
                    "message": message[:240],
                },
            )
            self.plugin_logger.info(
                "cycle_state_gate_short_circuit session=%s from=RESOLVING to=ACTIVE",
                session_id,
            )
            return ""
        if cycle_state == CycleState.CYCLE_TRANSITION:
            CycleStateMachine.transition(session, CycleState.CYCLE_ACTIVE)
            self.repository.save_session(session)
            self.repository.append_audit(
                session_id,
                {
                    "type": "cycle_state_gate_short_circuit",
                    "from_state": "CYCLE_TRANSITION",
                    "to_state": "CYCLE_ACTIVE",
                    "reason": "RA not yet implemented (PR 3)",
                    "actor": actor,
                    "message": message[:240],
                },
            )
            self.plugin_logger.info(
                "cycle_state_gate_short_circuit session=%s from=TRANSITION to=ACTIVE",
                session_id,
            )
            return ""
        return ""

    def _migrate_legacy_turn_fields(self) -> int:
        migrated = 0
        now = datetime.now(timezone.utc)
        for path in self.repository.saves_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.plugin_logger.warning("legacy_turn_migration_read_failed path=%s error=%s", path, exc)
                continue
            battle = data.get("battle")
            if not isinstance(battle, dict):
                continue
            turn = battle.get("turn")
            if not isinstance(turn, dict) or not turn.get("active"):
                continue
            changed = False
            if battle.get("active") is not True:
                battle["active"] = True
                changed = True
            if data.get("mode") != "tactical":
                data["mode"] = "tactical"
                changed = True
            if not turn.get("timeout_seconds"):
                turn["timeout_seconds"] = 120
                changed = True
            if str(turn.get("phase", "")) == "character_turn":
                current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
                if current_id and not battle.get("turn_entity_id"):
                    battle["turn_entity_id"] = current_id
                    changed = True
                if not turn.get("waiting_since_at"):
                    turn["waiting_since_at"] = now.isoformat()
                    changed = True
                if not turn.get("deadline_at"):
                    turn["deadline_at"] = (now + timedelta(seconds=120)).isoformat()
                    changed = True
                if not turn.get("wait_reset_reason"):
                    turn["wait_reset_reason"] = "legacy_migration"
                    changed = True
            if not changed:
                continue
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            migrated += 1
            session_id = str(data.get("session_id") or path.stem)
            try:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "legacy_turn_fields_migrated",
                        "save": path.name,
                        "battle_active": battle.get("active"),
                        "mode": data.get("mode"),
                        "timeout_seconds": turn.get("timeout_seconds"),
                        "deadline_at": turn.get("deadline_at", ""),
                    },
                )
            except Exception as exc:
                self.plugin_logger.warning("legacy_turn_migration_audit_failed path=%s error=%s", path, exc)
        return migrated

    def _migrate_character_bindings(self) -> int:
        migrated = 0
        for path in self.repository.saves_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session_id = str(data.get("session_id") or path.stem)
                session = self.repository.load_session(session_id)
            except Exception as exc:
                self.plugin_logger.warning("character_binding_migration_read_failed path=%s error=%s", path, exc)
                continue
            changed = False
            events: list[dict] = []

            for character_id, character in list(session.characters.items()):
                owner_id = str(character.player_id or "").strip()
                if not owner_id:
                    continue
                if _is_legacy_generic_character_id(character_id):
                    new_id = _default_pc_id_for_player(owner_id)
                    if new_id and new_id not in session.characters:
                        old_id = character_id
                        character.id = new_id
                        session.characters[new_id] = character
                        del session.characters[old_id]
                        for player_id, bound_id in list(session.player_character_map.items()):
                            if bound_id == old_id:
                                session.player_character_map[player_id] = new_id
                        if session.active_character_id == old_id:
                            session.active_character_id = new_id
                        _rename_battle_character_refs(session.battle, old_id, new_id)
                        events.append(
                            {
                                "type": "character_id_migrated",
                                "old_character_id": old_id,
                                "new_character_id": new_id,
                                "owner_player_id": owner_id,
                            }
                        )
                        changed = True
                        migrated += 1
                    elif new_id:
                        events.append(
                            {
                                "type": "character_id_migration_skipped",
                                "character_id": character_id,
                                "target_character_id": new_id,
                                "owner_player_id": owner_id,
                                "reason": "target_exists",
                            }
                        )

            for character_id, character in list(session.characters.items()):
                owner_id = str(character.player_id or "").strip()
                if not owner_id:
                    continue
                bound_id = str(session.player_character_map.get(owner_id, "") or "")
                if not bound_id or bound_id not in session.characters:
                    session.player_character_map[owner_id] = character_id
                    events.append(
                        {
                            "type": "player_character_binding_repaired",
                            "player_id": owner_id,
                            "character_id": character_id,
                            "previous_character_id": bound_id,
                        }
                    )
                    changed = True
                    migrated += 1
                if owner_id not in session.participants:
                    session.participants[owner_id] = {
                        "player_id": owner_id,
                        "display_name": owner_id,
                        "platform": "",
                        "last_seen_at": "",
                    }
                    events.append(
                        {
                            "type": "participant_repaired_from_character",
                            "player_id": owner_id,
                            "character_id": character_id,
                        }
                    )
                    changed = True
                    migrated += 1

            if not changed:
                continue
            try:
                self.repository.save_session(session)
                for event in events:
                    self.repository.append_audit(session.session_id, event)
            except Exception as exc:
                self.plugin_logger.warning("character_binding_migration_write_failed path=%s error=%s", path, exc)
        return migrated

    def _migrate_legacy_live_scene_state(self) -> int:
        migrated = 0
        for path in self.repository.saves_dir.glob("*.json"):
            try:
                session_id = path.stem
                data = json.loads(path.read_text(encoding="utf-8"))
                session_id = str(data.get("session_id") or session_id)
                session = self.repository.load_session(session_id)
            except Exception as exc:
                self.plugin_logger.warning("legacy_live_scene_migration_read_failed path=%s error=%s", path, exc)
                continue
            if not _campaign_action_pacing_enabled(session):
                continue
            scene = session.scene or {}
            summary = str(scene.get("summary") or "")
            if scene.get("_game_started") or scene.get("_legacy_live_campaign"):
                continue
            if summary and not summary.startswith("尚未开局"):
                continue
            scene["_legacy_live_campaign"] = True
            scene["_legacy_live_campaign_marked_at"] = _utc_now_iso()
            scene["summary"] = "跑团已在进行；这是旧存档兼容标记，具体事实以最近事件、角色状态、战棋与裁定记录为准。"
            session.scene = scene
            try:
                self.repository.save_session(session)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "legacy_live_scene_state_migrated",
                        "save": path.name,
                    },
                )
                migrated += 1
            except Exception as exc:
                self.plugin_logger.warning("legacy_live_scene_migration_write_failed path=%s error=%s", path, exc)
        return migrated

    async def terminate(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        self.plugin_logger.info("plugin_terminated")
        logger.info("Auto TRPG DM plugin terminated.")

    def _start_heartbeat_task(self) -> None:
        try:
            self._heartbeat_task = asyncio.create_task(self._turn_heartbeat_loop())
            self.plugin_logger.info("turn_heartbeat_started interval_seconds=%s", self.HEARTBEAT_INTERVAL_SECONDS)
        except RuntimeError as exc:
            self.plugin_logger.warning("turn_heartbeat_start_failed error=%s", exc)

    async def _turn_heartbeat_loop(self) -> None:
        await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
        while True:
            try:
                await self._run_turn_heartbeat_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.plugin_logger.exception("turn_heartbeat_failed error=%s", exc)
            await asyncio.sleep(self.HEARTBEAT_INTERVAL_SECONDS)

    async def _run_turn_heartbeat_once(self) -> None:
        scanned = 0
        active = 0
        advanced = 0
        initialized = 0
        notified = 0
        auto_paused = 0
        for path in sorted(self.repository.saves_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session_id = str(data.get("session_id") or path.stem)
            except Exception as exc:
                self.plugin_logger.warning("turn_heartbeat_read_failed path=%s error=%s", path, exc)
                continue
            scanned += 1
            turn_lock = self.router._turn_lock_for_session(session_id)
            session_lock = self.router._lock_for_session(session_id)
            async with turn_lock:
                async with session_lock:
                    result = await self._heartbeat_check_session(session_id)
            if result.get("active"):
                active += 1
            if result.get("advanced"):
                advanced += 1
            if result.get("initialized"):
                initialized += 1
            if result.get("auto_paused"):
                auto_paused += 1
            notice = str(result.get("notice") or "").strip()
            if notice and await self._send_heartbeat_message(session_id, notice):
                notified += 1
        self.plugin_logger.info(
            "turn_heartbeat_tick scanned=%s active=%s advanced=%s initialized=%s notified=%s auto_paused=%s",
            scanned,
            active,
            advanced,
            initialized,
            notified,
            auto_paused,
        )

    async def _heartbeat_check_session(self, session_id: str) -> dict[str, object]:
        session = self.repository.load_session(session_id)
        if bool((session.scene or {}).get("_dm_paused", False)):
            return {"active": False, "paused": True}
        battle = session.battle or {}
        turn = battle.get("turn")
        if not isinstance(turn, dict) or not turn.get("active"):
            return {"active": False}
        phase = str(turn.get("phase") or "")
        if phase not in {"character_turn", "scene_resolution"}:
            return {"active": True, "phase": phase}
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline is None:
            now = datetime.now(timezone.utc)
            turn["timeout_seconds"] = 120
            turn["waiting_since_at"] = now.isoformat()
            turn["deadline_at"] = (now + timedelta(seconds=120)).isoformat()
            turn["wait_reset_reason"] = "heartbeat_initialized_missing_deadline"
            self.repository.save_session(session)
            self.repository.append_audit(
                session_id,
                {
                    "type": "turn_heartbeat_timer_initialized",
                    "phase": phase,
                    "deadline_at": turn.get("deadline_at", ""),
                },
            )
            self.plugin_logger.info(
                "turn_heartbeat_timer_initialized session=%s phase=%s deadline=%s",
                session_id,
                phase,
                turn.get("deadline_at", ""),
            )
            return {"active": True, "initialized": True, "phase": phase}
        now = datetime.now(timezone.utc)
        if now < deadline:
            return {"active": True, "phase": phase}

        if phase == "scene_resolution":
            result = await TurnTools(self.repository, session_id, actor={"player_id": "__heartbeat__", "display_name": "本地心跳"}).turn_control(
                action="finish_scene_resolution",
                reason="本地心跳检查：场面结算阶段超过 120 秒未推进，自动进入下一角色回合。",
                output_limit_chars=int(turn.get("output_limit_chars") or 180),
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "turn_heartbeat_scene_resolution_advanced",
                    "deadline_at": deadline.isoformat(),
                    "result": result,
                },
            )
            self.plugin_logger.info(
                "turn_heartbeat_scene_resolution_advanced session=%s ok=%s next=%s",
                session_id,
                result.get("ok"),
                (result.get("turn") or {}).get("current_entity_id", ""),
            )
            notice = ""
            if result.get("ok"):
                notice = (
                    "场面结算超时：超过 120 秒未推进，已由本地心跳进入下一步。\n"
                    + self._format_turn_destination(self.repository.load_session(session_id))
                )
            return {"active": True, "advanced": bool(result.get("ok")), "phase": phase, "notice": notice}

        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "").strip()
        if not current_id:
            return {"active": True, "phase": phase, "missing_current": True}
        entities = dict((battle.get("grid") or {}).get("entities") or {})
        current_label = _entity_label(session, current_id, entities)
        waiting_since = _parse_datetime(turn.get("waiting_since_at")) or deadline
        elapsed = max(120, int((now - waiting_since).total_seconds()))
        summary = f"{current_label}超过 120 秒未响应，本地心跳采取保守行动：防御、保持掩体或跟随队伍，不消耗稀缺资源。"
        result = await TurnTools(
            self.repository,
            session_id,
            actor={"player_id": "__heartbeat__", "display_name": "本地心跳"},
        ).turn_control(
            action="auto_act_current",
            current_entity_id=current_id,
            summary=summary,
            reason=f"本地心跳检查：当前行动者从轮到自己起已等待约 {elapsed} 秒，deadline 已过，自动保守推进。",
            output_limit_chars=int(turn.get("output_limit_chars") or 180),
            auto_policy="defend_or_follow",
            advance_after=True,
        )
        timeout_pause = self._apply_turn_timeout_pause_if_needed(
            session_id,
            session,
            turn,
            current_id,
            entities,
            result,
            source="heartbeat_timeout",
            actor={"player_id": "__heartbeat__", "display_name": "本地心跳"},
        )
        self.repository.append_audit(
            session_id,
            {
                "type": "turn_heartbeat_auto_advanced",
                "current_entity_id": current_id,
                "current_label": current_label,
                "deadline_at": deadline.isoformat(),
                "elapsed_seconds": elapsed,
                "result": result,
                "timeout_pause": timeout_pause,
            },
        )
        self.plugin_logger.info(
            "turn_heartbeat_auto_advanced session=%s current=%s elapsed=%s ok=%s next=%s",
            session_id,
            current_id,
            elapsed,
            result.get("ok"),
            (result.get("turn") or {}).get("current_entity_id", ""),
        )
        notice = ""
        if result.get("ok"):
            updated_session = self.repository.load_session(session_id)
            notice = self._format_heartbeat_timeout_notice(current_label, elapsed, updated_session, timeout_pause)
        return {
            "active": True,
            "advanced": bool(result.get("ok")),
            "phase": phase,
            "notice": notice,
            "auto_paused": bool(timeout_pause.get("auto_paused")),
        }

    async def _send_heartbeat_message(self, session_id: str, text: str) -> bool:
        try:
            sent = await self.astr_context.send_message(session_id, MessageChain(chain=[Plain(text)]))
        except Exception as exc:
            self.plugin_logger.exception("turn_heartbeat_notify_failed session=%s error=%s", session_id, exc)
            return False
        if sent:
            self.plugin_logger.info("turn_heartbeat_notified session=%s chars=%s", session_id, len(text))
            return True
        self.plugin_logger.warning("turn_heartbeat_notify_no_platform session=%s", session_id)
        return False

    def _apply_turn_timeout_pause_if_needed(
        self,
        session_id: str,
        before_session,
        before_turn: dict,
        current_id: str,
        entities: dict,
        result: dict,
        *,
        source: str,
        actor: dict[str, str] | None = None,
    ) -> dict[str, object]:
        counter = self._build_turn_timeout_counter(before_session, before_turn, current_id, entities)
        info: dict[str, object] = {
            "auto_paused": False,
            "count": counter["count"],
            "total": counter["total"],
            "unit": counter["unit"],
            "scope": counter["scope"],
            "round": counter["round"],
        }
        if not result.get("ok") or not counter.get("current_counted"):
            return info

        updated_session = self.repository.load_session(session_id)
        updated_battle = updated_session.battle or {}
        updated_turn = updated_battle.get("turn")
        changed = False
        should_pause = bool(counter.get("should_pause"))
        if isinstance(updated_turn, dict):
            updated_round = _int_or_default(updated_turn.get("round"), int(counter["round"]))
            if updated_round == int(counter["round"]) or should_pause:
                self._store_turn_timeout_counter(updated_turn, counter)
                changed = True

        if should_pause:
            scene = dict(updated_session.scene or {})
            scene["_dm_paused"] = True
            scene["_dm_pause_reason"] = self._format_timeout_pause_line(info)
            scene["_dm_paused_by"] = actor or {"player_id": "__system__", "display_name": "本地轮次系统"}
            scene["_dm_paused_at"] = _utc_now_iso()
            updated_session.scene = scene
            info["auto_paused"] = True
            changed = True

        if changed:
            self.repository.save_session(updated_session)
            self.repository.append_audit(
                session_id,
                {
                    "type": "turn_timeout_counter_updated",
                    "source": source,
                    "current_entity_id": current_id,
                    "counter": counter,
                    "auto_paused": info["auto_paused"],
                },
            )
            self.plugin_logger.info(
                "turn_timeout_counter_updated session=%s source=%s current=%s count=%s total=%s scope=%s auto_paused=%s",
                session_id,
                source,
                current_id,
                counter["count"],
                counter["total"],
                counter["scope"],
                info["auto_paused"],
            )
        return info

    def _build_turn_timeout_counter(self, session, turn: dict, current_id: str, entities: dict) -> dict[str, object]:
        round_no = _int_or_default(turn.get("round"), 1)
        order = []
        for item in list(turn.get("turn_order") or []):
            entity_id = str(item or "").strip()
            if entity_id and entity_id not in order:
                order.append(entity_id)

        owner_by_entity: dict[str, str] = {}
        owner_order: list[str] = []
        for entity_id in order:
            owner_id = _entity_owner(session, entity_id, entities)
            if not owner_id:
                continue
            owner_by_entity[entity_id] = owner_id
            if owner_id not in owner_order:
                owner_order.append(owner_id)

        if owner_order:
            scope = "player"
            universe = owner_order
            current_key = owner_by_entity.get(current_id, "")
            unit = "名玩家"
        else:
            scope = "entity"
            universe = order
            current_key = current_id if current_id in order else ""
            unit = "个行动者"

        timeout_keys: set[str] = set()
        if (
            _int_or_default(turn.get("_timeout_tracker_round"), -1) == round_no
            and str(turn.get("_timeout_tracker_scope") or "") == scope
        ):
            timeout_keys = {
                str(key)
                for key in list(turn.get("_timeout_tracker_keys") or [])
                if str(key) in universe
            }
        current_counted = bool(current_key and current_key in universe)
        if current_counted:
            timeout_keys.add(current_key)
        total = len(universe)
        count = len(timeout_keys)
        return {
            "round": round_no,
            "scope": scope,
            "unit": unit,
            "keys": sorted(timeout_keys),
            "count": count,
            "total": total,
            "current_key": current_key,
            "current_counted": current_counted,
            "should_pause": current_counted and total > 0 and count * 2 >= total,
        }

    def _store_turn_timeout_counter(self, turn: dict, counter: dict[str, object]) -> None:
        turn["_timeout_tracker_round"] = int(counter.get("round") or 1)
        turn["_timeout_tracker_scope"] = str(counter.get("scope") or "entity")
        turn["_timeout_tracker_keys"] = list(counter.get("keys") or [])
        turn["_timeout_tracker_count"] = int(counter.get("count") or 0)
        turn["_timeout_tracker_total"] = int(counter.get("total") or 0)

    def _format_heartbeat_timeout_notice(
        self,
        current_label: str,
        elapsed: int,
        updated_session,
        timeout_pause: dict[str, object],
    ) -> str:
        lines = [
            f"轮次超时：{current_label}超过 {max(120, elapsed)} 秒未响应，已采取保守行动。",
        ]
        if timeout_pause.get("auto_paused"):
            lines.append(self._format_timeout_pause_line(timeout_pause))
            lines.append(self._format_turn_destination(updated_session, paused=True))
        else:
            lines.append(self._format_turn_destination(updated_session))
        return "\n".join(line for line in lines if line)

    def _format_timeout_pause_line(self, timeout_pause: dict[str, object]) -> str:
        count = int(timeout_pause.get("count") or 0)
        total = int(timeout_pause.get("total") or 0)
        unit = str(timeout_pause.get("unit") or "个行动者")
        if total <= 0:
            return "本轮半数行动者超时，流程已自动暂停。恢复时发 `/dm resume`。"
        return f"本轮已有 {count}/{total}{unit}超时，达到半数，流程已自动暂停。恢复时发 `/dm resume`。"

    def _format_turn_destination(self, session, *, paused: bool = False) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active"):
            return "当前轮次已结束。"
        phase = str(turn.get("phase") or "")
        if phase == "character_turn":
            current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "").strip()
            entities = dict((battle.get("grid") or {}).get("entities") or {})
            label = _entity_label(session, current_id, entities) if current_id else "未指定"
            if paused:
                return f"暂停前停在：{label}。"
            return f"建议行动：{label}；本轮未行动者也可直接行动。"
        if phase == "scene_resolution":
            round_no = _int_or_default(turn.get("round"), 1)
            if paused:
                return f"暂停前进入第 {round_no} 轮场面结算。"
            return f"进入第 {round_no} 轮场面结算。"
        return f"当前阶段：{phase or '未指定'}。"

    def _extract_routed_message(self, event: AstrMessageEvent, message: str) -> str:
        stripped = message.strip()
        lowered = stripped.lower()
        for prefix in self.trigger_prefixes:
            prefix_lower = prefix.lower()
            if lowered == prefix_lower:
                return ""
            if lowered.startswith(prefix_lower + " ") or lowered.startswith(prefix_lower + "\n"):
                return stripped[len(prefix) :].strip()
        return ""

    def _quoted_result(self, event: AstrMessageEvent, text: str, pending_outputs: list[dict] | None = None):
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        pending_outputs = pending_outputs or []
        components = []
        if not message_id:
            if not pending_outputs:
                return event.plain_result(text)
        else:
            components.append(Reply(id=message_id))
        components.append(Plain(text=text))
        for item in pending_outputs[:2]:
            if item.get("type") != "svg_map":
                continue
            file_path = str(item.get("path", ""))
            name = str(item.get("name", "") or "trpg_map.svg")
            if file_path:
                png_path = self._ensure_png_preview(file_path, item)
                if png_path:
                    components.append(ImageComponent.fromFileSystem(png_path))
                    self.plugin_logger.info("map_preview_attached path=%s", png_path)
                else:
                    components.append(Plain(text=f"\n地图已生成：{name}\n{file_path}"))
        return event.chain_result(components)

    def _format_dice_check(self, item: dict) -> str:
        rolls = item.get("rolls") or []
        if not rolls:
            return ""
        reason = _compact_text(item.get("reason") or "本轮行动需要随机裁定", 120)
        rule_name = _compact_text(item.get("rule_name") or "unknown_rule", 80)
        version = item.get("version")
        roll_text = "；".join(_format_roll_record(record) for record in rolls[:6])
        if len(rolls) > 6:
            roll_text += f"；另有 {len(rolls) - 6} 次掷骰"
        if item.get("ok"):
            result_text = _compact_result(item.get("rule_result"))
        else:
            result_text = _compact_text(item.get("error_reason") or item.get("error") or "规则执行失败", 160)
        suffix = f" v{version}" if version else ""
        return f"骰子检定：{reason}\n规则：{rule_name}{suffix}\n掷骰：{roll_text}\n结果：{result_text}"

    def _ensure_png_preview(self, svg_path: str, item: dict) -> str:
        path = Path(svg_path)
        if not path.exists():
            self.plugin_logger.warning("map_preview_missing_source path=%s", svg_path)
            return ""
        png_path = path.with_suffix(".png")
        if png_path.exists() and png_path.stat().st_mtime >= path.stat().st_mtime:
            return str(png_path)
        try:
            self._render_svg_preview(path, png_path, int(item.get("width") or 900), int(item.get("height") or 900))
            return str(png_path)
        except Exception as exc:
            self.plugin_logger.warning("map_preview_render_failed path=%s error=%s", svg_path, exc)
            return ""

    @staticmethod
    def _render_svg_preview(svg_path: Path, png_path: Path, fallback_width: int, fallback_height: int) -> None:
        import math

        from PIL import Image, ImageColor, ImageDraw, ImageFont

        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
        width = _svg_int(root.get("width"), fallback_width)
        height = _svg_int(root.get("height"), fallback_height)
        width = max(320, min(1600, width))
        height = max(320, min(1600, height))
        canvas = Image.new("RGBA", (width, height), "#f8fafcff")
        draw = ImageDraw.Draw(canvas, "RGBA")
        font_cache = {}

        def font_for(size: int, weight: object = ""):
            size = max(10, min(48, int(size or 16)))
            bold = str(weight or "").lower() in {"bold", "700", "800", "900"}
            key = (size, bold)
            if key in font_cache:
                return font_cache[key]
            for candidate in _font_candidates(svg_path, bold=bold):
                try:
                    font_cache[key] = ImageFont.truetype(str(candidate), size)
                    return font_cache[key]
                except Exception:
                    continue
            try:
                font_cache[key] = ImageFont.truetype("DejaVuSans.ttf", size)
            except Exception:
                font_cache[key] = ImageFont.load_default()
            return font_cache[key]

        def color(value: object, default: str = "#111827", opacity: float = 1.0):
            text = str(value or "").strip()
            if not text or text.lower() == "none":
                return None
            if text.startswith("url("):
                text = default
            try:
                rgba = ImageColor.getcolor(text, "RGBA")
            except Exception:
                rgba = ImageColor.getcolor(default, "RGBA")
            alpha = max(0, min(255, int(rgba[3] * max(0.0, min(1.0, opacity)))))
            return (rgba[0], rgba[1], rgba[2], alpha)

        def dash_pattern(value: object) -> list[float]:
            numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", str(value or ""))]
            return [max(1.0, number) for number in numbers[:8]]

        def draw_line_segment(
            start: tuple[float, float],
            end: tuple[float, float],
            fill,
            width: int,
            pattern: list[float] | None = None,
        ) -> None:
            if not pattern:
                draw.line([start, end], fill=fill, width=width)
                return
            x1, y1 = start
            x2, y2 = end
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= 0:
                return
            distance = 0.0
            index = 0
            draw_on = True
            while distance < length:
                step = pattern[index % len(pattern)]
                next_distance = min(length, distance + step)
                if draw_on:
                    ratio_a = distance / length
                    ratio_b = next_distance / length
                    point_a = (x1 + (x2 - x1) * ratio_a, y1 + (y2 - y1) * ratio_a)
                    point_b = (x1 + (x2 - x1) * ratio_b, y1 + (y2 - y1) * ratio_b)
                    draw.line([point_a, point_b], fill=fill, width=width)
                distance = next_distance
                index += 1
                draw_on = not draw_on

        def draw_polyline(points: list[tuple[float, float]], fill, width: int, pattern: list[float] | None = None) -> None:
            for index in range(len(points) - 1):
                draw_line_segment(points[index], points[index + 1], fill, width, pattern)

        def text_anchor(element: ET.Element) -> str:
            horizontal = str(element.get("text-anchor") or "").lower()
            baseline = str(element.get("dominant-baseline") or "").lower()
            h_anchor = "m" if horizontal == "middle" else "r" if horizontal == "end" else "l"
            if baseline in {"middle", "central"}:
                v_anchor = "m"
            elif baseline in {"hanging", "text-before-edge"}:
                v_anchor = "a"
            elif baseline in {"text-after-edge", "ideographic"}:
                v_anchor = "b"
            else:
                v_anchor = "s"
            return h_anchor + v_anchor

        def draw_text_with_anchor(
            position: tuple[float, float],
            text: str,
            font,
            fill,
            anchor: str,
            stroke_width: int,
            stroke_fill,
        ) -> None:
            try:
                draw.text(
                    position,
                    text,
                    fill=fill,
                    font=font,
                    anchor=anchor,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill,
                )
                return
            except TypeError:
                pass
            x, y = position
            try:
                bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
            except Exception:
                text_width = 0
                text_height = 0
            if anchor[0] == "m":
                x -= text_width / 2
            elif anchor[0] == "r":
                x -= text_width
            if anchor[1] == "m":
                y -= text_height / 2
            elif anchor[1] == "s":
                y -= text_height
            elif anchor[1] in {"b", "d"}:
                y -= text_height
            draw.text((x, y), text, fill=fill, font=font)

        def walk(element: ET.Element) -> None:
            tag = _svg_local_name(element.tag)
            opacity = _svg_opacity(element.get("opacity"), 1.0)
            fill = color(element.get("fill"), "#e5e7eb", opacity * _svg_opacity(element.get("fill-opacity"), 1.0))
            stroke = color(element.get("stroke"), "#111827", opacity * _svg_opacity(element.get("stroke-opacity"), 1.0))
            stroke_width = max(1, _svg_int(element.get("stroke-width"), 1))
            if tag == "rect":
                x = _svg_float(element.get("x"))
                y = _svg_float(element.get("y"))
                w = _svg_float(element.get("width"))
                h = _svg_float(element.get("height"))
                draw.rectangle([x, y, x + w, y + h], fill=fill, outline=stroke, width=stroke_width)
            elif tag == "line":
                draw_line_segment(
                    (_svg_float(element.get("x1")), _svg_float(element.get("y1"))),
                    (_svg_float(element.get("x2")), _svg_float(element.get("y2"))),
                    stroke or "#111827",
                    stroke_width,
                    dash_pattern(element.get("stroke-dasharray")),
                )
            elif tag in {"circle", "ellipse"}:
                cx = _svg_float(element.get("cx"))
                cy = _svg_float(element.get("cy"))
                rx = _svg_float(element.get("rx"), _svg_float(element.get("r"), 0))
                ry = _svg_float(element.get("ry"), _svg_float(element.get("r"), 0))
                draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=stroke, width=stroke_width)
            elif tag in {"polygon", "polyline"}:
                points = _svg_points(element.get("points"))
                if len(points) >= 2:
                    if tag == "polygon":
                        draw.polygon(points, fill=fill)
                        if stroke:
                            draw_polyline([*points, points[0]], stroke, stroke_width, dash_pattern(element.get("stroke-dasharray")))
                    else:
                        draw_polyline(points, stroke or "#111827", stroke_width, dash_pattern(element.get("stroke-dasharray")))
            elif tag == "text":
                text = _preview_text("".join(element.itertext()).strip(), _svg_int(element.get("font-size"), 18))
                if text:
                    font_size = _svg_int(element.get("font-size"), 18)
                    font = font_for(
                        font_size,
                        element.get("font-weight"),
                    )
                    x = _svg_float(element.get("x"))
                    y = _svg_float(element.get("y"))
                    text_fill = fill or stroke or color("#111827")
                    halo = _text_halo(text_fill)
                    draw_text_with_anchor(
                        (x, y),
                        text,
                        font,
                        text_fill,
                        text_anchor(element),
                        2 if font_size >= 22 else 1,
                        halo,
                    )
            for child in list(element):
                if _svg_local_name(child.tag) not in {"defs", "title", "desc"}:
                    walk(child)

        walk(root)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.convert("RGB").save(png_path, format="PNG")

    def _pop_pending_outputs(self, session_id: str) -> list[dict]:
        try:
            session = self.repository.load_session(session_id)
            pending = list((session.scene or {}).get("_pending_outputs") or [])
            if not pending:
                return []
            session.scene["_pending_outputs"] = []
            self.repository.save_session(session)
            return pending
        except Exception as exc:
            self.plugin_logger.warning("pending_outputs_pop_failed session=%s error=%s", session_id, exc)
            return []

    def _duplicate_reply(self, session_id: str, sender_id: str, routed_message: str) -> str:
        now = monotonic()
        expire_after = self.DEDUP_WINDOW_SECONDS * 3
        for key, seen_at in list(self._recent_dm_messages.items()):
            if now - seen_at > expire_after:
                self._recent_dm_messages.pop(key, None)
        normalized = self._dedupe_text(routed_message)
        if not normalized:
            return ""
        key = (session_id, sender_id, normalized)
        last_seen = self._recent_dm_messages.get(key)
        self._recent_dm_messages[key] = now
        if last_seen is not None and now - last_seen <= self.DEDUP_WINDOW_SECONDS:
            return "这句刚才已经进入结算，我不会重复处理。要改动作，请换一句新的 /dm 意图。"
        return ""

    @staticmethod
    def _dedupe_text(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @staticmethod
    def _friendly_error_message(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "quota" in lowered or "rate limit" in lowered or "429" in lowered or "额度" in text:
            return "DM 这边的模型额度/频率被打满了，这轮没有写入新结果。稍等一会儿后重发刚才那句。"
        if "badrequest" in lowered or "invalid_request" in lowered or "400" in lowered:
            return "DM 这轮请求被模型接口拒绝了，当前存档未改动。请把刚才的动作换个更短、更明确的说法再发一次。"
        return "DM 内核这轮没跑完，当前存档未改动。请稍后重试，或把动作说得更短一点。"

    def _config_list(self, key: str) -> List[str]:
        if not self.config:
            return []
        try:
            value = self.config.get(key, [])
        except AttributeError:
            value = getattr(self.config, key, [])
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _config_bool(self, key: str, default: bool) -> bool:
        if not self.config:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = getattr(self.config, key, default)
        return bool(value)


def _svg_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1] if "}" in str(tag) else str(tag)


def _looks_like_local_turn_end(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    exact = {
        "回合结束",
        "结束回合",
        "结束当前回合",
        "本回合结束",
        "我的回合结束",
        "我回合结束",
        "行动结束",
        "结束行动",
        "本轮结束",
        "待机",
        "防御",
        "防御待机",
        "保持防御",
        "保持警戒",
        "警戒待机",
        "警戒待射",
        "守望待机",
        "放弃行动",
        "跳过",
        "跳过回合",
        "过",
        "过回合",
        "下一位",
        "下一个",
        "pass",
        "skip",
        "done",
        "end turn",
    }
    if normalized in exact:
        return True
    end_patterns = (
        "我结束回合",
        "我结束当前回合",
        "我本回合结束",
        "我放弃行动",
        "我跳过回合",
        "我待机",
        "我保持警戒",
        "我保持防御",
    )
    return any(pattern in normalized for pattern in end_patterns)


def _looks_like_turn_status_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(term in normalized for term in ("我发动", "我要", "我想", "我进行", "攻击", "移动", "侦察", "观察", "搜索", "调查", "检定", "判定")):
        return False
    return (
        any(term in normalized for term in ("行动顺序", "战斗顺序", "轮动顺序", "当前轮次", "当前回合", "轮到", "谁行动"))
        or ("汇报" in normalized and any(term in normalized for term in ("当前", "轮到", "行动")))
    )


def _looks_like_turn_order_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(term in normalized for term in ("行动顺序", "战斗顺序", "轮动顺序", "顺序", "队列", "所有"))


def _looks_like_local_turn_push(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    exact = {
        "继续",
        "继续推进",
        "推进",
        "推进流程",
        "下一位",
        "下一个",
        "轮到下一个",
        "到下一个",
        "别等了",
        "超时了",
        "超时",
        "没人响应",
        "无人响应",
        "自动行动",
        "自动代管",
        "跳过",
        "跳过他",
        "跳过当前",
        "过",
        "skip",
        "next",
        "continue",
    }
    if normalized in exact:
        return True
    return any(
        pattern in normalized
        for pattern in (
            "推进到下一",
            "让下一个",
            "让下一位",
            "当前玩家超时",
            "当前角色超时",
            "当前行动者超时",
        )
    )


def _local_turn_end_summary(text: str, current_label: str) -> str:
    normalized = str(text or "").strip().lower()
    if any(term in normalized for term in ("跳过", "放弃", "skip", "pass")):
        return f"{current_label}放弃剩余行动，结束本回合。"
    if any(term in normalized for term in ("待机", "防御", "警戒", "守望", "射")):
        return f"{current_label}保持警戒/防御态势，结束本回合。"
    return f"{current_label}声明结束本回合。"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: object) -> datetime | None:
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


def _int_or_default(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _entity_label(session, entity_id: str, entities: dict) -> str:
    entity = dict(entities.get(entity_id, {}))
    if entity.get("name"):
        return str(entity["name"])
    character = session.characters.get(entity_id)
    if character:
        return character.name or character.id
    return entity_id


def _entity_owner(session, entity_id: str, entities: dict) -> str:
    entity = dict(entities.get(entity_id, {}))
    tags = dict(entity.get("tags", {}))
    if tags.get("player_id"):
        return str(tags["player_id"])
    character_id = str(tags.get("character_id", "") or entity_id)
    character = session.characters.get(character_id)
    if character and character.player_id:
        return str(character.player_id)
    for player_id, bound_id in session.player_character_map.items():
        if bound_id == character_id or bound_id == entity_id:
            return str(player_id)
    return ""


def _pending_entity_for_actor(session, turn: dict, actor_id: str, entities: dict) -> str:
    actor_id = str(actor_id or "").strip()
    if not actor_id:
        return ""
    order = _clean_turn_order(list(turn.get("turn_order") or []))
    actions = dict(turn.get("actions_this_round") or {})
    current_id = str(turn.get("current_entity_id") or (session.battle or {}).get("turn_entity_id", "") or "").strip()
    if current_id and current_id not in actions and _entity_owner(session, current_id, entities) == actor_id:
        return current_id
    for entity_id in order:
        if entity_id in actions:
            continue
        if _entity_owner(session, entity_id, entities) == actor_id:
            return entity_id
    return ""


def _clean_turn_order(order: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen = set()
    for item in order:
        value = str(item or "").strip()
        if value and value not in seen:
            cleaned.append(value)
            seen.add(value)
    return cleaned


def _game_started_text(session) -> str:
    if bool((session.scene or {}).get("_game_started")):
        return "已开始/主线锁定"
    return "未开始"


def _campaign_game_started(session) -> bool:
    return bool((session.scene or {}).get("_game_started"))


def _campaign_action_pacing_enabled(session) -> bool:
    if _campaign_game_started(session) or bool((session.battle or {}).get("active")):
        return True
    try:
        background_ready = has_campaign_background(session)
    except Exception:
        background_ready = bool((session.world_tags or {}).get("_background_ready"))
    if not background_ready:
        return False
    # Legacy live sessions created before start_game do not have _game_started,
    # but they can already have bound characters, rules, and active play.
    return bool(session.characters) and (bool(session.rules) or len(session.participants or {}) >= 2)


def _campaign_plot_locked(session) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    return bool(
        (scene.get("_game_started") and scene.get("_plot_locked", True))
        or world_tags.get("_plot_locked") is True
    )


def _looks_like_plot_rewrite_request(text: str) -> bool:
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


def _looks_like_paced_player_action(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if _looks_like_non_action_request(normalized):
        return False
    actor_terms = ("我", "俺", "咱", "角色", "自己", "my ")
    action_terms = (
        "移动",
        "走",
        "出门",
        "跑",
        "冲",
        "靠近",
        "后退",
        "撤",
        "爬",
        "跳",
        "躲",
        "攻击",
        "射",
        "砍",
        "刺",
        "打",
        "施法",
        "治疗",
        "防御",
        "掩护",
        "观察",
        "查看",
        "侦查",
        "搜索",
        "调查",
        "警告",
        "示警",
        "提醒",
        "叫醒",
        "喊",
        "说",
        "开门",
        "关门",
        "拿",
        "捡",
        "使用",
        "点燃",
        "潜行",
        "隐藏",
        "清醒",
        "检定",
        "判定",
        "发现",
        "注意",
        "盯",
        "准备",
        "等待",
        "继续",
    )
    return any(term in normalized for term in action_terms) and (
        any(term in normalized for term in actor_terms) or len(normalized) <= 40
    )


def _looks_like_non_action_request(text: str) -> bool:
    non_action_terms = (
        "status",
        "token",
        "tokens",
        "当前轮次",
        "当前回合",
        "轮次",
        "回合",
        "玩家列表",
        "角色列表",
        "有哪些玩家",
        "谁行动",
        "轮到谁",
        "日志",
        "debug",
        "地图",
        "画图",
        "生成地图",
        "加入游戏",
        "加入",
        "建卡",
        "角色卡",
        "我的名字",
        "我是一个",
        "我是名",
        "我叫",
        "补充设定",
        "背景设定",
        "开始游戏",
        "开场",
        "正式开始",
    )
    if any(term in text for term in non_action_terms):
        return True
    question_terms = ("吗", "呢", "什么", "多少", "怎么", "为什么", "能不能", "可不可以", "有没有", "?")
    action_verbs = ("攻击", "移动", "射", "砍", "走", "跑", "侦查", "搜索", "调查")
    return any(term in text for term in question_terms) and not any(term in text for term in action_verbs)


def _looks_like_player_roster_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    exact = {
        "玩家列表",
        "角色列表",
        "登记列表",
        "当前玩家",
        "当前角色",
        "当前有哪些玩家",
        "现在有哪些玩家",
        "现在游戏里有哪些玩家",
        "哪些玩家",
        "谁加入了",
        "谁还没绑定角色",
        "谁没有角色",
    }
    if normalized in exact:
        return True
    roster_terms = ("玩家", "成员", "登记", "加入", "绑定", "角色")
    query_terms = ("哪些", "列表", "一览", "当前", "现在", "所有", "全部", "谁", "有没有")
    return any(term in normalized for term in roster_terms) and any(term in normalized for term in query_terms)


def _is_legacy_generic_character_id(character_id: str) -> bool:
    normalized = str(character_id or "").strip().lower()
    return normalized in {"pc", "player", "character", "hero", "role", "user"}


def _default_pc_id_for_player(player_id: str) -> str:
    safe_player_id = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(player_id or "").strip()).strip("._-")
    return f"pc_{safe_player_id}" if safe_player_id else ""


def _rename_battle_character_refs(battle: dict, old_id: str, new_id: str) -> None:
    if not isinstance(battle, dict):
        return
    if battle.get("turn_entity_id") == old_id:
        battle["turn_entity_id"] = new_id
    turn = battle.get("turn")
    if isinstance(turn, dict):
        if turn.get("current_entity_id") == old_id:
            turn["current_entity_id"] = new_id
        if isinstance(turn.get("turn_order"), list):
            turn["turn_order"] = [new_id if item == old_id else item for item in turn["turn_order"]]
        actions = turn.get("actions_this_round")
        if isinstance(actions, dict) and old_id in actions and new_id not in actions:
            actions[new_id] = actions.pop(old_id)
    grid = battle.get("grid")
    if not isinstance(grid, dict):
        return
    entities = grid.get("entities")
    if isinstance(entities, dict):
        if old_id in entities and new_id not in entities:
            entities[new_id] = entities.pop(old_id)
            if isinstance(entities[new_id], dict):
                entities[new_id]["id"] = new_id
        for entity in entities.values():
            if not isinstance(entity, dict):
                continue
            tags = entity.get("tags")
            if isinstance(tags, dict) and tags.get("character_id") == old_id:
                tags["character_id"] = new_id


def _font_candidates(svg_path: Path, bold: bool = False) -> list[Path]:
    data_dir = svg_path.parent.parent
    regular_names = ["NotoSansCJKsc-Regular.otf", "NotoSansSC-Regular.otf", "SourceHanSansSC-Regular.otf"]
    bold_names = ["NotoSansCJKsc-Bold.otf", "NotoSansSC-Bold.otf", "SourceHanSansSC-Bold.otf"]
    names = [*bold_names, *regular_names] if bold else regular_names
    candidates: list[Path] = []
    for name in names:
        candidates.append(data_dir / "fonts" / name)
    candidates.extend(
        [
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/NotoSansCJKsc-Bold.otf"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/NotoSansSC-Bold.otf"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/SourceHanSansSC-Bold.otf"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/NotoSansCJKsc-Regular.otf"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/NotoSansSC-Regular.otf"),
            Path("/AstrBot/data/plugin_data/astrbot_plugin_auto_trpg_dm/fonts/SourceHanSansSC-Regular.otf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        ]
    )
    return candidates


def _svg_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _svg_opacity(value: object, default: float = 1.0) -> float:
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(0.0, min(1.0, float(str(value).strip())))
    except ValueError:
        return default


def _svg_int(value: object, default: int = 0) -> int:
    return int(round(_svg_float(value, float(default))))


def _svg_points(value: object) -> list[tuple[float, float]]:
    numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", str(value or ""))]
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, 2)]


def _preview_text(value: object, font_size: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    limit = 28 if font_size >= 24 else 18 if font_size >= 18 else 12
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _text_halo(fill: object):
    try:
        red, green, blue = fill[:3]
    except Exception:
        return (255, 255, 255, 220)
    luminance = 0.299 * red + 0.587 * green + 0.114 * blue
    if luminance > 170:
        return (15, 23, 42, 210)
    return (255, 255, 255, 230)


def _format_roll_record(record: object) -> str:
    if not isinstance(record, dict):
        return _compact_text(record, 80)
    expression = _compact_text(record.get("expression") or "roll", 40)
    rolls = record.get("rolls") or []
    modifier = int(record.get("modifier") or 0)
    total = record.get("total")
    roll_text = ",".join(str(item) for item in list(rolls)[:20])
    if len(rolls) > 20:
        roll_text += ",..."
    if modifier > 0:
        mod_text = f"+{modifier}"
    elif modifier < 0:
        mod_text = str(modifier)
    else:
        mod_text = ""
    return f"{expression}=[{roll_text}]{mod_text} => {total}"


def _compact_result(value: object) -> str:
    if value is None:
        return "规则执行完成"
    if isinstance(value, dict):
        preferred = []
        for key in ("total", "success", "degree", "outcome", "damage", "result", "message"):
            if key in value:
                preferred.append(f"{key}={value[key]}")
        if preferred:
            return _compact_text("；".join(preferred), 180)
    return _compact_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), 180)


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _extract_reset_confirmation_token(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    confirm_terms = ("确认重开", "确认清空", "确认重置", "确认新团", "confirm reset", "confirm-reset")
    if not any(term in lowered for term in confirm_terms):
        return ""
    match = re.search(r"\b(?:RESET-)?[A-Z0-9]{6,12}\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    token = match.group(0).upper()
    if not token.startswith("RESET-"):
        token = f"RESET-{token}"
    return token


def _looks_like_reset_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    non_save_restart_terms = (
        "重启插件",
        "重启机器人",
        "重启 bot",
        "重启bot",
        "重启服务",
        "重启容器",
        "重启 astrbot",
        "重启astrbot",
        "restart plugin",
        "restart bot",
        "restart service",
        "reload plugin",
    )
    if any(term in lowered for term in non_save_restart_terms):
        return False
    recovery_terms = ("恢复之前", "找回", "撤销重开", "误删", "备份", "backup", "restore")
    if any(term in lowered for term in recovery_terms):
        return False
    destructive_terms = (
        "清空存档",
        "删除存档",
        "重置存档",
        "重开存档",
        "重开当前团",
        "重开跑团",
        "重新开团",
        "开新团",
        "新开一团",
        "reset save",
        "reset campaign",
        "new campaign",
    )
    return any(term in lowered for term in destructive_terms)


def _looks_like_restore_latest_backup_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    restore_terms = ("恢复", "还原", "找回", "restore")
    backup_terms = ("上一个存档", "之前的跑团", "之前的存档", "上一份存档", "上个存档", "上一个备份", "最新备份", "backup")
    return any(term in lowered for term in restore_terms) and any(term in lowered for term in backup_terms)


def _looks_like_backup_list_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(term in lowered for term in ("备份列表", "有哪些备份", "查看备份", "最近备份", "backup list", "list backups"))


def _looks_like_manual_backup_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    create_terms = ("备份存档", "保存备份", "创建备份", "手动备份", "backup save", "create backup")
    list_terms = ("备份列表", "查看备份", "有哪些备份", "backup list")
    return any(term in lowered for term in create_terms) and not any(term in lowered for term in list_terms)


def _looks_like_enough_background_seed(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    explicit = any(token in lowered for token in ("背景", "世界观", "设定", "题材", "类型", "风格", "环境", "premise", "setting"))
    buckets = 0
    if any(token in lowered for token in ("末世", "废土", "科幻", "奇幻", "玄幻", "现代", "赛博", "克苏鲁", "悬疑", "武侠", "太空", "蒸汽", "欧洲", "中世纪", "历史", "低魔", "无魔", "纯剑", "dnd", "coc", "d20")):
        buckets += 1
    if any(token in lowered for token in ("严肃", "荒诞", "危险", "恐怖", "轻松", "黑暗", "求生", "调查", "热血", "压抑", "幽默")):
        buckets += 1
    if any(token in lowered for token in ("开场", "开局", "第一幕", "因为", "为了", "任务", "求救", "聚集", "来到", "醒来", "退休")):
        buckets += 1
    if any(token in lowered for token in ("地点", "城市", "村庄", "荒野", "废墟", "船上", "游艇", "空间站", "中继站", "塔", "地下城", "酒馆", "地球", "海上", "海战", "港口", "王国")):
        buckets += 1
    if any(token in lowered for token in ("势力", "组织", "公司", "教团", "军团", "帮派", "敌人", "怪物", "派系")):
        buckets += 1
    if any(token in lowered for token in ("规则", "系统", "检定", "骰", "属性", "等级", "没有魔", "没有魔法", "不存在超自然", "无超自然", "超自然力量")):
        buckets += 1
    return buckets >= 2 and (explicit or buckets >= 3 or len(lowered) >= 28)


def _looks_like_background_authoring_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    subject_terms = (
        "背景",
        "世界观",
        "设定",
        "环境",
        "题材",
        "类型",
        "风格",
        "世界",
        "campaign",
        "setting",
        "premise",
    )
    authoring_terms = (
        "生成",
        "创建",
        "建立",
        "补全",
        "补完",
        "完善",
        "扩写",
        "丰富",
        "整理",
        "随机",
        "写",
        "定",
        "设定",
        "我希望",
        "我想",
        "我要",
        "你来",
        "你定",
        "你决定",
        "帮我",
        "替我",
        "给我",
        "自动",
        "供选择",
        "背景是",
        "题材",
    )
    delegation_terms = (
        "你来定",
        "你定吧",
        "你决定",
        "随便定",
        "随机一个",
        "随机几个",
        "帮我定",
        "替我定",
        "直接定",
        "自动生成",
    )
    if any(token in lowered for token in delegation_terms):
        return True
    if not any(token in lowered for token in subject_terms):
        return False
    return any(token in lowered for token in authoring_terms) or len(lowered) >= 10
