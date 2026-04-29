from __future__ import annotations

import asyncio
import json
from typing import Any

from .memory import MemoryCompressor
from .modes import GameModeStateMachine
from .models import GameMode, utc_now_iso
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
            legacy_live_scene_changed = _ensure_legacy_live_scene_state(session)
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
            trace_record = self._persist_narrative_trace(
                latest_session,
                actor=actor,
                player_message=message,
                completion=completion,
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
            output_limit_chars=int(turn.get("output_limit_chars") or 180),
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
        repaired = dict(args)
        if tool_name == "execute_rule":
            allowed = {"rule_name", "args", "version", "reason"}
            nested_args = repaired.get("args")
            if not isinstance(nested_args, dict):
                nested_args = {}
            extras = {
                key: value
                for key, value in list(repaired.items())
                if key not in allowed and value not in (None, "", [], {})
            }
            for key in extras:
                repaired.pop(key, None)
            if extras:
                nested_args.update(extras)
            repaired["args"] = nested_args
            return repaired
        if tool_name == "update_character_tags":
            if args.get("tags"):
                return args
            repaired.setdefault("raw_text", raw_player_message)
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
            if "campaign_outline" not in repaired:
                for alias in ("plot_skeleton", "plot_outline", "outline", "story_outline"):
                    if repaired.get(alias):
                        repaired["campaign_outline"] = _coerce_campaign_outline(repaired.pop(alias))
                        break
            if "scene_patch" not in repaired:
                for alias in ("initial_scene", "scene", "current_scene"):
                    if repaired.get(alias):
                        scene_value = repaired.pop(alias)
                        repaired["scene_patch"] = scene_value if isinstance(scene_value, dict) else {"summary": str(scene_value)}
                        break
            allowed = {"opening_intro", "player_guidance", "campaign_outline", "scene_patch"}
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
        if _wants_opening_output(raw_player_message):
            limit = 900
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


def _should_record_narrative_trace(player_message: str, completion: str) -> bool:
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
