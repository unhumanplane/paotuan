from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .ambient_image import AmbientImageConfig, AmbientImageProvider
from .cycle_buffer import append_cycle_action, complete_cycle_without_ra, cycle_end_requested
from .environment_agent import RecorderAgent, complete_cycle_with_ra, recover_cycle_after_ra_failure
from .external_memory import (
    HonchoExternalMemory,
    audit_safe_external_memory_result,
    external_memory_observation,
)
from .memory import MemoryCompressor
from .modes import GameModeStateMachine
from .models import GameMode, utc_now_iso
from .outbound_cleanup import (
    SemanticReviewCandidate,
    apply_semantic_menu_judgment,
    cleanup_menu_like_guidance,
)
from .plugin_log import get_plugin_logger
from .prompts import (
    build_diagnostic_system_prompt,
    build_system_prompt,
    build_user_prompt,
    prompt_snapshot_projection_stats,
    prompt_component_chars,
)
from ..storage.json_repository import JsonGameRepository
from ..tools.ambient_image_tools import (
    AmbientImageTools,
    should_offer_ambient_image,
    update_ambient_image_activity_state,
)
from ..tools.registry import ToolRegistry
from ..tools.turn_tools import TurnTools


class LockedToolExecutor:
    def __init__(self, executor: Any, lock: asyncio.Lock):
        self.executor = executor
        self.lock = lock

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            return await self.executor.execute(tool_name, args)


class LlmFailureResponse:
    def __init__(self, completion_text: str):
        self.completion_text = completion_text
        self.tools_call_name: list[str] = []
        self.tools_call_args: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    def __str__(self) -> str:
        return self.completion_text


class ToolLoopResult:
    def __init__(self, completion_text: str, tool_results: list[dict[str, Any]] | None = None):
        self.completion_text = completion_text
        self.tool_results = tool_results or []


LLM_USAGE_FIELDS = (
    "prompt_tokens",
    "input_tokens",
    "completion_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "cached_tokens",
    "cached_input_tokens",
    "cached_content_token_count",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
)
LLM_USAGE_CONTAINER_FIELDS = (
    "usage",
    "token_usage",
    "usage_metadata",
    "response_metadata",
    "llm_output",
    "raw_response",
    "response",
)
LLM_USAGE_DETAIL_FIELDS = {
    "prompt_tokens_details": {"cached_tokens": "cached_tokens"},
    "input_tokens_details": {"cached_tokens": "cached_tokens"},
    "input_token_details": {"cached_tokens": "cached_tokens"},
    "cache": {
        "read_input_tokens": "cache_read_input_tokens",
        "creation_input_tokens": "cache_creation_input_tokens",
    },
}
OUTBOUND_MENU_JUDGE_SYSTEM_PROMPT = """你是跑团 DM 回复的尾部菜单分类器。
只判断候选文本是否在给玩家提供若干行动/意图选项，让玩家从中挑选。
不要改写、不要补写、不要输出解释段落，只输出一个 JSON 对象。
分类取值：
- closed_player_options：候选文本在给玩家多个行动/意图选项。
- soft_help_options：玩家明确求助时，候选文本仍以菜单形式给多个方向。
- necessary_clarification：候选文本只是在问一个必要澄清点，例如目标、对象或含义。
- factual_or_diagnostic：候选文本是事实、规则、骰子、状态、诊断或列表输出。
- open_world_narrative：候选文本是开放叙事、风险、后果或可感知信息。
- uncertain：无法可靠判断。
动作取值：
- delete_candidate：删除候选文本。
- replace_with_local_help：仅在玩家明确求助且候选是菜单时使用。
- keep：保留。
不确定时必须选择 keep。"""


def _extract_llm_usage_summary(response: Any) -> dict[str, int | float]:
    summary: dict[str, int | float] = {}
    visited: set[int] = set()

    def collect(value: Any, depth: int = 0) -> None:
        if value is None or depth > 5:
            return
        value_id = id(value)
        if value_id in visited:
            return
        visited.add(value_id)
        mapping = _as_usage_mapping(value)
        if not mapping:
            return
        for field in LLM_USAGE_FIELDS:
            number = _usage_number(mapping.get(field))
            if number is not None and field not in summary:
                summary[field] = number
        for detail_key, fields in LLM_USAGE_DETAIL_FIELDS.items():
            detail = _as_usage_mapping(mapping.get(detail_key))
            if not detail:
                continue
            for source_key, target_key in fields.items():
                number = _usage_number(detail.get(source_key))
                if number is not None and target_key not in summary:
                    summary[target_key] = number
        for container_key in LLM_USAGE_CONTAINER_FIELDS:
            if container_key in mapping:
                collect(mapping.get(container_key), depth + 1)

    collect(response)
    _add_cache_hit_ratio(summary)
    return summary


def _as_usage_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if value is None or isinstance(value, (str, bytes, int, float, bool)):
        return None
    mapping: dict[str, Any] = {}
    for field in (*LLM_USAGE_FIELDS, *LLM_USAGE_CONTAINER_FIELDS, *LLM_USAGE_DETAIL_FIELDS.keys()):
        try:
            if hasattr(value, field):
                mapping[field] = getattr(value, field)
        except Exception:
            continue
    return mapping or None


def _usage_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _add_cache_hit_ratio(summary: dict[str, int | float]) -> None:
    input_tokens = _first_usage_number(
        summary,
        ("prompt_tokens", "input_tokens", "prompt_token_count"),
    )
    cached_tokens = _first_usage_number(
        summary,
        (
            "cached_tokens",
            "cache_read_input_tokens",
            "cached_input_tokens",
            "cached_content_token_count",
            "prompt_cache_hit_tokens",
        ),
    )
    if input_tokens and cached_tokens is not None:
        summary["cache_hit_ratio_pct"] = round(cached_tokens / input_tokens * 100, 2)


def _first_usage_number(
    mapping: Mapping[str, int | float],
    keys: tuple[str, ...],
) -> int | float | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def _build_outbound_menu_judge_prompt(player_message: str, candidate: SemanticReviewCandidate) -> str:
    player_help = "true" if candidate.player_wants_help else "false"
    signals = ",".join(candidate.signals)
    return f"""请只判断下面的“候选尾部文本”是否在给玩家提供若干选项。
语义判断只服务于删除/保留这个候选段，不允许改写回复。

玩家上一条消息：
{_short_inferred_text(player_message, 240)}

玩家是否明确求助：
{player_help}

本地命中的可疑信号：
{signals}

候选尾部文本：
{candidate.text}

请输出 JSON：
{{
  "classification": "closed_player_options|soft_help_options|necessary_clarification|factual_or_diagnostic|open_world_narrative|uncertain",
  "action": "delete_candidate|replace_with_local_help|keep",
  "confidence": 0.0,
  "reason": "short reason"
}}"""


def _semantic_judge_action(
    classification: str,
    requested_action: str,
    confidence: float,
    *,
    player_wants_help: bool,
) -> str:
    normalized_classification = str(classification or "").strip().lower()
    normalized_action = str(requested_action or "").strip().lower()
    if confidence < 0.65:
        return "keep"
    if normalized_classification == "soft_help_options":
        return "replace_with_local_help" if player_wants_help else "delete_candidate"
    if normalized_classification == "closed_player_options":
        if normalized_action == "replace_with_local_help" and player_wants_help:
            return "replace_with_local_help"
        return "delete_candidate"
    return "keep"


