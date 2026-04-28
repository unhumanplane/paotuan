from __future__ import annotations

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core.models import GameMode
from ..rules.python_runtime import PythonRuleRuntime
from ..storage.json_repository import JsonGameRepository
from .diagnostic_tools import DiagnosticTools, EstimateTokenUsageArgs
from .map_tools import GenerateMapSvgArgs, MapTools
from .memory_tools import (
    BindPlayerCharacterArgs,
    CreateCharacterArgs,
    MemoryTools,
    SessionControlArgs,
    UpdateCharacterTagsArgs,
    UpdateSceneArgs,
    UpdateWorldTagsArgs,
)
from .rule_tools import ExecuteRuleArgs, ListRulesArgs, RegisterRuleArgs, RuleTools
from .spatial_tools import (
    CheckAttackVectorArgs,
    CreateGridArgs,
    MoveEntityArgs,
    PlaceEntityArgs,
    SpatialTools,
)
from .turn_tools import TurnControlArgs, TurnTools


ToolHandler = Callable[..., Awaitable[dict[str, Any]]]


class EmptyArgs(BaseModel):
    pass


@dataclass
class LocalFunctionTool(FunctionTool[AstrAgentContext]):
    name: str
    description: str
    parameters: dict
    handler: ToolHandler

    def __post_init__(self) -> None:
        self.validate_parameters()

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
    ):
        self.repository = repository
        self.rule_runtime = rule_runtime
        self.astr_context = astr_context

    def for_mode(
        self,
        mode: GameMode,
        session_id: str,
        actor: dict[str, str] | None = None,
        message: str = "",
        provider_id: str = "",
    ) -> tuple[ToolSet, list[str], LocalToolExecutor, list[dict[str, Any]]]:
        rule_tools = RuleTools(self.repository, self.rule_runtime, session_id)
        memory_tools = MemoryTools(self.repository, session_id, actor=actor)
        spatial_tools = SpatialTools(self.repository, session_id, actor=actor)
        turn_tools = TurnTools(self.repository, session_id, actor=actor)
        diagnostic_tools = DiagnosticTools(self.repository, session_id)
        map_tools = MapTools(
            repository=self.repository,
            session_id=session_id,
            astr_context=self.astr_context,
            provider_id=provider_id,
        )

        catalog: dict[str, LocalFunctionTool] = {
            "register_rule": make_tool(
                name="register_rule",
                description="注册一条新的 TRPG 纯计算规则。只有缺少对应规则或玩家正在设定机制时使用。",
                model=RegisterRuleArgs,
                handler=rule_tools.register_rule,
            ),
            "execute_rule": make_tool(
                name="execute_rule",
                description="执行已注册规则，用于检定、伤害、资源消耗和随机判定。",
                model=ExecuteRuleArgs,
                handler=rule_tools.execute_rule,
            ),
            "list_rules": make_tool(
                name="list_rules",
                description="列出当前已经注册的规则。默认返回二级摘要；只有需要入参/出参详情时才用 detail。",
                model=ListRulesArgs,
                handler=rule_tools.list_rules,
            ),
            "create_character": make_tool(
                name="create_character",
                description="创建或覆盖一个无模式 Tag 角色卡；player_id 为空时绑定当前发言人。",
                model=CreateCharacterArgs,
                handler=memory_tools.create_character,
            ),
            "bind_player_character": make_tool(
                name="bind_player_character",
                description="把当前发言人或指定玩家绑定到角色；多人跑团中处理“我是谁/我加入/这是我的角色”时使用。",
                model=BindPlayerCharacterArgs,
                handler=memory_tools.bind_player_character,
            ),
            "update_character_tags": make_tool(
                name="update_character_tags",
                description="新增或覆盖角色 Tag。优先传结构化 tags；若玩家刚用自然语言补充职业、专长、装备、风格、弱点或默认战斗行为，可把原文放入 raw_text 由本地兜底解析。不要用它直接修改战棋坐标。",
                model=UpdateCharacterTagsArgs,
                handler=memory_tools.update_character_tags,
            ),
            "update_scene": make_tool(
                name="update_scene",
                description="更新当前场景、冲突、地点、NPC 摘要等叙事状态。",
                model=UpdateSceneArgs,
                handler=memory_tools.update_scene,
            ),
            "update_world_tags": make_tool(
                name="update_world_tags",
                description="更新世界设定、剧本风格、势力、地点等长期状态。",
                model=UpdateWorldTagsArgs,
                handler=memory_tools.update_world_tags,
            ),
            "session_control": make_tool(
                name="session_control",
                description="会话控制工具：查询状态、重开当前会话、压缩记忆、查看最近调试记录。",
                model=SessionControlArgs,
                handler=memory_tools.session_control,
            ),
            "create_grid": make_tool(
                name="create_grid",
                description="创建或重置当前战棋地图，并进入战棋模式。",
                model=CreateGridArgs,
                handler=spatial_tools.create_grid,
            ),
            "place_entity": make_tool(
                name="place_entity",
                description="在战棋地图上放置一个实体。",
                model=PlaceEntityArgs,
                handler=spatial_tools.place_entity,
            ),
            "move_entity": make_tool(
                name="move_entity",
                description="移动实体；底层严格校验坐标、障碍、占位、路径和移动力。",
                model=MoveEntityArgs,
                handler=spatial_tools.move_entity,
            ),
            "check_attack_vector": make_tool(
                name="check_attack_vector",
                description="检查攻击向量，包括攻击距离、视线遮挡和掩体；不会直接造成伤害。",
                model=CheckAttackVectorArgs,
                handler=spatial_tools.check_attack_vector,
            ),
            "get_battle_snapshot": make_tool(
                name="get_battle_snapshot",
                description="获取当前战棋状态快照。",
                model=EmptyArgs,
                handler=spatial_tools.get_battle_snapshot,
            ),
            "turn_control": make_tool(
                name="turn_control",
                description="控制战斗轮动状态：场面结算、角色回合、行动顺序、推进下一位、120 秒超时、无人响应自动保守行动。",
                model=TurnControlArgs,
                handler=turn_tools.turn_control,
            ),
            "generate_map_svg": make_tool(
                name="generate_map_svg",
                description="当玩家明确或上下文明显需要视觉地图、战场示意、地形草图或 SVG 输出时使用；用独立 LLM 子上下文生成 SVG 并保存为文件。只生成视觉层，不改变物理网格、坐标、移动或视线事实。",
                model=GenerateMapSvgArgs,
                handler=map_tools.generate_map_svg,
            ),
        }

        def specs_for_mode(target_mode: GameMode) -> tuple[list[str], list[dict[str, Any]]]:
            names = self._with_llm_decided_tools(self._allowed_tool_names(target_mode))
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
            description="估算当前跑团上下文、快照、audit、system prompt 和 Function Tool schema 的 token/字符消耗；用于玩家询问 token、上下文、压缩状态。",
            model=EstimateTokenUsageArgs,
            handler=diagnostic_tools.estimate_token_usage,
        )
        diagnostic_tools.set_tool_specs_provider(specs_for_mode)

        allowed = self._with_llm_decided_tools(self._allowed_tool_names(mode, message=message))
        selected = {name: catalog[name] for name in allowed}
        specs = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in selected.values()
        ]
        return ToolSet(list(selected.values())), allowed, LocalToolExecutor(selected), specs

    @staticmethod
    def _allowed_tool_names(mode: GameMode, message: str = "") -> list[str]:
        if mode == GameMode.CHARACTER_CREATION:
            return [
                "create_character",
                "bind_player_character",
                "update_character_tags",
                "update_world_tags",
                "register_rule",
                "execute_rule",
                "list_rules",
                "session_control",
                "estimate_token_usage",
            ]
        if mode == GameMode.RULE_AUTHORING:
            return [
                "register_rule",
                "execute_rule",
                "list_rules",
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
                    "generate_map_svg",
                    "turn_control",
                    "create_grid",
                    "place_entity",
                    "move_entity",
                    "check_attack_vector",
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "execute_rule",
                    "register_rule",
                    "update_scene",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, BATTLE_JOIN_TERMS):
                return [
                    "get_battle_snapshot",
                    "turn_control",
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, CHARACTER_PROFILE_TERMS):
                return [
                    "create_character",
                    "bind_player_character",
                    "update_character_tags",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, TURN_FLOW_TERMS):
                return [
                    "get_battle_snapshot",
                    "turn_control",
                    "bind_player_character",
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
                    "execute_rule",
                    "register_rule",
                    "list_rules",
                    "session_control",
                    "estimate_token_usage",
                ]
            if _contains_any(text, COMBAT_ACTION_TERMS):
                tools = [
                    "get_battle_snapshot",
                    "turn_control",
                    "move_entity",
                    "check_attack_vector",
                    "execute_rule",
                    "update_scene",
                    "session_control",
                    "estimate_token_usage",
                ]
                if _contains_any(text, RULE_AUTHORING_LIKELY_TERMS):
                    tools.insert(6, "register_rule")
                return tools
            return [
                "get_battle_snapshot",
                "turn_control",
                "session_control",
                "estimate_token_usage",
            ]
        if mode == GameMode.RESOLUTION:
            return [
                "turn_control",
                "execute_rule",
                "register_rule",
                "update_character_tags",
                "bind_player_character",
                "update_scene",
                "list_rules",
                "session_control",
                "estimate_token_usage",
            ]
        base_tools = [
            "update_scene",
            "update_world_tags",
            "create_character",
            "bind_player_character",
            "update_character_tags",
            "register_rule",
            "execute_rule",
            "list_rules",
            "session_control",
            "estimate_token_usage",
        ]
        return base_tools

    @staticmethod
    def _with_llm_decided_tools(names: list[str]) -> list[str]:
        """Expose lightweight visual generation to the LLM without keyword gating."""
        if "generate_map_svg" in names:
            return names
        return [*names, "generate_map_svg"]


