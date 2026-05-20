from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .forensic_diff import compute_session_diff


class ForensicCollector:
    """Collects per-turn events and builds a Turn Envelope for post-hoc analysis."""

    def __init__(
        self,
        *,
        include_prompts: bool = True,
        include_raw_response: bool = True,
    ) -> None:
        self._include_prompts = include_prompts
        self._include_raw_response = include_raw_response

        self._turn_id: str = ""
        self._session_id: str = ""
        self._cycle_id: int = 0
        self._turn_sequence: int = 0
        self._start_time: str = ""
        self._actor: dict[str, str] = {}
        self._player_message: str = ""
        self._security_notes: list[str] = []

        self._state_before: dict[str, Any] | None = None
        self._state_after: dict[str, Any] | None = None

        self._routing: dict[str, Any] = {}
        self._prompts: dict[str, Any] | None = None
        self._llm_interactions: list[dict[str, Any]] = []
        self._guards_fired: list[dict[str, Any]] = []
        self._post_processing: dict[str, Any] = {}
        self._final_output: dict[str, Any] = {}

        self._fast_path: dict[str, Any] | None = None

    def start_turn(
        self,
        session_id: str,
        cycle_id: int,
        turn_sequence: int,
        actor: dict[str, str],
        player_message: str,
        security_notes: list[str] | None = None,
    ) -> None:
        self._turn_id = str(uuid.uuid4())
        self._session_id = session_id
        self._cycle_id = cycle_id
        self._turn_sequence = turn_sequence
        self._start_time = _utc_now_iso()
        self._actor = dict(actor)
        self._player_message = str(player_message or "")
        self._security_notes = list(security_notes or [])

    def record_state_before(self, snapshot: dict[str, Any]) -> None:
        self._state_before = dict(snapshot)

    def record_state_after(self, snapshot: dict[str, Any]) -> None:
        self._state_after = dict(snapshot)

    def record_routing(
        self,
        mode: str,
        provider_id: str,
        cycle_state: str,
        fast_path_triggered: bool = False,
        fast_path_action: str | None = None,
    ) -> None:
        self._routing = {
            "mode": mode,
            "provider_id": provider_id,
            "cycle_state": cycle_state,
            "security_notes": self._security_notes,
            "fast_path_triggered": fast_path_triggered,
            "fast_path_action": fast_path_action or None,
        }

    def record_prompts(
        self,
        system_prompt: str,
        user_prompt: str,
        tool_names: list[str],
        tool_specs: list[dict[str, Any]],
        projection_stats: dict[str, Any],
        component_chars: dict[str, Any],
    ) -> None:
        if not self._include_prompts:
            self._prompts = {
                "included": False,
                "system_prompt_chars": len(system_prompt),
                "user_prompt_chars": len(user_prompt),
                "tool_names": list(tool_names),
                "tool_specs_count": len(tool_specs),
                "projection_stats": projection_stats,
                "component_chars": component_chars,
            }
        else:
            self._prompts = {
                "included": True,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "tool_names": list(tool_names),
                "tool_specs": list(tool_specs),
                "projection_stats": projection_stats,
                "component_chars": component_chars,
            }

    def record_llm_request(
        self,
        step: int,
        prompt: str,
        contexts: list[dict[str, Any]],
        system_prompt: str,
    ) -> None:
        interaction = self._ensure_interaction(step)
        interaction["request"] = {
            "prompt": prompt,
            "contexts": list(contexts),
            "system_prompt": system_prompt,
        }

    def record_llm_response(
        self,
        step: int,
        completion_text: str,
        tool_calls: list[dict[str, Any]],
        raw_response_safe: dict[str, Any],
        usage: dict[str, Any],
        finish_reason: str = "",
    ) -> None:
        interaction = self._ensure_interaction(step)
        resp: dict[str, Any] = {
            "completion_text": completion_text,
            "tool_calls": list(tool_calls),
            "usage": dict(usage) if usage else {},
            "finish_reason": finish_reason or "",
        }
        if self._include_raw_response:
            resp["raw_response_safe"] = dict(raw_response_safe) if raw_response_safe else {}
        interaction["response"] = resp

    def record_tool_execution(
        self,
        step: int,
        tool: str,
        args: dict[str, Any],
        result: dict[str, Any],
        guard_blocked: bool = False,
        guard_reason: str = "",
    ) -> None:
        interaction = self._ensure_interaction(step)
        interaction.setdefault("tool_executions", []).append({
            "tool": tool,
            "args": dict(args) if args else {},
            "result": dict(result) if result else {},
            "guard_blocked": guard_blocked,
            "guard_reason": guard_reason or "",
        })

    def record_guard(self, name: str, decision: str, metadata: dict[str, Any] | None = None) -> None:
        self._guards_fired.append({
            "name": name,
            "decision": decision,
            "metadata": dict(metadata) if metadata else {},
        })

    def record_post_processing(
        self,
        *,
        completion_limited: dict[str, Any] | None = None,
        menu_cleanup: dict[str, Any] | None = None,
        continuity_audit: dict[str, Any] | None = None,
        deterministic_repair: dict[str, Any] | None = None,
    ) -> None:
        if completion_limited:
            self._post_processing["completion_limited"] = dict(completion_limited)
        if menu_cleanup:
            self._post_processing["menu_cleanup"] = dict(menu_cleanup)
        if continuity_audit:
            self._post_processing["continuity_audit"] = dict(continuity_audit)
        if deterministic_repair:
            self._post_processing["deterministic_repair"] = dict(deterministic_repair)

    def record_ra_resolution(self, ra_data: dict[str, Any]) -> None:
        self._post_processing["ra_resolution"] = dict(ra_data)

    def record_fast_path(self, action: str, reply: str) -> None:
        self._fast_path = {"action": action, "reply": reply}
        self._routing["fast_path_triggered"] = True
        self._routing["fast_path_action"] = action

    def record_final_output(
        self,
        completion_text: str,
        dice_summary: str = "",
        pending_outputs: list[dict[str, Any]] | None = None,
        sent_to_player: bool = True,
    ) -> None:
        self._final_output = {
            "completion_text": completion_text,
            "dice_summary": dice_summary,
            "pending_outputs": list(pending_outputs or []),
            "sent_to_player": sent_to_player,
        }

    def build_envelope(self) -> dict[str, Any]:
        end_time = _utc_now_iso()
        state_before = self._state_before or {}
        state_after = self._state_after or {}
        state_diff = compute_session_diff(state_before, state_after) if state_before or state_after else {}

        envelope: dict[str, Any] = {
            "envelope_version": "1.0",
            "turn_id": self._turn_id,
            "session_id": self._session_id,
            "cycle_id": self._cycle_id,
            "turn_sequence": self._turn_sequence,
            "timings": {
                "start": self._start_time,
                "end": end_time,
            },
            "actor": self._actor,
            "player_message": self._player_message,
            "routing": self._routing,
            "state": {
                "before": state_before,
                "after": state_after,
                "diff": state_diff,
            },
            "prompts": self._prompts,
            "llm_interactions": self._llm_interactions,
            "guards_fired": self._guards_fired,
            "post_processing": self._post_processing,
            "final_output": self._final_output,
            "metadata": {
                "envelope_size_bytes": 0,
                "prompt_hashes": {},
            },
        }

        # Compute hashes and size after assembly
        envelope_json = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
        envelope["metadata"]["envelope_size_bytes"] = len(envelope_json.encode("utf-8"))
        if self._prompts:
            sp = str(self._prompts.get("system_prompt") or self._prompts.get("system_prompt_chars") or "")
            up = str(self._prompts.get("user_prompt") or self._prompts.get("user_prompt_chars") or "")
            envelope["metadata"]["prompt_hashes"] = {
                "system": _short_hash(sp),
                "user": _short_hash(up),
            }

        return envelope

    def _ensure_interaction(self, step: int) -> dict[str, Any]:
        for interaction in self._llm_interactions:
            if interaction.get("step") == step:
                return interaction
        new_interaction: dict[str, Any] = {"step": step}
        self._llm_interactions.append(new_interaction)
        return new_interaction


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_hash(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
