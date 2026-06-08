from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from ..core.story_forge_runtime import (
    StoryForgeRuntimeConfig,
    record_story_forge_convergence,
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