def _confidence_value(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(str(value).strip())
        except ValueError:
            return 0.0
    return max(0.0, min(1.0, number))


def _llm_request_shape(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    contexts = kwargs.get("contexts") or []
    if isinstance(contexts, (list, tuple)):
        contexts_count = len(contexts)
    elif contexts:
        contexts_count = 1
    else:
        contexts_count = 0
    return {
        "prompt_chars": _safe_char_count(kwargs.get("prompt")),
        "system_prompt_chars": _safe_char_count(kwargs.get("system_prompt")),
        "contexts_count": contexts_count,
        "contexts_chars": _safe_char_count(contexts),
        "tool_enabled": bool(kwargs.get("func_tool") is not None or kwargs.get("tools") is not None),
    }


def _safe_char_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except Exception:
        return len(str(value))


def _short_hash(value: Any) -> str:
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


def _log_llm_usage_summary(response: Any, kwargs: Mapping[str, Any]) -> None:
    try:
        usage = _extract_llm_usage_summary(response)
        shape = _llm_request_shape(kwargs)
        usage_text = json.dumps(usage, ensure_ascii=False, separators=(",", ":"))
        get_plugin_logger().info(
            "llm_usage chat_provider=%s prompt_chars=%s system_prompt_chars=%s system_prompt_hash=%s contexts_count=%s contexts_chars=%s tool_enabled=%s usage_available=%s usage=%s",
            kwargs.get("chat_provider_id", ""),
            shape["prompt_chars"],
            shape["system_prompt_chars"],
            _short_hash(kwargs.get("system_prompt")),
            shape["contexts_count"],
            shape["contexts_chars"],
            shape["tool_enabled"],
            bool(usage),
            usage_text,
        )
    except Exception:
        return


class IntentRouter:
    def __init__(
        self,
        astr_context: Any,
        repository: JsonGameRepository,
        tool_registry: ToolRegistry,
        external_memory: HonchoExternalMemory | None = None,
        ambient_image_config: AmbientImageConfig | None = None,
        ambient_image_provider: AmbientImageProvider | None = None,
        ambient_image_sender: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None,
        max_steps: int = 8,
        ra_enabled: bool = False,
        ra_model_provider: str = "default",
        ra_max_tokens: int = 2048,
        prompt_snapshot_projection_enabled: bool = True,
    ):
        self.astr_context = astr_context
        self.repository = repository
        self.tool_registry = tool_registry
        self.external_memory = external_memory
        self.ambient_image_config = ambient_image_config or AmbientImageConfig(enabled=False)
        self.ambient_image_provider = ambient_image_provider or AmbientImageProvider(self.ambient_image_config)
        self.ambient_image_sender = ambient_image_sender
        self.mode_machine = GameModeStateMachine()
        self.memory_compressor = MemoryCompressor()
        self.max_steps = max_steps
        self.ra_enabled = ra_enabled
        self.ra_model_provider = ra_model_provider
        self.ra_max_tokens = ra_max_tokens
        self.prompt_snapshot_projection_enabled = prompt_snapshot_projection_enabled
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_turn_locks: dict[str, asyncio.Lock] = {}

    async def handle_message(
        self,
        event: Any,
        message_override: str | None = None,
        security_notes: list[str] | None = None,
    ) -> str:
        message = (message_override or getattr(event, "message_str", "") or "").strip()
        if not message:
            return ""

        session_id = self.session_id_for_event(event)
        actor = self.actor_context_for_event(event)
        lock = self._lock_for_session(session_id)

        provider_id = await self.astr_context.get_current_chat_provider_id(
            umo=event.unified_msg_origin
        )
        if await self._should_serialize_llm(session_id, message, lock):
            turn_lock = self._turn_lock_for_session(session_id)
            async with turn_lock:
                return await self._handle_message_once(
                    message=message,
                    session_id=session_id,
                    actor=actor,
                    lock=lock,
                    provider_id=provider_id,
                    security_notes=security_notes,
                )
        return await self._handle_message_once(
            message=message,
            session_id=session_id,
            actor=actor,
            lock=lock,
            provider_id=provider_id,
            security_notes=security_notes,
        )

    async def _handle_message_once(
        self,
        message: str,
        session_id: str,
        actor: dict[str, str],
        lock: asyncio.Lock,
        provider_id: str,
        security_notes: list[str] | None = None,
    ) -> str:
        async with lock:
            session = self.repository.load_session(session_id)
            self._touch_participant(session, actor)
            legacy_live_scene_changed = _ensure_legacy_live_scene_state(session)
            post_game_turn_closed = _maybe_close_concluded_turn(session, message)
            turn_policy_events = []
            if not post_game_turn_closed:
                turn_policy_events = TurnTools(
                    self.repository,
                    session_id,
                    actor=actor,
                ).apply_turn_timeout_policy(session, message)
            mode = self.mode_machine.detect(session, message)
            session.mode = mode
            snapshot_chars_before = self.memory_compressor.snapshot_chars(session)
            if self.memory_compressor.maybe_compress(session):
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "memory_compressed",
                        "phase": "pre_prompt",
                        "snapshot_chars_before": snapshot_chars_before,
                        "snapshot_chars_after": self.memory_compressor.snapshot_chars(session),
                        "summary_chars": len(session.memory_summary),
                    },
                )
            self.repository.save_session(session)
            if legacy_live_scene_changed:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "legacy_live_scene_state_marked",
                        "actor": actor,
                        "player_message": message,
                    },
                )
            if post_game_turn_closed:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "post_game_turn_closed",
                        "actor": actor,
                        "player_message": message,
                        **post_game_turn_closed,
                    },
                )
                get_plugin_logger().info(
                    "post_game_turn_closed session=%s phase=%s reason=%s",
                    session_id,
                    post_game_turn_closed.get("previous_phase", ""),
                    post_game_turn_closed.get("reason", ""),
                )
            for event in turn_policy_events:
                self.repository.append_audit(
                    session_id,
                    {
                        **event,
                        "actor": actor,
                        "player_message": message,
                    },
                )
                get_plugin_logger().info(
                    "turn_policy_event session=%s type=%s actor=%s current=%s owner=%s deadline=%s",
                    session_id,
                    event.get("type", ""),
                    actor.get("player_id", ""),
                    event.get("current_entity_id", ""),
                    event.get("owner_player_id", ""),
                    event.get("deadline_at", ""),
                )

            reasonableness_guard = _action_reasonableness_guard_reply(session, actor, message)
            if reasonableness_guard:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_action_reasonableness_guard",
                        "actor": actor,
                        "player_message": message,
                        "reply": reasonableness_guard,
                    },
                )
                get_plugin_logger().info(
                    "local_action_reasonableness_guard session=%s actor=%s text=%s",
                    session_id,
                    actor.get("player_id", ""),
                    message[:120].replace("\n", "\\n"),
                )
                return reasonableness_guard

            action_economy_guard = _action_economy_guard_reply(session, actor, message)
            if action_economy_guard:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "local_action_economy_guard",
                        "actor": actor,
                        "player_message": message,
                        "reply": action_economy_guard,
                    },
                )
                get_plugin_logger().info(
                    "local_action_economy_guard session=%s actor=%s text=%s",
                    session_id,
                    actor.get("player_id", ""),
                    message[:120].replace("\n", "\\n"),
                )
                return action_economy_guard

            toolset, tool_names, tool_executor, tool_specs = self.tool_registry.for_mode(
                mode,
                session_id,
                actor=actor,
                message=message,
                provider_id=provider_id,
            )
            diagnostic_prompt = _is_diagnostic_request(message)
            external_memory_context = ""
            external_memory_context_chars = 0
            if self.external_memory and not diagnostic_prompt:
                external_result = await self.external_memory.context_for_prompt(session, actor, message)
                if external_result.get("ok") and external_result.get("available"):
                    external_memory_context = str(external_result.get("context", "") or "")
                    external_memory_context_chars = len(external_memory_context)
                    self.repository.append_audit(
                        session_id,
                        {
                            "type": "external_memory_context_observed",
                            "provider": "honcho",
                            "result": external_memory_observation(external_result),
                        },
                    )
                elif not external_result.get("ok", True):
                    self.repository.append_audit(
                        session_id,
                        {
                            "type": "external_memory_context_failed",
                            "provider": "honcho",
                            "result": audit_safe_external_memory_result(external_result),
                        },
                    )
            if diagnostic_prompt:
                system_prompt = build_diagnostic_system_prompt(
                    session,
                    mode,
                    tool_names,
                    actor=actor,
                )
            else:
                system_prompt = build_system_prompt(
                    session,
                    mode,
                    tool_names,
                    tool_specs,
                    actor=actor,
                    external_memory_context=external_memory_context,
                    include_ra_context=self.ra_enabled,
                    message=message,
                    snapshot_projection_enabled=self.prompt_snapshot_projection_enabled,
                )
            tool_schema_text = json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":"))
            prompt_profile = "diagnostic" if diagnostic_prompt else "standard"
            component_chars = prompt_component_chars(
                session,
                mode,
                tool_names,
                actor=actor,
                external_memory_context=external_memory_context,
                include_ra_context=self.ra_enabled,
                profile=prompt_profile,
                message=message,
                snapshot_projection_enabled=self.prompt_snapshot_projection_enabled,
            )
            component_chars["system_prompt_chars"] = len(system_prompt)
            component_chars["tool_schema_chars"] = len(tool_schema_text)
            attributed_prompt_chars = sum(
                value
                for key, value in component_chars.items()
                if key.endswith("_chars") and key not in {"system_prompt_chars", "tool_schema_chars"}
                if isinstance(value, int)
            )
            shell_key = (
                "diagnostic_static_shell_chars"
                if prompt_profile == "diagnostic"
                else "static_prompt_shell_chars"
            )
            component_chars[shell_key] = max(0, len(system_prompt) - attributed_prompt_chars)
            component_tokens = _component_token_estimates(component_chars)
            component_chars_text = json.dumps(
                component_chars,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            component_tokens_text = json.dumps(
                component_tokens,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            projection_stats = prompt_snapshot_projection_stats(
                session,
                mode,
                message,
                actor=actor,
                include_ra_context=self.ra_enabled,
                snapshot_projection_enabled=(
                    self.prompt_snapshot_projection_enabled and not diagnostic_prompt
                ),
            )
            projection_stats_text = json.dumps(
                projection_stats,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            get_plugin_logger().info(
                "router_prepared session=%s mode=%s actor=%s tools=%s snapshot_chars=%s system_prompt_chars=%s system_prompt_hash=%s tool_schema_chars=%s tool_schema_hash=%s external_memory_chars=%s prompt_profile=%s prompt_component_chars=%s prompt_component_tokens=%s snapshot_projection=%s rough_total_tokens=%s",
                session_id,
                mode.value,
                actor.get("player_id", ""),
                ",".join(tool_names),
                self.memory_compressor.snapshot_chars(session),
                len(system_prompt),
                _short_hash(system_prompt),
                len(tool_schema_text),
                _short_hash(tool_schema_text),
                external_memory_context_chars,
                prompt_profile,
                component_chars_text,
                component_tokens_text,
                projection_stats_text,
                _rough_token_count(len(system_prompt) + len(tool_schema_text)),
            )

        loop_result = await self._run_llm_tool_loop(
            chat_provider_id=provider_id,
            system_prompt=system_prompt,
            initial_prompt=build_user_prompt(message, security_notes=security_notes),
            toolset=toolset,
            tool_executor=LockedToolExecutor(tool_executor, lock),
            session_id=session_id,
            audit_lock=lock,
            raw_player_message=message,
        )
        completion = self._sanitize_completion_text(loop_result.completion_text)
        tool_trace = loop_result.tool_results

        async with lock:
            latest_session = self.repository.load_session(session_id)
            raw_completion_chars = len(completion)
            completion = self._limit_completion(completion, latest_session, raw_player_message=message)
            if len(completion) < raw_completion_chars:
                get_plugin_logger().info(
                    "completion_limited session=%s mode=%s actor=%s from_chars=%s to_chars=%s message=%s",
                    session_id,
                    mode.value,
                    actor.get("player_id", ""),
                    raw_completion_chars,
                    len(completion),
                    message[:120].replace("\n", "\\n"),
                )
            completeness_guard = _adjudication_completeness_guard(
                latest_session,
                actor=actor,
                player_message=message,
                completion=completion,
                tool_results=tool_trace,
            )
            if completeness_guard:
                completion = self._limit_completion(
                    f"{completion}\n{completeness_guard.get('reply_suffix', '')}".strip(),
                    latest_session,
                    raw_player_message=message,
                )
                completion = self._sanitize_completion_text(completion)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "adjudication_completeness_guard",
                        "actor": actor,
                        "player_message": message,
                        "completion_excerpt": completion[:300],
                        **completeness_guard,
                    },
                )
                get_plugin_logger().info(
                    "adjudication_completeness_guard session=%s actor=%s reason=%s tools=%s",
                    session_id,
                    actor.get("player_id", ""),
                    completeness_guard.get("reason", ""),
                    ",".join(completeness_guard.get("tool_names", [])),
                )
            else:
                fallback_turn = await self._maybe_auto_advance_resolved_turn(
                    session=latest_session,
                    actor=actor,
                    player_message=message,
                    completion=completion,
                    session_id=session_id,
                )
                if fallback_turn:
                    completion = self._limit_completion(
                        f"{completion}\n{fallback_turn.get('reply_suffix', '')}".strip(),
                        self.repository.load_session(session_id),
                        raw_player_message=message,
                    )
                    completion = self._sanitize_completion_text(completion)
                    latest_session = self.repository.load_session(session_id)
                    self.repository.append_audit(
                        session_id,
                        {
                            "type": "turn_auto_advance_fallback",
                            "actor": actor,
                            "player_message": message,
                            **fallback_turn,
                        },
                    )
                    get_plugin_logger().info(
                        "turn_auto_advance_fallback session=%s actor=%s from=%s to=%s",
                        session_id,
                        actor.get("player_id", ""),
                        fallback_turn.get("from_entity_id", ""),
                        fallback_turn.get("to_entity_id", ""),
                    )
            completion_before_cleanup = completion
            cleanup = cleanup_menu_like_guidance(
                completion,
                player_message=message,
                diagnostic=diagnostic_prompt,
            )
            if cleanup.changed:
                completion = cleanup.text
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "outbound_menu_guidance_cleaned",
                        "actor": actor,
                        "player_message": message,
                        "reason": cleanup.reason,
                        "removed_blocks": cleanup.removed_blocks,
                        "replacement_used": cleanup.replacement_used,
                        "original_chars": cleanup.original_chars,
                        "cleaned_chars": cleanup.cleaned_chars,
                        "original_hash": _short_hash(completion_before_cleanup),
                        "cleaned_hash": _short_hash(completion),
                    },
                )
                get_plugin_logger().info(
                    "outbound_menu_guidance_cleaned session=%s mode=%s actor=%s reason=%s removed_blocks=%s original_chars=%s cleaned_chars=%s",
                    session_id,
                    mode.value,
                    actor.get("player_id", ""),
                    cleanup.reason,
                    cleanup.removed_blocks,
                    cleanup.original_chars,
                    cleanup.cleaned_chars,
                )
            elif cleanup.semantic_candidate:
                semantic_review = await self._judge_outbound_menu_candidate(
                    chat_provider_id=provider_id,
                    player_message=message,
                    candidate=cleanup.semantic_candidate,
                )
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "outbound_menu_guidance_semantic_reviewed",
                        "actor": actor,
                        "player_message": message,
                        "candidate_hash": _short_hash(cleanup.semantic_candidate.text),
                        "candidate_chars": len(cleanup.semantic_candidate.text),
                        "candidate_start": cleanup.semantic_candidate.start,
                        "candidate_end": cleanup.semantic_candidate.end,
                        "signals": list(cleanup.semantic_candidate.signals),
                        "classification": semantic_review.get("classification", "uncertain"),
                        "action": semantic_review.get("action", "keep"),
                        "confidence": semantic_review.get("confidence", 0.0),
                        "reason": semantic_review.get("reason", ""),
                        "parse_ok": semantic_review.get("parse_ok", False),
                    },
                )
                semantic_cleanup = apply_semantic_menu_judgment(
                    completion,
                    cleanup.semantic_candidate,
                    str(semantic_review.get("action") or "keep"),
                )
                if semantic_cleanup.changed:
                    completion = semantic_cleanup.text
                    self.repository.append_audit(
                        session_id,
                        {
                            "type": "outbound_menu_guidance_cleaned",
                            "actor": actor,
                            "player_message": message,
                            "reason": semantic_cleanup.reason,
                            "removed_blocks": semantic_cleanup.removed_blocks,
                            "replacement_used": semantic_cleanup.replacement_used,
                            "original_chars": semantic_cleanup.original_chars,
                            "cleaned_chars": semantic_cleanup.cleaned_chars,
                            "original_hash": _short_hash(completion_before_cleanup),
                            "cleaned_hash": _short_hash(completion),
                            "semantic_classification": semantic_review.get("classification", "uncertain"),
                            "semantic_confidence": semantic_review.get("confidence", 0.0),
                        },
                    )
                get_plugin_logger().info(
                    "outbound_menu_guidance_semantic_reviewed session=%s mode=%s actor=%s classification=%s action=%s confidence=%s parse_ok=%s candidate_chars=%s",
                    session_id,
                    mode.value,
                    actor.get("player_id", ""),
                    semantic_review.get("classification", "uncertain"),
                    semantic_review.get("action", "keep"),
                    semantic_review.get("confidence", 0.0),
                    semantic_review.get("parse_ok", False),
                    len(cleanup.semantic_candidate.text),
                )
            trace_record = self._persist_narrative_trace(
                latest_session,
                actor=actor,
                player_message=message,
                completion=completion,
            )
            if trace_record:
                update_ambient_image_activity_state(
                    latest_session,
                    actor=actor,
                    player_message=message,
                )
            cycle_action_record = None
            if trace_record:
                cycle_action_record = append_cycle_action(
                    latest_session,
                    actor=actor,
                    player_message=message,
                    completion=completion,
                    tool_results=tool_trace,
                )
            if trace_record:
                self.repository.save_session(latest_session)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "narrative_trace_recorded",
                        **trace_record,
                    },
                )
                get_plugin_logger().info(
                    "narrative_trace_recorded session=%s actor=%s character=%s",
                    session_id,
                    actor.get("player_id", ""),
                    trace_record.get("character_id", ""),
                )
                if self.external_memory:
                    external_write = await self.external_memory.write_key_event(
                        latest_session,
                        actor,
                        trace_record,
                    )
                    if external_write.get("synced"):
                        self.repository.save_session(latest_session)
                    if (
                        external_write.get("available")
                        or not external_write.get("ok", True)
                        or external_write.get("reason") == "duplicate_external_memory_event"
                    ):
                        self.repository.append_audit(
                            session_id,
                            {
                                "type": "external_memory_write_key_event",
                                "provider": "honcho",
                                "result": audit_safe_external_memory_result(external_write),
                            },
                        )
            if cycle_action_record:
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "cycle_action_recorded",
                        **cycle_action_record,
                    },
                )
            if cycle_end_requested(tool_trace):
                if self.ra_enabled:
                    ra_chat_provider = provider_id if (self.ra_model_provider or "default") == "default" else self.ra_model_provider
                    ra_result = await RecorderAgent(
                        self._llm_generate, ra_chat_provider, max_tokens=self.ra_max_tokens
                    ).run_cycle_resolution(latest_session)
                    if ra_result.get("ok"):
                        completion_record = complete_cycle_with_ra(latest_session, ra_result["summary"])
                        self.repository.save_session(latest_session)
                        self.repository.append_audit(
                            session_id,
                            {
                                "type": "ra_cycle_resolved",
                                "actor": actor,
                                **completion_record,
                                "prompt_chars": ra_result.get("prompt_chars", 0),
                                "output_chars": ra_result.get("output_chars", 0),
                            },
                        )
                    else:
                        recovery_record = recover_cycle_after_ra_failure(latest_session, ra_result)
                        self.repository.save_session(latest_session)
                        self.repository.append_audit(
                            session_id,
                            {
                                "type": "ra_cycle_failed",
                                "actor": actor,
                                "cycle_id": latest_session.current_cycle_id,
                                "error": ra_result.get("error", "ra_failed"),
                                "message": ra_result.get("message", ""),
                                "recovery": recovery_record,
                            },
                        )
                else:
                    complete_cycle_without_ra(latest_session)
                    self.repository.save_session(latest_session)
                    self.repository.append_audit(
                        session_id,
                        {
                            "type": "cycle_resolved_without_ra",
                            "actor": actor,
                            "cycle_id": latest_session.current_cycle_id - 1,
                            "reason": "ra_enabled_false",
                        },
                    )
            self.repository.append_audit(
                session_id,
                {
                    "type": "message_handled",
                    "mode": mode.value,
                    "player_message": message,
                    "actor": actor,
                    "tool_names": tool_names,
                    "completion": completion,
                },
            )
            snapshot_chars_before = self.memory_compressor.snapshot_chars(latest_session)
            if self.memory_compressor.maybe_compress(latest_session):
                self.repository.save_session(latest_session)
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "memory_compressed",
                        "phase": "post_message",
                        "snapshot_chars_before": snapshot_chars_before,
                        "snapshot_chars_after": self.memory_compressor.snapshot_chars(latest_session),
                        "summary_chars": len(latest_session.memory_summary),
                    },
                )
                if self.external_memory:
                    external_summary = await self.external_memory.write_memory_summary(
                        latest_session,
                        actor,
                        reason="post_message_compression",
                    )
                    if external_summary.get("synced"):
                        self.repository.save_session(latest_session)
                    if (
                        external_summary.get("available")
                        or not external_summary.get("ok", True)
                        or external_summary.get("reason") == "duplicate_external_memory_event"
                    ):
                        self.repository.append_audit(
                            session_id,
                            {
                                "type": "external_memory_write_summary",
                                "provider": "honcho",
                                "result": audit_safe_external_memory_result(external_summary),
                            },
                        )
            ambient_result = self._schedule_ambient_image_generation(
                session=latest_session,
                mode=mode,
                actor=actor,
                player_message=message,
                completion=completion,
                provider_id=provider_id,
                trace_record=trace_record,
            )
            if ambient_result.get("scheduled"):
                self.repository.append_audit(
                    session_id,
                    {
                        "type": "ambient_image_auto_scheduled",
                        "actor": actor,
                        "trigger": ambient_result.get("trigger", ""),
                        "story_moment": ambient_result.get("story_moment", ""),
                    },
                )
            get_plugin_logger().info(
                "message_handled session=%s mode=%s actor=%s completion_chars=%s",
                session_id,
                mode.value,
                actor.get("player_id", ""),
                len(completion),
            )
        return completion

    async def _maybe_auto_advance_resolved_turn(
        self,
        session: Any,
        actor: dict[str, str],
        player_message: str,
        completion: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        battle = session.battle or {}
        turn = dict(battle.get("turn") or {})
        if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
            return None
        if _scene_looks_concluded(session) or _looks_like_terminal_or_interlude_request(player_message):
            return None
        actor_id = str(actor.get("player_id") or "").strip()
        if not actor_id:
            return None
        acting_id = _turn_pending_entity_for_actor(session, turn, actor_id)
        if not acting_id:
            return None
        if not _looks_like_turn_consuming_player_action(player_message):
            return None
        if not _completion_indicates_resolved_turn_action(completion):
            return None

        label = _turn_entity_label(session, acting_id)
        summary = f"{label}：{_compact_text(completion, 170)}"
        result = await TurnTools(self.repository, session_id, actor=actor).turn_control(
            action="record_action",
            current_entity_id=acting_id,
            summary=summary,
            reason="LLM 已裁定当前发言人的本轮主要动作，但未显式调用 turn_control；本地兜底推进。",
            output_limit_chars=int(turn.get("output_limit_chars") or 1440),
            advance_after=True,
        )
        result_turn = dict(result.get("turn") or {})
        next_id = str(result_turn.get("current_entity_id") or "")
        next_label = str(result_turn.get("current_label") or next_id or "")
        if not result.get("ok"):
            return {
                "ok": False,
                "from_entity_id": acting_id,
                "to_entity_id": "",
                "turn_control_result": result,
                "reply_suffix": "",
            }
        if str(result_turn.get("phase") or "") == "scene_resolution":
            suffix = f"进入第 {result_turn.get('round', '?')} 轮场面结算。"
        else:
            suffix = f"现在轮到：{next_label}。"
        return {
            "ok": True,
            "from_entity_id": acting_id,
            "to_entity_id": next_id,
            "turn_control_result": result,
            "reply_suffix": suffix,
        }

    def _schedule_ambient_image_generation(
        self,
        *,
        session: Any,
        mode: GameMode,
        actor: dict[str, str],
        player_message: str,
        completion: str,
        provider_id: str,
        trace_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not should_offer_ambient_image(
            session,
            self.ambient_image_config,
            mode,
            player_message=player_message,
        ):
            return {"scheduled": False}
        if not trace_record:
            return {"scheduled": False, "reason": "ambient_image_no_narrative_trace"}
        story_moment = _ambient_story_moment(player_message, completion, trace_record)
        if not story_moment:
            return {"scheduled": False, "reason": "ambient_image_empty_story_moment"}
        self._mark_ambient_image_generation_started(session)
        task = asyncio.create_task(
            self._maybe_generate_ambient_image(
                session=session,
                mode=mode,
                actor=actor,
                player_message=player_message,
                completion=completion,
                provider_id=provider_id,
                trace_record=trace_record,
            )
        )
        task.add_done_callback(
            lambda completed: self._ambient_image_task_done(session.session_id, completed)
        )
        get_plugin_logger().info(
            "ambient_image_auto_scheduled session=%s actor=%s story_moment=%s",
            session.session_id,
            actor.get("player_id", ""),
            story_moment[:120].replace("\n", "\\n"),
        )
        return {
            "scheduled": True,
            "trigger": "auto",
            "story_moment": story_moment,
        }

    async def _maybe_generate_ambient_image(
        self,
        *,
        session: Any,
        mode: GameMode,
        actor: dict[str, str],
        player_message: str,
        completion: str,
        provider_id: str,
        trace_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if not should_offer_ambient_image(
            session,
            self.ambient_image_config,
            mode,
            player_message=player_message,
            ignore_generation_in_progress=True,
        ):
            return {"recorded": False}
        if not trace_record:
            return {"recorded": False, "reason": "ambient_image_no_narrative_trace"}
        story_moment = _ambient_story_moment(player_message, completion, trace_record)
        if not story_moment:
            return {"recorded": False, "reason": "ambient_image_empty_story_moment"}
        tools = AmbientImageTools(
            self.repository,
            session.session_id,
            self.ambient_image_config,
            self.ambient_image_provider,
            actor=actor,
            message=player_message,
            llm_generate=self._llm_generate,
            chat_provider_id=provider_id,
        )
        result = await tools.generate_ambient_image(
            story_moment=story_moment,
            rationale="叙事推进后内部条件触发氛围图。",
            send_to_chat=True,
            ignore_generation_in_progress=True,
        )
        if result.get("ok") and result.get("available"):
            send_result = await self._send_ambient_image_if_configured(
                session.session_id,
                result,
            )
            self.repository.append_audit(
                session.session_id,
                {
                    "type": "ambient_image_auto_generated",
                    "actor": actor,
                    "result": {
                        key: value
                        for key, value in result.items()
                        if key not in {"file_path", "metadata_path"}
                    },
                    "send_result": send_result,
                },
            )
            return {"recorded": True, "result": result}
        if result.get("reason") not in {"ambient_image_frequency_wait", "ambient_image_disabled"}:
            self.repository.append_audit(
                session.session_id,
                {
                    "type": "ambient_image_auto_skipped",
                    "actor": actor,
                    "result": result,
                },
            )
        return {"recorded": False, "result": result}

    async def _send_ambient_image_if_configured(
        self,
        session_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if not result.get("send_to_chat"):
            return {"sent": False, "reason": "ambient_image_send_disabled"}
        if self.ambient_image_sender is None:
            return {"sent": False, "reason": "ambient_image_sender_missing"}
        try:
            sent = await self.ambient_image_sender(session_id, result)
        except Exception as exc:
            get_plugin_logger().exception(
                "ambient_image_independent_send_failed session=%s error=%s",
                session_id,
                exc,
            )
            return {"sent": False, "reason": "ambient_image_send_failed"}
        return {"sent": bool(sent)}

    def _mark_ambient_image_generation_started(self, session: Any) -> None:
        scene = getattr(session, "scene", {}) or {}
        state = dict(scene.get("ambient_image_state") or {})
        state["generation_started_at"] = utc_now_iso()
        scene["ambient_image_state"] = state
        self.repository.save_session(session)

    def _clear_ambient_image_generation_started(self, session_id: str) -> None:
        try:
            session = self.repository.load_session(session_id)
            scene = getattr(session, "scene", {}) or {}
            state = dict(scene.get("ambient_image_state") or {})
            if "generation_started_at" not in state:
                return
            state.pop("generation_started_at", None)
            scene["ambient_image_state"] = state
            self.repository.save_session(session)
        except Exception as exc:
            get_plugin_logger().warning(
                "ambient_image_generation_clear_failed session=%s error=%s",
                session_id,
                exc,
            )

    def _ambient_image_task_done(self, session_id: str, task: asyncio.Task) -> None:
        self._clear_ambient_image_generation_started(session_id)
        if task.cancelled():
            get_plugin_logger().warning("ambient_image_task_cancelled session=%s", session_id)
            return
        try:
            exc = task.exception()
        except Exception as task_exc:
            get_plugin_logger().warning(
                "ambient_image_task_status_failed session=%s error=%s",
                session_id,
                task_exc,
            )
            return
        if exc:
            get_plugin_logger().error(
                "ambient_image_task_failed session=%s error=%s",
                session_id,
                exc,
            )

    @staticmethod
    def _persist_narrative_trace(
        session: Any,
        actor: dict[str, str],
        player_message: str,
        completion: str,
    ) -> dict[str, Any] | None:
        if not _should_record_narrative_trace(player_message, completion):
            return None
        _ensure_legacy_live_scene_state(session)
        player_id = str(actor.get("player_id") or "").strip()
        character_id = str((session.player_character_map or {}).get(player_id, "") or "")
        now = utc_now_iso()
        event = {
            "at": now,
            "player_id": player_id,
            "display_name": actor.get("display_name", ""),
            "character_id": character_id,
            "message": _compact_text(player_message, 140),
            "outcome": _compact_text(completion, 220),
        }
        recent = list((session.scene or {}).get("_recent_narrative_events") or [])
        recent.append(event)
        session.scene["_recent_narrative_events"] = recent[-12:]
        session.scene["last_resolution"] = {
            "at": now,
            "character_id": character_id,
            "player_message": _compact_text(player_message, 120),
            "outcome": _compact_text(completion, 180),
        }
        if character_id and character_id in session.characters:
            character = session.characters[character_id]
            character.upsert_tags(
                [
                    {
                        "key": "最近行动",
                        "value": f"{_compact_text(player_message, 90)} => {_compact_text(completion, 150)}",
                        "type": "text",
                        "source": "local_trace",
                        "layer": "status",
                    }
                ]
            )
        return event

    def _lock_for_session(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    def _turn_lock_for_session(self, session_id: str) -> asyncio.Lock:
        lock = self._session_turn_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_turn_locks[session_id] = lock
        return lock

    async def _should_serialize_llm(
        self,
        session_id: str,
        message: str,
        lock: asyncio.Lock,
    ) -> bool:
        async with lock:
            session = self.repository.load_session(session_id)
            mode = self.mode_machine.detect(session, message)
            return mode == GameMode.TACTICAL

    @staticmethod
    def _touch_participant(session: Any, actor: dict[str, str]) -> None:
        player_id = actor.get("player_id", "")
        if not player_id:
            return
        participant = dict(session.participants.get(player_id, {}))
        participant.update(
            {
                "player_id": player_id,
                "display_name": actor.get("display_name", "") or participant.get("display_name", ""),
                "platform": actor.get("platform", "") or participant.get("platform", ""),
                "last_seen_at": actor.get("seen_at", ""),
            }
        )
        session.participants[player_id] = participant
        bound_character_id = session.player_character_map.get(player_id, "")
        if bound_character_id and bound_character_id in session.characters:
            session.active_character_id = bound_character_id

    async def _judge_outbound_menu_candidate(
        self,
        *,
        chat_provider_id: str,
        player_message: str,
        candidate: SemanticReviewCandidate,
    ) -> dict[str, Any]:
        prompt = _build_outbound_menu_judge_prompt(player_message, candidate)
        try:
            response = await self._llm_generate(
                chat_provider_id=chat_provider_id,
                prompt=prompt,
                system_prompt=OUTBOUND_MENU_JUDGE_SYSTEM_PROMPT,
            )
        except Exception as exc:
            return {
                "classification": "uncertain",
                "action": "keep",
                "confidence": 0.0,
                "reason": f"judge_failed:{exc.__class__.__name__}",
                "parse_ok": False,
            }
        raw_text = getattr(response, "completion_text", "") or str(response)
        payload = _first_json_object_payload(raw_text)
        if not isinstance(payload, dict):
            return {
                "classification": "uncertain",
                "action": "keep",
                "confidence": 0.0,
                "reason": "invalid_json",
                "parse_ok": False,
            }
        classification = str(payload.get("classification") or "uncertain").strip()
        confidence = _confidence_value(payload.get("confidence"))
        action = _semantic_judge_action(
            classification,
            str(payload.get("action") or "").strip(),
            confidence,
            player_wants_help=candidate.player_wants_help,
        )
        reason = f"classification:{classification or 'uncertain'}"
        if action == "keep":
            reason = f"{reason};not_actionable"
        return {
            "classification": classification,
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "parse_ok": True,
        }

    async def _run_llm_tool_loop(
        self,
        chat_provider_id: str,
        system_prompt: str,
        initial_prompt: str,
        toolset: Any,
        tool_executor: Any,
        session_id: str,
        audit_lock: asyncio.Lock | None = None,
        raw_player_message: str = "",
    ) -> ToolLoopResult:
        contexts: list[dict[str, str]] = []
        prompt = initial_prompt
        last_error_tool = ""
        repeated_error_count = 0
        all_tool_results: list[dict[str, Any]] = []

        for step in range(self.max_steps):
            response = await self._llm_generate(
                chat_provider_id=chat_provider_id,
                prompt=prompt,
                contexts=contexts,
                system_prompt=system_prompt,
                func_tool=toolset,
            )
            tool_calls = self._extract_tool_calls(response)
            completion_text = getattr(response, "completion_text", "") or str(response)
            if not tool_calls:
                tool_calls = self._extract_text_tool_calls(completion_text)
            if not tool_calls:
                completion_text = self._sanitize_completion_text(completion_text)
                get_plugin_logger().info(
                    "llm_text_response session=%s step=%s chars=%s",
                    session_id,
                    step + 1,
                    len(completion_text),
                )
                return ToolLoopResult(completion_text, all_tool_results)
            get_plugin_logger().info(
                "llm_tool_calls session=%s step=%s tools=%s",
                session_id,
                step + 1,
                ",".join(str(item.get("name", "")) for item in tool_calls),
            )

            contexts.append(
                {
                    "role": "user",
                    "content": prompt,
                }
            )
            contexts.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "tool_calls": [
                                {
                                    "name": item["name"],
                                    "args": item["args"],
                                }
                                for item in tool_calls
                            ]
                        },
                        ensure_ascii=False,
                    ),
                }
            )

            tool_results: list[dict[str, Any]] = []
            for item in tool_calls:
                tool_name = item["name"]
                args = item["args"]
                if not isinstance(args, dict):
                    args = {}
                args = self._repair_tool_args(tool_name, args, raw_player_message)
                result = await tool_executor.execute(tool_name, args)
                tool_results.append(
                    {
                        "tool": tool_name,
                        "args": args,
                        "result": result,
                    }
                )
                if isinstance(result, dict) and not result.get("ok", True):
                    get_plugin_logger().warning(
                        "tool_result_error session=%s step=%s tool=%s error=%s reason=%s",
                        session_id,
                        step + 1,
                        tool_name,
                        result.get("error") or result.get("error_code"),
                        str(result.get("reason") or result.get("message") or "")[:240],
                    )
                    if last_error_tool == tool_name:
                        repeated_error_count += 1
                    else:
                        last_error_tool = tool_name
                        repeated_error_count = 1
            all_tool_results.extend(tool_results)
            audit_record = {
                "type": "llm_tool_step",
                "step": step + 1,
                "tool_results": _audit_safe_tool_results(tool_results),
            }
            if audit_lock is None:
                self.repository.append_audit(session_id, audit_record)
            else:
                async with audit_lock:
                    self.repository.append_audit(session_id, audit_record)
            contexts.append(
                {
                    "role": "user",
                    "content": "本轮工具返回：\n"
                    + json.dumps(tool_results, ensure_ascii=False, indent=2),
                }
            )
            if repeated_error_count >= 2:
                prompt = "同一个工具连续失败。请不要继续重复调用它，基于失败原因向玩家说明无法完成、给出可选行动，或提出一个必要的澄清问题。"
            else:
                prompt = "请基于工具返回继续。若还需要客观验证，可以继续调用允许的工具；若事实已经足够，请输出给玩家的最终叙事。"

        final_response = await self._llm_generate(
            chat_provider_id=chat_provider_id,
            prompt="工具循环已达到最大步数。请基于已有工具结果输出阶段性叙事，必要时请玩家确认下一步。",
            contexts=contexts,
            system_prompt=system_prompt,
        )
        return ToolLoopResult(
            self._sanitize_completion_text(getattr(final_response, "completion_text", "") or str(final_response)),
            all_tool_results,
        )

    @staticmethod
    def _repair_tool_args(tool_name: str, args: dict[str, Any], raw_player_message: str) -> dict[str, Any]:
        repaired = dict(args)
        if tool_name == "execute_rule":
            allowed = {"rule_name", "args", "version", "reason"}
            nested_args = repaired.get("args")
            if not isinstance(nested_args, dict):
                nested_args = {}
            for alias in ("name", "rule", "rule_id", "ruleName"):
                if not str(repaired.get("rule_name") or "").strip() and str(repaired.get(alias) or "").strip():
                    repaired["rule_name"] = repaired.pop(alias)
            for alias in ("name", "rule", "rule_id", "ruleName"):
                if not str(repaired.get("rule_name") or "").strip() and str(nested_args.get(alias) or "").strip():
                    repaired["rule_name"] = nested_args.pop(alias)
            if not repaired.get("version") and nested_args.get("version"):
                repaired["version"] = nested_args.pop("version")
            if not repaired.get("reason") and nested_args.get("reason"):
                repaired["reason"] = nested_args.pop("reason")
            inner_args = nested_args.get("args")
            if isinstance(inner_args, dict) and (
                "rule_name" in nested_args or "version" in nested_args or "reason" in nested_args
            ):
                if not repaired.get("rule_name") and nested_args.get("rule_name"):
                    repaired["rule_name"] = nested_args.get("rule_name")
                if not repaired.get("version") and nested_args.get("version"):
                    repaired["version"] = nested_args.get("version")
                if not repaired.get("reason") and nested_args.get("reason"):
                    repaired["reason"] = nested_args.get("reason")
                nested_args = inner_args
            extras = {
                key: value
                for key, value in list(repaired.items())
                if key not in allowed and value not in (None, "", [], {})
            }
            for key in extras:
                repaired.pop(key, None)
            if extras:
                nested_args.update(extras)
            rule_name = str(repaired.get("rule_name") or "").strip()
            version = repaired.get("version")
            version_match = re.match(r"^(.+?)@v(\d+)$", rule_name, flags=re.IGNORECASE)
            if not version_match:
                version_match = re.match(r"^(.+?)_v(\d+)$", rule_name, flags=re.IGNORECASE)
            if version_match:
                repaired["rule_name"] = version_match.group(1)
                if version in (None, "", 0):
                    repaired["version"] = int(version_match.group(2))
            repaired["args"] = nested_args
            return repaired
        if tool_name == "query_core_rules":
            if not str(repaired.get("query") or "").strip():
                for alias in ("question", "rule", "action", "text", "message", "topic"):
                    if str(repaired.get(alias) or "").strip():
                        repaired["query"] = str(repaired.get(alias)).strip()
                        break
            if not str(repaired.get("query") or "").strip():
                repaired["query"] = raw_player_message
            allowed = {"query", "purpose", "categories", "limit", "max_chars"}
            return {key: value for key, value in repaired.items() if key in allowed}
        if tool_name == "update_character_tags":
            if args.get("tags"):
                repaired.setdefault("character_id", "")
                return repaired
            repaired.setdefault("raw_text", raw_player_message)
            repaired.setdefault("character_id", "")
            return repaired
        if tool_name in {"update_scene", "update_world_tags"}:
            patch = repaired.get("patch")
            if isinstance(patch, dict) and patch:
                return repaired
            top_level_patch = {
                key: value
                for key, value in repaired.items()
                if key != "patch" and value not in (None, "", [], {})
            }
            if top_level_patch:
                return {"patch": top_level_patch}
            if tool_name == "update_scene":
                inferred_scene_patch = _infer_scene_patch_from_text(raw_player_message)
                if inferred_scene_patch:
                    repaired["patch"] = inferred_scene_patch
                return repaired
            inferred_patch = _infer_world_tags_from_text(raw_player_message)
            if inferred_patch:
                repaired["patch"] = inferred_patch
            elif _looks_like_background_generation_request(raw_player_message):
                repaired["patch"] = _default_generated_background_patch(raw_player_message)
            return repaired
        if tool_name == "start_game":
            if "opening_intro" not in repaired and repaired.get("intro"):
                repaired["opening_intro"] = repaired.pop("intro")
            if "opening_intro" not in repaired and repaired.get("opening"):
                repaired["opening_intro"] = repaired.pop("opening")
            if "player_guidance" not in repaired and repaired.get("guidance"):
                repaired["player_guidance"] = repaired.pop("guidance")
            if "campaign_outline" in repaired:
                repaired["campaign_outline"] = _coerce_campaign_outline(repaired.get("campaign_outline"))
            if "campaign_outline" not in repaired:
                for alias in ("plot_skeleton", "plot_outline", "outline", "story_outline"):
                    if repaired.get(alias):
                        repaired["campaign_outline"] = _coerce_campaign_outline(repaired.pop(alias))
                        break
            if "scene_patch" in repaired and not isinstance(repaired.get("scene_patch"), dict):
                scene_value = repaired.get("scene_patch")
                repaired["scene_patch"] = {"summary": str(scene_value)} if str(scene_value or "").strip() else {}
            if "scene_patch" not in repaired:
                for alias in ("initial_scene", "scene", "current_scene"):
                    if repaired.get(alias):
                        scene_value = repaired.pop(alias)
                        repaired["scene_patch"] = scene_value if isinstance(scene_value, dict) else {"summary": str(scene_value)}
                        break
            allowed = {"opening_intro", "player_guidance", "campaign_outline", "scene_patch"}
            return {key: value for key, value in repaired.items() if key in allowed}
        if tool_name == "session_control":
            if not str(repaired.get("action") or "").strip():
                repaired["action"] = "status"
            allowed = {"action", "reason", "confirm_token"}
            return {key: value for key, value in repaired.items() if key in allowed}
        return repaired

    async def _llm_generate(self, **kwargs: Any) -> Any:
        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await self._llm_generate_once(**kwargs)
            except Exception as exc:
                if isinstance(exc, TypeError) or not _is_retryable_llm_error(exc):
                    raise
                last_exc = exc
                if attempt >= max_attempts:
                    break
                delay = 1.5 * attempt
                get_plugin_logger().warning(
                    "llm_generate_retry attempt=%s retry_left=%s delay=%.1fs error=%s",
                    attempt,
                    max_attempts - attempt,
                    delay,
                    str(exc)[:200],
                )
                await asyncio.sleep(delay)
        get_plugin_logger().error(
            "llm_generate_failed_after_retries attempts=%s error=%s",
            max_attempts,
            str(last_exc)[:240] if last_exc else "",
        )
        return LlmFailureResponse(
            "模型这轮连续调用失败，我不会编造结算结果；当前只保留已完成的本地状态校验。请稍后重试，或把动作压短再发一次。"
        )

    async def _llm_generate_once(self, **kwargs: Any) -> Any:
        try:
            return await self._llm_generate_raw(kwargs)
        except json.JSONDecodeError as exc:
            get_plugin_logger().warning(
                "llm_tool_arguments_json_error fallback_to_text error=%s",
                str(exc)[:200],
            )
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("func_tool", None)
            retry_kwargs.pop("tools", None)
            retry_kwargs["prompt"] = (
                str(retry_kwargs.get("prompt") or "")
                + "\n\n刚才工具参数 JSON 无法解析。请不要再调用工具，直接用简短自然语言说明本轮无法完成工具结算，并让玩家重发更短动作。"
            )
            try:
                return await self._llm_generate_raw(retry_kwargs)
            except Exception:
                raise exc
        except TypeError as exc:
            if "func_tool" not in kwargs:
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs["tools"] = retry_kwargs.pop("func_tool")
            try:
                return await self._llm_generate_raw(retry_kwargs)
            except json.JSONDecodeError as json_exc:
                get_plugin_logger().warning(
                    "llm_tool_arguments_json_error fallback_to_text error=%s",
                    str(json_exc)[:200],
                )
                text_retry_kwargs = dict(kwargs)
                text_retry_kwargs.pop("func_tool", None)
                text_retry_kwargs.pop("tools", None)
                text_retry_kwargs["prompt"] = (
                    str(text_retry_kwargs.get("prompt") or "")
                    + "\n\n刚才工具参数 JSON 无法解析。请不要再调用工具，直接用简短自然语言说明本轮无法完成工具结算，并让玩家重发更短动作。"
                )
                try:
                    return await self._llm_generate_raw(text_retry_kwargs)
                except Exception:
                    raise json_exc
            except TypeError:
                raise exc

    async def _llm_generate_raw(self, kwargs: dict[str, Any]) -> Any:
        response = await self.astr_context.llm_generate(**kwargs)
        _log_llm_usage_summary(response, kwargs)
        return response

    @staticmethod
    def _limit_completion(text: str, session: Any, raw_player_message: str = "") -> str:
        if not text:
            return text
        if _wants_full_status_output(raw_player_message) or _wants_expanded_detail_output(raw_player_message):
            limit = 2200
            if len(text) <= limit:
                return text
            return text[:limit].rstrip()
        if _wants_opening_output(raw_player_message):
            limit = 900
            if len(text) <= limit:
                return text
            return text[:limit].rstrip()
        limit = 700
        combat_or_turn_output = _wants_combat_or_turn_output(raw_player_message)
        try:
            turn = ((session.battle or {}).get("turn") or {})
            if turn.get("active") and turn.get("output_limit_chars"):
                limit = int(turn.get("output_limit_chars"))
            else:
                style = session.world_tags.get("response_style", {})
                if isinstance(style, dict) and style.get("hard_limit_chars"):
                    limit = int(style.get("hard_limit_chars"))
        except Exception:
            limit = 700
        if combat_or_turn_output:
            limit = max(limit, 360)
        limit = max(360, min(1800, limit))
        if len(text) <= limit:
            return text
        cutoff = limit - 1
        punctuation = "。！？；\n"
        best = max(text.rfind(mark, 0, cutoff) for mark in punctuation)
        if best >= int(limit * 0.55):
            cutoff = best + 1
        return text[:cutoff].rstrip() + "…"

    @staticmethod
    def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
        names = list(getattr(response, "tools_call_name", None) or [])
        args_list = list(getattr(response, "tools_call_args", None) or [])
        if names:
            return [
                {
                    "name": str(name),
                    "args": args_list[index] if index < len(args_list) else {},
                }
                for index, name in enumerate(names)
            ]

        raw_calls = getattr(response, "tool_calls", None)
        if raw_calls is None and isinstance(response, dict):
            raw_calls = response.get("tool_calls")
        calls: list[dict[str, Any]] = []
        for raw in raw_calls or []:
            if isinstance(raw, dict):
                function = raw.get("function") if isinstance(raw.get("function"), dict) else raw
                name = function.get("name") or raw.get("name")
                args = function.get("arguments") or raw.get("args") or raw.get("arguments") or {}
            else:
                function = getattr(raw, "function", raw)
                name = getattr(function, "name", None) or getattr(raw, "name", None)
                args = getattr(function, "arguments", None) or getattr(raw, "args", None) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name:
                calls.append({"name": str(name), "args": args if isinstance(args, dict) else {}})
        return calls

    @staticmethod
    def _extract_text_tool_calls(text: str) -> list[dict[str, Any]]:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()
        payloads: list[Any] = []
        try:
            payloads.append(json.loads(stripped))
        except json.JSONDecodeError:
            payloads.extend(payload for _, _, payload in _json_object_payloads(stripped))
        if not payloads:
            return []
        for payload in payloads:
            calls = _tool_calls_from_payload(payload)
            if calls:
                return calls
        return []

    @staticmethod
    def _sanitize_completion_text(text: str) -> str:
        cleaned = _strip_tool_call_payloads(str(text or ""))
        cleaned = cleaned.strip()
        if cleaned:
            return cleaned
        return "我已完成这轮工具处理，但没有生成适合展示的叙事文本；请继续描述下一步。"

    @staticmethod
    def session_id_for_event(event: Any) -> str:
        umo = getattr(event, "unified_msg_origin", "")
        if umo:
            return str(umo)
        message_obj = getattr(event, "message_obj", None)
        if message_obj and getattr(message_obj, "session_id", ""):
            return str(message_obj.session_id)
        return "default"

    @staticmethod
    def actor_context_for_event(event: Any) -> dict[str, str]:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None)

        player_id = ""
        for getter in ("get_sender_id", "get_user_id"):
            func = getattr(event, getter, None)
            if callable(func):
                try:
                    value = func()
                except Exception:
                    value = ""
                if value:
                    player_id = str(value)
                    break
        if not player_id and sender is not None:
            for attr in ("user_id", "sender_id", "id"):
                value = getattr(sender, attr, "")
                if value:
                    player_id = str(value)
                    break

        display_name = ""
        if sender is not None:
            for attr in ("nickname", "card", "name", "username"):
                value = getattr(sender, attr, "")
                if value:
                    display_name = str(value)
                    break
        if not display_name:
            display_name = player_id or "未知玩家"

        platform = ""
        func = getattr(event, "get_platform_id", None)
        if callable(func):
            try:
                platform = str(func() or "")
            except Exception:
                platform = ""

        from .models import utc_now_iso

        return {
            "player_id": player_id,
            "display_name": display_name,
            "platform": platform,
            "session_id": IntentRouter.session_id_for_event(event),
            "seen_at": utc_now_iso(),
        }


