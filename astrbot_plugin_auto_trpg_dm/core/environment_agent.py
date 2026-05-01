from __future__ import annotations

import json
import re
from typing import Any

from .cycle_state_machine import CycleStateMachine
from .models import CycleState, GameSession
from .plugin_log import get_plugin_logger
from .prompts import build_ra_system_prompt


class RecorderAgent:
    """RA — Recorder Agent (状态记录员).

    Runs once per cycle end to produce structured state summaries.
    RA does not see player input directly; it consumes the DM's narrative
    output and tool traces from the audit buffer.
    """

    def __init__(self, astr_context: Any, repository: Any):
        self.astr_context = astr_context
        self.repository = repository

    async def resolve_cycle(self, session_id: str, session: GameSession) -> dict[str, Any]:
        cycle_id = session.current_cycle_id
        ra_input = session.ra_cycle_input

        system_prompt = build_ra_system_prompt()
        user_prompt = self._build_user_prompt(session, ra_input)

        try:
            response = await self._call_llm(system_prompt, user_prompt)
            completion = getattr(response, "completion_text", "") or str(response)
            parsed = self._parse_json(completion)
        except Exception as exc:
            get_plugin_logger().warning(
                "ra_llm_failed session=%s cycle_id=%s error=%s",
                session_id,
                cycle_id,
                exc,
            )
            return {
                "ok": False,
                "error": "ra_llm_failed",
                "reason": str(exc),
            }

        summary = self._normalize_summary(parsed, cycle_id)
        session.environment_summaries.append(summary)

        # Transition: RESOLVING -> TRANSITION -> ACTIVE (new cycle)
        CycleStateMachine.transition(session, CycleState.CYCLE_TRANSITION)
        CycleStateMachine.start_new_cycle(session)
        self.repository.save_session(session)

        get_plugin_logger().info(
            "ra_cycle_resolved session=%s cycle_id=%s summary_chars=%s discrepancies=%s",
            session_id,
            cycle_id,
            len(summary.get("summary", "")),
            len(summary.get("discrepancies", [])),
        )

        return {
            "ok": True,
            "summary": summary,
        }

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> Any:
        if self.astr_context is None:
            raise RuntimeError("astr_context not available; cannot run RA LLM call")
        return await self.astr_context.llm_generate(
            system_prompt=system_prompt,
            prompt=user_prompt,
        )

    def _build_user_prompt(self, session: GameSession, ra_input: Any) -> str:
        snapshot = {
            "cycle_id": session.current_cycle_id,
            "cycle_state": session.cycle_state.value,
            "character_count": len(session.characters),
            "characters": {
                cid: {
                    "name": c.name,
                    "player_id": c.player_id,
                    "summary": c.summary,
                }
                for cid, c in session.characters.items()
            },
            "scene_summary": str((session.scene or {}).get("summary", "")),
            "current_conflict": str((session.scene or {}).get("current_conflict", "")),
        }
        ra_input_dict = {
            "cycle_id": ra_input.cycle_id,
            "actions": ra_input.actions,
        }
        return f"""请基于以下周期审计数据，生成结构化的周期总结 JSON。

【当前会话快照】
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

【RA 周期输入（已脱敏）】
{json.dumps(ra_input_dict, ensure_ascii=False, indent=2)}

请输出合法 JSON，包含以下字段：
- summary: 字符串，周期叙事摘要
- character_status: 对象，角色ID -> {{hp, mp, conditions, position, alive}}
- enemy_status: 对象，敌人ID -> {{hp, mp, conditions, alive}}
- world_changes: 对象，包含 scene_updates, new_entities, removed_entities
- rules_triggered: 字符串数组，本周期触发的规则名
- dm_narrative_aligned: 布尔值，DM叙事是否与tool trace一致
- discrepancies: 字符串数组，如有不一致请记录

禁止输出 markdown 代码块，只输出纯 JSON。"""

    def _parse_json(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            match = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
        return json.loads(stripped)

    def _normalize_summary(self, parsed: dict[str, Any], cycle_id: int) -> dict[str, Any]:
        return {
            "cycle_id": cycle_id,
            "summary": str(parsed.get("summary", "")),
            "character_status": dict(parsed.get("character_status", {})),
            "enemy_status": dict(parsed.get("enemy_status", {})),
            "world_changes": dict(parsed.get("world_changes", {})),
            "rules_triggered": list(parsed.get("rules_triggered", [])),
            "dm_narrative_aligned": bool(parsed.get("dm_narrative_aligned", True)),
            "discrepancies": list(parsed.get("discrepancies", [])),
        }
