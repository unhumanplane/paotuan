from __future__ import annotations

import asyncio
import json
from typing import Any

from .memory import MemoryCompressor
from .modes import GameModeStateMachine
from .models import GameMode
from .plugin_log import get_plugin_logger
from .prompts import build_system_prompt, build_user_prompt
from ..storage.json_repository import JsonGameRepository
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


class IntentRouter:
    def __init__(
        self,
        astr_context: Any,
        repository: JsonGameRepository,
        tool_registry: ToolRegistry,
        max_steps: int = 8,
    ):
        self.astr_context = astr_context
        self.repository = repository
        self.tool_registry = tool_registry
        self.mode_machine = GameModeStateMachine()
        self.memory_compressor = MemoryCompressor()
        self.max_steps = max_steps
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

            toolset, tool_names, tool_executor, tool_specs = self.tool_registry.for_mode(
                mode,
                session_id,
                actor=actor,
                message=message,
                provider_id=provider_id,
            )
            system_prompt = build_system_prompt(session, mode, tool_names, tool_specs, actor=actor)
            tool_schema_text = json.dumps(tool_specs, ensure_ascii=False, separators=(",", ":"))
            get_plugin_logger().info(
                "router_prepared session=%s mode=%s actor=%s tools=%s snapshot_chars=%s system_prompt_chars=%s tool_schema_chars=%s rough_total_tokens=%s",
                session_id,
                mode.value,
                actor.get("player_id", ""),
                ",".join(tool_names),
                self.memory_compressor.snapshot_chars(session),
                len(system_prompt),
                len(tool_schema_text),
                max(1, (len(system_prompt) + len(tool_schema_text)) // 2),
            )

        completion = await self._run_llm_tool_loop(
            chat_provider_id=provider_id,
            system_prompt=system_prompt,
            initial_prompt=build_user_prompt(message, security_notes=security_notes),
            toolset=toolset,
            tool_executor=LockedToolExecutor(tool_executor, lock),
            session_id=session_id,
            audit_lock=lock,
            raw_player_message=message,
        )

        async with lock:
            latest_session = self.repository.load_session(session_id)
            completion = self._limit_completion(completion, latest_session, raw_player_message=message)
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
            get_plugin_logger().info(
                "message_handled session=%s mode=%s actor=%s completion_chars=%s",
                session_id,
                mode.value,
                actor.get("player_id", ""),
                len(completion),
            )
        return completion

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
    ) -> str:
        contexts: list[dict[str, str]] = []
        prompt = initial_prompt
        last_error_tool = ""
        repeated_error_count = 0

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
                get_plugin_logger().info(
                    "llm_text_response session=%s step=%s chars=%s",
                    session_id,
                    step + 1,
                    len(completion_text),
                )
                return completion_text
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
            audit_record = {
                "type": "llm_tool_step",
                "step": step + 1,
                "tool_results": tool_results,
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
        return getattr(final_response, "completion_text", "") or str(final_response)

    @staticmethod
    def _repair_tool_args(tool_name: str, args: dict[str, Any], raw_player_message: str) -> dict[str, Any]:
        if tool_name != "update_character_tags":
            return args
        if args.get("tags"):
            return args
        repaired = dict(args)
        repaired.setdefault("raw_text", raw_player_message)
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
            return await self.astr_context.llm_generate(**kwargs)
        except TypeError as exc:
            if "func_tool" not in kwargs:
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs["tools"] = retry_kwargs.pop("func_tool")
            try:
                return await self.astr_context.llm_generate(**retry_kwargs)
            except TypeError:
                raise exc

    @staticmethod
    def _limit_completion(text: str, session: Any, raw_player_message: str = "") -> str:
        if not text:
            return text
        if _wants_full_status_output(raw_player_message):
            limit = 2200
            if len(text) <= limit:
                return text
            return text[:limit].rstrip()
        limit = 220
        try:
            turn = ((session.battle or {}).get("turn") or {})
            if turn.get("output_limit_chars"):
                limit = int(turn.get("output_limit_chars"))
            else:
                style = session.world_tags.get("response_style", {})
                if isinstance(style, dict) and style.get("hard_limit_chars"):
                    limit = int(style.get("hard_limit_chars"))
        except Exception:
            limit = 220
        limit = max(80, min(500, limit))
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
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return []
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


def _is_retryable_llm_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "readtimeout",
        "connecttimeout",
        "connection",
        "server disconnected",
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
