from __future__ import annotations

import asyncio
import hashlib
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
from .core.admin_web import AutoTrpgAdminWeb
from .core.external_memory import HonchoExternalMemory, HonchoMemoryConfig
from .core.map_core import load_active_strict_grid_entities
from .core.map_delivery_cadence import filter_map_pending_outputs_for_delivery
from .core.map_tool_routing import looks_visual_map_request
from .core.plugin_log import configure_plugin_logging
from .core.router import IntentRouter
from .core.scenario_templates import (
    build_campaign_preference_question,
    build_campaign_preset_patch,
    build_campaign_seed_patch,
    campaign_preset_start_requested,
    format_campaign_preset_list,
    format_campaign_preset_loaded_reply,
    looks_like_campaign_preset_list_request,
    looks_like_campaign_generation_request,
    looks_like_campaign_preference_answer,
    looks_like_custom_campaign_brief,
    select_campaign_preset,
    should_ask_campaign_preferences,
    template_by_key,
)
from .core.security import security_precheck
from .core.models import CycleState, GameMode
from .core.timeline import timeline_status_text
from .core.turn_labels import fallback_turn_entity_label, public_turn_entity_label, turn_actor_kind, turn_entity_owner_id
from .rules.python_runtime import PythonRuleRuntime
from .storage.json_repository import JsonGameRepository
from .tools.ambient_image_tools import (
    AmbientImageTools,
    ambient_image_gate,
    audit_safe_ambient_image_result,
)
from .tools.diagnostic_tools import DiagnosticTools
from .tools.memory_tools import MemoryTools, has_campaign_background
from .core.scene_hooks import format_scene_tracking_status
from .tools.registry import ToolRegistry
from .tools.turn_tools import TurnTools


PLUGIN_VERSION = "0.1.124"

DEFAULT_REASSURANCE_PHRASES = (
    "??????????",
    "????????????",
    "???????????",
    "??????",
    "???????",
    "??????",
    "??????????",
    "??????????",
    "????????",
    "????????",
)
DEFAULT_REASSURANCE_MAP_PHRASES = (
    "????????",
    "?????????",
    "???????????",
    "????",
    "??????",
    "??????",
    "????????",
    "???????",
    "SVG?????",
    "????????",
)
DEFAULT_REASSURANCE_STYLE_POOLS = {
    "fantasy": (
        "?????????",
        "????????",
        "??????????",
        "??????",
        "????????",
        "??????",
        "????????",
        "???????",
        "?????????",
        "???????",
    ),
    "grimdark_scifi": (
        "???????????",
        "???????????",
        "?????????",
        "???????",
        "????????",
        "??????",
        "??????????",
        "?????????",
        "????????",
        "????????",
    ),
    "urban_occult": (
        "????????",
        "??????????",
        "????????",
        "???????",
        "????????",
        "????????",
        "??????",
        "??????",
        "????????",
        "???????",
    ),
    "post_apocalyptic": (
        "?????????",
        "????????",
        "??????????",
        "???????",
        "????????",
        "???????",
        "???????",
        "????????",
        "????????",
        "?????????",
    ),
}
REASSURANCE_STYLE_ALIASES = {
    "fantasy": ("fantasy", "dnd", "d&d", "??", "??", "??", "??"),
    "grimdark_scifi": ("grimdark_scifi", "grimdark", "warhammer", "40k", "??", "??", "??"),
    "urban_occult": ("urban_occult", "urban", "occult", "??", "??", "??", "??"),
    "post_apocalyptic": ("post_apocalyptic", "apocalypse", "wasteland", "??", "??", "????"),
}
REASSURANCE_MAP_TERMS = (
    "??",
    "????",
    "??",
    "svg",
    "????",
    "????",
    "???",
    "???",
    "battle map",
    "map",
    "tactical layout",
    "terrain sketch",
)
REASSURANCE_UNSAFE_TERMS = (
    "???",
    "????",
    "??",
    "????",
    "??",
    "??",
    "????",
    "????",
    "???",
    "????",
    "????",
    "?????",
    "????",
    "????",
)
REASSURANCE_CHOICE_TERMS = (
    "???",
    "?????",
    "????",
    "??????",
    "??",
    "?????",
    "???",
    "???",
    "what do you do",
    "choose",
    "option",
)