DIAGNOSTIC_TERMS = ("token", "上下文", "压缩", "调试", "debug", "日志", "消耗", "预算")
MAP_SETUP_TERMS = ("创建地图", "重置地图", "生成地图", "放置", "摆放", "开战棋", "布置地图", "设置地图")
CHARACTER_PROFILE_TERMS = (
    "人物卡",
    "角色卡",
    "职业",
    "种族",
    "专长",
    "风格",
    "装备",
    "能力加入",
    "补充",
    "默认战斗行为",
    "战斗习惯",
    "主武器",
    "常用法术",
    "次要法术",
    "次级法术",
)
BATTLE_JOIN_TERMS = ("我加入", "加入队伍", "加入战场", "参战", "绑定角色", "为我绑定", "排入战队", "排入战斗")
TURN_FLOW_TERMS = (
    "轮动",
    "行动顺序",
    "战斗顺序",
    "开始回合",
    "开始轮",
    "下一位",
    "下一个",
    "跳过",
    "无人响应",
    "没人响应",
    "超时",
    "自动",
    "继续",
    "推进",
    "开始结算",
    "场面结算",
)
STATE_QUERY_TERMS = (
    "我在哪里",
    "什么状态",
    "状态怎么样",
    "当前状态",
    "战况",
    "地图情况",
    "谁行动",
    "轮到谁",
    "当前位置",
    "还在不在",
)
RULE_QUERY_TERMS = ("规则列表", "有哪些规则", "已有规则", "规则详情", "怎么判定", "怎么骰", "骰子规则")
COMBAT_ACTION_TERMS = (
    "移动",
    "走",
    "冲",
    "撤",
    "靠近",
    "绕",
    "攻击",
    "射击",
    "点射",
    "近战",
    "猛攻",
    "施法",
    "火球",
    "治疗",
    "圣光",
    "嘲讽",
    "掩护",
    "防御",
    "闪避",
    "目标",
    "敌",
    "怪",
)
RULE_AUTHORING_LIKELY_TERMS = (
    "检定",
    "判定",
    "伤害",
    "命中",
    "豁免",
    "骰",
    "攻击",
    "射击",
    "点射",
    "近战",
    "猛攻",
    "施法",
    "治疗",
    "圣光",
    "火球",
    "嘲讽",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        schema = model.model_json_schema()
    else:
        schema = model.schema()
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return schema
