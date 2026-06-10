from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..core.story_forge_runtime import (
    StoryForgeRuntimeConfig,
    advance_story_forge_pressure_clock,
    record_story_forge_convergence,
    record_story_forge_encounter_contract,
    record_story_forge_pressure_clock,
)
from ..storage.json_repository import JsonGameRepository


class RecordStoryForgeConvergenceArgs(BaseModel):
    thread_id: str = Field(default="", description="Open hook/thread id this next scene goal advances.")
    action_type: str = Field(default="next_scene", description="next_scene, clue_reveal, pressure_escalation, choice, combat, social, travel, or puzzle.")
    available_action: str = Field(default="", description="Player-facing action summary; no hidden truth.")
    scene_goal: Any = Field(..., description="Executable next scene goal, or an object with goal/objective plus scene goal fields.")
    entry_cost: str = Field(default="", description="Cost, risk, resource, time, position, or fictional commitment needed to enter the scene.")
    success_signal: str = Field(default="", description="What players can observe when the goal succeeds.")
    failure_forward: str = Field(default="", description="How the scene remains playable if the check/action fails.")
    evidence: List[str] = Field(default_factory=list, description="Visible clues, tool results, or scene facts supporting this goal.")
    map_grid_seed: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional player-visible grid seed: width, height, cells, entities, doors, hazards, obstacles, labels.",
    )
    send_to_chat: bool = Field(default=True, description="Whether a rendered map seed should be attached to this turn if eligible.")


class RecordStoryForgePressureClockArgs(BaseModel):
    clock_id: str = Field(default="", description="Stable clock id. Leave empty to derive from label.")
    label: str = Field(..., description="Player-facing pressure clock label.")
    pressure_type: str = Field(default="time", description="time, resource, relation, space, moral, information, or danger.")
    value: int = Field(default=0, description="Current clock value.")
    max: int = Field(default=4, description="Clock completion value.")
    visibility: str = Field(default="public", description="public, partial, or hidden. Hidden clocks are not projected to player brief.")
    public_signal: str = Field(default="", description="What players can currently perceive without hidden truth.")
    stakes: str = Field(default="", description="Playable consequence if pressure keeps rising.")
    next_risk_hint: str = Field(default="", description="Short hint for what kind of choice will tick this clock next.")
    counterplay_hint: str = Field(default="", description="Short hint for how players can slow, reduce, or redirect this pressure.")
    on_complete: Dict[str, Any] = Field(
        default_factory=dict,
        description="Completion effect with at least one of failure_forward, new_scene_goal, or state_change.",
    )


class RecordStoryForgeEncounterContractArgs(BaseModel):
    contract_id: str = Field(default="", description="Stable encounter contract id. Leave empty to derive one.")
    encounter_decision: str = Field(
        ...,
        description="free_narrative, single_check, pressure_scene, soft_turns, strict_turns, or strict_grid.",
    )
    reason: str = Field(..., description="Why this gameplay structure fits the visible situation.")
    scene_goal: str = Field(..., description="Executable scene goal currently in front of the players.")
    stakes: str = Field(default="", description="Visible failure, delay, resource, space, moral, or information cost.")
    participants: List[str] = Field(default_factory=list, description="Visible actor/entity ids or labels involved.")
    pressure_vectors: List[str] = Field(
        default_factory=list,
        description="time, resource, relation, space, moral, information, and/or danger.",
    )
    action_economy: str = Field(default="", description="none, one_actor_focus, side_based, or strict_order.")
    map_need: str = Field(default="", description="none, sketch, or strict_grid.")
    turn_order_source: str = Field(
        default="",
        description="none, derived_scene, derived_battle_state, existing_state, or rule_initiative.",
    )
    recommended_next_tool: str = Field(
        default="",
        description="resolve_check, execute_rule, turn_control, create_strict_map, start_combat_on_map, pressure clock tools, update_scene, or final_response.",
    )
    player_visible_brief: str = Field(default="", description="Short player-safe brief; no hidden truth.")
    evidence: List[str] = Field(default_factory=list, description="Visible clues, scene facts, or tool results supporting the decision.")