@register(
    "auto_trpg_dm",
    "codex",
    "????? TRPG DM?????????????Tag ?????????",
    PLUGIN_VERSION,
)
class AutoTrpgDmPlugin(Star):
    DEDUP_WINDOW_SECONDS = 18.0
    IN_FLIGHT_DUPLICATE_WINDOW_SECONDS = 300.0
    ROUTE_CLAIM_WINDOW_SECONDS = 600.0
    ACTION_PACING_SECONDS = 12
    HEARTBEAT_INTERVAL_SECONDS = 60
    DM_ACK_COOLDOWN_SECONDS = 10.0

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.astr_context = context
        self.trigger_prefixes = ["/dm"]
        self._recent_dm_messages: dict[tuple[str, str, str], float] = {}
        self._inflight_dm_messages: dict[tuple[str, str, str], float] = {}
        self._recent_dm_route_claims: dict[str, float] = {}
        self._recent_dm_acks: dict[tuple[str, str], float] = {}
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_auto_trpg_dm"
        self.repository = JsonGameRepository(data_dir)
        self.plugin_logger = configure_plugin_logging(self.repository.plugin_log_path())
        self.reassurance_enabled = self._config_bool("reassurance_enabled", True)
        self.reassurance_delay_seconds = max(0, self._config_int("reassurance_delay_seconds", 30))
        self.reassurance_cooldown_seconds = max(0, self._config_int("reassurance_cooldown_seconds", 300))
        self.reassurance_prefix = self._config_str("reassurance_prefix", "??????") or "??????"
        self.reassurance_phrases = tuple(self._config_list("reassurance_phrases") or DEFAULT_REASSURANCE_PHRASES)
        self.reassurance_map_phrases = tuple(
            self._config_list("reassurance_map_phrases") or DEFAULT_REASSURANCE_MAP_PHRASES
        )
        self.reassurance_style_phrases_enabled = self._config_bool("reassurance_style_phrases_enabled", True)
        self.reassurance_style_phrase_pools = self._config_reassurance_style_phrase_pools()
        self._recent_reassurance_sent: dict[str, float] = {}
        self._reassurance_tasks: set[asyncio.Task] = set()
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
        llm_tool_loop_max_steps = max(1, self._config_int("llm_tool_loop_max_steps", 16))
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
            max_steps=llm_tool_loop_max_steps,
            ra_enabled=self._config_bool("ra_enabled", False),
            ra_model_provider=self._config_str("ra_model_provider", "default") or "default",
            ra_max_tokens=self._config_int("ra_max_tokens", 2048),
            continuity_auditor_enabled=self._config_bool("continuity_auditor_enabled", True),
            continuity_auditor_model_provider=self._config_str("continuity_auditor_model_provider", "default") or "default",
            continuity_auditor_max_tokens=self._config_int("continuity_auditor_max_tokens", 1200),
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
        self.admin_web = AutoTrpgAdminWeb(self.repository)
        self.plugin_logger.info(
            "plugin_initialized version=%s data_dir=%s honcho_enabled=%s honcho_workspace=%s ambient_image_enabled=%s ambient_image_mode=%s prompt_snapshot_projection_enabled=%s continuity_auditor_enabled=%s heartbeat_idle_log_interval=%s llm_tool_loop_max_steps=%s",
            PLUGIN_VERSION,
            data_dir,
            honcho_config.enabled,
            bool(honcho_config.workspace_id),
            ambient_image_config.enabled,
            ambient_image_config.api_mode,
            prompt_snapshot_projection_enabled,
            self._config_bool("continuity_auditor_enabled", True),
            heartbeat_idle_log_interval,
            llm_tool_loop_max_steps,
        )
        logger.info("Auto TRPG DM plugin initialized.")

    async def initialize(self) -> None:
        registered = self.admin_web.register_routes(self.astr_context)
        if registered:
            self.plugin_logger.info("admin_web_routes_registered count=%s", registered)

    @filter.command("dm")
    async def on_dm_command(self, event: AstrMessageEvent, content: GreedyStr):
        """???????/dm ?????????????"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("DM")
    async def on_dm_command_upper(self, event: AstrMessageEvent, content: GreedyStr):
        """?????? /DM??????? AstrBot ?????"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("Dm")
    async def on_dm_command_title(self, event: AstrMessageEvent, content: GreedyStr):
        """?????? /Dm?"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    @filter.command("dM")
    async def on_dm_command_mixed(self, event: AstrMessageEvent, content: GreedyStr):
        """?????? /dM?"""
        async for result in self._handle_dm_command_content(event, content):
            yield result

    async def _handle_dm_command_content(self, event: AstrMessageEvent, content: GreedyStr):
        routed_message = self._routed_message_from_command_content(content, event=event)
        if not routed_message:
            self.plugin_logger.info(
                "empty_dm_command_ignored session=%s",
                IntentRouter.session_id_for_event(event),
            )
            yield self._quoted_result(event, "??? `/dm` ????????????????")
            event.stop_event()
            return
        if not self._claim_dm_event_route(event, "command"):
            return
        async for result in self._handle_dm_event(event, routed_message):
            yield result

    def _routed_message_from_command_content(self, content: Any, event: AstrMessageEvent | None = None) -> str:
        event_argument = _dm_command_argument_from_event(event)
        if isinstance(content, str):
            routed_message = content.strip()
        elif content is None:
            routed_message = ""
        else:
            routed_message = str(content or "").strip()
            if routed_message == "GreedyStr":
                routed_message = ""
        if event_argument is not None and len(event_argument) > len(routed_message):
            routed_message = event_argument
        if routed_message == "GreedyStr":
            if event_argument is not None:
                routed_message = event_argument
            elif event is not None:
                routed_message = ""
        if not routed_message:
            return ""
        return routed_message

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_any_message(self, event: AstrMessageEvent):
        """????? /dm ??????????? LLM?"""
        message = _event_best_plain_text(event)
        if not message:
            return
        routed_message = self._extract_best_routed_message(event, message)
        if not routed_message:
            return
        if not self._claim_dm_event_route(event, "event_message_type"):
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
            self._mark_message_finished(session_id, sender_id, routed_message)
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
            self._mark_message_finished(session_id, sender_id, routed_message)
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
        reassurance_task = None
        try:
            if self._should_send_dm_ack(session_id, sender_id):
                yield self._quoted_result(event, "????????????")
            reassurance_task = self._start_long_running_reassurance_task(session_id, actor, routed_message)
            completion = await self.router.handle_message(
                event,
                message_override=routed_message,
                security_notes=security.notes,
            )
        except asyncio.CancelledError:
            await self._cancel_long_running_reassurance_task(reassurance_task)
            self._mark_message_finished(session_id, sender_id, routed_message)
            raise
        except Exception as exc:
            await self._cancel_long_running_reassurance_task(reassurance_task)
            self.plugin_logger.exception("dm_failed session=%s sender=%s error=%s", session_id, sender_id, exc)
            logger.exception("Auto TRPG DM failed to handle message.")
            yield self._quoted_result(event, self._friendly_error_message(exc))
            event.stop_event()
            self._mark_message_finished(session_id, sender_id, routed_message)
            return
        await self._cancel_long_running_reassurance_task(reassurance_task)
        pending_outputs = self._pop_pending_outputs(session_id)
        dice_outputs = [item for item in pending_outputs if item.get("type") == "dice_check"]
        other_outputs = [item for item in pending_outputs if item.get("type") != "dice_check"]
        dice_summary = self._format_dice_summary(dice_outputs)
        sent_any = False
        if completion or other_outputs or dice_summary:
            if not completion and other_outputs:
                completion = "??????????"
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
        self._mark_message_finished(session_id, sender_id, routed_message)

    def _start_long_running_reassurance_task(
        self,
        session_id: str,
        actor: dict[str, str],
        routed_message: str,
    ) -> asyncio.Task | None:
        if not bool(getattr(self, "reassurance_enabled", True)):
            return None
        try:
            task = asyncio.create_task(
                self._run_long_running_reassurance_task(session_id, actor, routed_message)
            )
        except RuntimeError as exc:
            self.plugin_logger.warning("long_running_reassurance_schedule_failed session=%s error=%s", session_id, exc)
            return None
        tasks = getattr(self, "_reassurance_tasks", None)
        if tasks is None:
            tasks = set()
            self._reassurance_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        self.plugin_logger.info(
            "long_running_reassurance_scheduled session=%s delay_seconds=%s",
            session_id,
            self._reassurance_delay_seconds(),
        )
        return task

    async def _cancel_long_running_reassurance_task(self, task: asyncio.Task | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self.plugin_logger.info("long_running_reassurance_cancelled")

    async def _run_long_running_reassurance_task(
        self,
        session_id: str,
        actor: dict[str, str],
        routed_message: str,
    ) -> None:
        started_at = monotonic()
        try:
            await self._long_running_reassurance_after_delay(session_id, actor, routed_message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.plugin_logger.exception(
                "long_running_reassurance_failed session=%s error=%s",
                session_id,
                exc,
            )
            self._audit_long_running_reassurance(
                session_id,
                {
                    "type": "long_running_reassurance_send_failed",
                    "actor": actor,
                    "reason": exc.__class__.__name__,
                    "message_hash": _stable_short_hash(routed_message),
                    "elapsed_seconds": round(monotonic() - started_at, 3),
                },
            )

    async def _long_running_reassurance_after_delay(
        self,
        session_id: str,
        actor: dict[str, str],
        routed_message: str,
    ) -> None:
        started_at = monotonic()
        await asyncio.sleep(self._reassurance_delay_seconds())
        choice = self._select_long_running_reassurance(session_id, routed_message)
        if not choice:
            self._audit_long_running_reassurance(
                session_id,
                {
                    "type": "long_running_reassurance_suppressed",
                    "actor": actor,
                    "reason": "no_safe_phrase",
                    "message_hash": _stable_short_hash(routed_message),
                    "elapsed_seconds": round(monotonic() - started_at, 3),
                },
            )
            return
        now = monotonic()
        cooldown_allowed, cooldown_remaining = self._reserve_reassurance_cooldown(session_id, now)
        if not cooldown_allowed:
            self._audit_long_running_reassurance(
                session_id,
                {
                    "type": "long_running_reassurance_suppressed",
                    "actor": actor,
                    "reason": "cooldown",
                    "cooldown_remaining_seconds": cooldown_remaining,
                    "cooldown_seconds": self._reassurance_cooldown_seconds(),
                    "phrase_source": choice["source"],
                    "message_hash": _stable_short_hash(routed_message),
                    "elapsed_seconds": round(now - started_at, 3),
                },
            )
            self.plugin_logger.info(
                "long_running_reassurance_suppressed session=%s reason=cooldown remaining=%s",
                session_id,
                cooldown_remaining,
            )
            return
        text = self._format_long_running_reassurance_text(choice["phrase"])
        sent, failure_reason = await self._send_long_running_reassurance_message(session_id, text)
        record_type = "long_running_reassurance_sent" if sent else "long_running_reassurance_send_failed"
        record = {
            "type": record_type,
            "actor": actor,
            "delay_seconds": self._reassurance_delay_seconds(),
            "cooldown_seconds": self._reassurance_cooldown_seconds(),
            "phrase_source": choice["source"],
            "phrase_hash": _stable_short_hash(choice["phrase"]),
            "text_chars": len(text),
            "message_hash": _stable_short_hash(routed_message),
            "elapsed_seconds": round(monotonic() - started_at, 3),
        }
        if failure_reason:
            record["reason"] = failure_reason
        self._audit_long_running_reassurance(session_id, record)

    async def _send_long_running_reassurance_message(self, session_id: str, text: str) -> tuple[bool, str]:
        try:
            sent = await self.astr_context.send_message(session_id, MessageChain(chain=[Plain(text)]))
        except Exception as exc:
            self.plugin_logger.exception("long_running_reassurance_send_failed session=%s error=%s", session_id, exc)
            return False, exc.__class__.__name__
        if sent:
            self.plugin_logger.info("long_running_reassurance_sent session=%s chars=%s", session_id, len(text))
            return True, ""
        self.plugin_logger.warning("long_running_reassurance_send_no_platform session=%s", session_id)
        return False, "no_platform"

    def _reserve_reassurance_cooldown(self, session_id: str, now: float) -> tuple[bool, int]:
        cooldown = self._reassurance_cooldown_seconds()
        recent = getattr(self, "_recent_reassurance_sent", None)
        if recent is None:
            recent = {}
            self._recent_reassurance_sent = recent
        last = recent.get(session_id)
        if cooldown > 0 and last is not None:
            elapsed = now - last
            if elapsed < cooldown:
                return False, max(1, int(cooldown - elapsed))
        recent[session_id] = now
        stale_before = now - max(cooldown * 4, 600)
        for key, value in list(recent.items()):
            if value < stale_before:
                recent.pop(key, None)
        return True, 0

    def _select_long_running_reassurance(self, session_id: str, routed_message: str) -> dict[str, str] | None:
        if _looks_like_reassurance_map_request(routed_message):
            phrase = self._pick_safe_reassurance_phrase(
                self.reassurance_map_phrases,
                session_id=session_id,
                routed_message=routed_message,
                source="map",
            )
            if phrase:
                return {"phrase": phrase, "source": "map"}
        style_key = self._reassurance_style_key_for_session(session_id)
        if style_key and bool(getattr(self, "reassurance_style_phrases_enabled", True)):
            pools = getattr(self, "reassurance_style_phrase_pools", {}) or {}
            phrase = self._pick_safe_reassurance_phrase(
                pools.get(style_key, ()),
                session_id=session_id,
                routed_message=routed_message,
                source=f"style:{style_key}",
            )
            if phrase:
                return {"phrase": phrase, "source": f"style:{style_key}"}
        phrase = self._pick_safe_reassurance_phrase(
            self.reassurance_phrases,
            session_id=session_id,
            routed_message=routed_message,
            source="neutral",
        )
        if phrase:
            return {"phrase": phrase, "source": "neutral"}
        fallback = self._pick_safe_reassurance_phrase(
            DEFAULT_REASSURANCE_PHRASES,
            session_id=session_id,
            routed_message=routed_message,
            source="default_neutral",
        )
        if fallback:
            return {"phrase": fallback, "source": "default_neutral"}
        return None

    def _pick_safe_reassurance_phrase(
        self,
        phrases: tuple[str, ...] | list[str],
        *,
        session_id: str,
        routed_message: str,
        source: str,
    ) -> str:
        safe = [
            phrase
            for phrase in (str(item).strip() for item in phrases)
            if _is_safe_reassurance_phrase(phrase, self.reassurance_prefix)
        ]
        if not safe:
            return ""
        seed = f"{session_id}:{source}:{routed_message}"
        index = int(hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:8], 16) % len(safe)
        return safe[index]

    def _format_long_running_reassurance_text(self, phrase: str) -> str:
        prefix = str(getattr(self, "reassurance_prefix", "") or "??????").strip() or "??????"
        body = str(phrase or "").strip()
        while body.startswith(prefix):
            body = body[len(prefix) :].strip()
        return f"{prefix}{body}"

    def _reassurance_style_key_for_session(self, session_id: str) -> str:
        try:
            session = self.repository.load_session(session_id)
        except Exception as exc:
            self.plugin_logger.warning("long_running_reassurance_style_load_failed session=%s error=%s", session_id, exc)
            return ""
        world_tags = getattr(session, "world_tags", {}) or {}
        scene = getattr(session, "scene", {}) or {}
        payload = {
            "title": getattr(session, "title", ""),
            "world_tags": world_tags,
            "scene_summary": str(scene.get("summary", ""))[:1200] if isinstance(scene, dict) else "",
            "current_conflict": str(scene.get("current_conflict", ""))[:600] if isinstance(scene, dict) else "",
        }
        try:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).lower()
        except Exception:
            text = str(payload).lower()
        for style_key, aliases in REASSURANCE_STYLE_ALIASES.items():
            for alias in aliases:
                if str(alias).lower() in text:
                    return style_key
        return ""

    def _audit_long_running_reassurance(self, session_id: str, record: dict[str, object]) -> None:
        try:
            self.repository.append_audit(session_id, record)
        except Exception as exc:
            self.plugin_logger.warning("long_running_reassurance_audit_failed session=%s error=%s", session_id, exc)

    def _reassurance_delay_seconds(self) -> float:
        try:
            return max(0.0, float(getattr(self, "reassurance_delay_seconds", 30)))
        except (TypeError, ValueError):
            return 30.0

    def _reassurance_cooldown_seconds(self) -> float:
        try:
            return max(0.0, float(getattr(self, "reassurance_cooldown_seconds", 300)))
        except (TypeError, ValueError):
            return 300.0

    async def _local_fast_path(
        self,
        event: AstrMessageEvent,
        session_id: str,
        actor: dict[str, str],
        routed_message: str,
    ) -> str:
        text = self._dedupe_text(routed_message)
        normalized = text.lower()
        visual_map_request = looks_visual_map_request(text)
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
            return "???????????????????? `/dm status`?`/dm token`?`/dm ????` ????????????????????????????"

        if not has_campaign_background(session):
            preset_reply = await self._campaign_preset_fast_path(session_id, session, actor, text)
            if preset_reply:
                return preset_reply
            session = self.repository.load_session(session_id)

        pending_campaign_reply = await self._campaign_preference_fast_path(session_id, session, actor, text)
        if pending_campaign_reply:
            return pending_campaign_reply
        session = self.repository.load_session(session_id)

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
                "??????????? AI ?????????????????"
                "????? DM ???????????? 120 ????????????????"
            )

        if _looks_like_manual_ambient_image_request(text):
            return self._handle_manual_ambient_image_request(event, session_id, session, actor, text)

        if normalized in {"pause", "??", "????", "????"}:
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
                story_moment="???????????????????",
                rationale="????????????? 2 ??????????",
            )
            return "?????????????????????????????? `/dm resume` ? `/dm ??`?"

        if _looks_like_resume_flow_command(normalized, paused=paused):
            if paused:
                if not _clear_stale_turn_timeout_pause(session.scene, resumed=True):
                    session.scene["_dm_paused"] = False
                session.scene["_dm_resumed_by"] = actor
                session.scene["_dm_resumed_at"] = _utc_now_iso()
                session.scene["_dm_resume_command"] = text[:120]
                self.repository.save_session(session)
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "resume", "actor": actor})
            self._schedule_pause_resume_ambient_image(
                event,
                session_id,
                actor,
                text,
                story_moment="??????????????????????",
                rationale="??????????????? 2 ??????????",
            )
            return "????????? `/dm` ???????????"

        if _looks_like_restart_latest_backup_story_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "restart_latest_backup_story",
                reason=text,
            )
            self.repository.append_audit(
                session_id,
                {"type": "local_fast_path", "action": "restart_backup_story", "actor": actor, "result": result},
            )
            self.plugin_logger.info(
                "dm_restart_backup_story session=%s sender=%s ok=%s error=%s",
                session_id,
                actor.get("player_id", ""),
                result.get("ok"),
                result.get("error", ""),
            )
            return str(result.get("message") or "???????????")

        if _looks_like_backup_preview_request(text):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "preview_latest_backup",
                reason=text,
            )
            self.repository.append_audit(
                session_id,
                {"type": "local_fast_path", "action": "backup_preview", "actor": actor, "result": result},
            )
            return str(result.get("message") or "??????????")

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
                return "??????????"
            lines = ["?????"]
            for item in backups[:5]:
                size = int(item.get("size") or 0)
                lines.append(f"- {item.get('mtime', '')}?{size // 1024}K?{item.get('reason', '') or item.get('name', '')}")
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
            return str(result.get("message") or "????????")

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
            return str(result.get("message") or "????????")

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
            message = str(result.get("message") or "??????")
            if result.get("ok"):
                message += "\n???????????????????????/?????`/dm ?????40K????????????????`?"
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
            return str(result.get("message") or "????????????????")

        if (
            _looks_like_new_campaign_seed_request(text)
            and _session_has_meaningful_campaign_content(session)
            and not _looks_like_in_campaign_content_expansion_request(text)
        ):
            result = await MemoryTools(self.repository, session_id, actor=actor, message=text).session_control(
                "reset",
                reason=f"?????????{text}",
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
            message = str(result.get("message") or "????????????????")
            return "??????????????????????????????????????????\n" + message

        if not has_campaign_background(session):
            background_patch = {}
            background_action = "guided_background_bootstrap"
            if looks_like_campaign_generation_request(text):
                background_patch = build_campaign_seed_patch(text)
                background_action = "campaign_llm_bootstrap"
            if not background_patch:
                background_patch = _guided_background_patch_from_text(text)
            if not background_patch and visual_map_request:
                background_patch = _visual_map_background_patch_from_text(text)
                background_action = "visual_map_background_bootstrap"
            if background_patch:
                result = await MemoryTools(self.repository, session_id, actor=actor, message=text).update_world_tags(background_patch)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": background_action,
                        "actor": actor,
                        "text": text[:240],
                        "result": result,
                    },
                )
                self.plugin_logger.info(
                    "%s session=%s sender=%s ok=%s keys=%s",
                    background_action,
                    session_id,
                    actor.get("player_id", ""),
                    result.get("ok"),
                    ",".join(str(key) for key in background_patch.keys()),
                )
                if result.get("ok"):
                    return ""
                return str(result.get("message") or "????????????????????")

        if normalized in {"status", "??", "????"}:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "status", "actor": actor})
            return self._format_local_status(session)

        if _looks_like_scene_tracking_status_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "scene_tracking_status", "actor": actor})
            return format_scene_tracking_status(session.scene or {})

        if normalized in {"token", "tokens", "token??", "???", "?????"}:
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
                    f"Honcho ?????????? {external_memory.get('configured_max_context_chars', 0)} ??"
                    "???????? router ?????"
                )
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "token", "actor": actor})
            return (
                "Token ???"
                f"?? {current.get('compact_snapshot_chars', 0)} ??? {rough.get('heuristic', 0)} token?"
                f"???? {current.get('full_save_chars', 0)} ??"
                f"?????? {compression.get('snapshot_chars_remaining_before_compression', 0)} ??"
                f"{external_note}"
            )

        if normalized in {"????", "????", "??", "??", "???", "???", "????", "????", "????"} or _looks_like_turn_status_request(text):
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "turn_status", "actor": actor})
            return self._format_turn_status(session, include_order=_looks_like_turn_order_request(text))

        authoritative_state_reply = _authoritative_state_fast_reply(session, text)
        if authoritative_state_reply:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "authoritative_state_check",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return authoritative_state_reply

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

        unbound_reply = _unbound_tactical_actor_reply(session, actor, text) or _unbound_live_action_reply(session, actor, text)
        if unbound_reply:
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "unbound_actor_action",
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
            return "?????????????????????????????????????????????????????????"

        if paused:
            self.repository.append_audit(session_id, {"type": "local_fast_path", "action": "paused_block", "actor": actor})
            return "???????????????????????? `/dm status`?`/dm token`?`/dm ????` ?????? `/dm resume` ???"

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
                "????????????????????????/???"
                "??????`/dm ?????40K??????`??? `/dm ???????`?"
            )

        return ""

    async def _campaign_preset_fast_path(
        self,
        session_id: str,
        session,
        actor: dict[str, str],
        text: str,
    ) -> str:
        if has_campaign_background(session):
            return ""
        if looks_like_campaign_preset_list_request(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "campaign_preset_list",
                    "actor": actor,
                    "text": text[:240],
                },
            )
            return format_campaign_preset_list()

        template = select_campaign_preset(text)
        if not template:
            return ""
        patch = build_campaign_preset_patch(template, request_text=text)
        result = await MemoryTools(self.repository, session_id, actor=actor, message=text).update_world_tags(patch)
        updated_session = self.repository.load_session(session_id)
        updated_scene = updated_session.scene or {}
        if "_pending_campaign_preferences" in updated_scene:
            updated_scene.pop("_pending_campaign_preferences", None)
            updated_session.scene = updated_scene
            self.repository.save_session(updated_session)
        self.repository.append_audit(
            session_id,
            {
                "type": "local_fast_path",
                "action": "campaign_preset_loaded",
                "actor": actor,
                "template_key": template.key,
                "text": text[:240],
                "result": result,
            },
        )
        self.plugin_logger.info(
            "campaign_preset_loaded session=%s sender=%s ok=%s template=%s",
            session_id,
            actor.get("player_id", ""),
            result.get("ok"),
            template.key,
        )
        if not result.get("ok"):
            return str(result.get("message") or "??????????????????????????")
        if campaign_preset_start_requested(text):
            return ""
        return format_campaign_preset_loaded_reply(template)

    async def _campaign_preference_fast_path(
        self,
        session_id: str,
        session,
        actor: dict[str, str],
        text: str,
    ) -> str:
        if has_campaign_background(session):
            return ""
        scene = session.scene or {}
        pending = dict(scene.get("_pending_campaign_preferences") or {})
        actor_id = str(actor.get("player_id", "") or "")
        if pending and _pending_campaign_preference_matches(pending, actor_id):
            if looks_like_campaign_preference_answer(text):
                seed = str(pending.get("seed") or "").strip()
                pending_template_key = str(pending.get("template_key") or "")
                template = None
                if pending_template_key and pending_template_key != "custom_player_brief":
                    template = template_by_key(pending_template_key)
                patch = build_campaign_seed_patch(seed, preference_text=text, template=template)
                patch["campaign_preferences"] = {
                    "intensity_and_style": text[:1200],
                    "asked_at": str(pending.get("asked_at") or ""),
                    "answered_at": _utc_now_iso(),
                    "actor_id": actor_id,
                }
                result = await MemoryTools(self.repository, session_id, actor=actor, message=text).update_world_tags(patch)
                updated_session = self.repository.load_session(session_id)
                updated_scene = updated_session.scene or {}
                updated_scene.pop("_pending_campaign_preferences", None)
                updated_session.scene = updated_scene
                self.repository.save_session(updated_session)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_fast_path",
                        "action": "campaign_preference_answered",
                        "actor": actor,
                        "seed": seed[:240],
                        "preferences": text[:240],
                        "result": result,
                    },
                )
                self.plugin_logger.info(
                    "campaign_preference_answered session=%s sender=%s ok=%s template=%s",
                    session_id,
                    actor_id,
                    result.get("ok"),
                    patch.get("campaign_contract", {}).get("template_key", ""),
                )
                if result.get("ok"):
                    return ""
                return str(result.get("message") or "??????????????????????")
            if looks_like_campaign_generation_request(text):
                _clear_pending_campaign_preferences(self.repository, session_id, session)
            else:
                return str(pending.get("question") or "?????????????????????????")

        if should_ask_campaign_preferences(text):
            template = None
            is_custom_brief = looks_like_custom_campaign_brief(text)
            template_key = "custom_player_brief" if is_custom_brief else "llm_generated_campaign"
            template_title = "???????" if is_custom_brief else "LLM ????"
            question = build_campaign_preference_question(text, template)
            session.scene["_pending_campaign_preferences"] = {
                "seed": text[:12000],
                "template_key": template_key,
                "template_title": template_title,
                "question": question,
                "actor_id": actor_id,
                "asked_at": _utc_now_iso(),
            }
            self.repository.save_session(session)
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "campaign_preference_question",
                    "actor": actor,
                    "template_key": template_key,
                    "text": text[:240],
                },
            )
            self.plugin_logger.info(
                "campaign_preference_question session=%s sender=%s template=%s",
                session_id,
                actor_id,
                template_key,
            )
            return question
        return ""

    async def _cycle_readonly_fast_path(
        self,
        session_id: str,
        session,
        actor: dict[str, str],
        text: str,
        normalized: str,
    ) -> str:
        if normalized in {"status", "??", "????"}:
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

        if _looks_like_scene_tracking_status_request(text):
            self.repository.append_audit(
                session_id,
                {
                    "type": "local_fast_path",
                    "action": "scene_tracking_status",
                    "actor": actor,
                    "cycle_state": session.cycle_state.value,
                },
            )
            return format_scene_tracking_status(session.scene or {})

        if normalized in {"token", "tokens", "token??", "???", "?????"}:
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
                "Token ???"
                f"?? {current.get('compact_snapshot_chars', 0)} ??? {rough.get('heuristic', 0)} token?"
                f"???? {current.get('full_save_chars', 0)} ??"
                f"?????? {compression.get('snapshot_chars_remaining_before_compression', 0)} ??"
            )

        if normalized in {"????", "????", "??", "??", "???", "???", "????", "????", "????"} or _looks_like_turn_status_request(text):
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
                return "??????????"
            lines = ["?????"]
            for item in backups[:5]:
                size = int(item.get("size") or 0)
                lines.append(f"- {item.get('mtime', '')}?{size // 1024}K?{item.get('reason', '') or item.get('name', '')}")
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
        entities = load_active_strict_grid_entities(session.maps, battle)
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
                    reason=f"??????????{text[:80]}",
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
                return self._format_turn_advance_result(result, f"{acting_label}??????")

        if _looks_like_local_turn_push(text):
            if owner_id and actor_id == owner_id:
                summary = f"{current_label}???????????????"
                result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
                    action="record_action",
                    current_entity_id=current_id,
                    summary=summary,
                    reason=f"??????????{text[:80]}",
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
                return self._format_turn_advance_result(result, f"{current_label}??????")
            deadline = _parse_datetime(turn.get("deadline_at"))
            now = datetime.now(timezone.utc)
            if deadline is not None and now >= deadline:
                elapsed = int((now - (_parse_datetime(turn.get("waiting_since_at")) or deadline)).total_seconds())
                summary = (
                    f"{current_label}?? 120 ????????????"
                    "?????????????????????"
                )
                result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
                    action="auto_act_current",
                    current_entity_id=current_id,
                    summary=summary,
                    reason=f"????????????? {max(120, elapsed)} ??{text[:80]}",
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
        owner_name = str((session.participants.get(owner_id) or {}).get("display_name") or owner_id or "???")
        return f"?????? {current_label}?{owner_name}????? {remaining} ?????????????????????????? `/dm ???` ???????"

    def _format_turn_advance_result(self, result: dict, prefix: str) -> str:
        if not result.get("ok"):
            return str(result.get("message") or f"???????{result.get('error', 'unknown_error')}")
        turn = dict(result.get("turn") or {})
        phase = str(turn.get("phase") or "")
        if phase == "scene_resolution":
            return f"{prefix}\n??? {turn.get('round', '?')} ??????"
        current_id = str(turn.get("current_entity_id") or "")
        if turn.get("current_label"):
            current_label = str(turn["current_label"])
        else:
            current_label = fallback_turn_entity_label(current_id)
        return f"{prefix}\n?????{current_label}??????????????"

    def _format_local_status(self, session) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        paused = "???" if (session.scene or {}).get("_dm_paused") else "???"
        if turn.get("active"):
            turn_text = self._format_turn_status(session)
        else:
            turn_text = "?????????"
        return (
            f"???{session.title}????{session.mode.value}????{paused}????{_game_started_text(session)}?\n"
            f"????{timeline_status_text(session.timeline)}?\n"
            f"?? {len(session.participants)}??? {len(session.characters)}??? {len(session.rules)}?\n"
            f"{turn_text}"
        )

    def _format_turn_status(self, session, include_order: bool = False) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active"):
            return "?????????"
        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
        entities = load_active_strict_grid_entities(session.maps, battle)
        label = _entity_label(session, current_id, entities) if current_id else "???"
        owner_id = _entity_owner(session, current_id, entities) if current_id else ""
        owner_name = str((session.participants.get(owner_id) or {}).get("display_name") or owner_id or "???")
        deadline = _parse_datetime(turn.get("deadline_at"))
        if deadline:
            remaining = max(0, int((deadline - datetime.now(timezone.utc)).total_seconds()))
            wait_text = f"????? {remaining} ??"
        else:
            wait_text = "??????????? /dm ???? 120 ??"
        base = (
            f"? {int(turn.get('round') or 0)} ?????{turn.get('phase', 'idle')}?\n"
            f"????/?????{label}?{current_id or '?'}??????{owner_name}?\n"
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
            marker = "????" if str(entity_id) == current_id else ""
            acted = "???" if str(entity_id) in actions else "???"
            order_labels.append(f"{index}. {entity_label}{marker}?{acted}")
        return base + "\n" + "\n".join(order_labels)

    def _format_player_roster(self, session) -> str:
        participants = session.participants or {}
        if not participants:
            return "??????????"
        lines = ["?????"]
        for index, (player_id, participant) in enumerate(participants.items(), start=1):
            display_name = str(participant.get("display_name") or player_id)
            character_id = str(session.player_character_map.get(player_id, "") or "")
            character = session.characters.get(character_id) if character_id else None
            if character:
                character_name = character.name or character.id
                lines.append(f"{index}. {display_name}?{player_id}? -> {character_name} [{character.id}]")
            else:
                lines.append(f"{index}. {display_name}?{player_id}? -> ?????")
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
                    f"???????????????????? {remaining} ??"
                    "?????????????????????????????? `/dm status` ? `/dm ????`?"
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
                data = json.loads(path.read_text(encoding="utf-8-sig"))
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
                data = json.loads(path.read_text(encoding="utf-8-sig"))
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
                data = json.loads(path.read_text(encoding="utf-8-sig"))
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
            if summary and not summary.startswith("????"):
                continue
            scene["_legacy_live_campaign"] = True
            scene["_legacy_live_campaign_marked_at"] = _utc_now_iso()
            scene["summary"] = "??????????????????????????????????????????"
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
        reassurance_tasks = list(getattr(self, "_reassurance_tasks", set()) or [])
        for task in reassurance_tasks:
            task.cancel()
        for task in reassurance_tasks:
            with suppress(asyncio.CancelledError):
                await task
        if hasattr(self, "_reassurance_tasks"):
            self._reassurance_tasks.clear()
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
                data = json.loads(path.read_text(encoding="utf-8-sig"))
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
            result = await TurnTools(self.repository, session_id, actor={"player_id": "__heartbeat__", "display_name": "????"}).turn_control(
                action="finish_scene_resolution",
                reason="??????????????? 120 ????????????????",
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
                    "????????? 120 ?????????????????\n"
                    + self._format_turn_destination(self.repository.load_session(session_id))
                )
            return {"active": True, "advanced": bool(result.get("ok")), "phase": phase, "notice": notice}

        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "").strip()
        if not current_id:
            return {"active": True, "phase": phase, "missing_current": True}
        entities = load_active_strict_grid_entities(session.maps, battle)
        current_label = _entity_label(session, current_id, entities)
        waiting_since = _parse_datetime(turn.get("waiting_since_at")) or deadline
        elapsed = max(120, int((now - waiting_since).total_seconds()))
        actor_kind = turn_actor_kind(session, current_id, entities)
        if actor_kind == "player":
            summary = f"{current_label}?? 120 ?????????????????????????????????????"
        elif actor_kind == "enemy":
            summary = f"{current_label}????????? {elapsed} ????????????????????????????????????"
        else:
            summary = f"{current_label}??????? {elapsed} ??????????????????????????????????"
        result = await TurnTools(
            self.repository,
            session_id,
            actor={"player_id": "__heartbeat__", "display_name": "????"},
        ).turn_control(
            action="auto_act_current",
            current_entity_id=current_id,
            summary=summary,
            reason=f"?????????????????????? {elapsed} ??deadline ??????????",
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
            actor={"player_id": "__heartbeat__", "display_name": "????"},
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
            notice = self._format_heartbeat_timeout_notice(
                current_label,
                elapsed,
                updated_session,
                timeout_pause,
                actor_kind=actor_kind,
            )
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
            _clear_stale_turn_timeout_pause(scene)
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
            notice = "?????????????/???????????????????????????"
        elif reason == "social_or_political_scene":
            notice = "???????????????????????????????????????????????????"
        else:
            notice = "??????????????????????????????????????????"
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
            scene["_dm_paused_by"] = actor or {"player_id": "__system__", "display_name": "??????"}
            scene["_dm_paused_at"] = _utc_now_iso()
            scene["_dm_pause_source"] = "turn_timeout"
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
            unit = "???"
        else:
            scope = "entity"
            universe = order
            current_key = current_id if current_id in order else ""
            unit = "????"

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
        *,
        actor_kind: str = "player",
    ) -> str:
        if actor_kind == "player":
            first_line = f"?????{current_label}?? {max(120, elapsed)} ?????????????"
        elif actor_kind == "enemy":
            first_line = f"?????{current_label}??????? {max(120, elapsed)} ????????????"
        else:
            first_line = f"?????{current_label}????? {max(120, elapsed)} ????????????"
        lines = [first_line]
        if timeout_pause.get("auto_paused"):
            lines.append(self._format_timeout_pause_line(timeout_pause))
            lines.append(self._format_turn_destination(updated_session, paused=True))
        else:
            lines.append(self._format_turn_destination(updated_session))
        return "\n".join(line for line in lines if line)

    def _format_timeout_pause_line(self, timeout_pause: dict[str, object]) -> str:
        count = int(timeout_pause.get("count") or 0)
        total = int(timeout_pause.get("total") or 0)
        unit = str(timeout_pause.get("unit") or "????")
        if total <= 0:
            return "?????????????????????? `/dm resume`?"
        return f"???? {count}/{total}{unit}???????????????????? `/dm resume`?"

    def _format_turn_destination(self, session, *, paused: bool = False) -> str:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active"):
            return "????????"
        phase = str(turn.get("phase") or "")
        if phase == "character_turn":
            current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "").strip()
            entities = load_active_strict_grid_entities(session.maps, battle)
            label = _entity_label(session, current_id, entities) if current_id else "???"
            if paused:
                return f"??????{label}?"
            return f"?????{label}??????????????"
        if phase == "scene_resolution":
            round_no = _int_or_default(turn.get("round"), 1)
            if paused:
                return f"?????? {round_no} ??????"
            return f"??? {round_no} ??????"
        return f"?????{phase or '???'}?"

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

    def _extract_best_routed_message(self, event: AstrMessageEvent, message: str) -> str:
        best = self._extract_routed_message(event, message)
        for candidate in _event_plain_text_candidates(event):
            routed = self._extract_routed_message(event, candidate)
            if len(routed) > len(best):
                best = routed
        return best

    def _claim_dm_event_route(self, event: AstrMessageEvent, source: str) -> bool:
        key = _dm_event_route_key(event)
        if not key:
            return True
        claims = getattr(self, "_recent_dm_route_claims", None)
        if claims is None:
            claims = {}
            self._recent_dm_route_claims = claims
        now = monotonic()
        for item_key, seen_at in list(claims.items()):
            if now - seen_at > self.ROUTE_CLAIM_WINDOW_SECONDS:
                claims.pop(item_key, None)
        if key in claims:
            self.plugin_logger.info(
                "dm_route_duplicate_suppressed source=%s route_key=%s",
                source,
                key[:80],
            )
            return False
        claims[key] = now
        return True

    async def _send_independent_ambient_image(self, session_id: str, result: dict[str, Any]) -> bool:
        if not result.get("ok") or not result.get("available") or not result.get("send_to_chat"):
            return False
        file_path = str(result.get("file_path") or "")
        title = str(result.get("title") or "").strip() or "???"
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
        rationale = "???????????? API ??????????"
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
        return "?????????????? API key??????????????"

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
                    components.append(Plain(text=f"\n??????{name}"))
        return event.chain_result(components)

    def _format_dice_check(self, item: dict) -> str:
        rolls = item.get("rolls") or []
        if not rolls:
            return ""
        reason = _compact_text(item.get("reason") or "??????????", 120)
        rule_name = _compact_text(item.get("rule_name") or "unknown_rule", 80)
        version = item.get("version")
        roll_text = "?".join(_format_roll_record(record) for record in rolls[:6])
        if len(rolls) > 6:
            roll_text += f"??? {len(rolls) - 6} ???"
        if item.get("ok"):
            result_text = _compact_result(item.get("rule_result"))
        else:
            result_text = _compact_text(item.get("error_reason") or item.get("error") or "??????", 160)
        suffix = f" v{version}" if version else ""
        return f"?????{reason}\n???{rule_name}{suffix}\n???{roll_text}\n???{result_text}"

    def _format_dice_summary(self, items: list[dict]) -> str:
        lines = []
        for index, item in enumerate(items[:3], start=1):
            dice_text = self._format_dice_check(item)
            if dice_text:
                lines.append(f"{index}. {dice_text}")
        if not lines:
            return ""
        if len(items) > 3:
            lines.append(f"?? {len(items) - 3} ???????")
        return "???????\n" + "\n".join(lines)

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
            visible_pending, _state, delivery_decisions = filter_map_pending_outputs_for_delivery(session.scene, visible_pending)
            session.scene["_pending_outputs"] = []
            self.repository.save_session(session)
            if dropped_ambient:
                self.plugin_logger.info(
                    "ambient_image_pending_outputs_dropped session=%s count=%s",
                    session_id,
                    dropped_ambient,
                )
            skipped_maps = [decision for decision in delivery_decisions if not decision.should_send]
            if skipped_maps:
                self.plugin_logger.info(
                    "map_pending_outputs_suppressed session=%s count=%s reasons=%s",
                    session_id,
                    len(skipped_maps),
                    ",".join(sorted({decision.reason for decision in skipped_maps})),
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
        recent = getattr(self, "_recent_dm_messages", None)
        if recent is None:
            recent = {}
            self._recent_dm_messages = recent
        inflight = getattr(self, "_inflight_dm_messages", None)
        if inflight is None:
            inflight = {}
            self._inflight_dm_messages = inflight
        recent_expire_after = self.DEDUP_WINDOW_SECONDS * 3
        for key, seen_at in list(recent.items()):
            if now - seen_at > recent_expire_after:
                recent.pop(key, None)
        for key, seen_at in list(inflight.items()):
            if now - seen_at > self.IN_FLIGHT_DUPLICATE_WINDOW_SECONDS:
                inflight.pop(key, None)
        normalized = self._dedupe_text(routed_message)
        if not normalized:
            return ""
        key = (session_id, sender_id, normalized)
        if key in inflight:
            return "???????????????????????????????????? `/dm` ???"
        last_seen = recent.get(key)
        recent[key] = now
        inflight[key] = now
        if last_seen is not None and now - last_seen <= self.DEDUP_WINDOW_SECONDS:
            inflight.pop(key, None)
            return "?????????????????????????????? `/dm` ???"
        return ""

    def _mark_message_finished(self, session_id: str, sender_id: str, routed_message: str) -> None:
        normalized = self._dedupe_text(routed_message)
        if not normalized:
            return
        inflight = getattr(self, "_inflight_dm_messages", None)
        if not inflight:
            return
        inflight.pop((session_id, sender_id, normalized), None)

    @staticmethod
    def _dedupe_text(text: str) -> str:
        return " ".join(str(text or "").strip().split())

    @staticmethod
    def _friendly_error_message(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "quota" in lowered or "rate limit" in lowered or "429" in lowered or "??" in text:
            return "DM ???????/??????????????????????????????"
        if "badrequest" in lowered or "invalid_request" in lowered or "400" in lowered:
            return "DM ????????????????????????????????????????????"
        return "DM ?????????????????????????????????"

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
            if normalized in {"false", "0", "no", "off", "?", "??"}:
                return False
            if normalized in {"true", "1", "yes", "on", "?", "??"}:
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

    def _config_reassurance_style_phrase_pools(self) -> dict[str, tuple[str, ...]]:
        pools: dict[str, tuple[str, ...]] = {
            key: tuple(values)
            for key, values in DEFAULT_REASSURANCE_STYLE_POOLS.items()
        }
        if not self.config:
            return pools
        try:
            value = self.config.get("reassurance_style_phrase_pools", None)
        except AttributeError:
            value = getattr(self.config, "reassurance_style_phrase_pools", None)
        if not value:
            return pools
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return pools
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                self.plugin_logger.warning("reassurance_style_phrase_pools_invalid_json")
                return pools
        custom = _coerce_reassurance_style_pools(value)
        if not custom:
            return pools
        pools.update(custom)
        return pools


def _stable_short_hash(value: object) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        except Exception:
            text = str(value)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _coerce_reassurance_style_pools(value: object) -> dict[str, tuple[str, ...]]:
    if isinstance(value, dict):
        result: dict[str, tuple[str, ...]] = {}
        for key, phrases in value.items():
            style_key = str(key or "").strip()
            if not style_key:
                continue
            phrase_list = _coerce_phrase_list(phrases)
            if phrase_list:
                result[style_key] = tuple(phrase_list)
        return result
    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            style_key = str(item.get("style") or item.get("key") or item.get("name") or "").strip()
            phrase_list = _coerce_phrase_list(item.get("phrases") or item.get("values") or item.get("items"))
            if style_key and phrase_list:
                result[style_key] = tuple(phrase_list)
        return result
    return {}


def _coerce_phrase_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _looks_like_reassurance_map_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(term in normalized for term in REASSURANCE_MAP_TERMS)


def _is_safe_reassurance_phrase(phrase: str, prefix: str = "??????") -> bool:
    body = str(phrase or "").strip()
    if not body:
        return False
    prefix = str(prefix or "").strip()
    while prefix and body.startswith(prefix):
        body = body[len(prefix) :].strip()
    if not body or len(body) > 48:
        return False
    if "\n" in body or "\r" in body:
        return False
    lowered = body.lower()
    if any(term in body or str(term).lower() in lowered for term in REASSURANCE_UNSAFE_TERMS):
        return False
    if any(term in body or str(term).lower() in lowered for term in REASSURANCE_CHOICE_TERMS):
        return False
    if "?" in body or "?" in body:
        return False
    if re.search(r"(^|\s|[?:])([0-9]+[.?)]|[?????????])", body):
        return False
    return True


def _svg_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1] if "}" in str(tag) else str(tag)


def _looks_like_manual_ambient_image_request(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    diagnostic_terms = (
        "???",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "???",
        "??",
        "??",
        "debug",
        "diagnose",
        "??",
    )
    if any(term in normalized for term in diagnostic_terms):
        return False
    negative_terms = (
        "????",
        "???",
        "????",
        "?????",
        "????",
        "???",
        "??????",
        "????",
        "????",
    )
    if any(term in normalized for term in negative_terms):
        return False
    leading_terms = (
        "??",
        "??",
        "??",
        "??",
        "???",
        "????",
        "?????",
        "????",
        "???",
        "???",
        "???",
        "generate image",
        "draw image",
        "make an image",
    )
    if normalized in leading_terms:
        return True
    if any(normalized.startswith(term) for term in leading_terms):
        return True
    image_terms = ("??", "???", "??", "??", "??", "??", "??", "image", "illustration", "picture")
    if any(term in normalized for term in ("???", "???")) and any(term in normalized for term in image_terms):
        return True
    if any(term in normalized for term in ("????", "???", "??", "??", "??")) and any(
        term in normalized for term in image_terms
    ):
        return True
    action_terms = ("??", "?", "??", "?", "??", "???", "??", "???", "?????", "???", "????")
    return any(term in normalized for term in action_terms) and any(term in normalized for term in image_terms)


def _ambient_image_story_moment_from_manual_request(text: str, session) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(
        r"^(?|??|??|??|???(?:apikey|api key|api)?|???(?:apikey|api key|api)?)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(??|?|??|?|?|?)?\s*(??|??|?|?)?\s*(???|??|??|??|??|??|image|illustration|picture)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" ?:?,?.-")
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
        sections.append(f"?????{summary}")
    if conflict:
        sections.append(f"?????{conflict}")
    if last_resolution_text:
        sections.append(f"?????{last_resolution_text}")
    if sections:
        return _compact_text("?".join(sections), 500)
    return "??????????????????"


def _format_ambient_image_failure_reply(result: dict[str, Any]) -> str:
    code = _ambient_image_reason_code(result)
    if code == "ambient_image_disabled":
        return "??????????????????? `ambient_image_enabled`???????? API key?"
    if code == "ambient_image_api_key_missing":
        env_name = str(result.get("api_key_env") or "PACKYAPI_SORA_API_KEY").strip()
        if not _looks_like_env_var_name(env_name):
            return (
                "???? API key ??????"
                "?????????? `ambient_image_api_key` ??? key?"
                "`ambient_image_api_key_env` ????????????????"
            )
        return (
            "???? API key ??????"
            "?????????????? `ambient_image_api_key` ???? key?"
            f"?????????????????? `{env_name}`?????? key ??? AstrBot ????????????????"
        )
    if code == "ambient_image_api_mode_invalid":
        return "???? API ?????`ambient_image_api_mode` ??? `images` ? `chat_completions`?"
    if code == "ambient_image_base_url_missing":
        return "???? API base URL ????????? `ambient_image_base_url`???? `https://www.packyapi.com`?"
    if code == "ambient_image_send_disabled":
        return "?????????????????? `ambient_image_send_to_chat` ?? `true`?????????????????"
    if code == "ambient_image_combat_active":
        return "??????/????????????????????????????? API?"
    if code == "ambient_image_generation_in_progress":
        elapsed = result.get("generation_minutes_elapsed", 0)
        required = result.get("generation_minutes_required", 5)
        return f"????????????????????? {elapsed} ???? {required} ??????????"
    if code == "ambient_image_prompt_model_missing":
        return "??? prompt ?????????? API ?????????????????????? prompt?"
    if code == "ambient_image_prompt_model_failed":
        reason = _compact_text(result.get("reason", ""), 180)
        return f"??? prompt ????????? API ??????{reason}"
    if code == "ambient_image_http_error":
        status = result.get("status", "")
        reason = _compact_text(result.get("reason", ""), 180)
        return f"???? API ?? HTTP ?? {status}?{reason}"
    if code == "ambient_image_network_error":
        reason = _compact_text(result.get("reason", ""), 180)
        return f"???? API ???????{reason}"
    if code == "ambient_image_timeout":
        return "???? API ????????????????????????"
    if code == "ambient_image_result_missing":
        return "???? API ????????? URL ? base64 ???????????API ??????????"
    if code == "ambient_image_content_type_invalid":
        return "???? API ???????????????????? provider ??? URL ? `response_format`?"
    if code == "ambient_image_too_large":
        return "???? API ??????????????????????????"
    if code in {"ambient_image_send_failed", "ambient_image_sender_missing"}:
        return "?????????????????????????? `ambient_image_independent_send_failed` ???"
    detail = _compact_text(result.get("reason") or result.get("message") or "", 180)
    if detail:
        return f"?????????????? `{code}`?{detail}"
    return f"?????????????? `{code}`?"


def _ambient_image_reason_code(result: dict[str, Any]) -> str:
    return str(result.get("error") or result.get("reason") or "ambient_image_failed")


def _looks_like_env_var_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,80}", str(value or "").strip()))


def _looks_like_local_turn_end(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    exact = {
        "????",
        "????",
        "??????",
        "?????",
        "??????",
        "?????",
        "????",
        "????",
        "????",
        "??",
        "??",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "??",
        "????",
        "?",
        "???",
        "???",
        "???",
        "pass",
        "skip",
        "done",
        "end turn",
    }
    if normalized in exact:
        return True
    end_patterns = (
        "?????",
        "???????",
        "??????",
        "?????",
        "?????",
        "???",
        "?????",
        "?????",
    )
    return any(pattern in normalized for pattern in end_patterns)


def _looks_like_turn_status_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(term in normalized for term in ("???", "??", "??", "???", "??", "??", "??", "??", "??", "??", "??", "??")):
        return False
    return (
        any(term in normalized for term in ("????", "????", "????", "????", "????", "??", "???"))
        or ("??" in normalized and any(term in normalized for term in ("??", "??", "??")))
    )


def _looks_like_scene_tracking_status_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    exact = {
        "????",
        "????",
        "????",
        "????",
        "????",
        "??",
        "??",
        "??",
        "????",
        "????",
        "????",
        "???????",
        "?????",
        "???",
        "????",
        "????",
        "????",
        "current objective",
        "current clues",
        "clues",
        "objectives",
        "hooks",
    }
    if normalized in exact:
        return True
    tracking_terms = ("??", "??", "??", "??", "??", "??", "objective", "clue", "hook", "mystery")
    query_terms = ("??", "??", "???", "??", "??", "?", "??", "summary", "current", "status", "what")
    return any(term in normalized for term in tracking_terms) and any(term in normalized for term in query_terms)


def _authoritative_state_fast_reply(session, text: str) -> str:
    if not _looks_like_authoritative_state_request(text):
        return ""
    scene = session.scene or {}
    timeline = timeline_status_text(session.timeline)
    location = _authoritative_state_location_text(scene)
    summary = _compact_text(scene.get("summary") or "?????????", 260)
    objective = _compact_text(scene.get("current_objective") or "???????", 220)
    conflict = _compact_text(scene.get("current_conflict") or "???????", 220)
    lines = [
        f"?????{timeline}?",
    ]
    if location:
        lines.append(f"???{location}")
    lines.extend([
        f"???{summary}",
        f"???{objective}",
        f"???{conflict}",
    ])
    hooks_text = _authoritative_state_visible_hooks_text(scene)
    if hooks_text:
        lines.append(f"???{hooks_text}")
    if any(term in str(text or "") for term in ("???", "?2?", "? 2 ?", "??", "??", "??", "??", "??", "??", "??")):
        lines.append("??????????????????????????????????????")
    battle = session.battle or {}
    turn = dict(battle.get("turn") or {})
    if bool(battle.get("active")) or bool(turn.get("active")):
        phase = str(turn.get("phase") or "unknown")
        round_no = int(turn.get("round") or 0)
        current_id = str(turn.get("current_entity_id") or battle.get("turn_entity_id") or "")
        lines.append(f"???? {round_no} ???? {phase}????? {current_id or '?'}?")
    return "\n".join(lines)


def _authoritative_state_location_text(scene: dict) -> str:
    raw_location = scene.get("location")
    if isinstance(raw_location, dict):
        for key in ("name", "title", "text", "description", "summary"):
            value = str(raw_location.get(key) or "").strip()
            if value:
                return _compact_text(value, 120)
    elif raw_location not in (None, "", [], {}):
        return _compact_text(raw_location, 120)
    threads = scene.get("scene_threads")
    active_thread_id = str(scene.get("active_scene_thread_id") or "").strip()
    active_thread = threads.get(active_thread_id) if isinstance(threads, dict) and active_thread_id else None
    if isinstance(active_thread, dict):
        location = str(active_thread.get("location") or "").strip()
        if location:
            return _compact_text(location, 120)
    return ""


def _authoritative_state_visible_hooks_text(scene: dict) -> str:
    parts: list[str] = []
    for key in ("open_hooks", "clues", "mysteries"):
        value = scene.get(key)
        items = value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])
        for item in items:
            if not isinstance(item, dict):
                continue
            visibility = str(item.get("visibility") or "player").strip().lower()
            status = str(item.get("status") or "").strip().lower()
            if visibility in {"hidden", "secret", "dm", "dm_only", "gm", "gm_only", "private"}:
                continue
            if status in {"hidden", "secret", "undiscovered"}:
                continue
            text = str(item.get("text") or item.get("summary") or item.get("description") or "").strip()
            if text:
                parts.append(_compact_text(text, 100))
            if len(parts) >= 3:
                return "?".join(parts)
    return "?".join(parts)


def _looks_like_authoritative_state_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if any(term in normalized for term in ("??", "??", "???", "??", "??", "??", "??", "??", "??", "??", "????")):
        return False
    direct_terms = (
        "???",
        "?????",
        "????",
        "????",
        "????",
        "??????",
    )
    if any(term in normalized for term in direct_terms):
        return True
    state_terms = (
        "???",
        "?2?",
        "? 2 ?",
        "??",
        "??",
        "????",
        "???",
        "????",
        "????",
        "??????",
        "????",
        "????",
        "????",
        "????",
        "???",
        "?????",
        "?????",
        "???",
        "?????",
        "????",
        "????",
        "???",
    )
    query_terms = ("?", "?", "?", "??", "??", "??", "??", "???", "??", "??", "??", "??")
    return any(term in normalized for term in state_terms) and any(term in normalized for term in query_terms)


def _looks_like_turn_order_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(term in normalized for term in ("????", "????", "????", "??", "??", "??"))


def _looks_like_local_turn_push(text: str) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    exact = {
        "??",
        "????",
        "??",
        "????",
        "???",
        "???",
        "?????",
        "????",
        "???",
        "???",
        "??",
        "????",
        "????",
        "????",
        "????",
        "??",
        "???",
        "????",
        "?",
        "skip",
        "next",
        "continue",
    }
    if normalized in exact:
        return True
    return any(
        pattern in normalized
        for pattern in (
            "?????",
            "????",
            "????",
            "??????",
            "??????",
            "???????",
        )
    )


def _local_turn_end_summary(text: str, current_label: str) -> str:
    normalized = str(text or "").strip().lower()
    if any(term in normalized for term in ("??", "??", "skip", "pass")):
        return f"{current_label}?????????????"
    if any(term in normalized for term in ("??", "??", "??", "??", "?")):
        return f"{current_label}????/???????????"
    return f"{current_label}????????"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pending_campaign_preference_matches(pending: dict, actor_id: str) -> bool:
    owner = str(pending.get("actor_id") or "")
    return not owner or not actor_id or owner == actor_id


def _clear_pending_campaign_preferences(repository, session_id: str, session) -> None:
    scene = session.scene or {}
    if "_pending_campaign_preferences" not in scene:
        return
    scene.pop("_pending_campaign_preferences", None)
    session.scene = scene
    repository.save_session(session)


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
    return public_turn_entity_label(session, entity_id, entities)


def _entity_owner(session, entity_id: str, entities: dict) -> str:
    return turn_entity_owner_id(session, entity_id, entities)


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
        return "????????????????????????????????"
    return "????????????/??????????"


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
            "????????????????????????????????????????????"
            "?????????????????????????????????????????/??????????"
            "?????????????????"
        )
    return ""


def _looks_like_late_join_power_overreach(text: str) -> bool:
    mythic_identity = (
        "??",
        "??",
        "??",
        "??",
        "????",
        "????",
        "??",
        "???",
        "???",
        "????",
        "??",
        "??",
        "??",
        "??",
    )
    force_terms = (
        "?????",
        "?????",
        "13???",
        "13???",
        "????",
        "????",
        "????",
        "????",
        "????",
        "??",
        "??",
        "????",
    )
    join_or_command = (
        "???",
        "????",
        "???",
        "??",
        "???",
        "????",
        "????",
        "??",
        "??",
        "??",
        "??",
        "???",
        "???",
    )
    return (
        any(term in text for term in mythic_identity)
        and any(term in text for term in join_or_command)
    ) or any(term in text for term in force_terms)


def _looks_like_world_law_rewrite(text: str) -> bool:
    law_terms = ("????", "???", "??", "??", "????", "????", "????", "dnd2024")
    rewrite_terms = ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??")
    target_terms = ("???", "???", "??", "???", "??", "??", "??", "??")
    return any(term in text for term in law_terms) and any(term in text for term in rewrite_terms) and any(
        term in text for term in target_terms
    )


def _looks_like_forced_scene_takeover(text: str) -> bool:
    force_terms = ("????", "????", "???", "??", "??", "???", "????", "????")
    outcome_terms = ("????", "???", "??", "??", "??", "??", "??", "????", "????")
    return any(term in text for term in force_terms) and any(term in text for term in outcome_terms)


def _looks_like_self_cleansing_overreach(text: str) -> bool:
    status_terms = ("debuff", "????", "????", "??", "??", "??", "??", "??")
    cleanse_terms = ("????", "??", "??", "??", "??", "??", "??", "????")
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
        "????????????????????????"
        "????????????? `/dm ?????????`?"
        "???????????????? `/dm ????` ? `/dm ????`?"
    )


def _unbound_live_action_reply(session, actor: dict[str, str], text: str) -> str:
    if not _campaign_game_started(session):
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
        "????????????????????????????"
        "??? `/dm ?????????????????????`?"
        "? `/dm ?????????` ?????????????????"
        "?????????"
    )


def _looks_like_unbound_scene_action(text: str) -> bool:
    action_terms = (
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "??",
        "??",
        "??",
        "?",
        "??",
        "?",
        "?",
        "?",
        "?",
        "??",
        "???",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "??",
        "??",
        "?",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "????",
        "??",
        "??",
        "?",
        "?",
    )
    return _looks_like_paced_player_action(text) or any(term in text for term in action_terms)


def _looks_like_terminal_or_interlude_for_pacing(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    terminal_terms = (
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "??",
        "???",
        "??",
        "?????",
        "?????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "??????",
        "????",
        "??????",
        "????",
        "????",
        "??????",
        "?????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "????",
        "routed",
        "retreated",
        "fled",
    )
    interlude_terms = (
        "????",
        "????",
        "????",
        "???",
        "????",
        "????",
        "????",
        "????",
        "????",
        "??????",
        "?????",
        "??????",
        "??????",
        "??",
        "??",
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
            "???????",
            "???????",
            "????",
            "??????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "??????",
            "????",
            "??????",
            "????",
            "????",
            "??????",
            "?????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "routed",
            "retreated",
            "fled",
            "????????",
            "????",
            "???????",
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
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
    )
    combat_terms = (
        "??",
        "??",
        "??",
        "????",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "hp",
        "???",
        "??",
        "??",
    )
    return any(term in current_conflict for term in social_terms) and not any(
        term in current_conflict for term in combat_terms
    )


def _repeated_timeout_after_auto_pause(session, turn: dict) -> bool:
    scene = session.scene or {}
    pause_reason = str(scene.get("_dm_pause_reason") or "")
    if "??" not in pause_reason and "????" not in pause_reason and "??" not in pause_reason:
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


def _looks_like_resume_flow_command(normalized: str, *, paused: bool = False) -> bool:
    text = str(normalized or "").strip().lower()
    if text in {"resume", "unpause", "??", "????", "????"}:
        return True
    if paused and text == "??":
        return True
    tokens = [token.strip() for token in re.split(r"[\s/|,?;?]+", text) if token.strip()]
    if not tokens:
        return False
    resume_tokens = {"resume", "unpause", "??", "????", "????"}
    if any(token in resume_tokens for token in tokens):
        return True
    return "dm" in tokens and any(token in {"resume", "unpause"} for token in tokens)


def _clear_stale_turn_timeout_pause(scene: dict[str, Any], *, resumed: bool = False) -> bool:
    if not isinstance(scene, dict) or not scene.get("_dm_paused"):
        return False
    reason = str(scene.get("_dm_pause_reason") or "")
    paused_by = scene.get("_dm_paused_by") if isinstance(scene.get("_dm_paused_by"), dict) else {}
    source = str(scene.get("_dm_pause_source") or "")
    is_timeout_pause = (
        source == "turn_timeout"
        or str(paused_by.get("player_id") or "") in {"__heartbeat__", "__system__"}
        or any(term in reason for term in ("??", "????", "??"))
    )
    if not is_timeout_pause:
        return False
    now = _utc_now_iso()
    scene["_dm_paused"] = False
    scene["_dm_pause_cleared_at"] = now
    scene["_dm_pause_cleared_reason"] = reason
    scene["_dm_pause_cleared_by"] = "resume" if resumed else "encounter_end"
    for key in ("_dm_pause_reason", "_dm_paused_by", "_dm_paused_at", "_dm_pause_source"):
        scene.pop(key, None)
    return True


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
    if any(token in normalized for token in ("??", "??", "??", "????", "??")) and not any(
        token in normalized for token in ("??", "??", "??", "??", "???")
    ):
        return False
    if _looks_like_in_character_clue_request(normalized):
        return False
    plot_terms = ("??", "??", "??", "???", "??", "??", "??", "??", "??", "????", "??", "??")
    rewrite_terms = ("??", "??", "??", "??", "??", "??", "???", "???", "??", "????", "???")
    direct_rewrite = any(term in normalized for term in plot_terms) and any(term in normalized for term in rewrite_terms)
    fact_injection = any(term in normalized for term in ("??", "???", "??", "?????", "???")) and any(
        term in normalized for term in plot_terms
    )
    return direct_rewrite or fact_injection


def _looks_like_in_character_clue_request(text: str) -> bool:
    speech_terms = (
        "???",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
    )
    investigation_terms = ("????", "??", "??", "???", "??", "??", "??", "??")
    rewrite_terms = ("??", "??", "??", "??", "??", "??", "???", "???", "??", "??", "???", "??")
    if any(term in text for term in speech_terms) and any(term in text for term in investigation_terms):
        return not any(term in text for term in rewrite_terms)
    return False


def _looks_like_dm_autopilot_takeover(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    all_player_scope = any(
        term in normalized
        for term in (
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "?????",
            "????",
        )
    )
    takeover = any(
        term in normalized
        for term in (
            "?????",
            "?????",
            "????",
            "????",
            "??ai",
            "?? ai",
            "????",
            "????",
            "????????",
            "????????",
            "???????",
            "??????",
            "???????",
            "??????",
        )
    )
    return all_player_scope and takeover


def _looks_like_paced_player_action(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if _looks_like_non_action_request(normalized):
        return False
    actor_terms = ("?", "?", "?", "??", "??", "my ")
    action_terms = (
        "??",
        "?",
        "??",
        "?",
        "?",
        "??",
        "??",
        "?",
        "?",
        "?",
        "?",
        "??",
        "?",
        "?",
        "?",
        "?",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "?",
        "??",
        "??",
        "?",
        "?",
        "??",
        "??",
        "?",
        "?",
        "?",
        "?",
        "?",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
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
        "????",
        "????",
        "??",
        "??",
        "????",
        "????",
        "?????",
        "???",
        "???",
        "??",
        "debug",
        "??",
        "??",
        "????",
        "????",
        "??",
        "??",
        "????",
        "????",
        "???",
        "???",
        "????",
        "????",
        "???",
        "????",
        "????",
        "???",
        "??",
        "????",
        "????",
        "????",
        "??",
        "????",
    )
    if any(term in text for term in non_action_terms):
        return True
    question_terms = ("?", "?", "??", "??", "??", "???", "???", "????", "???", "?")
    action_verbs = ("??", "??", "?", "?", "?", "?", "??", "??", "??")
    return any(term in text for term in question_terms) and not any(term in text for term in action_verbs)


def _looks_like_rules_or_adjudication_meta_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    meta_terms = (
        "????",
        "??",
        "????",
        "????",
        "????",
        "????????",
        "???",
        "????",
        "????",
        "??????",
        "????",
        "????",
        "dc???",
        "dc ???",
        "???????",
        "??????",
        "????????",
    )
    rules_context = ("??", "??", "??", "?", "dc", "??", "??", "??", "???", "??")
    return any(term in normalized for term in meta_terms) and any(term in normalized for term in rules_context)


def _looks_like_player_roster_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    exact = {
        "????",
        "????",
        "????",
        "????",
        "????",
        "???????",
        "???????",
        "??????????",
        "????",
        "????",
        "???????",
        "?????",
    }
    if normalized in exact:
        return True
    roster_terms = ("??", "??", "??", "??", "??", "??")
    query_terms = ("??", "??", "??", "??", "??", "??", "??", "?", "???")
    return any(term in normalized for term in roster_terms) and any(term in normalized for term in query_terms)


def _event_has_empty_dm_command(event: AstrMessageEvent | None) -> bool:
    return _dm_command_argument_from_event(event) == ""


def _dm_event_route_key(event: AstrMessageEvent | None) -> str:
    if event is None:
        return ""
    message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
    if message_id:
        return f"message:{message_id}"
    return f"event:{id(event)}"


def _dm_command_argument_from_event(event: AstrMessageEvent | None) -> str | None:
    best: str | None = None
    for message in _event_plain_text_candidates(event):
        normalized = message.replace("\u3000", " ")
        match = re.fullmatch(r"/[dD][mM](?:\s+(?P<argument>.*))?", normalized, flags=re.DOTALL)
        if not match:
            continue
        argument = str(match.group("argument") or "").strip()
        if best is None or len(argument) > len(best):
            best = argument
    return best


def _event_best_plain_text(event: AstrMessageEvent | None) -> str:
    best = ""
    for candidate in _event_plain_text_candidates(event):
        if len(candidate) > len(best):
            best = candidate
    return best


def _event_plain_text_candidates(event: AstrMessageEvent | None) -> list[str]:
    if event is None:
        return []
    candidates: list[str] = []
    _append_text_candidate(candidates, getattr(event, "message_str", None))
    getter = getattr(event, "get_message_str", None)
    if callable(getter):
        try:
            _append_text_candidate(candidates, getter())
        except Exception:
            pass
    message_obj = getattr(event, "message_obj", None)
    if message_obj is not None:
        _append_text_candidate(candidates, getattr(message_obj, "message_str", None))
        _append_text_candidate(candidates, _plain_text_from_message_value(getattr(message_obj, "message", None)))
        _append_text_candidate(candidates, _plain_text_from_message_value(getattr(message_obj, "raw_message", None)))
    return candidates


def _append_text_candidate(candidates: list[str], value: object) -> None:
    text = str(value or "").strip()
    if text and text not in candidates:
        candidates.append(text)


def _plain_text_from_message_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        direct = _plain_text_from_message_dict(value)
        if direct:
            return direct
        parts = []
        for key in ("message", "raw_message", "messages"):
            if key in value:
                text = _plain_text_from_message_value(value.get(key))
                if text:
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, (list, tuple)):
        return "".join(_plain_text_from_message_value(item) for item in value)
    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    data_attr = getattr(value, "data", None)
    if data_attr is not None:
        return _plain_text_from_message_value(data_attr)
    return ""


def _plain_text_from_message_dict(value: dict) -> str:
    component_type = str(value.get("type") or value.get("msg_type") or value.get("message_type") or "").lower()
    if component_type in {"text", "plain"}:
        data = value.get("data")
        if isinstance(data, dict):
            return str(data.get("text") or data.get("content") or "")
        if isinstance(data, str):
            return data
        return str(value.get("text") or value.get("content") or "")
    if not component_type:
        if isinstance(value.get("text"), str):
            return str(value.get("text") or "")
        if isinstance(value.get("content"), str):
            return str(value.get("content") or "")
    return ""


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
    return text[: max(1, limit - 1)].rstrip() + "?"


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
        return "??????"
    if isinstance(value, dict):
        preferred = []
        for key in ("total", "success", "degree", "outcome", "damage", "result", "message"):
            if key in value:
                preferred.append(f"{key}={value[key]}")
        if preferred:
            return _compact_text("?".join(preferred), 180)
    return _compact_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), 180)


def _compact_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "?"


def _compact_campaign_background(value: object) -> str:
    return _compact_text(value, 12000)


def _compact_starting_premise(value: object) -> str:
    return _compact_text(value, 6000)


def _compact_background_factions(value: object) -> str:
    return _compact_text(value, 4000)


def _extract_reset_confirmation_token(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return ""
    confirm_terms = ("????", "????", "????", "????", "confirm reset", "confirm-reset")
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
        for term in ("????", "????", "????", "????", "??", "confirm reset", "confirm-reset")
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
        "????",
        "?????",
        "?? bot",
        "??bot",
        "????",
        "????",
        "?? astrbot",
        "??astrbot",
        "restart plugin",
        "restart bot",
        "restart service",
        "reload plugin",
    )
    if any(term in lowered for term in non_save_restart_terms):
        return False
    recovery_terms = ("????", "??", "????", "??", "??", "backup", "restore")
    if any(term in lowered for term in recovery_terms):
        return False
    destructive_terms = (
        "????",
        "????",
        "????",
        "????",
        "?????",
        "????",
        "????",
        "???",
        "????",
        "reset save",
        "reset campaign",
        "new campaign",
    )
    return any(term in lowered for term in destructive_terms)


def _looks_like_restore_latest_backup_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if _looks_like_restart_latest_backup_story_request(text):
        return False
    restore_terms = (
        "??",
        "??",
        "??",
        "???",
        "????",
        "???",
        "??",
        "??",
        "??",
        "??",
        "restore",
        "load",
    )
    backup_terms = ("?????", "?????", "?????", "?????", "????", "?????", "????", "backup")
    return any(term in lowered for term in restore_terms) and any(term in lowered for term in backup_terms)


def _looks_like_restart_latest_backup_story_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    restart_terms = (
        "???",
        "????",
        "??",
        "???",
        "???",
        "???",
        "???",
        "??",
        "??",
        "restart",
        "start over",
    )
    source_terms = (
        "?????",
        "?????",
        "?????",
        "????",
        "?????",
        "????",
        "?????",
        "????",
        "?????",
        "???",
        "??",
        "backup",
    )
    story_scope_terms = ("??", "??", "??", "??", "??????", "?????", "????", "????", "????")
    return (
        any(term in lowered for term in restart_terms)
        and any(term in lowered for term in source_terms)
        and (any(term in lowered for term in story_scope_terms) or "???" in lowered or "??" in lowered)
    )


def _looks_like_backup_preview_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    list_terms = ("????", "?????", "????", "backup list", "list backups")
    if any(term in lowered for term in list_terms):
        return False
    view_terms = ("??", "??", "???", "??", "??", "???", "??", "??", "view", "preview", "show")
    backup_terms = ("?????", "?????", "?????", "?????", "????", "?????", "????", "backup")
    return any(term in lowered for term in view_terms) and any(term in lowered for term in backup_terms)


def _looks_like_backup_list_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(term in lowered for term in ("????", "?????", "????", "????", "backup list", "list backups"))


def _looks_like_manual_backup_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    create_terms = ("????", "????", "????", "????", "backup save", "create backup")
    list_terms = ("????", "????", "?????", "backup list")
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
    character_only = any(term in lowered for term in ("???", "???", "??", "??????", "????"))
    delegated_background = any(
        term in lowered
        for term in (
            "???",
            "???",
            "???",
            "???",
            "????",
            "????",
            "????",
            "????",
            "??",
            "??",
            "??",
            "??",
            "???",
        )
    )
    if character_only and not (_looks_like_enough_background_seed(text) or delegated_background):
        return {}
    if any(term in lowered for term in ("??", "40k", "warhammer", "????", "????", "?????", "??", "??")):
        location = "???Underhive?" if any(term in lowered for term in ("??", "??", "??")) else "??????"
        return {
            "genre": "grimdark_sci_fi",
            "tone": "???????????????????",
            "factions": ["Ultramarines??????", "Genestealer Cult?????????"],
            "starting_premise": _compact_starting_premise(text),
            "location": location,
            "ruleset": "homebrew_warhammer40k_adaptation??????????????? d20/?????",
            "campaign_background": _compact_campaign_background(text),
        }
    patch: dict = {}
    genre_terms = _terms_found(
        lowered,
        (
            "??",
            "??",
            "??",
            "??",
            "??",
            "??",
            "???",
            "??",
            "??",
            "??",
            "???",
            "??",
            "??",
            "??",
            "??",
            "???",
            "??",
            "??",
            "??",
            "dnd",
            "coc",
        ),
    )
    if genre_terms:
        patch["genre"] = "?".join(genre_terms)
    tone_terms = _terms_found(lowered, ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??"))
    if tone_terms:
        patch["tone"] = "?".join(tone_terms)
    location_terms = _terms_found(lowered, ("??", "??", "??", "??", "??", "??", "??", "??", "???", "???", "???", "??", "??", "??", "??"))
    if location_terms:
        patch["location"] = "?".join(location_terms)
    if any(term in lowered for term in ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??")):
        patch["factions"] = _compact_background_factions(text)
    if any(term in lowered for term in ("??", "??", "??", "?", "d20", "dnd", "coc", "??", "???")):
        patch["ruleset"] = "? d20 ?????????????????????"
    if any(term in lowered for term in ("????", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "???", "??", "??")):
        patch["starting_premise"] = _compact_starting_premise(text)
    if patch:
        patch.setdefault("tone", "? DM ????????????????????")
        patch.setdefault("ruleset", "? d20 ?????????????????????")
        if _looks_like_enough_background_seed(text) or _looks_like_new_campaign_seed_request(text):
            patch.setdefault("starting_premise", _compact_starting_premise(text))
        patch.setdefault("campaign_background", _compact_campaign_background(text))
        return patch
    if delegated_background:
        return {
            "genre": "LLM ????",
            "tone": "? LLM ?????????????????????????",
            "starting_premise": "???? DM ??????????????????????????? LLM ????????",
            "location": "? LLM ????????????????????????????",
            "factions": "? LLM ??????????? NPC??????????",
            "ruleset": "? d20 ?????????????????????",
            "campaign_background": "?????????? DM ?????????????????????????",
            "campaign_generation": {
                "source": "llm_generated_campaign",
                "status": "ready_for_opening",
                "seed": _compact_starting_premise(text),
                "opening_instruction": "? LLM ??????????????initial_hook???????????????????? scene_patch??????? start_game?",
            },
        }
    return {}


def _terms_found(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term and term in text]


def _visual_map_background_patch_from_text(text: str) -> dict:
    source = _compact_starting_premise(text or "????????")
    return {
        "genre": "??????",
        "tone": "??????????",
        "starting_premise": "??????????????DM ??????????????????????????",
        "location": "???????",
        "ruleset": "? d20 ???????? SVG ????????????????????????????",
        "campaign_background": f"???????{source}???????????????? visual-only SVG ??????????????",
        "background_source": "visual_map_request_bootstrap",
    }


def _looks_like_enough_background_seed(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    explicit = any(token in lowered for token in ("??", "???", "??", "??", "??", "??", "??", "premise", "setting"))
    buckets = 0
    if any(token in lowered for token in ("??", "??", "??", "??", "??", "??", "????", "??", "??", "??", "??", "???", "??", "??", "??", "??", "???", "??", "??", "??", "??", "??", "???", "??", "??", "??", "??", "dnd", "coc", "d20", "??", "40k", "warhammer", "grimdark", "????", "????")):
        buckets += 1
    if any(token in lowered for token in ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??")):
        buckets += 1
    if any(token in lowered for token in ("????", "????", "??", "??", "???", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "???", "??", "??")):
        buckets += 1
    if any(token in lowered for token in ("??", "??", "??", "??", "??", "??", "??", "???", "???", "?", "???", "??", "???", "?", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??")):
        buckets += 1
    if any(token in lowered for token in ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "????", "?????", "????", "????")):
        buckets += 1
    if any(token in lowered for token in ("??", "??", "??", "?", "??", "??", "???", "????", "??????", "????", "?????")):
        buckets += 1
    delegated_start = any(token in lowered for token in ("??", "??", "????", "????", "????", "????", "??", "??"))
    return buckets >= 2 and (explicit or delegated_start or buckets >= 3 or len(lowered) >= 28)


def _looks_like_background_authoring_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    subject_terms = (
        "??",
        "???",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "campaign",
        "setting",
        "premise",
    )
    authoring_terms = (
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "?",
        "?",
        "??",
        "???",
        "??",
        "??",
        "??",
        "??",
        "???",
        "??",
        "??",
        "??",
        "??",
        "???",
        "???",
        "??",
    )
    delegation_terms = (
        "???",
        "???",
        "???",
        "???",
        "????",
        "????",
        "???",
        "???",
        "???",
        "????",
        "????",
        "??????",
        "????",
        "????",
        "????",
    )
    if any(token in lowered for token in delegation_terms):
        return True
    if any(token in lowered for token in ("??", "??", "??", "??", "????", "??", "??")) and _looks_like_enough_background_seed(text):
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
            "????",
            "????",
            "??",
            "??",
            "????",
            "?????",
            "?????",
            "????",
            "????",
            "??",
            "??",
            "??",
        )
    )
    return start_or_delegate and _looks_like_enough_background_seed(text)


def _looks_like_in_campaign_content_expansion_request(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    expansion_terms = (
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
    )
    current_content_terms = (
        "npc",
        "??",
        "??",
        "??",
        "??",
        "???",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "???",
        "??",
        "??",
        "????",
    )
    start_terms = (
        "???",
        "???",
        "??",
        "??",
        "??",
        "????",
        "????",
        "????",
        "??",
        "??",
    )
    return (
        any(term in lowered for term in expansion_terms)
        and any(term in lowered for term in current_content_terms)
        and not any(term in lowered for term in start_terms)
    )


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
    if summary and not any(token in summary for token in ("????", "????", "???")):
        return True
    return False