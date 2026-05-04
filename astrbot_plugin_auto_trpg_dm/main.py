from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from time import monotonic
from typing import Any, List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.message.components import Image as ImageComponent, Plain, Reply
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.ambient_image import AmbientImageConfig, AmbientImageProvider
from .core.external_memory import HonchoExternalMemory, HonchoMemoryConfig
from .core.plugin_log import configure_plugin_logging
from .core.router import IntentRouter
from .core.security import security_precheck
from .core.models import CycleState, GameMode
from .rules.python_runtime import PythonRuleRuntime
from .storage.json_repository import JsonGameRepository
from .tools.ambient_image_tools import (
    AmbientImageTools,
    ambient_image_gate,
    audit_safe_ambient_image_result,
)
from .tools.diagnostic_tools import DiagnosticTools
from .tools.memory_tools import MemoryTools, has_campaign_background
from .tools.registry import ToolRegistry
from .tools.turn_tools import TurnTools


PLUGIN_VERSION = "0.1.89"


@register(
    "auto_trpg_dm",
    "codex",
    "全自然语言 TRPG DM：动态规则、战棋物理验证、Tag 角色卡与自动剧本。",
    PLUGIN_VERSION,
)
class AutoTrpgDmPlugin(Star):
    DEDUP_WINDOW_SECONDS = 18.0
    ACTION_PACING_SECONDS = 12
    HEARTBEAT_INTERVAL_SECONDS = 60
    DM_ACK_COOLDOWN_SECONDS = 10.0

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.astr_context = context
        self.trigger_prefixes = ["/dm"]
        self._recent_dm_messages: dict[tuple[str, str, str], float] = {}
        self._recent_dm_acks: dict[tuple[str, str], float] = {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_auto_trpg_dm"
        self.repository = JsonGameRepository(data_dir)
        self.plugin_logger = configure_plugin_logging(self.repository.plugin_log_path())
        rule_runtime = PythonRuleRuntime(data_dir / "rules")
        honcho_config = HonchoMemoryConfig(
            enabled=self._config_bool("honcho_enabled", False),
            target=self._config_str("honcho_target", "auto"),
            workspace_id=self._config_str("honcho_workspace_id", ""),
            api_key_env=self._config_str("honcho_api_key_env", "HONCHO_API_KEY"),
            cloud_api_key_env=self._config_str("honcho_cloud_api_key_env", ""),
            base_url=self._config_str("honcho_base_url", ""),
            self_hosted_api_key_env=self._config_str("honcho_self_hosted_api_key_env", ""),
            self_hosted_auth_enabled=self._config_bool(
                "honcho_self_hosted_auth_enabled",
                False,
            ),
            environment=self._config_str("honcho_environment", "production"),
            timeout_seconds=self._config_int("honcho_timeout_seconds", 8),
            max_context_chars=self._config_int("honcho_max_context_chars", 1600),
            write_enabled=self._config_bool("honcho_write_enabled", True),
            read_enabled=self._config_bool("honcho_read_enabled", True),
            assistant_peer_id=self._config_str("honcho_assistant_peer_id", "paotuan_dm"),
            cross_campaign_personalization_enabled=self._config_bool(
                "honcho_cross_campaign_personalization_enabled",
                False,
            ),
        )
        self.honcho_config = honcho_config
        external_memory = HonchoExternalMemory(honcho_config)
        ambient_image_config = AmbientImageConfig(
            enabled=self._config_bool("ambient_image_enabled", False),
            api_mode=self._config_str("ambient_image_api_mode", "images"),
            base_url=self._config_str("ambient_image_base_url", "https://www.packyapi.com"),
            api_key=self._config_str("ambient_image_api_key", ""),
            api_key_env=self._config_str("ambient_image_api_key_env", "PACKYAPI_SORA_API_KEY"),
            user_agent=self._config_str("ambient_image_user_agent", ""),
            model=self._config_str("ambient_image_model", "gpt-image-2"),
            prompt_model=self._config_str("ambient_image_prompt_model", ""),
            size=self._config_str("ambient_image_size", "1536x1024"),
            quality=self._config_str("ambient_image_quality", "medium"),
            output_format=self._config_str("ambient_image_output_format", "png"),
            response_format=self._config_str("ambient_image_response_format", "url"),
            timeout_seconds=self._config_int("ambient_image_timeout_seconds", 120),
            send_to_chat=self._config_bool("ambient_image_send_to_chat", True),
            frequency=self._config_str("ambient_image_frequency", "medium"),
            prompt_template=self._config_str("ambient_image_prompt_template", ""),
            activity_window_minutes=self._config_int("ambient_image_activity_window_minutes", 60),
            activity_min_messages=self._config_int("ambient_image_activity_min_messages", 10),
            activity_min_players=self._config_int("ambient_image_activity_min_players", 2),
            similarity_recent_count=self._config_int("ambient_image_similarity_recent_count", 3),
            similarity_threshold=self._config_float("ambient_image_similarity_threshold", 0.82),
            similarity_retry_enabled=self._config_bool("ambient_image_similarity_retry_enabled", True),
        )
        self.ambient_image_config = ambient_image_config
        prompt_snapshot_projection_enabled = self._config_bool("prompt_snapshot_projection_enabled", True)
        heartbeat_idle_log_interval = max(1, self._config_int("heartbeat_idle_log_interval", 10))
        self.prompt_snapshot_projection_enabled = prompt_snapshot_projection_enabled
        self.heartbeat_idle_log_interval = heartbeat_idle_log_interval
        ambient_image_provider = AmbientImageProvider(ambient_image_config)
        tool_registry = ToolRegistry(
            repository=self.repository,
            rule_runtime=rule_runtime,
            astr_context=context,
            external_memory_config=honcho_config,
            external_memory=external_memory,
        )
        self.router = IntentRouter(
            astr_context=context,
            repository=self.repository,
            tool_registry=tool_registry,
            external_memory=external_memory,
            ambient_image_config=ambient_image_config,
            ambient_image_provider=ambient_image_provider,
            ambient_image_sender=self._send_independent_ambient_image,
            ra_enabled=self._config_bool("ra_enabled", False),
            ra_model_provider=self._config_str("ra_model_provider", "default") or "default",
            ra_max_tokens=self._config_int("ra_max_tokens", 2048),
            prompt_snapshot_projection_enabled=prompt_snapshot_projection_enabled,
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
        self._heartbeat_idle_ticks = 0
        self._start_heartbeat_task()
        self.plugin_logger.info(
            "plugin_initialized version=%s data_dir=%s honcho_enabled=%s honcho_workspace=%s ambient_image_enabled=%s ambient_image_mode=%s prompt_snapshot_projection_enabled=%s heartbeat_idle_log_interval=%s",
            PLUGIN_VERSION,
            data_dir,
            honcho_config.enabled,
            bool(honcho_config.workspace_id),
            ambient_image_config.enabled,
            ambient_image_config.api_mode,
            prompt_snapshot_projection_enabled,
            heartbeat_idle_log_interval,
        )
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
        fast_reply = await self._local_fast_path(event, session_id, actor, routed_message)
        if fast_reply:
            self.plugin_logger.info(
                "dm_fast_path session=%s sender=%s text=%s",
                session_id,
                sender_id,
                self._dedupe_text(routed_message)[:160],
            )
            pending_outputs = self._pop_pending_outputs(session_id)
            yield self._quoted_result(event, fast_reply, pending_outputs=pending_outputs)
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
        try:
            if self._should_send_dm_ack(session_id, sender_id):
                yield self._quoted_result(event, "收到，正在结算这一幕……")
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
        dice_summary = self._format_dice_summary(dice_outputs)
        sent_any = False
        if completion or other_outputs or dice_summary:
            if not completion and other_outputs:
                completion = "地图已生成，已附上。"
            self.plugin_logger.info(
                "dm_completed session=%s sender=%s reply_chars=%s pending_outputs=%s",
                session_id,
                sender_id,
                len(completion),
                len(pending_outputs),
            )
            yield self._quoted_result(event, completion, pending_outputs=other_outputs, dice_summary=dice_summary)
            sent_any = True
        if sent_any:
            event.stop_event()

    async def _local_fast_path(
        self,
        event: AstrMessageEvent,
        session_id: str,
        actor: dict[str, str],
        routed_message: str,
    ) -> str:
        text = self._dedupe_text(routed_message)
        normalized = text.lower()
        session = self.repository.load_session(session_id)
        paused = bool((session.scene or {}).get("_dm_paused", False))

        if session.cycle_state != CycleState.CYCLE_ACTIVE:
            readonly_reply = await self._cycle_readonly_fast_path(session_id, session, actor, text, normalized)
            if readonly_reply:
                return readonly_reply
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "cycle_state_block",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                    "text": text[:240],
                },
            )
            return "当前叙事周期正在结算或过渡中。我可以回答 `/dm status`、`/dm token`、`/dm 当前轮次` 这类只读查询；新的行动、重置、备份恢复或推进轮次请稍候。"

        if _looks_like_dm_autopilot_takeover(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "dm_autopilot_takeover_blocked",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return (
                "不能把所有玩家角色交给 AI 全权托管，也不能自动推完整段剧情。"
                "我会继续当 DM 引导局势；单个行动者超过 120 秒未响应时，才会按规则保守代管。"
            )

        if _looks_like_manual_ambient_image_request(text):
            return self._handle_manual_ambient_image_request(event, session_id, session, actor, text)

        if normalized in {"pause", "暂停", "暂停流程", "暂停游戏"}:
            session.scene["_dm_paused"] = True
            session.scene["_dm_pause_reason"] = text
            session.scene["_dm_paused_by"] = actor
            session.scene["_dm_paused_at"] = _utc_now_iso()
            self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "pause", "actor": actor})
            self._schedule_pause_resume_ambient_image(
                event,
                session_id,
                actor,
                text,
                story_moment="跑团流程暂停，角色和场景进入短暂静止。",
                rationale="暂停是特殊剧情节奏事件，按 2 小时冷却尝试氛围图。",
            )
            return "流程已暂停。我不会推进轮次、替人行动或调用模型；需要继续时发 `/dm resume` 或 `/dm 恢复`。"

        if normalized in {"resume", "unpause", "恢复", "继续流程", "解除暂停"} or (paused and normalized == "继续"):
            if paused:
                session.scene["_dm_paused"] = False
                session.scene["_dm_resumed_by"] = actor
                session.scene["_dm_resumed_at"] = _utc_now_iso()
                self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "resume", "actor": actor})
            self._schedule_pause_resume_ambient_image(
                event,
                session_id,
                actor,
                text,
                story_moment="跑团流程从暂停中恢复，镜头重新回到当前场景。",
                rationale="暂停恢复是特殊剧情节奏事件，按 2 小时冷却尝试氛围图。",
            )
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
        if not reset_token and _looks_like_reset_confirmation_request(text):
            reset_token = _pending_reset_confirmation_token(session, actor)
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
            message = str(result.get("message") or "存档未改动。")
            if result.get("ok"):
                message += "\n新团可以直接给一句方向，我会先补背景再引导建卡/开场。例：`/dm 来一个战锤40K底巢清剿剧本，我是极限战士喷火兵`。"
            return message

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

        if _looks_like_new_campaign_seed_request(text) and _session_has_meaningful_campaign_content(session):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "reset",
                reason=f"开新团前清空旧团：{text}",
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "new_campaign_requires_reset",
                    "actor": actor,
                    "text": text[:240],
                    "result": result,
                },
            )
            message = str(result.get("message") or "重开需要二次确认，存档暂未改动。")
            return "当前群已有一场跑团存档；同群只能同时保留一场。若要把这句作为新团开场，需先清空旧团。\n" + message

        if not has_campaign_background(session):
            background_patch = _guided_background_patch_from_text(text)
            if background_patch:
                result = await MemoryTools(self.repository, session_id, actor=actor, message=text).update_world_tags(background_patch)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "guided_background_bootstrap",
                        "actor": actor,
                        "text": text[:240],
                        "result": result,
                    },
                )
                self.plugin_logger.info(
                    "guided_background_bootstrap session=%s sender=%s ok=%s keys=%s",
                    session_id,
                    actor.get("player_id", ""),
                    result.get("ok"),
                    ",".join(str(key) for key in background_patch.keys()),
                )
                if result.get("ok"):
                    return ""
                return str(result.get("message") or "背景写入失败；请换一句更明确的背景方向。")

        if normalized in {"status", "状态", "当前状态"}:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "status", "actor": actor})
            return self._format_local_status(session)

        if normalized in {"token", "tokens", "token消耗", "上下文", "上下文消耗"}:
            usage = await DiagnosticTools(
                self.repository,
                session_id,
                external_memory_enabled=self.honcho_config.enabled,
                external_memory_read_enabled=self.honcho_config.read_enabled,
                external_memory_max_context_chars=self.honcho_config.max_context_chars,
            ).estimate_token_usage("summary")
            current = usage.get("current", {})
            rough = usage.get("rough_token_estimate", {})
            compression = usage.get("compression", {})
            external_memory = usage.get("external_memory", {})
            external_note = ""
            if external_memory.get("enabled") and external_memory.get("read_enabled"):
                external_note = (
                    f"Honcho 外置记忆本轮预算上限 {external_memory.get('configured_max_context_chars', 0)} 字；"
                    "实际读取字符数以 router 日志为准。"
                )
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "token", "actor": actor})
            return (
                "Token 粗算："
                f"快照 {current.get('compact_snapshot_chars', 0)} 字，约 {rough.get('heuristic', 0)} token；"
                f"完整存档 {current.get('full_save_chars', 0)} 字。"
                f"距自动压缩约 {compression.get('snapshot_chars_remaining_before_compression', 0)} 字。"
                f"{external_note}"
            )

        if normalized in {"当前轮次", "当前回合", "轮次", "回合", "谁行动", "轮到谁", "行动顺序", "战斗顺序", "轮动顺序"} or _looks_like_turn_status_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "turn_status", "actor": actor})
            return self._format_turn_status(session, include_order=_looks_like_turn_order_request(text))

        reasonableness_reply = _post_start_reasonableness_fast_reply(session, text)
        if reasonableness_reply:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "post_start_reasonableness_guard",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return reasonableness_reply

        turn_reply = await self._local_turn_fast_path(session_id, session, actor, text)
        if turn_reply:
            return turn_reply

        if _looks_like_player_roster_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "player_roster", "actor": actor})
            return self._format_player_roster(session)

        unbound_reply = _unbound_tactical_actor_reply(session, actor, text)
        if unbound_reply:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "unbound_tactical_actor",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return unbound_reply

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
                "我先需要一句背景方向，之后会自动补细节并引导建卡/开场。"
                "可以直接说：`/dm 来一个战锤40K底巢清剿剧本`，或 `/dm 你来定一个废土科幻开局`。"
            )

        return ""

    async def _cycle_readonly_fast_path(
        self,
        session_id: str,
        session,
        actor: dict[str, str],
        text: str,
        normalized: str,
    ) -> str:
        if normalized in {"status", "状态", "当前状态"}:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "status",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                },
            )
            return self._format_local_status(session)

        if normalized in {"token", "tokens", "token消耗", "上下文", "上下文消耗"}:
            usage = await DiagnosticTools(self.repository, session_id).estimate_token_usage("summary")
            current = usage.get("current", {})
            rough = usage.get("rough_token_estimate", {})
            compression = usage.get("compression", {})
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "token",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                },
            )
            return (
                "Token 粗算："
                f"快照 {current.get('compact_snapshot_chars', 0)} 字，约 {rough.get('heuristic', 0)} token；"
                f"完整存档 {current.get('full_save_chars', 0)} 字。"
                f"距自动压缩约 {compression.get('snapshot_chars_remaining_before_compression', 0)} 字。"
            )

        if normalized in {"当前轮次", "当前回合", "轮次", "回合", "谁行动", "轮到谁", "行动顺序", "战斗顺序", "轮动顺序"} or _looks_like_turn_status_request(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "turn_status",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                },
            )
            return self._format_turn_status(session, include_order=_looks_like_turn_order_request(text))

        if _looks_like_player_roster_request(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "player_roster",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                },
            )
            return self._format_player_roster(session)

        if _looks_like_backup_list_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "list_backups",
                reason=text,
            )
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "backup_list",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                    "result": result,
                },
            )
            backups = result.get("backups") or []
            if not backups:
                return "当前还没有自动备份。"
            lines = ["最近备份："]
            for item in backups[:5]:
                size = int(item.get("size") or 0)
                lines.append(f"- {item.get('mtime', '')}；{size // 1024}K；{item.get('reason', '') or item.get('name', '')}")
            return "\n".join(lines)

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
                    output_limit_chars=1440,
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
                    output_limit_chars=1440,
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
                    output_limit_chars=1440,
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
        if _looks_like_terminal_or_interlude_for_pacing(text):
            return ""
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
            try:
                output_limit_chars = int(turn.get("output_limit_chars") or 0)
            except (TypeError, ValueError):
                output_limit_chars = 0
            if output_limit_chars < 1440:
                turn["output_limit_chars"] = 1440
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
        important_tick = any((active, advanced, initialized, notified, auto_paused))
        if important_tick:
            self._heartbeat_idle_ticks = 0
        else:
            self._heartbeat_idle_ticks += 1
        should_log_idle = self._heartbeat_idle_ticks == 1 or (
            self._heartbeat_idle_ticks % self.heartbeat_idle_log_interval == 0
        )
        if important_tick or should_log_idle:
            self.plugin_logger.info(
                "turn_heartbeat_tick scanned=%s active=%s advanced=%s initialized=%s notified=%s auto_paused=%s idle_ticks=%s",
                scanned,
                active,
                advanced,
                initialized,
                notified,
                auto_paused,
                self._heartbeat_idle_ticks,
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
        suspended = self._suspend_heartbeat_turn_if_needed(session_id, session, battle, turn, phase)
        if suspended:
            return suspended
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
                output_limit_chars=int(turn.get("output_limit_chars") or 1440),
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
            output_limit_chars=int(turn.get("output_limit_chars") or 1440),
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

    def _suspend_heartbeat_turn_if_needed(
        self,
        session_id: str,
        session,
        battle: dict,
        turn: dict,
        phase: str,
    ) -> dict[str, object] | None:
        reason = _heartbeat_turn_suspend_reason(session, turn, phase)
        if not reason:
            return None

        scene = dict(session.scene or {})
        now = _utc_now_iso()
        previous_phase = str(turn.get("phase") or "")
        previous_entity_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
        post_game = reason in {"scene_concluded", "terminal_turn_log"}

        turn["active"] = False
        turn["phase"] = "ended" if post_game else "suspended"
        turn["current_entity_id"] = ""
        turn["current_index"] = -1
        turn["deadline_at"] = ""
        turn["waiting_since_at"] = ""
        battle["active"] = False
        battle["turn"] = turn
        battle["turn_entity_id"] = ""
        session.battle = battle
        session.mode = GameMode.NARRATIVE
        scene["_heartbeat_turn_suspended_at"] = now
        scene["_heartbeat_turn_suspended_reason"] = reason
        if post_game:
            scene["_post_game"] = True
            scene["_encounter_ended_at"] = now
        session.scene = scene
        self.repository.save_session(session)

        audit = {
            "type": "turn_heartbeat_suspended",
            "reason": reason,
            "previous_phase": previous_phase,
            "previous_entity_id": previous_entity_id,
            "post_game": post_game,
        }
        self.repository.append_audit(session_id, audit)
        self.plugin_logger.info(
            "turn_heartbeat_suspended session=%s reason=%s phase=%s current=%s post_game=%s",
            session_id,
            reason,
            previous_phase,
            previous_entity_id,
            post_game,
        )

        if post_game:
            notice = "检测到当前场面已经进入终章/间幕，轮次心跳已停止；不会再因超时自动代管或推进战斗。"
        elif reason == "social_or_political_scene":
            notice = "当前冲突更像谈判、征税或社会场面，不适合按战斗心跳代打；我已停止本轮自动超时推进，后续按普通叙事裁定。"
        else:
            notice = "检测到重复超时或旧终局信号，轮次心跳已停止；需要继续战斗时可由玩家重新明确进入轮次。"
        return {"active": False, "suspended": True, "reason": reason, "notice": notice}

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

    async def _send_independent_ambient_image(self, session_id: str, result: dict[str, Any]) -> bool:
        if not result.get("ok") or not result.get("available") or not result.get("send_to_chat"):
            return False
        file_path = str(result.get("file_path") or "")
        title = str(result.get("title") or "").strip() or "氛围图"
        if not file_path or not Path(file_path).exists():
            self.plugin_logger.warning(
                "ambient_image_independent_send_missing_file session=%s file=%s",
                session_id,
                file_path,
            )
            return False
        chain = MessageChain(chain=[Plain(text=title), ImageComponent.fromFileSystem(file_path)])
        try:
            sent = await self.astr_context.send_message(session_id, chain)
        except Exception as exc:
            self.plugin_logger.exception(
                "ambient_image_independent_send_failed session=%s error=%s",
                session_id,
                exc,
            )
            return False
        if sent:
            self.plugin_logger.info(
                "ambient_image_independent_sent session=%s file=%s title=%s",
                session_id,
                file_path,
                title,
            )
            return True
        self.plugin_logger.warning("ambient_image_independent_send_no_platform session=%s", session_id)
        return False

    def _handle_manual_ambient_image_request(
        self,
        event: AstrMessageEvent,
        session_id: str,
        session,
        actor: dict[str, str],
        text: str,
    ) -> str:
        provider_unavailable = self._ambient_image_provider_unavailable()
        if provider_unavailable:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "manual_ambient_image_blocked",
                    "actor": actor,
                    "text": text[:240],
                    "result": audit_safe_ambient_image_result(provider_unavailable),
                },
            )
            return _format_ambient_image_failure_reply(provider_unavailable)
        if not bool(self.ambient_image_config.send_to_chat):
            result = {"ok": False, "available": False, "reason": "ambient_image_send_disabled"}
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "manual_ambient_image_blocked",
                    "actor": actor,
                    "text": text[:240],
                    "result": result,
                },
            )
            return _format_ambient_image_failure_reply(result)
        gate = ambient_image_gate(
            session,
            self.ambient_image_config,
            trigger_override="manual",
        )
        if not gate.get("ok"):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "manual_ambient_image_blocked",
                    "actor": actor,
                    "text": text[:240],
                    "result": audit_safe_ambient_image_result(gate),
                },
            )
            return _format_ambient_image_failure_reply(gate)
        story_moment = _ambient_image_story_moment_from_manual_request(text, session)
        rationale = "玩家显式请求使用独立图片 API 生成当前跑团氛围图。"
        self.router._mark_ambient_image_generation_started(session)
        self._schedule_manual_ambient_image(
            event,
            session_id,
            actor,
            text,
            story_moment=story_moment,
            rationale=rationale,
        )
        self.repository.append_audit(
            session_id,
            {
                "type": "local_fast_path",
                "action": "manual_ambient_image_scheduled",
                "actor": actor,
                "text": text[:240],
                "story_moment": story_moment,
            },
        )
        return "已开始生成氛围图，走独立图片 API key；完成后会作为独立消息发送。"

    def _ambient_image_provider_unavailable(self) -> dict[str, Any]:
        provider = getattr(self.router, "ambient_image_provider", None)
        checker = getattr(provider, "_unavailable", None)
        if not callable(checker):
            return {}
        result = checker()
        return dict(result) if isinstance(result, dict) and result else {}

    def _schedule_manual_ambient_image(
        self,
        event: AstrMessageEvent,
        session_id: str,
        actor: dict[str, str],
        message: str,
        *,
        story_moment: str,
        rationale: str,
    ) -> None:
        umo = str(getattr(event, "unified_msg_origin", "") or session_id)
        task = asyncio.create_task(
            self._maybe_generate_manual_ambient_image(
                session_id,
                umo,
                actor,
                message,
                story_moment=story_moment,
                rationale=rationale,
            )
        )
        task.add_done_callback(
            lambda completed: self._manual_ambient_image_task_done(session_id, completed)
        )
        self.plugin_logger.info(
            "ambient_image_manual_scheduled session=%s actor=%s story_moment=%s",
            session_id,
            actor.get("player_id", ""),
            story_moment[:120].replace("\n", "\\n"),
        )

    def _manual_ambient_image_task_done(self, session_id: str, task: asyncio.Task) -> None:
        self.router._clear_ambient_image_generation_started(session_id)
        if task.cancelled():
            self.plugin_logger.warning("ambient_image_manual_task_cancelled session=%s", session_id)
            return
        try:
            exc = task.exception()
        except Exception as task_exc:
            self.plugin_logger.warning(
                "ambient_image_manual_task_status_failed session=%s error=%s",
                session_id,
                task_exc,
            )
            return
        if exc:
            self.plugin_logger.error(
                "ambient_image_manual_task_failed session=%s error=%s",
                session_id,
                exc,
            )

    async def _maybe_generate_manual_ambient_image(
        self,
        session_id: str,
        umo: str,
        actor: dict[str, str],
        message: str,
        *,
        story_moment: str,
        rationale: str,
    ) -> None:
        try:
            provider_id = await self.astr_context.get_current_chat_provider_id(
                umo=umo,
            )
        except TypeError:
            provider_id = ""
        except Exception as exc:
            self.plugin_logger.warning("ambient_image_manual_provider_id_failed session=%s error=%s", session_id, exc)
            provider_id = ""
        tools = AmbientImageTools(
            self.repository,
            session_id,
            self.ambient_image_config,
            self.router.ambient_image_provider,
            actor=actor,
            message=message,
            llm_generate=self.router._llm_generate,
            chat_provider_id=provider_id,
        )
        result = await tools.generate_ambient_image(
            story_moment=story_moment,
            rationale=rationale,
            send_to_chat=True,
            trigger_override="manual",
            ignore_generation_in_progress=True,
        )
        send_result = {"sent": False, "reason": "ambient_image_not_generated"}
        if result.get("ok") and result.get("available"):
            send_result = await self.router._send_ambient_image_if_configured(session_id, result)
        self.repository.append_audit(
            session_id,
            {
                "type": "ambient_image_manual_attempt",
                "actor": actor,
                "result": {
                    key: value
                    for key, value in result.items()
                    if key not in {"file_path", "metadata_path"}
                },
                "send_result": send_result,
            },
        )
        if result.get("ok") and result.get("available") and send_result.get("sent"):
            self.plugin_logger.info(
                "ambient_image_manual_generated session=%s actor=%s",
                session_id,
                actor.get("player_id", ""),
            )
            return
        failure = result if not (result.get("ok") and result.get("available")) else send_result
        await self._send_manual_ambient_image_failure(session_id, failure)

    async def _send_manual_ambient_image_failure(self, session_id: str, result: dict[str, Any]) -> None:
        text = _format_ambient_image_failure_reply(result)
        chain = MessageChain(chain=[Plain(text=text)])
        try:
            await self.astr_context.send_message(session_id, chain)
        except Exception as exc:
            self.plugin_logger.warning(
                "ambient_image_manual_failure_send_failed session=%s error=%s",
                session_id,
                exc,
            )

    def _quoted_result(
        self,
        event: AstrMessageEvent,
        text: str,
        pending_outputs: list[dict] | None = None,
        dice_summary: str = "",
    ):
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        pending_outputs = pending_outputs or []
        components = []
        if not message_id:
            if not pending_outputs:
                body = _join_reply_sections(dice_summary, text)
                return event.plain_result(body)
        else:
            components.append(Reply(id=message_id))
        components.append(Plain(text=_join_reply_sections(dice_summary, text)))
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

    def _format_dice_summary(self, items: list[dict]) -> str:
        lines = []
        for index, item in enumerate(items[:3], start=1):
            dice_text = self._format_dice_check(item)
            if dice_text:
                lines.append(f"{index}. {dice_text}")
        if not lines:
            return ""
        if len(items) > 3:
            lines.append(f"另有 {len(items) - 3} 条检定已省略。")
        return "本轮检定摘要：\n" + "\n".join(lines)

    def _should_send_dm_ack(self, session_id: str, sender_id: str, now: float | None = None) -> bool:
        current = monotonic() if now is None else now
        recent = getattr(self, "_recent_dm_acks", None)
        if recent is None:
            recent = {}
            self._recent_dm_acks = recent
        key = (session_id, sender_id)
        last = recent.get(key)
        if last is not None and current - last < self.DM_ACK_COOLDOWN_SECONDS:
            return False
        recent[key] = current
        stale_before = current - max(self.DM_ACK_COOLDOWN_SECONDS * 6, 60.0)
        for old_key, old_time in list(recent.items()):
            if old_time < stale_before:
                recent.pop(old_key, None)
        return True

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
            visible_pending = [item for item in pending if item.get("type") != "ambient_image"]
            dropped_ambient = len(pending) - len(visible_pending)
            session.scene["_pending_outputs"] = []
            self.repository.save_session(session)
            if dropped_ambient:
                self.plugin_logger.info(
                    "ambient_image_pending_outputs_dropped session=%s count=%s",
                    session_id,
                    dropped_ambient,
                )
            return visible_pending
        except Exception as exc:
            self.plugin_logger.warning("pending_outputs_pop_failed session=%s error=%s", session_id, exc)
            return []

    def _schedule_pause_resume_ambient_image(
        self,
        event: AstrMessageEvent,
        session_id: str,
        actor: dict[str, str],
        message: str,
        *,
        story_moment: str,
        rationale: str,
    ) -> None:
        if not self.ambient_image_config.enabled:
            return
        umo = str(getattr(event, "unified_msg_origin", "") or session_id)
        task = asyncio.create_task(
            self._maybe_generate_pause_resume_ambient_image(
                session_id,
                umo,
                actor,
                message,
                story_moment=story_moment,
                rationale=rationale,
            )
        )
        task.add_done_callback(
            lambda completed: self._pause_resume_ambient_image_task_done(session_id, completed)
        )
        self.plugin_logger.info(
            "ambient_image_pause_resume_scheduled session=%s actor=%s",
            session_id,
            actor.get("player_id", ""),
        )

    def _pause_resume_ambient_image_task_done(self, session_id: str, task: asyncio.Task) -> None:
        if task.cancelled():
            self.plugin_logger.warning("ambient_image_pause_resume_task_cancelled session=%s", session_id)
            return
        try:
            exc = task.exception()
        except Exception as task_exc:
            self.plugin_logger.warning(
                "ambient_image_pause_resume_task_status_failed session=%s error=%s",
                session_id,
                task_exc,
            )
            return
        if exc:
            self.plugin_logger.error(
                "ambient_image_pause_resume_task_failed session=%s error=%s",
                session_id,
                exc,
            )

    async def _maybe_generate_pause_resume_ambient_image(
        self,
        session_id: str,
        umo: str,
        actor: dict[str, str],
        message: str,
        *,
        story_moment: str,
        rationale: str,
    ) -> None:
        if not self.ambient_image_config.enabled:
            return
        try:
            provider_id = await self.astr_context.get_current_chat_provider_id(
                umo=umo,
            )
        except TypeError:
            provider_id = ""
        except Exception as exc:
            self.plugin_logger.warning("ambient_image_provider_id_failed session=%s error=%s", session_id, exc)
            provider_id = ""
        tools = AmbientImageTools(
            self.repository,
            session_id,
            self.ambient_image_config,
            self.router.ambient_image_provider,
            actor=actor,
            message=message,
            llm_generate=self.router._llm_generate,
            chat_provider_id=provider_id,
        )
        result = await tools.generate_ambient_image(
            story_moment=story_moment,
            rationale=rationale,
            send_to_chat=True,
            trigger_override="pause_resume",
        )
        send_result = await self.router._send_ambient_image_if_configured(session_id, result)
        self.repository.append_audit(
            session_id,
            {
                "type": "ambient_image_pause_resume_attempt",
                "actor": actor,
                "result": {
                    key: value
                    for key, value in result.items()
                    if key not in {"file_path", "metadata_path"}
                },
                "send_result": send_result,
            },
        )
        if result.get("ok") and result.get("available"):
            self.plugin_logger.info(
                "ambient_image_pause_resume_generated session=%s actor=%s",
                session_id,
                actor.get("player_id", ""),
            )

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
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"false", "0", "no", "off", "否", "关闭"}:
                return False
            if normalized in {"true", "1", "yes", "on", "是", "开启"}:
                return True
        return bool(value)

    def _config_str(self, key: str, default: str = "") -> str:
        if not self.config:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = getattr(self.config, key, default)
        if value is None:
            return default
        return str(value).strip()

    def _config_int(self, key: str, default: int) -> int:
        if not self.config:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = getattr(self.config, key, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _config_float(self, key: str, default: float) -> float:
        if not self.config:
            return default
        try:
            value = self.config.get(key, default)
        except AttributeError:
            value = getattr(self.config, key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def _svg_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1] if "}" in str(tag) else str(tag)


def _looks_like_manual_ambient_image_request(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    diagnostic_terms = (
        "为什么",
        "为何",
        "怎么",
        "如何",
        "失败",
        "无法",
        "不能",
        "不可用",
        "配置",
        "设置",
        "debug",
        "diagnose",
        "诊断",
    )
    if any(term in normalized for term in diagnostic_terms):
        return False
    negative_terms = (
        "不要配图",
        "别配图",
        "无需配图",
        "不需要配图",
        "不要生图",
        "别生图",
        "不要生成图片",
        "关闭配图",
        "停止配图",
    )
    if any(term in normalized for term in negative_terms):
        return False
    leading_terms = (
        "配图",
        "生图",
        "出图",
        "画图",
        "氛围图",
        "生成图片",
        "生成一张图",
        "画一张图",
        "画张图",
        "发张图",
        "来张图",
        "generate image",
        "draw image",
        "make an image",
    )
    if normalized in leading_terms:
        return True
    if any(normalized.startswith(term) for term in leading_terms):
        return True
    image_terms = ("配图", "氛围图", "图片", "插图", "生图", "出图", "画图", "image", "illustration", "picture")
    if any(term in normalized for term in ("走独立", "用独立")) and any(term in normalized for term in image_terms):
        return True
    if any(term in normalized for term in ("当前场景", "这一幕", "这幕", "现在", "此刻")) and any(
        term in normalized for term in image_terms
    ):
        return True
    action_terms = ("生成", "画", "绘制", "发", "来张", "来一张", "来个", "做一张", "给当前场景", "给这幕", "给这一幕")
    return any(term in normalized for term in action_terms) and any(term in normalized for term in image_terms)


def _ambient_image_story_moment_from_manual_request(text: str, session) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(
        r"^(请|帮我|给我|麻烦|走独立(?:apikey|api key|api)?|用独立(?:apikey|api key|api)?)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(生成|画|绘制|发|来|做)?\s*(一张|一幅|张|个)?\s*(氛围图|配图|生图|出图|图片|插图|image|illustration|picture)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" ：:，,。.-")
    if cleaned and len(cleaned) >= 2:
        return _compact_text(cleaned, 500)
    scene = getattr(session, "scene", {}) or {}
    summary = str(scene.get("summary", "") or "").strip()
    conflict = str(scene.get("current_conflict", "") or "").strip()
    last_resolution = scene.get("last_resolution", "")
    if isinstance(last_resolution, dict):
        last_resolution_text = json.dumps(last_resolution, ensure_ascii=False, separators=(",", ":"))
    else:
        last_resolution_text = str(last_resolution or "")
    sections = []
    if summary:
        sections.append(f"当前场景：{summary}")
    if conflict:
        sections.append(f"当前冲突：{conflict}")
    if last_resolution_text:
        sections.append(f"最近结算：{last_resolution_text}")
    if sections:
        return _compact_text("；".join(sections), 500)
    return "根据当前跑团场景生成一张视觉氛围图。"


def _format_ambient_image_failure_reply(result: dict[str, Any]) -> str:
    code = _ambient_image_reason_code(result)
    if code == "ambient_image_disabled":
        return "氛围图功能当前未启用；请在插件配置打开 `ambient_image_enabled`，再配置独立生图 API key。"
    if code == "ambient_image_api_key_missing":
        env_name = str(result.get("api_key_env") or "PACKYAPI_SORA_API_KEY").strip()
        if not _looks_like_env_var_name(env_name):
            return (
                "独立生图 API key 没有读取到。"
                "可以直接在插件配置的 `ambient_image_api_key` 填真实 key；"
                "`ambient_image_api_key_env` 只在你想用环境变量时填写变量名。"
            )
        return (
            "独立生图 API key 没有读取到。"
            "最简单的填法：在插件配置里把 `ambient_image_api_key` 填成真实 key。"
            f"如果你想继续用环境变量，则当前会读取 `{env_name}`，需要把真实 key 设置到 AstrBot 运行进程的这个环境变量里并重启。"
        )
    if code == "ambient_image_api_mode_invalid":
        return "独立生图 API 模式无效；`ambient_image_api_mode` 只能是 `images` 或 `chat_completions`。"
    if code == "ambient_image_base_url_missing":
        return "独立生图 API base URL 为空或无效；请检查 `ambient_image_base_url`，默认是 `https://www.packyapi.com`。"
    if code == "ambient_image_send_disabled":
        return "氛围图生成后的聊天发送被关闭了；请把 `ambient_image_send_to_chat` 设为 `true`，否则手动配图会看起来像没有结果。"
    if code == "ambient_image_combat_active":
        return "当前处于战斗/战棋模式，氛围图不会生成；战棋地图走地图工具，不走独立生图 API。"
    if code == "ambient_image_generation_in_progress":
        elapsed = result.get("generation_minutes_elapsed", 0)
        required = result.get("generation_minutes_required", 5)
        return f"已有氛围图生成任务在进行中；当前锁定已过约 {elapsed} 分钟，满 {required} 分钟会自动允许重试。"
    if code == "ambient_image_prompt_model_missing":
        return "氛围图 prompt 模型不可用；独立图片 API 只负责出图，前面还需要当前对话模型先生成图片 prompt。"
    if code == "ambient_image_prompt_model_failed":
        reason = _compact_text(result.get("reason", ""), 180)
        return f"氛围图 prompt 生成失败，独立图片 API 尚未被调用。{reason}"
    if code == "ambient_image_http_error":
        status = result.get("status", "")
        reason = _compact_text(result.get("reason", ""), 180)
        return f"独立生图 API 返回 HTTP 错误 {status}；{reason}"
    if code == "ambient_image_network_error":
        reason = _compact_text(result.get("reason", ""), 180)
        return f"独立生图 API 网络请求失败；{reason}"
    if code == "ambient_image_timeout":
        return "独立生图 API 请求超时；本轮没有生成图片，跑团存档不会受影响。"
    if code == "ambient_image_result_missing":
        return "独立生图 API 返回里没有可用图片 URL 或 base64 图片数据；请检查模型、API 模式和返回格式配置。"
    if code == "ambient_image_content_type_invalid":
        return "独立生图 API 返回的图片地址下载后不是图片内容；请检查 provider 返回的 URL 或 `response_format`。"
    if code == "ambient_image_too_large":
        return "独立生图 API 返回的图片或响应体超过插件安全大小限制，本轮已跳过。"
    if code in {"ambient_image_send_failed", "ambient_image_sender_missing"}:
        return "氛围图已生成，但独立消息发送失败；请查看插件日志里的 `ambient_image_independent_send_failed` 记录。"
    detail = _compact_text(result.get("reason") or result.get("message") or "", 180)
    if detail:
        return f"氛围图这次没有生成；失败项是 `{code}`：{detail}"
    return f"氛围图这次没有生成；失败项是 `{code}`。"


def _ambient_image_reason_code(result: dict[str, Any]) -> str:
    return str(result.get("error") or result.get("reason") or "ambient_image_failed")


def _looks_like_env_var_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", str(value or "").strip()))


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
    if _campaign_game_started(session):
        return "开场后（主线与既有角色卡锁定，只记录场内状态；新玩家可建新角色）"
    return "开场前（可设定背景、建卡/补卡，尚未进入主线）"


def _campaign_game_started(session) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    return bool(scene.get("_game_started") or scene.get("_legacy_live_campaign") or world_tags.get("_plot_locked") is True)


def _campaign_action_pacing_enabled(session) -> bool:
    scene = session.scene or {}
    battle = session.battle or {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if _scene_looks_concluded_for_pacing(session):
        return False
    if (
        bool(battle.get("active"))
        or bool(turn.get("active"))
    ):
        return True
    return False


def _post_start_reasonableness_fast_reply(session, text: str) -> str:
    if not _campaign_game_started(session):
        return ""
    normalized = str(text or "").strip().lower()
    if not normalized:
        return ""
    if (
        _looks_like_late_join_power_overreach(normalized)
        or _looks_like_world_law_rewrite(normalized)
        or _looks_like_forced_scene_takeover(normalized)
        or _looks_like_self_cleansing_overreach(normalized)
    ):
        return (
            "这个主张不能直接进入场内事实。开场后允许新玩家加入，但只能创建符合当前团尺度的个人角色；"
            "不能自带军队、传奇随从、跨作品神级身份，不能路过式改写阵营胜负，也不能用“世界意志/规则修正”清除现实。"
            "请改成一个有限、可检定的个人目标。"
        )
    return ""


def _looks_like_late_join_power_overreach(text: str) -> bool:
    mythic_identity = (
        "帝皇",
        "神皇",
        "原体",
        "禁军",
        "星际战士",
        "阿斯塔特",
        "战锤",
        "创世神",
        "造物主",
        "世界意志",
        "神格",
        "半神",
        "神明",
        "神祇",
    )
    force_terms = (
        "十三个原体",
        "十三名原体",
        "13个原体",
        "13名原体",
        "一队原体",
        "带着原体",
        "带着十三",
        "带着军队",
        "带着军团",
        "随从",
        "护卫",
        "军团路过",
    )
    join_or_command = (
        "我加入",
        "我要加入",
        "角色是",
        "我是",
        "我正好",
        "路过这里",
        "刚好路过",
        "带着",
        "降临",
        "指挥",
        "收走",
        "征的税",
        "砍卫兵",
    )
    return (
        any(term in text for term in mythic_identity)
        and any(term in text for term in join_or_command)
    ) or any(term in text for term in force_terms)


def _looks_like_world_law_rewrite(text: str) -> bool:
    law_terms = ("世界意志", "世界观", "现实", "法则", "底层逻辑", "位面基石", "宇宙规则", "dnd2024")
    rewrite_terms = ("修正", "清除", "清理", "抹除", "排除", "踢出", "移除", "重塑", "改写", "纠正")
    target_terms = ("不符合", "不合理", "异界", "跨作品", "所有", "一切", "事物", "存在")
    return any(term in text for term in law_terms) and any(term in text for term in rewrite_terms) and any(
        term in text for term in target_terms
    )


def _looks_like_forced_scene_takeover(text: str) -> bool:
    force_terms = ("所有卫兵", "全部卫兵", "税务官", "军团", "平民", "所有人", "整个小镇", "所有事物")
    outcome_terms = ("都听我的", "交给我", "收走", "清除", "臣服", "跪下", "投降", "全部消失", "直接成功")
    return any(term in text for term in force_terms) and any(term in text for term in outcome_terms)


def _looks_like_self_cleansing_overreach(text: str) -> bool:
    status_terms = ("debuff", "负面状态", "负面效果", "震慑", "眩晕", "反噬", "劣势", "惩罚")
    cleanse_terms = ("不受任何", "不受", "免疫", "无视", "清除", "消除", "解除", "不会受到")
    return any(term in text for term in status_terms) and any(term in text for term in cleanse_terms)


def _unbound_tactical_actor_reply(session, actor: dict[str, str], text: str) -> str:
    battle = session.battle or {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if not turn.get("active") or str(turn.get("phase") or "") != "character_turn":
        return ""
    player_id = str(actor.get("player_id") or "").strip()
    if not player_id:
        return ""
    bound_id = str((session.player_character_map or {}).get(player_id, "") or "").strip()
    if bound_id and bound_id in (session.characters or {}):
        return ""
    normalized = str(text or "").strip().lower()
    if not normalized or _looks_like_non_action_request(normalized):
        return ""
    if not _looks_like_unbound_scene_action(normalized):
        return ""
    return (
        "你还没有绑定本场角色，这句不能作为角色行动结算。"
        "开场后仍可加入新角色：请发 `/dm 我加入，角色是……`；"
        "如果只是交代旁观或问状态，可以用 `/dm 当前轮次` 或 `/dm 玩家列表`。"
    )


def _looks_like_unbound_scene_action(text: str) -> bool:
    action_terms = (
        "上交",
        "交出",
        "交给",
        "缴纳",
        "支付",
        "掏出",
        "拿出",
        "递给",
        "捡起",
        "拾取",
        "带走",
        "攻击",
        "射击",
        "施法",
        "移动",
        "靠近",
        "搜索",
        "调查",
        "侦查",
        "防御",
        "躲避",
    )
    return _looks_like_paced_player_action(text) or any(term in text for term in action_terms)


def _looks_like_terminal_or_interlude_for_pacing(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    terminal_terms = (
        "全局结算",
        "展示结算",
        "退出游戏",
        "结束游戏",
        "跑团结束",
        "本场结束",
        "本次结束",
        "到此结束",
        "正式落幕",
        "圆满落幕",
        "个人结局",
        "结局",
        "后日谈",
        "尾声",
        "下一段冒险",
        "下一次冒险",
        "下次冒险",
        "下个冒险",
        "下回冒险",
        "下次开团",
        "下回开团",
    )
    interlude_terms = (
        "休息一会",
        "休息一下",
        "休息到下",
        "睡到下",
        "沉睡直到",
        "沉睡到下",
        "休眠直到",
        "休眠到下",
        "直到下次",
        "无人可以打扰",
        "玩家们都累",
        "来点背景剧情",
        "背景剧情描述",
        "间幕",
        "休整",
    )
    return any(term in normalized for term in terminal_terms) or any(term in normalized for term in interlude_terms)


def _scene_looks_concluded_for_pacing(session) -> bool:
    scene = session.scene or {}
    if scene.get("_post_game") or scene.get("_encounter_ended_at"):
        return True
    try:
        text = json.dumps(
            [
                scene.get("summary", ""),
                scene.get("current_conflict", ""),
                scene.get("last_resolution", {}),
            ],
            ensure_ascii=False,
        )
    except TypeError:
        text = str(scene)
    return any(
        term in text
        for term in (
            "危机已正式解除",
            "危机已落下帷幕",
            "跑团到此",
            "本场跑团到此",
            "圆满结束",
            "圆满落幕",
            "正式落幕",
            "全局结算",
            "最终结局",
            "暂无。世界正处于",
            "暂无冲突",
            "当前冲突：暂无",
        )
    )


def _heartbeat_turn_suspend_reason(session, turn: dict, phase: str) -> str:
    if _scene_looks_concluded_for_pacing(session):
        return "scene_concluded"
    if _recent_turn_log_has_terminal_or_interlude(turn):
        return "terminal_turn_log"
    if phase == "character_turn" and _scene_looks_social_or_political_for_heartbeat(session):
        return "social_or_political_scene"
    if _repeated_timeout_after_auto_pause(session, turn):
        return "repeated_timeout_after_auto_pause"
    return ""


def _recent_turn_log_has_terminal_or_interlude(turn: dict) -> bool:
    entries = list(turn.get("turn_log") or [])[-16:]
    if not entries:
        return False
    try:
        text = json.dumps(entries, ensure_ascii=False)
    except Exception:
        text = str(entries)
    return _looks_like_terminal_or_interlude_for_pacing(text)


def _scene_looks_social_or_political_for_heartbeat(session) -> bool:
    scene = session.scene or {}
    current_conflict = str(scene.get("current_conflict") or "")
    if not current_conflict.strip():
        return False
    social_terms = (
        "谈判",
        "劝说",
        "威吓",
        "征税",
        "税务",
        "收缴",
        "免税",
        "军团",
        "平民",
        "镇民",
        "抗议",
        "骚乱",
        "软禁",
        "从军",
        "审判",
        "交涉",
        "政治",
        "秩序",
        "统治",
    )
    combat_terms = (
        "战斗",
        "遭遇",
        "先攻",
        "敌方回合",
        "攻击",
        "命中",
        "伤害",
        "龙息",
        "红龙",
        "怪物",
        "hp",
        "生命值",
        "战棋",
        "格子",
    )
    return any(term in current_conflict for term in social_terms) and not any(
        term in current_conflict for term in combat_terms
    )


def _repeated_timeout_after_auto_pause(session, turn: dict) -> bool:
    scene = session.scene or {}
    pause_reason = str(scene.get("_dm_pause_reason") or "")
    if "超时" not in pause_reason and "自动暂停" not in pause_reason and "半数" not in pause_reason:
        return False
    if not scene.get("_dm_resumed_at"):
        return False
    try:
        count = int(turn.get("_timeout_tracker_count") or len(list(turn.get("_timeout_tracker_keys") or [])))
        total = int(turn.get("_timeout_tracker_total") or len(list(turn.get("turn_order") or [])))
    except (TypeError, ValueError):
        return False
    if total <= 0:
        return False
    return count >= 2 and count * 3 >= total


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


def _looks_like_dm_autopilot_takeover(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    all_player_scope = any(
        term in normalized
        for term in (
            "所有玩家",
            "全部玩家",
            "全体玩家",
            "所有角色",
            "全部角色",
            "所有人物",
            "全部人物",
            "玩家将不再",
            "玩家不再",
        )
    )
    takeover = any(
        term in normalized
        for term in (
            "交由你操作",
            "交给你操作",
            "由你操作",
            "由你控制",
            "交给ai",
            "交给 ai",
            "全权托管",
            "全员托管",
            "自动推演后续剧情",
            "自动结算后续剧情",
            "玩家将不再干预",
            "玩家不再干预",
            "玩家将不再介入",
            "玩家不再介入",
        )
    )
    return all_player_scope and takeover


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
    if _looks_like_terminal_or_interlude_for_pacing(text):
        return True
    if _looks_like_rules_or_adjudication_meta_request(text):
        return True
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


def _looks_like_rules_or_adjudication_meta_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    meta_terms = (
        "重新判定",
        "重判",
        "重新计算",
        "重新裁定",
        "要求修复",
        "修复或者重新判定",
        "你误解",
        "你的错误",
        "规则错误",
        "脚本中有错误",
        "判定脚本",
        "骰子判定",
        "dc不合理",
        "dc 不合理",
        "加值你没有计算",
        "没有计算加值",
        "正确计算我的加值",
    )
    rules_context = ("规则", "判定", "检定", "骰", "dc", "加值", "脚本", "错误", "不合理", "裁定")
    return any(term in normalized for term in meta_terms) and any(term in normalized for term in rules_context)


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


def _join_reply_sections(*sections: object) -> str:
    return "\n\n".join(str(section).strip() for section in sections if str(section or "").strip())


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


def _looks_like_reset_confirmation_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return bool(lowered) and any(
        term in lowered
        for term in ("确认重开", "确认清空", "确认重置", "确认新团", "确认", "confirm reset", "confirm-reset")
    )


def _pending_reset_confirmation_token(session, actor: dict[str, str]) -> str:
    scene = dict(getattr(session, "scene", {}) or {})
    pending = dict(scene.get("_pending_reset_confirmation") or {})
    token = str(pending.get("token") or "").strip().upper()
    if not token:
        return ""
    requester = str(pending.get("requester_player_id") or "").strip()
    current = str((actor or {}).get("player_id") or "").strip()
    if requester and current and requester != current:
        return ""
    expires_at = _parse_datetime(pending.get("expires_at"))
    if expires_at and datetime.now(timezone.utc) > expires_at:
        return ""
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


def _guided_background_patch_from_text(text: str) -> dict:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return {}
    if not (
        _looks_like_background_authoring_request(text)
        or _looks_like_enough_background_seed(text)
        or _looks_like_new_campaign_seed_request(text)
    ):
        return {}
    character_only = any(term in lowered for term in ("角色卡", "人物卡", "建卡", "随机创建角色", "随机建卡"))
    delegated_background = any(
        term in lowered
        for term in (
            "你来定",
            "你定吧",
            "你决定",
            "随便定",
            "自动生成",
            "智能补完",
            "不用多问",
            "直接开始",
            "故事",
            "剧本",
            "副本",
            "背景",
            "世界观",
        )
    )
    if character_only and not (_looks_like_enough_background_seed(text) or delegated_background):
        return {}
    if any(term in lowered for term in ("战锤", "40k", "warhammer", "极限战士", "阿斯塔特", "基因窃取者", "底巢", "巢都")):
        location = "底巢（Underhive）" if any(term in lowered for term in ("底巢", "巢都", "下巢")) else "帝国边境战区"
        return {
            "genre": "grimdark_sci_fi",
            "tone": "哥特军事恐怖、克制高压、重视火力与代价",
            "factions": ["Ultramarines（极限战士）", "Genestealer Cult（基因窃取者教派）"],
            "starting_premise": _compact_text(text, 240),
            "location": location,
            "ruleset": "homebrew_warhammer40k_adaptation；风险、命中、伤害和资源消耗用 d20/伤害骰裁定",
            "campaign_background": _compact_text(text, 320),
        }
    patch: dict = {}
    genre_terms = _terms_found(
        lowered,
        (
            "末世",
            "废土",
            "核战",
            "科幻",
            "奇幻",
            "玄幻",
            "异世界",
            "穿越",
            "现代",
            "赛博",
            "克苏鲁",
            "悬疑",
            "武侠",
            "太空",
            "蒸汽",
            "中世纪",
            "历史",
            "低魔",
            "无魔",
            "dnd",
            "coc",
        ),
    )
    if genre_terms:
        patch["genre"] = "、".join(genre_terms)
    tone_terms = _terms_found(lowered, ("严肃", "荒诞", "宏大", "危险", "恐怖", "轻松", "黑暗", "求生", "调查", "热血", "压抑", "幽默", "温馨"))
    if tone_terms:
        patch["tone"] = "、".join(tone_terms)
    location_terms = _terms_found(lowered, ("地球", "海上", "港口", "王国", "城市", "村庄", "荒野", "废墟", "空间站", "中继站", "地下城", "酒馆", "学院", "宗门", "领地"))
    if location_terms:
        patch["location"] = "、".join(location_terms)
    if any(term in lowered for term in ("势力", "组织", "公司", "教团", "军团", "帮派", "敌人", "怪物", "派系", "贵族", "朝廷")):
        patch["factions"] = _compact_text(text, 180)
    if any(term in lowered for term in ("规则", "系统", "检定", "骰", "d20", "dnd", "coc", "无魔", "没有魔")):
        patch["ruleset"] = "以 d20 检定为基础；概率、风险和对抗行动必须投骰。"
    if any(term in lowered for term in ("开始游戏", "开场", "开局", "故事", "剧本", "副本", "任务", "求救", "来到", "醒来", "我是", "我们是", "扮演", "担任")):
        patch["starting_premise"] = _compact_text(text, 240)
    if patch:
        patch.setdefault("tone", "由 DM 补全细节，保持可裁定、可推进、不过度追问")
        patch.setdefault("ruleset", "以 d20 检定为基础；概率、风险和对抗行动必须投骰。")
        patch.setdefault("campaign_background", _compact_text(text, 320))
        return patch
    if delegated_background:
        return {
            "genre": "低魔边境冒险",
            "tone": "克制、危险、重选择后果",
            "starting_premise": "玩家授权 DM 自动生成：一份异常委托把角色带到边境港镇，第一幕从失踪货船与封锁码头开始。",
            "location": "边境港镇与近海航道",
            "factions": "港务行会、旧贵族私兵、海盗残党、沉默教团",
            "ruleset": "以 d20 检定为基础；概率、风险和对抗行动必须投骰。",
            "campaign_background": "玩家未指定细节，DM 自动生成一个易开场、可裁定的低魔边境冒险背景。",
        }
    return {}


def _terms_found(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term and term in text]


def _looks_like_enough_background_seed(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    explicit = any(token in lowered for token in ("背景", "世界观", "设定", "题材", "类型", "风格", "环境", "premise", "setting"))
    buckets = 0
    if any(token in lowered for token in ("末世", "废土", "核战", "修仙", "仙侠", "文明", "文明重建", "科幻", "奇幻", "玄幻", "异界", "异世界", "穿越", "重生", "现代", "赛博", "克苏鲁", "悬疑", "武侠", "太空", "蒸汽", "欧洲", "中世纪", "历史", "低魔", "无魔", "纯剑", "dnd", "coc", "d20", "战锤", "40k", "warhammer", "grimdark", "暗黑科幻", "哥特科幻")):
        buckets += 1
    if any(token in lowered for token in ("严肃", "荒诞", "宏大", "悲剧", "失败", "危险", "恐怖", "轻松", "日常", "经营", "种田", "后宫", "宫斗", "黑暗", "求生", "调查", "热血", "压抑", "幽默", "温馨")):
        buckets += 1
    if any(token in lowered for token in ("开始游戏", "正式开始", "开场", "开局", "第一幕", "故事", "剧本", "副本", "因为", "为了", "想要", "最终", "任务", "求救", "聚集", "来到", "醒来", "退休", "我是", "我们是", "担任", "扮演")):
        buckets += 1
    if any(token in lowered for token in ("地点", "城市", "村庄", "荒野", "废墟", "船上", "游艇", "空间站", "中继站", "塔", "地下城", "酒馆", "咖啡馆", "店", "学院", "宗门", "宫廷", "领地", "地球", "海上", "海战", "港口", "王国", "底巢", "巢都", "星球", "战区")):
        buckets += 1
    if any(token in lowered for token in ("势力", "组织", "公司", "教团", "军团", "帮派", "敌人", "怪物", "派系", "店员", "猫娘", "贵族", "朝廷", "极限战士", "基因窃取者", "阿斯塔特", "星际战士")):
        buckets += 1
    if any(token in lowered for token in ("规则", "系统", "检定", "骰", "属性", "等级", "没有魔", "没有魔法", "不存在超自然", "无超自然", "超自然力量")):
        buckets += 1
    delegated_start = any(token in lowered for token in ("补全", "补完", "智能补完", "不用多问", "直接开始", "开始游戏", "开局", "开场"))
    return buckets >= 2 and (explicit or delegated_start or buckets >= 3 or len(lowered) >= 28)


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
        "你来补充",
        "你来完善",
        "补充更多细节",
        "自动生成",
        "智能补完",
        "不用多问",
    )
    if any(token in lowered for token in delegation_terms):
        return True
    if any(token in lowered for token in ("补全", "补完", "完善", "扩写", "开始游戏", "开局", "开场")) and _looks_like_enough_background_seed(text):
        return True
    if not any(token in lowered for token in subject_terms):
        return False
    return any(token in lowered for token in authoring_terms) or len(lowered) >= 10


def _looks_like_new_campaign_seed_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if _looks_like_reset_request(text):
        return True
    start_or_delegate = any(
        token in lowered
        for token in (
            "开始游戏",
            "正式开始",
            "开局",
            "开场",
            "进入剧情",
            "补完后开始",
            "补全后开始",
            "智能补完",
            "不用多问",
            "故事",
            "剧本",
            "副本",
        )
    )
    return start_or_delegate and _looks_like_enough_background_seed(text)


def _session_has_meaningful_campaign_content(session) -> bool:
    if session.characters or session.player_character_map or session.rules:
        return True
    if has_campaign_background(session):
        return True
    if bool((session.battle or {}).get("active")):
        return True
    scene = dict(session.scene or {})
    if scene.get("_game_started") or scene.get("_plot_locked") or scene.get("_legacy_live_campaign"):
        return True
    summary = str(scene.get("summary", "") or "").strip()
    if summary and not any(token in summary for token in ("尚未开局", "等待玩家", "未开始")):
        return True
    return False
