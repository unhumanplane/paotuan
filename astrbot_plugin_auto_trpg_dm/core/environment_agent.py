from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .cycle_buffer import sanitize_ra_payload
from .cycle_state_machine import CycleStateMachine
from .models import AuditBuffer, CycleState, GameSession, RACycleInput, utc_now_iso
from .prompts import build_cycle_start_prompt, build_ra_cycle_prompt, build_ra_system_prompt


LlmGenerate = Callable[..., Awaitable[Any]]


class RecorderAgent:
    """Runs the RA cycle summarizer without importing AstrBot APIs."""

    def __init__(self, llm_generate: LlmGenerate, chat_provider_id: str, max_tokens: int = 0):
        self.llm_generate = llm_generate
        self.chat_provider_id = chat_provider_id
        self.max_tokens = max_tokens

    async def run_cycle_resolution(self, session: GameSession) -> dict[str, Any]:
        ra_input = build_ra_input_view(session)
        authority_snapshot = build_ra_authority_snapshot(session)
        prompt = build_ra_cycle_prompt(ra_input, authority_snapshot)
        llm_kwargs: dict[str, Any] = {
            "chat_provider_id": self.chat_provider_id,
            "prompt": prompt,
            "contexts": [],
            "system_prompt": build_ra_system_prompt(),
        }
        if self.max_tokens > 0:
            llm_kwargs["max_tokens"] = self.max_tokens
        try:
            response = await self.llm_generate(**llm_kwargs)
        except TypeError as exc:
            if "max_tokens" not in llm_kwargs:
                return {
                    "ok": False,
                    "error": "ra_llm_exception",
                    "message": str(exc)[:240],
                    "cycle_id": session.ra_cycle_input.cycle_id,
                }
            fallback_kwargs = dict(llm_kwargs)
            fallback_kwargs.pop("max_tokens", None)
            try:
                response = await self.llm_generate(**fallback_kwargs)
            except Exception as fallback_exc:
                return {
                    "ok": False,
                    "error": "ra_llm_exception",
                    "message": str(fallback_exc)[:240],
                    "cycle_id": session.ra_cycle_input.cycle_id,
                }
        except Exception as exc:
            return {
                "ok": False,
                "error": "ra_llm_exception",
                "message": str(exc)[:240],
                "cycle_id": session.ra_cycle_input.cycle_id,
            }

        text = _response_text(response)
        payload = _parse_json_object(text)
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "invalid_ra_json",
                "message": "Recorder Agent did not return a JSON object.",
                "cycle_id": session.ra_cycle_input.cycle_id,
                "output_excerpt": text[:240],
            }

        summary = _normalise_ra_summary(payload, session.ra_cycle_input.cycle_id)
        if not summary.get("ok", False):
            return summary
        return {
            "ok": True,
            "summary": summary["summary"],
            "prompt_chars": len(prompt),
            "output_chars": len(text),
        }


def build_ra_input_view(session: GameSession) -> dict[str, Any]:
    payload = {
        "cycle_id": session.ra_cycle_input.cycle_id,
        "actions": session.ra_cycle_input.actions,
    }
    sanitized = sanitize_ra_payload(payload)
    return sanitized if isinstance(sanitized, dict) else {"cycle_id": session.ra_cycle_input.cycle_id, "actions": []}


def build_ra_authority_snapshot(session: GameSession) -> dict[str, Any]:
    snapshot = {
        "cycle_id": session.current_cycle_id,
        "mode": session.mode.value,
        "title": session.title,
        "characters": [_character_authority_view(character) for character in session.characters.values()],
        "scene": _compact_json_value(session.scene, depth=3),
        "world_tags": _compact_json_value(session.world_tags, depth=3),
        "rule_sets": _compact_json_value(session.rule_sets, depth=3),
        "battle": _compact_json_value(session._compact_battle(), depth=4),
        "environment_summaries": _compact_json_value(session.environment_summaries[-3:], depth=3),
    }
    sanitized = sanitize_ra_payload(snapshot)
    return sanitized if isinstance(sanitized, dict) else {}