class AdvanceStoryForgePressureClockArgs(BaseModel):
    clock_id: str = Field(..., description="Existing pressure clock id.")
    delta: int = Field(default=1, description="Clock delta, usually 1. Negative values can relieve pressure.")
    trigger: str = Field(..., description="Concrete trigger such as continued_investigation, failed_stealth, loud_entry, or ignored_warning.")
    cause: str = Field(..., description="Specific in-fiction reason this clock changed.")
    visible_effect: str = Field(..., description="Player-visible signal of the clock movement. No hidden truth.")


class StoryForgeTools:
    def __init__(
        self,
        repository: JsonGameRepository,
        session_id: str,
        *,
        actor: dict[str, str] | None = None,
        config: StoryForgeRuntimeConfig | None = None,
    ):
        self.repository = repository
        self.session_id = session_id
        self.actor = actor or {}
        self.config = config or StoryForgeRuntimeConfig()

    async def record_story_forge_convergence(
        self,
        thread_id: str = "",
        action_type: str = "next_scene",
        available_action: str = "",
        scene_goal: Any = "",
        entry_cost: str = "",
        success_signal: str = "",
        failure_forward: str = "",
        evidence: List[str] | None = None,
        map_grid_seed: Dict[str, Any] | None = None,
        send_to_chat: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "thread_id": thread_id,
            "action_type": action_type,
            "available_action": available_action,
            "scene_goal": scene_goal,
            "entry_cost": entry_cost,
            "success_signal": success_signal,
            "failure_forward": failure_forward,
            "evidence": evidence or [],
            "map_grid_seed": map_grid_seed or {},
            "send_to_chat": send_to_chat,
        }
        return record_story_forge_convergence(
            self.repository,
            self.session_id,
            actor=self.actor,
            payload=payload,
            config=self.config,
        )

    async def record_story_forge_pressure_clock(
        self,
        clock_id: str = "",
        label: str = "",
        pressure_type: str = "time",
        value: int = 0,
        max: int = 4,
        visibility: str = "public",
        public_signal: str = "",
        stakes: str = "",
        next_risk_hint: str = "",
        counterplay_hint: str = "",
        on_complete: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return record_story_forge_pressure_clock(
            self.repository,
            self.session_id,
            actor=self.actor,
            clock={
                "clock_id": clock_id,
                "label": label,
                "pressure_type": pressure_type,
                "value": value,
                "max": max,
                "visibility": visibility,
                "public_signal": public_signal,
                "stakes": stakes,
                "next_risk_hint": next_risk_hint,
                "counterplay_hint": counterplay_hint,
                "on_complete": on_complete or {},
            },
            config=self.config,
        )

    async def record_story_forge_encounter_contract(
        self,
        contract_id: str = "",
        encounter_decision: str = "",
        reason: str = "",
        scene_goal: str = "",
        stakes: str = "",
        participants: List[str] | None = None,
        pressure_vectors: List[str] | None = None,
        action_economy: str = "",
        map_need: str = "",
        turn_order_source: str = "",
        recommended_next_tool: str = "",
        player_visible_brief: str = "",
        evidence: List[str] | None = None,
    ) -> Dict[str, Any]:
        return record_story_forge_encounter_contract(
            self.repository,
            self.session_id,
            actor=self.actor,
            payload={
                "contract_id": contract_id,
                "encounter_decision": encounter_decision,
                "reason": reason,
                "scene_goal": scene_goal,
                "stakes": stakes,
                "participants": participants or [],
                "pressure_vectors": pressure_vectors or [],
                "action_economy": action_economy,
                "map_need": map_need,
                "turn_order_source": turn_order_source,
                "recommended_next_tool": recommended_next_tool,
                "player_visible_brief": player_visible_brief,
                "evidence": evidence or [],
            },
            config=self.config,
        )

    async def advance_story_forge_pressure_clock(
        self,
        clock_id: str,
        delta: int = 1,
        trigger: str = "",
        cause: str = "",
        visible_effect: str = "",
    ) -> Dict[str, Any]:
        return advance_story_forge_pressure_clock(
            self.repository,
            self.session_id,
            actor=self.actor,
            clock_id=clock_id,
            delta=delta,
            trigger=trigger,
            cause=cause,
            visible_effect=visible_effect,
            config=self.config,
        )
