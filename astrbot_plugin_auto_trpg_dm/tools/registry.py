from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core.models import GameMode
from ..core.map_request_guard import looks_text_only_map_request
from ..core.map_tool_routing import add_map_renderer_tools, looks_legacy_svg_fallback_request, looks_visual_map_request
from ..rules.python_runtime import PythonRuleRuntime
from ..storage.json_repository import JsonGameRepository
from .cycle_tools import CycleControlArgs, CycleTools
from .diagnostic_tools import DiagnosticTools, EstimateTokenUsageArgs
from .external_memory_tools import ExternalMemoryTools, SearchExternalMemoryArgs
from .map_tools import GenerateMapSvgArgs, MapTools
from .memory_tools import (
    BindPlayerCharacterArgs,
    ClarifyEntityTimelineArgs,
    CreateCharacterArgs,
    MemoryTools,
    RecordTimelineEventArgs,
    SessionControlArgs,
    StartGameArgs,
    UpdateCharacterTagsArgs,
    UpdateSceneArgs,
    UpdateWorldTagsArgs,
    has_campaign_background,
)
from .overview_topology_render_tools import (
    OverviewTopologyRenderTools,
    RenderOverviewTopologySvgArgs,
)
from .rule_tools import ExecuteRuleArgs, ListRulesArgs, RegisterRuleArgs, ResolveCheckArgs, RuleTools
from .rulebook_tools import QueryCoreRulesArgs, RulebookTools
from .spatial_tools import (
    CheckAttackVectorArgs,
    CreateGridArgs,
    MoveEntityArgs,
    PlaceEntityArgs,
    SpatialTools,
)
from .strict_grid_render_tools import RenderStrictGridSvgArgs, StrictGridRenderTools
from .strict_lifecycle_tools import (
    CreateStrictMapArgs,
    EndCombatArgs,
    StartCombatOnMapArgs,
    StrictLifecycleTools,
)
from .turn_tools import TurnControlArgs, TurnTools


ToolHandler = Callable[..., Awaitable[Dict[str, Any]]]


class EmptyArgs(BaseModel):
    pass


class FinalResponseArgs(BaseModel):
    reply: str = Field(
        ...,
        description="Final player-facing reply. Use this only when all needed tool facts are available.",
    )