def complete_cycle_with_ra(session: GameSession, summary: dict[str, Any]) -> dict[str, Any]:
    if session.cycle_state != CycleState.CYCLE_RESOLVING:
        raise ValueError(f"invalid_cycle_completion_state:{session.cycle_state.value}")

    validation = validate_ra_patch_candidates(session, summary)
    record = dict(summary)
    record["patch_validation"] = validation
    record["created_at"] = utc_now_iso()
    session.environment_summaries.append(record)

    state_machine = CycleStateMachine()
    state_machine.begin_transition(session)
    completed_cycle_id = session.current_cycle_id
    session.current_cycle_id = completed_cycle_id + 1
    session.audit_buffer = AuditBuffer(cycle_id=session.current_cycle_id)
    session.ra_cycle_input = RACycleInput(cycle_id=session.current_cycle_id)
    state_machine.activate(session)
    return {
        "cycle_id": completed_cycle_id,
        "next_cycle_id": session.current_cycle_id,
        "patch_validation": validation,
    }


def recover_cycle_after_ra_failure(session: GameSession, failure: dict[str, Any]) -> dict[str, Any]:
    if session.cycle_state == CycleState.CYCLE_RESOLVING:
        CycleStateMachine().activate(session)
    scene = session.scene if isinstance(session.scene, dict) else {}
    log = list(scene.get("_ra_recovery_log", []))
    record = {
        "cycle_id": session.current_cycle_id,
        "error": str(failure.get("error", "ra_failed")),
        "message": str(failure.get("message", ""))[:240],
        "created_at": utc_now_iso(),
    }
    log.append(record)
    scene["_ra_recovery_log"] = log[-10:]
    session.scene = scene
    return record


def validate_ra_patch_candidates(session: GameSession, summary: dict[str, Any]) -> dict[str, Any]:
    successful_tools = _successful_tool_names(session)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    categories = {
        "character_status": {"update_character_tags", "turn_control", "execute_rule"},
        "enemy_status": {"update_scene", "update_character_tags", "turn_control", "move_entity", "check_attack_vector", "execute_rule"},
        "world_changes": {"update_scene", "update_world_tags", "start_game"},
        "rule_sets": {"register_rule", "execute_rule"},
    }
    for category, backing_tools in categories.items():
        items = summary.get(category, [])
        if items in ("", None, []):
            continue
        if not isinstance(items, list):
            rejected.append(
                {
                    "category": category,
                    "reason": "invalid_candidate_type",
                    "value": _compact_json_value(items, depth=2),
                }
            )
            continue
        if not successful_tools.intersection(backing_tools):
            for item in items[:24]:
                rejected.append(
                    {
                        "category": category,
                        "reason": "missing_tool_backing",
                        "value": _compact_json_value(item, depth=2),
                    }
                )
            continue
        for item in items[:24]:
            applied = _apply_ra_patch_candidate(session, category, item)
            accepted.append(
                {
                    "category": category,
                    "reason": "tool_backed_candidate_recorded",
                    "backing_tools": sorted(successful_tools.intersection(backing_tools)),
                    "applied": applied,
                    "value": _compact_json_value(item, depth=2),
                }
            )
    return {"accepted": accepted, "rejected": rejected}