def _tool_calls_from_payload(payload: Any) -> list[dict[str, Any]]:
    raw_calls = payload.get("tool_calls") if isinstance(payload, dict) else None
    calls: list[dict[str, Any]] = []
    for raw in raw_calls or []:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        args = raw.get("args") or {}
        if name:
            calls.append({"name": str(name), "args": args if isinstance(args, dict) else {}})
    return calls


def _strip_tool_call_payloads(text: str) -> str:
    cleaned = str(text or "")
    for start, end, payload in reversed(_json_object_payloads(cleaned)):
        if _tool_calls_from_payload(payload):
            cleaned = f"{cleaned[:start]}{cleaned[end:]}"
    markers = ('{"tool_calls"', '{ "tool_calls"', '"tool_calls":')
    marker_positions = [cleaned.find(marker) for marker in markers if cleaned.find(marker) >= 0]
    if marker_positions:
        index = min(marker_positions)
        brace = cleaned.rfind("{", 0, index + 1)
        cleaned = cleaned[: brace if brace >= 0 else index]
    return _clean_tool_call_artifacts(cleaned)


def _clean_tool_call_artifacts(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped in {"```json", "```"}:
            continue
        if stripped.startswith('"tool_calls"') or stripped.startswith("'tool_calls'"):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _json_object_payloads(text: str) -> list[tuple[int, int, Any]]:
    payloads: list[tuple[int, int, Any]] = []
    source = str(text or "")
    start = -1
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(source):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = source[start : index + 1]
                if "tool_calls" in candidate:
                    try:
                        payload = json.loads(candidate)
                    except json.JSONDecodeError:
                        payload = None
                    if payload is not None:
                        payloads.append((start, index + 1, payload))
                start = -1
    return payloads


def _first_json_object_payload(text: str) -> Any | None:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return payload

    source = stripped
    start = -1
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(source):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    payload = json.loads(source[start : index + 1])
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    return payload
                start = -1
    return None


def _is_retryable_llm_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    retryable_markers = (
        "candidate.content.parts",
        "content.parts",
        "parts 为空",
        "parts为空",
        "empty response",
        "empty candidate",
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
        "connection",
        "server disconnected",
        "clientconnectorerror",
        "connectionreseterror",
        "connection reset",
        "cannot connect",
        "connection refused",
        "connect call failed",
        "ssl handshake",
        "temporarily unavailable",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
    )
    return any(marker in name or marker in text for marker in retryable_markers)


def _wants_full_status_output(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    full_terms = (
        "所有",
        "全部",
        "完整",
        "详细",
        "列表",
        "一览",
        "全员",
        "敌我",
        "all",
        "full",
        "list",
    )
    status_terms = (
        "状态",
        "行动顺序",
        "顺序",
        "队列",
        "轮次",
        "回合",
        "战况",
        "位置",
        "角色",
        "敌人",
        "我方",
        "status",
        "initiative",
        "turn order",
    )
    return any(term in text for term in full_terms) and any(term in text for term in status_terms)


def _wants_expanded_detail_output(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    explicit_phrases = (
        "人物属性",
        "角色属性",
        "具体数值",
        "属性面板",
        "角色面板",
        "完整角色卡",
        "详细角色卡",
        "完整状态",
        "详细状态",
        "完整属性",
        "详细属性",
        "full character sheet",
        "character sheet",
        "full stats",
    )
    if any(phrase in text for phrase in explicit_phrases):
        return True
    if "状态" in text and any(term in text for term in ("物品", "装备", "道具", "背包", "库存")):
        return True
    if any(phrase in text for phrase in ("状态与物品", "状态和物品", "状态及物品", "状态与装备", "状态和装备", "状态及装备")):
        return True
    request_terms = (
        "看",
        "看看",
        "查看",
        "显示",
        "展示",
        "列出",
        "给我",
        "告诉我",
        "能看到",
        "show",
        "list",
        "display",
        "details",
    )
    detail_terms = (
        "属性",
        "数值",
        "角色卡",
        "面板",
        "详情",
        "详细",
        "完整",
        "状态",
        "生命值",
        "护甲等级",
        "技能",
        "法术",
        "装备",
        "物品",
        "道具",
        "背包",
        "库存",
        "物品列表",
        "道具列表",
        "豁免",
        "熟练",
        "专长",
        "hp",
        "ac",
        "stats",
        "sheet",
        "status",
    )
    return any(term in text for term in request_terms) and any(term in text for term in detail_terms)


def _wants_opening_output(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(
        term in text
        for term in (
            "开始游戏",
            "正式开始",
            "开始吧",
            "开场",
            "开局",
            "进入剧情",
            "进入正片",
            "游戏开始",
            "拉开第一幕",
            "start game",
        )
    )


def _wants_combat_or_turn_output(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    terms = (
        "攻击",
        "伤害",
        "命中",
        "豁免",
        "检定",
        "判定",
        "骰",
        "施法",
        "治疗",
        "移动",
        "掩护",
        "闪避",
        "触发",
        "附赠动作",
        "重置",
        "尝试",
        "使用",
        "点燃",
        "侦查",
        "搜索",
        "调查",
        "寻找",
        "询问",
        "打听",
        "沟通",
        "安抚",
        "分享",
        "索要",
        "索取",
        "取走",
        "浇",
        "挥砍",
        "砍",
        "斩",
        "劈",
        "刺",
        "击",
        "跳起",
        "加入角色",
        "加入战斗",
        "动作如潮",
        "顺劈斩",
        "回合",
        "轮次",
        "场面结算",
        "敌人",
        "怪物",
        "战斗",
        "遭遇",
        "battle",
        "turn",
        "attack",
        "damage",
    )
    return any(term in text for term in terms)


def _infer_world_tags_from_text(message: str) -> dict[str, Any]:
    text = " ".join(str(message or "").strip().split())
    lowered = text.lower()
    if not text:
        return {}
    patch: dict[str, Any] = {}

    genre_terms = _matched_terms(
        lowered,
        (
            "末世",
            "废土",
            "科幻",
            "奇幻",
            "玄幻",
            "现代",
            "赛博",
            "克苏鲁",
            "悬疑",
            "武侠",
            "太空",
            "蒸汽",
            "欧洲中世纪",
            "中世纪",
            "历史",
            "低魔",
            "无魔",
            "纯剑",
            "海战",
            "异界",
            "异世界",
            "穿越",
            "重生",
            "核战",
            "修仙",
            "仙侠",
            "文明",
            "文明重建",
        ),
    )
    if genre_terms:
        patch["genre"] = "、".join(genre_terms)

    tone_terms = _matched_terms(
        lowered,
        ("严肃", "荒诞", "宏大", "悲剧", "失败", "危险", "恐怖", "轻松", "黑暗", "求生", "调查", "热血", "压抑", "幽默", "写实", "日常", "经营", "种田", "后宫", "宫斗", "温馨"),
    )
    if tone_terms:
        patch["tone"] = "、".join(tone_terms)

    era_terms = _matched_terms(lowered, ("中世纪", "近代", "现代", "古代", "维多利亚", "冷战", "未来"))
    if era_terms:
        patch["era"] = "、".join(era_terms)

    location_terms = _matched_terms(
        lowered,
        ("欧洲", "类似地球", "地球", "海上", "港口", "王国", "城市", "村庄", "荒野", "废墟", "船上", "游艇", "空间站", "中继站", "地下城", "酒馆", "咖啡馆", "店", "宫廷", "学院", "宗门", "领地"),
    )
    if location_terms:
        patch["location"] = "、".join(location_terms)

    ruleset_terms = _matched_terms(
        lowered,
        ("没有魔法", "没有魔", "不存在超自然", "无超自然", "无魔法", "无魔", "纯剑", "d20", "dnd", "coc"),
    )
    if ruleset_terms:
        patch["ruleset"] = "、".join(ruleset_terms)

    if any(token in lowered for token in ("势力", "组织", "公司", "教团", "军团", "帮派", "派系", "店员", "猫娘", "贵族", "朝廷")):
        patch["factions"] = _short_inferred_text(text, 160)
    if any(token in lowered for token in ("开始游戏", "开场", "开局", "第一幕", "故事", "剧本", "副本", "任务", "求救", "聚集", "来到", "醒来", "退休", "导入", "我是", "我们是", "扮演", "担任")):
        patch["starting_premise"] = _short_inferred_text(text, 240)

    if any(token in lowered for token in ("我是", "我们是", "扮演", "担任", "店长", "领主", "队长", "调查员", "学生", "佣兵", "冒险者")):
        patch.setdefault("player_role_premise", _short_inferred_text(text, 160))
    if any(token in lowered for token in ("补全", "补完", "智能补完", "不用多问", "直接开始", "开始游戏", "开场", "开局")) and len(patch) >= 1:
        patch.setdefault("tone", "由 DM 补全细节，保持可裁定、可推进、不过度追问")
        patch.setdefault("ruleset", "以 d20 检定为基础；概率、风险和对抗行动必须投骰。")

    if len(patch) >= 2 or ("genre" in patch and len(str(patch["genre"])) >= 8):
        patch.setdefault("campaign_background", _short_inferred_text(text, 280))
        return patch
    return {}


def _looks_like_background_generation_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    if any(token in text for token in ("角色卡", "人物卡", "建卡", "加入战场", "攻击", "移动")):
        return False
    subject_terms = ("背景", "世界观", "设定", "环境", "题材", "类型", "风格", "世界", "campaign", "setting", "premise")
    author_terms = (
        "生成",
        "创建",
        "建立",
        "补全",
        "补完",
        "完善",
        "扩写",
        "随机",
        "写",
        "定",
        "你来",
        "你定",
        "你决定",
        "帮我",
        "替我",
        "给我",
        "供选择",
    )
    delegation_terms = (
        "你来定",
        "你定吧",
        "你决定",
        "随便定",
        "随机一个",
        "随机几个",
        "直接定",
        "自动生成",
        "智能补完",
        "不用多问",
        "补完后开始",
        "补全后开始",
        "故事",
        "剧本",
        "副本",
    )
    return any(term in text for term in delegation_terms) or (
        any(term in text for term in subject_terms) and any(term in text for term in author_terms)
    )


def _infer_scene_patch_from_text(message: str) -> dict[str, Any]:
    if not _looks_like_stateful_player_message(message):
        return {}
    return {
        "last_player_intent": {
            "at": utc_now_iso(),
            "text": _compact_text(message, 180),
            "source": "local_arg_repair",
        }
    }


def _default_generated_background_patch(message: str) -> dict[str, Any]:
    source = _short_inferred_text(" ".join(str(message or "").strip().split()), 180)
    return {
        "genre": "低魔边境冒险",
        "tone": "克制、危险、重选择后果",
        "starting_premise": "一份来历不明的委托把玩家聚到边境港镇；第一幕从失踪货船与封锁码头开始。",
        "location": "边境港镇与近海航道",
        "factions": "港务行会、旧贵族私兵、海盗残党、沉默教团",
        "ruleset": "以 d20 检定为基础；概率、风险和对抗行动必须投骰。",
        "campaign_background": (
            "玩家授权 DM 自动生成背景。默认采用低魔边境冒险：边境港镇、失踪货船、封锁码头、"
            "互相牵制的港务行会与海盗残党。"
        ),
        "background_source": source or "dm_generated_fallback",
    }


def _coerce_campaign_outline(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"acts": [str(item) for item in value if str(item).strip()]}
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
        return {"acts": [str(item) for item in decoded if str(item).strip()]}
    parts = [
        part.strip(" \n\t-0123456789.、:：")
        for part in re.split(r"[\n；;]+", text)
        if part.strip(" \n\t-0123456789.、:：")
    ]
    if len(parts) >= 3:
        return {"acts": parts[:6], "outline": _short_inferred_text(text, 500)}
    return {"outline": _short_inferred_text(text, 500)}


def _ensure_legacy_live_scene_state(session: Any) -> bool:
    if not _looks_like_legacy_live_campaign(session):
        return False
    scene = session.scene or {}
    changed = False
    summary = str(scene.get("summary") or "")
    if not scene.get("_legacy_live_campaign"):
        scene["_legacy_live_campaign"] = True
        scene["_legacy_live_campaign_marked_at"] = utc_now_iso()
        changed = True
    if summary.startswith("尚未开局") or not summary.strip():
        scene["summary"] = "跑团已在进行；这是旧存档兼容标记，具体事实以最近事件、角色状态、战棋与裁定记录为准。"
        changed = True
    session.scene = scene
    return changed


def _maybe_close_concluded_turn(session: Any, message: str) -> dict[str, Any] | None:
    battle = session.battle or {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    if not turn.get("active") and not battle.get("active"):
        return None
    terminal_request = _looks_like_terminal_or_interlude_request(message)
    scene_concluded = _scene_looks_concluded(session)
    if not terminal_request and not scene_concluded:
        return None
    previous_phase = str(turn.get("phase") or "")
    previous_entity_id = str(turn.get("current_entity_id") or "")
    turn["active"] = False
    turn["phase"] = "ended"
    turn["current_entity_id"] = ""
    turn["current_index"] = -1
    turn["deadline_at"] = ""
    turn["waiting_since_at"] = ""
    battle["active"] = False
    battle["turn"] = turn
    battle["turn_entity_id"] = ""
    scene = session.scene or {}
    scene["_post_game"] = True
    scene["_encounter_ended_at"] = utc_now_iso()
    session.scene = scene
    session.battle = battle
    session.mode = GameMode.NARRATIVE
    return {
        "previous_phase": previous_phase,
        "previous_entity_id": previous_entity_id,
        "reason": "terminal_or_interlude_request" if terminal_request else "scene_already_concluded",
    }


def _looks_like_legacy_live_campaign(session: Any) -> bool:
    scene = session.scene or {}
    if scene.get("_game_started") or scene.get("_legacy_live_campaign"):
        return True
    if bool((session.battle or {}).get("active")):
        return True
    if not _has_background_ready(session):
        return False
    return bool(session.characters) and (bool(session.rules) or len(session.participants or {}) >= 2)


def _has_background_ready(session: Any) -> bool:
    world_tags = dict(session.world_tags or {})
    if world_tags.get("_background_ready") is True:
        return True
    matched = 0
    text_chars = 0
    background_keys = {
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
        "背景",
        "世界观",
        "时代",
        "地点",
        "势力",
        "主题",
        "开场前提",
    }
    for key, value in world_tags.items():
        key_text = str(key)
        if key_text.startswith("_"):
            continue
        if key_text.lower() in background_keys or key_text in background_keys:
            value_text = str(value).strip()
            if value_text and value_text not in {"{}", "[]", "None"}:
                matched += 1
                text_chars += len(value_text)
    return (matched >= 2 and text_chars >= 12) or (matched >= 1 and text_chars >= 40)


def _scene_looks_concluded(session: Any) -> bool:
    scene = session.scene or {}
    if scene.get("_post_game") or scene.get("_encounter_ended_at"):
        return True
    text = _flatten_for_guard(
        [
            scene.get("summary", ""),
            scene.get("current_conflict", ""),
            scene.get("last_resolution", {}),
            scene.get("immediate_hooks", ""),
        ]
    )
    concluded_terms = (
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
    return any(term in text for term in concluded_terms)


def _looks_like_terminal_or_interlude_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
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
    return any(term in text for term in terminal_terms) or any(term in text for term in interlude_terms)


def _looks_like_post_game_meta_message(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    meta_terms = (
        "谁最菜",
        "谁最强",
        "评价",
        "评估",
        "规则书",
        "职业等级",
        "传奇等级",
        "多少级",
        "个人结局",
        "后日谈",
        "尾声",
        "休息一会",
        "背景剧情描述",
        "下一段冒险",
        "下一次冒险",
        "下次冒险",
        "下个冒险",
        "下回冒险",
        "沉睡直到",
        "休眠直到",
        "直到下次",
    )
    return any(term in text for term in meta_terms)


def _flatten_for_guard(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _should_record_narrative_trace(player_message: str, completion: str) -> bool:
    if _looks_like_post_game_meta_message(player_message):
        return False
    if not _looks_like_stateful_player_message(player_message):
        return False
    result_text = str(completion or "").strip()
    if not result_text:
        return False
    if any(token in result_text for token in ("模型这轮连续调用失败", "工具循环已达到最大步数")):
        return False
    return True


def _looks_like_stateful_player_message(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    non_stateful_terms = (
        "status",
        "token",
        "tokens",
        "当前轮次",
        "当前回合",
        "行动顺序",
        "轮动次序",
        "列表",
        "一览",
        "日志",
        "debug",
        "规则列表",
        "画图",
        "地图",
        "生成地图",
    )
    if any(term in text for term in non_stateful_terms):
        return False
    if any(term in text for term in ("为什么", "怎么回事", "哪里不对")) and not any(
        term in text for term in ("判定", "检定", "观察", "搜索", "调查")
    ):
        return False
    action_terms = (
        "我",
        "去",
        "走",
        "跑",
        "冲",
        "移动",
        "靠近",
        "观察",
        "查看",
        "侦查",
        "搜索",
        "调查",
        "攻击",
        "射",
        "盲射",
        "砍",
        "刺",
        "打",
        "施法",
        "治疗",
        "防御",
        "掩护",
        "示警",
        "警告",
        "提醒",
        "叫醒",
        "说",
        "喊",
        "拿",
        "捡",
        "使用",
        "点燃",
        "潜行",
        "隐藏",
        "发呆",
        "打盹",
        "清醒",
        "等待",
        "准备",
        "检定",
        "判定",
        "发现",
        "注意",
    )
    return any(term in text for term in action_terms)


def _looks_like_turn_consuming_player_action(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    info_terms = (
        "status",
        "token",
        "当前轮次",
        "当前回合",
        "行动顺序",
        "战斗顺序",
        "轮动顺序",
        "轮到谁",
        "谁行动",
        "汇报当前",
        "规则列表",
        "地图",
        "日志",
        "debug",
        "为什么",
        "怎么回事",
    )
    if any(term in text for term in info_terms):
        return False
    action_terms = (
        "我",
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
        "守望",
        "潜伏",
        "潜行",
        "检定",
        "判定",
        "发现",
        "注意",
        "示警",
        "提醒",
        "叫醒",
        "拿",
        "捡",
        "使用",
        "点燃",
        "装填",
        "待射",
    )
    return any(term in text for term in action_terms)


def _completion_indicates_resolved_turn_action(completion: str) -> bool:
    text = str(completion or "").strip()
    if not text:
        return False
    blocked_terms = (
        "请补",
        "补一句",
        "需要你",
        "需要指定",
        "请选择",
        "还没轮到",
        "轮不到",
        "不能替",
        "无法完成",
        "没跑完",
        "连续调用失败",
        "工具循环已达到",
        "需要做一次",
        "需要一次",
        "需要投骰",
        "需要检定",
        "需做一次",
        "需投骰",
        "需检定",
        "我先按",
    )
    if any(term in text for term in blocked_terms):
        return False
    resolved_terms = (
        "直接成立",
        "成功",
        "失败",
        "没发现",
        "未发现",
        "发现",
        "已经",
        "保持",
        "进入",
        "造成",
        "命中",
        "未命中",
        "躲开",
        "挡住",
        "稳住",
        "完成",
        "结果",
    )
    return any(term in text for term in resolved_terms)


def _action_reasonableness_guard_reply(session: Any, actor: dict[str, str], message: str) -> str:
    if not _campaign_started_for_guard(session):
        return ""
    text = str(message or "").strip().lower()
    if not text:
        return ""
    clear_overreach = (
        _claims_absurd_dc(text)
        or _claims_extra_major_actions(text)
        or _claims_mass_control(text)
        or _claims_unbounded_magic(text)
        or _claims_absurd_spell_slots(text)
        or _claims_forced_npc_cooperation(text)
        or _claims_perpetual_energy_auto_success(text)
        or _claims_post_start_world_or_power_rewrite(text)
    )
    if not clear_overreach:
        return ""
    return (
        "这个主张超出当前角色卡、资源或场景事实，不能直接成立或写入存档。"
        "开场后不能单方面追加职业/等级/永久能力、全世界神迹、愿力资源或既成胜利。"
        "可以把它改成一个有限目标来尝试；是否成功、范围、代价和后果由规则检定或 DM 裁定。"
    )


def _campaign_started_for_guard(session: Any) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    battle = session.battle or {}
    turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
    return bool(
        scene.get("_game_started")
        or scene.get("_legacy_live_campaign")
        or world_tags.get("_plot_locked") is True
        or battle.get("active")
        or turn.get("active")
    )


def _claims_absurd_dc(text: str) -> bool:
    return bool(re.search(r"\bdc\s*(?:\+\s*)?(?:[3-9]\d|\d{3,})\b", text, flags=re.IGNORECASE))


def _claims_extra_major_actions(text: str) -> bool:
    if not any(term in text for term in ("主要动作", "额外动作", "额外主要动作", "本轮攻击")):
        return False
    return any(term in text for term in ("增加两个", "增加2", "获得两个", "获得2", "新的主要动作", "重置本轮攻击", "刷新本轮攻击"))


def _claims_mass_control(text: str) -> bool:
    if any(term in text for term in ("所有除了我以外", "半径200", "半径 200", "所有人", "所有智慧生物")):
        return any(term in text for term in ("不会撒谎", "不能撒谎", "必须", "都不会", "都要", "逼迫", "交出", "控制"))
    return False


def _claims_unbounded_magic(text: str) -> bool:
    return any(
        term in text
        for term in (
            "任意法术",
            "任意魔法",
            "所有法术",
            "所有魔法",
            "随意使用超魔",
            "随意施法",
            "魔法能量转化为任意法术",
            "力量是无限",
            "力量无限",
            "能力是无限",
            "能力无限",
            "法术能力是无限",
            "法术能力无限",
            "不受任何负面影响",
            "不受任何debuff",
            "不受任何 debuff",
            "免疫debuff",
            "免疫所有debuff",
            "消除我的所有debuff",
            "消除所有debuff",
        )
    )


def _claims_absurd_spell_slots(text: str) -> bool:
    if "每环12个法术位" in text or "每环十二个法术位" in text:
        return True
    if re.search(r"(?:1[1-9]|[2-9]\d|\d{3,})\s*个?\s*9\s*环法术位", text):
        return True
    if re.search(r"9\s*环法术位\s*(?:有|拥有)?\s*(?:1[1-9]|[2-9]\d|\d{3,})\s*个?", text):
        return True
    return False


def _claims_forced_npc_cooperation(text: str) -> bool:
    return any(term in text for term in ("一定会协助", "一定会帮助", "肯定会协助", "肯定会帮助", "必须协助", "必须帮助"))


def _claims_perpetual_energy_auto_success(text: str) -> bool:
    return any(term in text for term in ("永动", "无限能量", "无限资源")) and any(
        term in text for term in ("所以", "因此", "核心碎", "自动", "必定", "持续带电")
    )


def _claims_post_start_world_or_power_rewrite(text: str) -> bool:
    if any(term in text for term in ("全世界", "全球", "所有时空", "全位面")) and any(
        term in text for term in ("愿力", "感激", "神迹", "投影", "流星雨", "陨石")
    ):
        return True
    if any(
        term in text
        for term in (
            "帝皇",
            "神皇",
            "原体",
            "禁军",
            "星际战士",
            "阿斯塔特",
            "战锤",
            "创世神",
            "造物主",
        )
    ) and any(
        term in text
        for term in (
            "我加入",
            "我要加入",
            "角色是",
            "我是",
            "路过这里",
            "刚好路过",
            "带着",
            "降临",
            "指挥",
            "收走",
            "砍卫兵",
        )
    ):
        return True
    if any(
        term in text
        for term in (
            "十三个原体",
            "十三名原体",
            "13个原体",
            "13名原体",
            "带着原体",
            "带着军队",
            "带着军团",
        )
    ):
        return True
    if any(term in text for term in ("世界意志", "世界观", "现实", "法则", "底层逻辑", "位面基石", "dnd2024")) and any(
        term in text for term in ("修正", "清除", "清理", "抹除", "排除", "踢出", "移除", "重塑", "改写", "纠正")
    ) and any(term in text for term in ("不符合", "不合理", "异界", "跨作品", "所有", "一切", "事物", "存在")):
        return True
    if any(term in text for term in ("补充设定", "现在演绎", "现在刚刚到场", "刚刚到场", "派出")) and any(
        term in text
        for term in (
            "传奇战士",
            "传奇牧师",
            "传奇法师",
            "神眷者",
            "大量补给",
            "税务官",
            "收人头税",
            "呼吸税",
            "睡眠税",
        )
    ):
        return True
    if any(term in text for term in ("无数古树", "所有出路", "每一个人", "所有人", "整个小镇")) and any(
        term in text for term in ("堵死", "缠绕", "控制", "必须", "归还", "占据")
    ):
        return True
    if any(term in text for term in ("不存在失败", "不存在失败的可能", "一定可以", "必定可以", "自动成功", "不会失败")) and any(
        term in text for term in ("召唤", "虫群", "泰伦", "撕裂空间", "传送门", "法术", "检定", "判定")
    ):
        return True
    if any(term in text for term in ("一打刀虫", "虫巢暴君", "泰伦虫族")) and any(
        term in text for term in ("撕破虚空", "来到我的身边", "召唤", "现在刚刚到场")
    ):
        return True
    if any(term in text for term in ("记得记录", "记录一下", "写入", "加入角色卡")) and any(
        term in text for term in ("职业等级", "传奇等级", "兼职", "传奇赐福", "魔网权限", "愿力")
    ):
        return True
    if any(term in text for term in ("现在我是", "我现在是", "成为", "我是")) and any(
        term in text for term in ("虚空德鲁伊", "提夫林", "魔网化身", "神格", "神明", "半神")
    ):
        return True
    if any(term in text for term in ("直接获得", "永久获得", "从此拥有", "已经拥有")) and any(
        term in text for term in ("召唤陨石", "虚空陨石", "任意法术", "所有法术", "传奇能力")
    ):
        return True
    return False


def _action_economy_guard_reply(session: Any, actor: dict[str, str], message: str) -> str:
    battle = session.battle or {}
    turn = dict(battle.get("turn") or {})
    if not turn.get("active") or str(turn.get("phase", "")) != "character_turn":
        return ""
    actor_id = str(actor.get("player_id") or "").strip()
    if not actor_id:
        return ""
    text = str(message or "").strip().lower()
    if not text:
        return ""

    attack_terms = ("攻击", "射击", "砍", "斩", "劈", "命中", "击杀", "杀死", "连斩")
    result_claim_terms = (
        "判定成功",
        "检定成功",
        "已经成功",
        "直接成功",
        "必定成功",
        "自动成功",
        "全都命中",
        "不需要检定",
        "无需检定",
        "不用检定",
        "已经杀死",
        "直接击杀",
        "直接杀死",
    )
    extra_action_terms = (
        "再次攻击",
        "连续攻击",
        "多次攻击",
        "第二次攻击",
        "第三次攻击",
        "第四次",
        "第4次",
        "连击",
        "连斩",
        "动作如潮",
        "额外动作",
        "额外攻击",
    )
    cooldown_terms = ("重置", "刷新", "冷却", "cd")
    attempt_terms = (
        "尝试",
        "试图",
        "想要",
        "打算",
        "申请",
        "能否",
        "可否",
        "可以吗",
        "能不能",
        "如果可以",
        "若可以",
    )

    has_attack = any(term in text for term in attack_terms)
    claims_result = any(term in text for term in result_claim_terms)
    extra_action = any(term in text for term in extra_action_terms)
    asks_for_ruling = any(term in text for term in attempt_terms)
    claims_extra_action = has_attack and extra_action and not asks_for_ruling
    claims_cooldown_reset = has_attack and any(term in text for term in cooldown_terms)
    if not (claims_result or claims_extra_action or claims_cooldown_reset):
        return ""

    return (
        "这句把结果或额外行动直接写死了，不能直接成立。"
        "你可以声明一次主要动作，例如“我用双斧攻击最近的敌人”；"
        "是否触发动作如潮、额外攻击、冷却重置或击杀，需要已有能力/资源和检定结果来裁定。"
    )


OBJECTIVE_ADJUDICATION_TOOLS = {
    "execute_rule",
    "move_entity",
    "check_attack_vector",
    "turn_control",
    "update_scene",
    "update_character_tags",
    "create_grid",
    "place_entity",
    "start_game",
}

ROLL_REQUIRED_ACTION_TERMS = (
    "攻击",
    "射击",
    "命中",
    "伤害",
    "治疗",
    "潜行",
    "躲藏",
    "偷",
    "开锁",
    "破解",
    "说服",
    "威胁",
    "欺骗",
    "搜索",
    "调查",
    "察觉",
    "发现",
    "逃脱",
    "闪避",
    "豁免",
    "检定",
    "骰",
    "杀",
    "击倒",
    "控制",
    "强迫",
    "施法",
    "火球",
    "陷阱",
    "解除",
    "冲锋",
    "抓住",
    "捕获",
    "收服",
    "驯服",
    "转化",
    "采集",
    "分解",
    "侵入",
    "寄生",
    "点燃",
    "引火",
)

SPATIAL_REQUIRED_ACTION_TERMS = (
    "移动",
    "走到",
    "冲到",
    "靠近",
    "绕到",
    "撤离",
    "射程",
    "视线",
    "掩体",
    "距离",
    "坐标",
)

RESOLVED_OUTCOME_TERMS = (
    "成功",
    "命中",
    "击中",
    "击倒",
    "击杀",
    "杀死",
    "造成",
    "治疗",
    "恢复",
    "发现",
    "说服",
    "打开",
    "破解",
    "解除",
    "躲过",
    "避开",
    "逃脱",
    "获得",
    "完成",
    "倒下",
    "受伤",
    "失去",
    "变成",
)

UNRESOLVED_OUTCOME_MARKERS = (
    "不能直接成功",
    "不能成功",
    "尚未成功",
    "还没有成功",
    "没有成功",
    "需要检定",
    "需要投骰",
    "需要掷骰",
    "下一步检定",
    "先做检定",
    "未完成",
    "不把成功写死",
    "不能把成功写死",
    "可以尝试",
    "可尝试",
)

LOW_RISK_DIRECT_TERMS = (
    "查看状态",
    "当前状态",
    "status",
    "token",
    "debug",
    "日志",
    "规则",
    "规则列表",
    "规则详情",
    "备份",
    "恢复",
    "重开",
    "暂停",
    "resume",
)


def _adjudication_completeness_guard(
    session: Any,
    *,
    actor: dict[str, str],
    player_message: str,
    completion: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    if not _campaign_started_for_guard(session):
        return {}
    text = str(player_message or "").strip().lower()
    reply = str(completion or "").strip().lower()
    if not text or not reply:
        return {}
    if _contains_any_term(text, LOW_RISK_DIRECT_TERMS):
        return {}
    tool_names = [str(item.get("tool") or "") for item in tool_results if isinstance(item, dict)]
    successful_tools = {
        str(item.get("tool") or "")
        for item in tool_results
        if isinstance(item, dict) and _tool_result_ok(item.get("result"))
    }
    if _looks_like_rule_setup_request(text) and "register_rule" in successful_tools:
        return {}
    if not _contains_any_term(text, ROLL_REQUIRED_ACTION_TERMS + SPATIAL_REQUIRED_ACTION_TERMS):
        return {}
    if not _completion_claims_resolved_outcome(reply):
        return {}

    has_roll_support = "execute_rule" in successful_tools
    has_spatial_support = bool(successful_tools.intersection({"move_entity", "check_attack_vector", "create_grid", "place_entity"}))
    has_turn_support = "turn_control" in successful_tools
    has_state_support = bool(successful_tools.intersection({"update_scene", "update_character_tags", "start_game"}))
    needs_roll = _contains_any_term(text, ROLL_REQUIRED_ACTION_TERMS)
    needs_spatial = _contains_any_term(text, SPATIAL_REQUIRED_ACTION_TERMS) or _contains_any_term(
        text,
        ("攻击", "射击", "近战", "远程", "冲锋"),
    )

    if needs_roll and not has_roll_support:
        reason = "missing_execute_rule_for_risky_outcome"
    elif needs_spatial and not (has_spatial_support or has_turn_support):
        reason = "missing_spatial_or_turn_tool_for_positioned_outcome"
    elif not successful_tools.intersection(OBJECTIVE_ADJUDICATION_TOOLS):
        reason = "missing_objective_tool_for_resolved_outcome"
    elif not has_state_support and _completion_claims_state_change(reply):
        reason = "state_change_not_written"
    else:
        return {}

    return {
        "reason": reason,
        "tool_names": tool_names,
        "actor_player_id": str(actor.get("player_id") or ""),
        "reply_suffix": (
            "裁定补充：这步涉及风险、对抗或客观状态变化，但本轮还没有足够的骰子/战棋/状态工具结果支撑成功。"
            "我先不把成功写死；请确认目标和方式，下一步按规则检定或工具结算。"
        ),
    }


def _completion_claims_resolved_outcome(reply: str) -> bool:
    if _contains_any_term(reply, UNRESOLVED_OUTCOME_MARKERS):
        return False
    return _contains_any_term(reply, RESOLVED_OUTCOME_TERMS)


def _looks_like_rule_setup_request(text: str) -> bool:
    config_terms = (
        "玩法",
        "规则",
        "数值",
        "最大值",
        "上限",
        "命中不需要",
        "不需要骰",
        "简单回合制",
        "回合制",
        "自动战斗",
    )
    action_terms = (
        "我要",
        "找个",
        "攻击",
        "射击",
        "移动",
        "飞",
        "跑",
        "搜索",
        "调查",
        "收服",
        "捕获",
        "治疗",
        "偷",
        "开锁",
        "点燃",
        "引火",
        "转化",
        "采集",
        "寄生",
        "杀",
    )
    hits = sum(1 for term in config_terms if term and term in text)
    return hits >= 2 and not _contains_any_term(text, action_terms)


def _completion_claims_state_change(reply: str) -> bool:
    return _contains_any_term(
        reply,
        (
            "状态",
            "受伤",
            "倒地",
            "中毒",
            "束缚",
            "目盲",
            "生命值",
            "hp",
            "资源",
            "消耗",
            "位置",
            "进入",
            "离开",
        ),
    )


def _tool_result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("ok", True))
    return result is not None


def _audit_safe_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_results: list[dict[str, Any]] = []
    for item in tool_results:
        safe_item = dict(item)
        result = safe_item.get("result")
        if item.get("tool") == "search_external_memory" and isinstance(result, dict):
            safe_item["result"] = audit_safe_external_memory_result(result)
        safe_results.append(safe_item)
    return safe_results


def _contains_any_term(text: str, terms: tuple[str, ...]) -> bool:
    return any(term and term.lower() in text for term in terms)


def _rough_token_count(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, chars // 2)


def _rough_token_estimate(chars: int) -> dict[str, int]:
    if chars <= 0:
        return {"low": 0, "heuristic": 0, "high": 0}
    return {
        "low": max(1, chars // 4),
        "heuristic": _rough_token_count(chars),
        "high": max(1, int(chars / 1.5)),
    }


def _component_token_estimates(component_chars: dict[str, object]) -> dict[str, object]:
    estimates: dict[str, object] = {}
    for key, value in component_chars.items():
        if key.endswith("_chars") and isinstance(value, int):
            estimates[key[: -len("_chars")] + "_tokens"] = _rough_token_estimate(value)["heuristic"]
        else:
            estimates[key] = value
    return estimates


DIAGNOSTIC_REQUEST_TERMS = (
    "token",
    "tokens",
    "上下文",
    "压缩",
    "调试",
    "debug",
    "日志",
    "消耗",
    "预算",
    "audit",
)


def _is_diagnostic_request(message: str) -> bool:
    return _contains_any_term(str(message or "").strip().lower(), DIAGNOSTIC_REQUEST_TERMS)


def _turn_entity_label(session: Any, entity_id: str) -> str:
    grid = (session.battle or {}).get("grid") or {}
    entity = dict((grid.get("entities") or {}).get(entity_id, {}))
    if entity.get("name"):
        return str(entity["name"])
    character = session.characters.get(entity_id)
    if character:
        return character.name or character.id
    return entity_id


def _turn_owner_player_id(session: Any, entity_id: str) -> str:
    grid = (session.battle or {}).get("grid") or {}
    entity = dict((grid.get("entities") or {}).get(entity_id, {}))
    tags = dict(entity.get("tags", {}))
    if tags.get("player_id"):
        return str(tags["player_id"])
    character_id = str(tags.get("character_id", "") or entity_id)
    character = session.characters.get(character_id)
    if character and character.player_id:
        return str(character.player_id)
    for player_id, bound_id in (session.player_character_map or {}).items():
        if bound_id == character_id or bound_id == entity_id:
            return str(player_id)
    return ""


def _turn_pending_entity_for_actor(session: Any, turn: dict, actor_id: str) -> str:
    actor_id = str(actor_id or "").strip()
    if not actor_id:
        return ""
    order = _clean_turn_order(list(turn.get("turn_order") or []))
    actions = dict(turn.get("actions_this_round") or {})
    current_id = str(turn.get("current_entity_id") or (session.battle or {}).get("turn_entity_id", "") or "").strip()
    if current_id and current_id not in actions and _turn_owner_player_id(session, current_id) == actor_id:
        return current_id
    for entity_id in order:
        if entity_id in actions:
            continue
        if _turn_owner_player_id(session, entity_id) == actor_id:
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


def _compact_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _ambient_story_moment(player_message: str, completion: str, trace_record: dict[str, Any] | None = None) -> str:
    trace_record = trace_record or {}
    pieces = [
        str(trace_record.get("message", "") or player_message or ""),
        str(trace_record.get("outcome", "") or completion or ""),
    ]
    return _compact_text(" => ".join(piece for piece in pieces if piece.strip()), 720)


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    matched: list[str] = []
    for term in terms:
        if term and term.lower() in text and term not in matched:
            matched.append(term)
    return matched


def _short_inferred_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"