@dataclass
class LocalFunctionTool(FunctionTool[AstrAgentContext]):
    name: str
    description: str
    parameters: dict
    handler: ToolHandler

    def __post_init__(self) -> None:
        validate = getattr(self, "validate_parameters", None)
        if callable(validate):
            validate()
            return
        _validate_tool_parameters(self.parameters)

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs: Any,
    ) -> str:
        import json

        result = await self.handler(**kwargs)
        return json.dumps(result, ensure_ascii=False)

    async def execute_dict(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        return await self.handler(**kwargs)


def make_tool(
    name: str,
    description: str,
    model: type[BaseModel],
    handler: ToolHandler,
) -> LocalFunctionTool:
    return LocalFunctionTool(
        name=name,
        description=description,
        parameters=model_schema(model),
        handler=handler,
    )


async def submit_final_response(reply: str) -> Dict[str, Any]:
    return {"ok": True, "reply": str(reply or "")}


def _validate_tool_parameters(parameters: dict[str, Any]) -> None:
    if not isinstance(parameters, dict):
        raise TypeError("tool parameters must be a JSON schema object")
    if parameters.get("type", "object") != "object":
        raise ValueError("tool parameters schema type must be object")
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        raise TypeError("tool parameters properties must be a mapping")
    required = parameters.get("required", [])
    if not isinstance(required, list):
        raise TypeError("tool parameters required must be a list")


class LocalToolExecutor:
    def __init__(self, tools: dict[str, LocalFunctionTool]):
        self.tools = tools

    async def execute(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(tool_name)
        if not tool:
            return {
                "ok": False,
                "error": "tool_not_allowed_or_not_found",
                "tool": tool_name,
            }
        try:
            return await tool.execute_dict(args)
        except TypeError as exc:
            return {
                "ok": False,
                "error": "invalid_tool_arguments",
                "tool": tool_name,
                "reason": str(exc),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": "tool_execution_failed",
                "tool": tool_name,
                "reason": str(exc),
            }


class ToolRegistry:
    def __init__(
        self,
        repository: JsonGameRepository,
        rule_runtime: PythonRuleRuntime,
        astr_context: Any | None = None,
        external_memory_config: Any | None = None,
        external_memory: Any | None = None,
    ):
        self.repository = repository
        self.rule_runtime = rule_runtime
        self.astr_context = astr_context
        self.external_memory_config = external_memory_config
        self.external_memory = external_memory

    def for_mode(
        self,
        mode: GameMode,
        session_id: str,
        actor: dict[str, str] | None = None,
        message: str = "",
        provider_id: str = "",
    ) -> tuple[ToolSet, list[str], LocalToolExecutor, list[dict[str, Any]]]:
        rule_tools = RuleTools(
            self.repository,
            self.rule_runtime,
            session_id,
            actor=actor,
            message=message,
        )
        memory_tools = MemoryTools(self.repository, session_id, actor=actor, message=message)
        external_memory_tools = ExternalMemoryTools(
            self.repository,
            session_id,
            actor=actor,
            message=message,
            external_memory=self.external_memory,
        )
        spatial_tools = SpatialTools(self.repository, session_id, actor=actor)
        strict_grid_render_tools = StrictGridRenderTools(self.repository, session_id, actor=actor)
        strict_lifecycle_tools = StrictLifecycleTools(self.repository, session_id, actor=actor)
        turn_tools = TurnTools(self.repository, session_id, actor=actor)
        cycle_tools = CycleTools(self.repository, session_id, actor=actor)
        external_memory_config = self.external_memory_config
        diagnostic_tools = DiagnosticTools(
            self.repository,
            session_id,
            external_memory_enabled=bool(getattr(external_memory_config, "enabled", False)),
            external_memory_read_enabled=bool(getattr(external_memory_config, "read_enabled", False)),
            external_memory_max_context_chars=_safe_int(
                getattr(external_memory_config, "max_context_chars", 0)
            ),
        )
        rulebook_tools = RulebookTools(self.repository, session_id)
        map_tools = MapTools(
            repository=self.repository,
            session_id=session_id,
            astr_context=self.astr_context,
            provider_id=provider_id,
        )
        overview_topology_tools = OverviewTopologyRenderTools(
            repository=self.repository,
            session_id=session_id,
            actor=actor,
        )

        catalog: dict[str, LocalFunctionTool] = {
            "final_response": make_tool(
                name="final_response",
                description=(
                    "Submit the final player-facing DM reply and end this LLM/tool loop. "
                    "Use it only after all required checks, state writes, map renders, or clarifications are complete; "
                    "do not combine it with other tool calls in the same step."
                ),
                model=FinalResponseArgs,
                handler=submit_final_response,
            ),
            "register_rule": make_tool(
                name="register_rule",
                description="?????? TRPG ???????????????????????????",
                model=RegisterRuleArgs,
                handler=rule_tools.register_rule,
            ),
            "resolve_check": make_tool(
                name="resolve_check",
                description=(
                    "Preferred tool for ordinary d20 checks such as searching, persuading, sneaking, lockpicking, "
                    "equipment operation, decoding, or risky preparation. Provide action, optional actor, dc or "
                    "difficulty, final bonus, advantage/disadvantage, modifier_note, and stakes. It accepts natural "
                    "check context like ability, skill, and proficiency; do not call list_rules first for ordinary checks."
                ),
                model=ResolveCheckArgs,
                handler=rule_tools.resolve_check,
            ),
            "execute_rule": make_tool(
                name="execute_rule",
                description=(
                    "??????????????????????????????????"
                    "??????????????????? d20 ?????? resolve_check?"
                    "?????????????/???????????/???buff?????????"
                    "??? reason ? args ??????/?????????????????"
                ),
                model=ExecuteRuleArgs,
                handler=rule_tools.execute_rule,
            ),
            "list_rules": make_tool(
                name="list_rules",
                description="???????????????????????/???? tag ? detail???????????",
                model=ListRulesArgs,
                handler=rule_tools.list_rules,
            ),
            "query_core_rules": make_tool(
                name="query_core_rules",
                description="?? DND 2024 ????? DM ??????????????????????????????????????????????????? DM ?????????????????????????????????? execute_rule?",
                model=QueryCoreRulesArgs,
                handler=rulebook_tools.query_core_rules,
            ),
            "create_character": make_tool(
                name="create_character",
                description="??????? Tag ????player_id ???????????????????????????/?????????????",
                model=CreateCharacterArgs,
                handler=memory_tools.create_character,
            ),
            "bind_player_character": make_tool(
                name="bind_player_character",
                description="????????????????????????????/???/???????????",
                model=BindPlayerCharacterArgs,
                handler=memory_tools.bind_player_character,
            ),
            "update_character_tags": make_tool(
                name="update_character_tags",
                description="??????? Tag??????? tags???????????????????????????????????????? raw_text ?????????????????????",
                model=UpdateCharacterTagsArgs,
                handler=memory_tools.update_character_tags,
            ),
            "update_scene": make_tool(
                name="update_scene",
                description=(
                    "?????????????NPC ??????????????????"
                    "??????? scene_thread_id/????/?????????????? location?"
                    "????? scene_time_label/scene_time_of_day ?????????"
                    "???? summary/current_objective/current_conflict/stakes ??????????????????????????"
                    "?????/???????????????? status=closed/resolved/retired/archived?"
                    "??????????????????????????????????? timeline_patch/cycle_control?"
                    "????????????????????????????????? "
                    "clues/open_hooks/mysteries/current_objective/stakes/pressure_clock?"
                    "clue status ? discovered/suspected/resolved/false_lead/blocked?????????????"
                ),
                model=UpdateSceneArgs,
                handler=memory_tools.update_scene,
            ),
            "record_timeline_event": make_tool(
                name="record_timeline_event",
                description=(
                    "?????????????????????????????????????????"
                    "??????????????????????NPC ?????????"
                    "???????????????? unknowns?????????? supersedes/retracted_by??????????"
                ),
                model=RecordTimelineEventArgs,
                handler=memory_tools.record_timeline_event,
            ),
            "clarify_entity_timeline": make_tool(
                name="clarify_entity_timeline",
                description=(
                    "???????????????????????????????? scene thread ? NPC known_facts/open_hooks?"
                    "????????????????????????????????????????????????????"
                    "????????????????????????????????????????"
                ),
                model=ClarifyEntityTimelineArgs,
                handler=memory_tools.clarify_entity_timeline,
            ),
            "update_world_tags": make_tool(
                name="update_world_tags",
                description="???????????????????????",
                model=UpdateWorldTagsArgs,
                handler=memory_tools.update_world_tags,
            ),
            "start_game": make_tool(
                name="start_game",
                description=(
                    "???????????????????????????????????"
                    "initial_hook ???????????????????????????"
                    "?? scene_patch ??? current_objective????? open_hooks?stakes ? pressure_clock?"
                    "????????????"
                ),
                model=StartGameArgs,
                handler=memory_tools.start_game,
            ),
            "session_control": make_tool(
                name="session_control",
                description=(
                    "??????????????????????????????????"
                    "??????????????????????????????????????????"
                    "?????????????????????"
                    "??/??????????????? confirm_reset ? confirm_token ?????"
                ),
                model=SessionControlArgs,
                handler=memory_tools.session_control,
            ),
            "search_external_memory": make_tool(
                name="search_external_memory",
                description="???? Honcho ??????????????????????????? recap?????????????????????????????????? HP?????????????????????",
                model=SearchExternalMemoryArgs,
                handler=external_memory_tools.search_external_memory,
            ),
            "create_grid": make_tool(
                name="create_grid",
                description="????????????????????",
                model=CreateGridArgs,
                handler=spatial_tools.create_grid,
            ),
            "create_strict_map": make_tool(
                name="create_strict_map",
                description="??????? strict_local_map?????????????????????",
                model=CreateStrictMapArgs,
                handler=strict_lifecycle_tools.create_strict_map,
            ),
            "start_combat_on_map": make_tool(
                name="start_combat_on_map",
                description="??? strict_local_map ??????????????? battle.active ? battle.map_id?",
                model=StartCombatOnMapArgs,
                handler=strict_lifecycle_tools.start_combat_on_map,
            ),
            "end_combat": make_tool(
                name="end_combat",
                description="????????? strict_local_map???? combat ?????????",
                model=EndCombatArgs,
                handler=strict_lifecycle_tools.end_combat,
            ),
            "place_entity": make_tool(
                name="place_entity",
                description="?????????????",
                model=PlaceEntityArgs,
                handler=spatial_tools.place_entity,
            ),
            "move_entity": make_tool(
                name="move_entity",
                description="???????????????????????????",
                model=MoveEntityArgs,
                handler=spatial_tools.move_entity,
            ),
            "check_attack_vector": make_tool(
                name="check_attack_vector",
                description="???????????????????????????????",
                model=CheckAttackVectorArgs,
                handler=spatial_tools.check_attack_vector,
            ),
            "get_battle_snapshot": make_tool(
                name="get_battle_snapshot",
                description="???????????",
                model=EmptyArgs,
                handler=spatial_tools.get_battle_snapshot,
            ),
            "render_strict_grid_svg": make_tool(
                name="render_strict_grid_svg",
                description="??? player_view strict_local_map ?????????? SVG???? LLM ? SVG/XML??????????",
                model=RenderStrictGridSvgArgs,
                handler=strict_grid_render_tools.render_strict_grid_svg,
            ),
            "turn_control": make_tool(
                name="turn_control",
                description="???????????????????????????????????????????120 ???????????????",
                model=TurnControlArgs,
                handler=turn_tools.turn_control,
            ),
            "cycle_control": make_tool(
                name="cycle_control",
                description=(
                    '???????????MVP ??? action="end_cycle"?????????????????'
                    "?????????????????????????? timeline_patch?"
                    "????????/??????????? sync_policy=strict?"
                    "?? AFK ??????????? sync_policy=timeout/quorum ?????????? AFK ?????"
                ),
                model=CycleControlArgs,
                handler=cycle_tools.cycle_control,
            ),
            "generate_map_svg": make_tool(
                name="generate_map_svg",
                description=(
                    "?????????????? strict/overview ??????????????"
                    "??? LLM ?????? visual_only SVG ?????????"
                    "?????????? legacy/LLM SVG fallback??????????????"
                    "?????????????????? map facts?????????????????"
                ),
                model=GenerateMapSvgArgs,
                handler=map_tools.generate_map_svg,
            ),
            "render_overview_topology_svg": make_tool(
                name="render_overview_topology_svg",
                description="??? active overview map ?? player_view ?????? topology/layout facts????????/??????????????????? SVG???? LLM ? SVG???? map facts?",
                model=RenderOverviewTopologySvgArgs,
                handler=overview_topology_tools.render_overview_topology_svg,
            ),
        }

        def specs_for_mode(
            target_mode: GameMode,
            message: str = "",
        ) -> tuple[list[str], list[dict[str, Any]]]:
            names = self._with_llm_decided_tools(
                self._allowed_tool_names(target_mode, message=message),
                message=message,
            )
            try:
                session_for_specs = self.repository.load_session(session_id)
                if _post_game_tool_scope(session_for_specs, message):
                    names = self._with_llm_decided_tools(
                        _post_game_tool_names(message),
                        message=message,
                    )
                if not has_campaign_background(session_for_specs):
                    names = self._background_first_tool_names(names, message=message)
            except Exception:
                pass
            names = self._prune_diagnostic_tools(names, message=message)
            names = self._with_loop_control_tools(names)
            specs_for_names = [
                {
                    "name": catalog[name].name,
                    "description": catalog[name].description,
                    "parameters": catalog[name].parameters,
                }
                for name in names
                if name in catalog
            ]
            return names, specs_for_names

        catalog["estimate_token_usage"] = make_tool(
            name="estimate_token_usage",
            description="?????????????audit?system prompt ? Function Tool schema ? token/??????????? token??????????",
            model=EstimateTokenUsageArgs,
            handler=diagnostic_tools.estimate_token_usage,
        )
        diagnostic_tools.set_tool_specs_provider(specs_for_mode)

        session = self.repository.load_session(session_id)
        unbound_post_start_actor = _post_start_unbound_actor_scope(session, actor)
        allowed = self._with_llm_decided_tools(self._allowed_tool_names(mode, message=message), message=message)
        if _post_game_tool_scope(session, message):
            allowed = self._with_llm_decided_tools(_post_game_tool_names(message), message=message)
        if unbound_post_start_actor:
            allowed = self._late_join_actor_tool_names(allowed)
        if not has_campaign_background(session):
            allowed = self._background_first_tool_names(allowed, message=message)
        allowed = self._prune_diagnostic_tools(allowed, message=message)
        allowed = self._with_loop_control_tools(allowed)
        if unbound_post_start_actor:
            allowed = [name for name in allowed if name != "cycle_control"]
        selected = {name: catalog[name] for name in allowed}
        specs = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in selected.values()
        ]
        return _make_tool_set(list(selected.values())), allowed, LocalToolExecutor(selected), specs

    @staticmethod
    def _allowed_tool_names(mode: GameMode, message: str = "") -> list[str]:
        if mode == GameMode.CHARACTER_CREATION:
            text = (message or "").strip().lower()
            tools = [
                "create_character",
                "bind_player_character",
                "update_character_tags",
                "update_world_tags",
                "record_timeline_event",
                "clarify_entity_timeline",
                "start_game",
                "register_rule",
                "resolve_check",
                "execute_rule",
                "list_rules",
                "query_core_rules",
                "session_control",
                "estimate_token_usage",
            ]
            if (
                _contains_any(text, COMBAT_ACTION_TERMS)
                or _contains_any(text, BATTLE_RESOLUTION_TERMS)
                or _contains_any(text, TURN_FLOW_TERMS)
            ):
                tools.extend(["update_scene", "turn_control", "get_battle_snapshot"])
            return list(dict.fromkeys(tools))
        if mode == GameMode.RULE_AUTHORING:
            return [
                "register_rule",
                "resolve_check",
                "execute_rule",
                "list_rules",
                "query_core_rules",
                "session_control",
                "estimate_token_usage",
            ]
        if mode == GameMode.TACTICAL:
            text = (message or "").strip().lower()
            if _contains_any(text, DIAGNOSTIC_TERMS):
                return [
                    "session_control",
                    "estimate_token_usage",
                    "get_battle_snapshot",
                ]
            if _contains_any(text, MAP_SETUP_TERMS):
                return [
                    "get_battle_snapshot",
                    "render_strict_grid_svg",
                    "turn_control",
                    "create_strict_map",
                    "start_combat_on_map",
                    "create_grid",
                    "place_entity",
                    "move_entity",
                    "check_attack_vector",
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "resolve_check",
                    "execute_rule",
                    "register_rule",
                    "update_scene",
                    "record_timeline_event",
                    "clarify_entity_timeline",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, BATTLE_JOIN_TERMS):
                return [
                    "get_battle_snapshot",
                    "start_combat_on_map",
                    "turn_control",
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, BATTLE_RESOLUTION_TERMS):
                return [
                    "get_battle_snapshot",
                    "end_combat",
                    "turn_control",
                    "query_core_rules",
                    "resolve_check",
                    "execute_rule",
                    "update_scene",
                    "update_character_tags",
                    "record_timeline_event",
                    "clarify_entity_timeline",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, CHARACTER_PROFILE_TERMS):
                return [
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "query_core_rules",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, COMBAT_ACTION_TERMS):
                tools = [
                    "get_battle_snapshot",
                    "start_combat_on_map",
                    "turn_control",
                    "query_core_rules",
                    "move_entity",
                    "check_attack_vector",
                    "resolve_check",
                    "execute_rule",
                    "update_scene",
                    "update_character_tags",
                    "record_timeline_event",
                    "clarify_entity_timeline",
                    "session_control",
                    "estimate_token_usage",
                ]
                if _contains_any(text, RULE_AUTHORING_LIKELY_TERMS):
                    tools.insert(6, "register_rule")
                return tools
            if _contains_any(text, TURN_FLOW_TERMS):
                return [
                    "get_battle_snapshot",
                    "end_combat",
                    "turn_control",
                    "bind_player_character",
                    "update_character_tags",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, STATE_QUERY_TERMS):
                return [
                    "get_battle_snapshot",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, RULE_QUERY_TERMS):
                return [
                    "get_battle_snapshot",
                    "query_core_rules",
                    "resolve_check",
                    "execute_rule",
                    "register_rule",
                    "list_rules",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, DM_GUIDANCE_TERMS):
                return [
                    "get_battle_snapshot",
                    "query_core_rules",
                    "session_control",
                    "estimate_token_usage",
                ]
            return [
                "get_battle_snapshot",
                "turn_control",
                "resolve_check",
                "execute_rule",
                "update_scene",
                "update_character_tags",
                "session_control",
                "estimate_token_usage",
            ]
        if mode == GameMode.RESOLUTION:
            return [
                "turn_control",
                "query_core_rules",
                "resolve_check",
                "execute_rule",
                "register_rule",
                "update_character_tags",
                "bind_player_character",
                "update_scene",
                "record_timeline_event",
                "clarify_entity_timeline",
                "start_game",
                "list_rules",
                "session_control",
                "estimate_token_usage",
            ]
        base_tools = [
            "update_scene",
            "record_timeline_event",
            "clarify_entity_timeline",
            "update_world_tags",
            "create_character",
            "bind_player_character",
            "update_character_tags",
            "start_game",
            "register_rule",
            "resolve_check",
            "execute_rule",
            "list_rules",
            "session_control",
            "estimate_token_usage",
        ]
        text = (message or "").strip().lower()
        if (
            _contains_any(text, RULE_QUERY_TERMS)
            or _contains_any(text, COMBAT_ACTION_TERMS)
            or _contains_any(text, DM_GUIDANCE_TERMS)
        ):
            base_tools.insert(8, "query_core_rules")
        return base_tools

    @staticmethod
    def _with_loop_control_tools(names: list[str]) -> list[str]:
        selected = list(dict.fromkeys(names))
        for name in ("cycle_control", "final_response"):
            if name not in selected:
                selected.append(name)
        return selected

    @staticmethod
    def _late_join_actor_tool_names(names: list[str]) -> list[str]:
        """Keep unbound post-start players from writing scene state before a character exists."""
        safe = {
            "create_character",
            "bind_player_character",
            "update_character_tags",
            "query_core_rules",
            "list_rules",
            "session_control",
            "estimate_token_usage",
            "search_external_memory",
            "final_response",
        }
        selected = [name for name in names if name in safe]
        for name in ("create_character", "bind_player_character", "query_core_rules", "session_control"):
            if name not in selected:
                selected.append(name)
        return list(dict.fromkeys(selected))

    @staticmethod
    def _with_llm_decided_tools(names: list[str], message: str = "") -> list[str]:
        """Expose expensive/optional tools only when the message makes them useful."""
        selected = list(names)
        text = (message or "").strip().lower()
        if text and _contains_any(text, EXTERNAL_MEMORY_TERMS):
            selected.append("search_external_memory")
        if message and _looks_text_only_request(message):
            return list(dict.fromkeys(selected))
        return add_map_renderer_tools(selected, message)

    @staticmethod
    def _prune_diagnostic_tools(names: list[str], message: str = "") -> list[str]:
        """Keep diagnostic-only tools out of ordinary gameplay tool schemas."""
        selected = list(dict.fromkeys(names))
        text = (message or "").strip().lower()
        if text and _contains_any(text, DIAGNOSTIC_TERMS):
            return selected
        return [name for name in selected if name != "estimate_token_usage"]

    @staticmethod
    def _background_first_tool_names(names: list[str], message: str = "") -> list[str]:
        """Before the campaign background exists, expose only setup-safe tools."""
        allowed = []
        opening_seed = _looks_like_delegated_opening_seed(message)
        visual_map_request = looks_visual_map_request(message)
        legacy_svg_fallback_request = looks_legacy_svg_fallback_request(message)
        for name in names:
            if name in {"update_world_tags", "query_core_rules", "session_control", "estimate_token_usage"} and name not in allowed:
                allowed.append(name)
            if visual_map_request and name in {"render_strict_grid_svg", "render_overview_topology_svg"} and name not in allowed:
                allowed.append(name)
            if (visual_map_request or legacy_svg_fallback_request) and name == "generate_map_svg" and name not in allowed:
                allowed.append(name)
            if opening_seed and name in {"create_character", "bind_player_character", "start_game"} and name not in allowed:
                allowed.append(name)
        if "update_world_tags" not in allowed:
            allowed.insert(0, "update_world_tags")
        elif allowed[0] != "update_world_tags":
            allowed.remove("update_world_tags")
            allowed.insert(0, "update_world_tags")
        if opening_seed:
            for name in ("bind_player_character", "create_character", "start_game"):
                if name in names and name not in allowed:
                    allowed.append(name)
        if "session_control" not in allowed:
            allowed.append("session_control")
        if "estimate_token_usage" not in allowed:
            allowed.append("estimate_token_usage")
        return allowed


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


DIAGNOSTIC_TERMS = ("token", "tokens", "???", "??", "??", "debug", "??", "??", "??", "audit")
EXTERNAL_MEMORY_TERMS = (
    "honcho",
    "????",
    "????",
    "???",
    "??",
    "??",
    "??",
    "??",
    "??",
    "???",
    "??",
    "recap",
    "??",
    "??",
    "??",
    "??",
    "??",
)
MAP_SETUP_TERMS = ("????", "????", "????", "??", "??", "???", "????", "????")
CHARACTER_PROFILE_TERMS = (
    "???",
    "???",
    "????",
    "????",
    "???",
    "????",
    "??????",
    "???",
    "????",
    "???",
    "????",
    "????",
    "????",
    "???",
    "?????",
    "????",
    "???",
    "????",
    "????",
    "??",
    "??",
    "??",
    "????",
    "????",
    "????",
    "????",
    "????",
    "????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??????",
    "??????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "????",
    "??",
    "??????",
    "????",
    "????",
    "????",
    "????",
    "????",
    "???",
    "???",
    "????",
    "????",
    "????",
)
BATTLE_JOIN_TERMS = (
    "???",
    "????",
    "????",
    "????",
    "??????",
    "????",
    "????",
    "???",
    "???",
    "?????",
    "????",
    "???",
    "????",
    "??",
    "??",
    "??",
    "????",
    "????",
    "????",
    "????",
    "????",
    "????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "????",
    "????",
    "????",
    "????",
)
TURN_FLOW_TERMS = (
    "??",
    "????",
    "????",
    "????",
    "???",
    "???",
    "???",
    "??",
    "????",
    "????",
    "??",
    "??",
    "??",
    "??",
    "????",
    "????",
)
BATTLE_RESOLUTION_TERMS = (
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
)
STATE_QUERY_TERMS = (
    "????",
    "????",
    "?????",
    "????",
    "??",
    "????",
    "???",
    "???",
    "????",
    "????",
)
RULE_QUERY_TERMS = (
    "????",
    "?????",
    "????",
    "????",
    "????",
    "???",
    "????",
    "dnd",
    "DND",
    "???",
    "????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "????",
    "??",
    "????",
    "????",
    "????",
    "?????",
)
DM_GUIDANCE_TERMS = (
    "dm??",
    "dm????",
    "????",
    "????",
    "??dm",
    "???dm",
    "????",
    "????",
    "??????",
    "????",
    "????",
    "????",
    "????",
    "??",
    "????",
    "yes and",
    "no but",
    "????",
    "????",
    "??",
    "????",
    "????",
    "????",
    "????",
    "????",
    "???",
    "???",
    "???",
    "??",
    "????",
    "????",
    "????",
    "???",
    "???",
    "??",
    "????",
    "????",
    "????",
    "????",
    "????",
)
COMBAT_ACTION_TERMS = (
    "??",
    "?",
    "?",
    "?",
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
    "??",
    "??",
    "??",
    "??",
    "????",
    "??",
    "????",
    "????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "?????",
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
    "???",
    "??",
    "??",
    "????",
    "??",
    "??",
    "?",
    "??",
    "?",
    "?",
    "?",
    "??",
    "??",
    "????",
    "??",
    "??",
    "??",
    "??",
    "??",
    "??",
    "?",
    "?",
    "?",
    "?",
    "?",
    "??",
    "?",
    "?",
    "??",
    "??",
    "??",
    "??",
    "?",
    "??",
    "??",
    "??",
    "?",
    "?",
)


def _post_game_tool_scope(session: Any, message: str) -> bool:
    scene = session.scene or {}
    if scene.get("_post_game") or scene.get("_encounter_ended_at"):
        return True
    text = _flatten_for_scope(
        [
            scene.get("summary", ""),
            scene.get("current_conflict", ""),
            scene.get("last_resolution", {}),
        ]
    )
    if any(
        term in text
        for term in (
            "????",
            "????",
            "????",
            "???????",
            "???????",
            "????",
            "???????",
        )
    ):
        return True
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    return any(
        term in lowered
        for term in (
            "????",
            "????",
            "????",
            "???",
            "??",
            "???",
            "???",
            "??",
            "??",
            "????",
            "????",
            "????",
            "??????",
            "?????",
            "?????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "????",
            "??????",
        )
    )


def _post_start_unbound_actor_scope(session: Any, actor: dict[str, str] | None) -> bool:
    scene = session.scene or {}
    world_tags = session.world_tags or {}
    campaign_started = bool(
        scene.get("_game_started")
        or scene.get("_legacy_live_campaign")
        or world_tags.get("_plot_locked") is True
    )
    if not campaign_started:
        return False
    player_id = str((actor or {}).get("player_id") or "").strip()
    if not player_id:
        return False
    bound_id = str((session.player_character_map or {}).get(player_id, "") or "").strip()
    return not bool(bound_id and bound_id in (session.characters or {}))


def _post_game_tool_names(message: str) -> list[str]:
    text = str(message or "").strip().lower()
    names = ["query_core_rules", "session_control", "estimate_token_usage"]
    scene_or_rest_terms = (
        "??",
        "??",
        "??",
        "??",
        "??",
        "??",
        "???",
        "??",
        "??",
        "?????",
        "?????",
        "????",
        "????",
        "????",
        "????",
    )
    reset_terms = (
        "????",
        "??",
        "??",
        "reset",
        "confirm_reset",
    )
    post_game_action_terms = (
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
        "??",
        "??",
    )
    if any(term in text for term in scene_or_rest_terms):
        names.insert(0, "update_scene")
    if any(term in text for term in reset_terms):
        return list(dict.fromkeys(names))
    if (
        _contains_any(text, COMBAT_ACTION_TERMS)
        or _contains_any(text, RULE_QUERY_TERMS)
        or any(term in text for term in post_game_action_terms)
    ):
        for name in ("resolve_check", "execute_rule", "update_scene", "update_character_tags"):
            if name not in names:
                names.insert(0, name)
    elif any(
        term in text
        for term in (
            "??",
            "??",
            "buff",
            "debuff",
            "??",
            "??",
            "??",
        )
    ):
        names.insert(0, "update_character_tags")
    return list(dict.fromkeys(names))


def _flatten_for_scope(value: Any) -> str:
    try:
        import json

        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
RULE_AUTHORING_LIKELY_TERMS = (
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
    "??",
)

TEXT_ONLY_TERMS = (
    "token",
    "???",
    "??",
    "??",
    "debug",
    "??",
    "????",
    "?????",
    "????",
    "????",
    "dm??",
    "????",
    "??",
    "??",
    "????",
    "????",
    "???",
    "???",
    "??",
    "????",
    "??",
    "????",
    "????",
    "??",
    "??",
    "??",
    "??",
    "???",
    "???",
    "???",
    "???",
    "??",
    "status",
    "initiative",
    "turn order",
    "order",
    "queue",
    "list",
    "rules",
    "rule list",
)

def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _looks_text_only_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    if looks_text_only_map_request(text):
        return True
    if looks_visual_map_request(text):
        return False
    return _contains_any(text, TEXT_ONLY_TERMS)


def _looks_like_delegated_opening_seed(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    setup_terms = (
        "????",
        "????",
        "??",
        "??",
        "????",
        "????",
        "?????",
        "?????",
        "????",
        "????",
        "????",
        "??",
        "??",
        "??",
    )
    if not any(term in text for term in setup_terms):
        return False
    buckets = 0
    if any(term in text for term in ("??", "???", "??", "??", "??", "??", "??", "??", "??", "??", "????", "??", "??", "??", "??", "??", "???", "??", "??", "???", "??", "dnd", "coc", "d20")):
        buckets += 1
    if any(term in text for term in ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??")):
        buckets += 1
    if any(term in text for term in ("??", "???", "??", "??", "??", "??", "??", "??", "???", "??", "???")):
        buckets += 1
    if any(term in text for term in ("???", "?", "??", "??", "??", "??", "??", "??", "??", "???", "???", "??", "??")):
        buckets += 1
    if any(term in text for term in ("??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??", "??")):
        buckets += 1
    return buckets >= 2 or len(text) >= 30


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        schema = model.model_json_schema()
    else:
        schema = model.schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return schema


def _make_tool_set(tools: list[LocalFunctionTool]) -> ToolSet:
    try:
        return ToolSet(tools)
    except TypeError:
        tool_set = ToolSet()
        setattr(tool_set, "tools", tools)
        return tool_set