def _apply_ra_patch_candidate(session: GameSession, category: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if category == "world_changes":
        patch = item.get("scene_patch") if isinstance(item.get("scene_patch"), dict) else item.get("patch")
        if not isinstance(patch, dict):
            return False
        allowed_scene_keys = {
            "summary",
            "current_conflict",
            "location",
            "npcs",
            "immediate_hooks",
            "recent_events",
        }
        safe_patch = {
            str(key): _compact_json_value(value, depth=3)
            for key, value in patch.items()
            if str(key) in allowed_scene_keys
        }
        if not safe_patch:
            return False
        session.scene.update(safe_patch)
        return True
    if category == "character_status":
        character_id = str(item.get("character_id") or item.get("id") or "").strip()
        character = session.characters.get(character_id)
        tags = item.get("tags")
        if not character or not isinstance(tags, list):
            return False
        safe_tags: list[dict[str, Any]] = []
        for tag in tags[:12]:
            if not isinstance(tag, dict):
                continue
            layer = str(tag.get("layer") or "status").strip().lower()
            if layer not in {"status", "notes"}:
                continue
            key = str(tag.get("key") or "").strip()
            if not key:
                continue
            safe_tags.append(
                {
                    "key": key[:80],
                    "value": _compact_json_value(tag.get("value", ""), depth=2),
                    "type": str(tag.get("type") or "text")[:40],
                    "source": "ra_validated_patch",
                    "layer": layer,
                }
            )
        if not safe_tags:
            return False
        character.upsert_tags(safe_tags)
        return True
    return False


def _normalise_ra_summary(payload: dict[str, Any], expected_cycle_id: int) -> dict[str, Any]:
    raw_cycle_id = payload.get("cycle_id", expected_cycle_id)
    try:
        cycle_id = int(raw_cycle_id)
    except (TypeError, ValueError):
        cycle_id = expected_cycle_id
    if cycle_id != expected_cycle_id:
        return {
            "ok": False,
            "error": "stale_ra_cycle_id",
            "message": f"expected {expected_cycle_id}, got {cycle_id}",
            "cycle_id": expected_cycle_id,
        }
    summary = {
        "cycle_id": cycle_id,
        "summary": _compact_text(payload.get("summary", "本周期没有可确认摘要。"), 1500),
        "character_status": _list_value(payload.get("character_status", [])),
        "enemy_status": _list_value(payload.get("enemy_status", [])),
        "world_changes": _list_value(payload.get("world_changes", [])),
        "rules_triggered": _list_value(payload.get("rules_triggered", [])),
        "rule_sets": _list_value(payload.get("rule_sets", [])),
        "dm_narrative_aligned": bool(payload.get("dm_narrative_aligned", True)),
        "discrepancies": _list_value(payload.get("discrepancies", [])),
    }
    return {"ok": True, "summary": sanitize_ra_payload(summary)}


def _response_text(response: Any) -> str:
    return str(getattr(response, "completion_text", "") or response or "")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    for start, end in _json_object_spans(stripped):
        try:
            payload = json.loads(stripped[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _json_object_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
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
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                spans.append((start, index + 1))
                start = -1
    return spans


def _successful_tool_names(session: GameSession) -> set[str]:
    names: set[str] = set()
    actions = getattr(session.audit_buffer, "actions", []) or []
    for action in actions:
        tools = _value(action, "tools_called", [])
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            result = tool.get("result", {})
            if isinstance(result, dict) and result.get("ok") is False:
                continue
            name = str(tool.get("name") or tool.get("tool") or "").strip()
            if name:
                names.add(name)
    return names


def _character_authority_view(character: Any) -> dict[str, Any]:
    tags = []
    for tag in getattr(character, "tags", [])[:48]:
        tags.append(
            {
                "key": str(getattr(tag, "key", ""))[:80],
                "value": _compact_json_value(getattr(tag, "value", ""), depth=2),
                "type": str(getattr(tag, "type", "text"))[:40],
                "layer": str(getattr(tag, "layer", "notes"))[:40],
            }
        )
    return {
        "id": str(getattr(character, "id", "")),
        "name": str(getattr(character, "name", ""))[:80],
        "summary": _compact_text(getattr(character, "summary", ""), 240),
        "tags": tags,
    }


def _compact_json_value(value: Any, depth: int = 3) -> Any:
    if depth <= 0:
        return _compact_text(value, 240)
    if isinstance(value, dict):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 32:
                compacted["_truncated"] = True
                break
            compacted[str(key)[:80]] = _compact_json_value(item, depth - 1)
        return compacted
    if isinstance(value, list):
        return [_compact_json_value(item, depth - 1) for item in value[:32]]
    if isinstance(value, str):
        return _compact_text(value, 500)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _compact_text(value, 240)


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [_compact_json_value(item, depth=3) for item in value[:48]]
    if value in ("", None):
        return []
    return [_compact_json_value(value, depth=2)]


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
