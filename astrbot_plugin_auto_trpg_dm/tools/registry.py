from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.astr_agent_context import AstrAgentContext

from ..core.models import GameMode
from ..rules.python_runtime import PythonRuleRuntime
from ..storage.json_repository import JsonGameRepository
from .cycle_tools import CycleControlArgs, CycleTools
from .diagnostic_tools import DiagnosticTools, EstimateTokenUsageArgs
from .external_memory_tools import ExternalMemoryTools, SearchExternalMemoryArgs
from .map_tools import GenerateMapSvgArgs, MapTools
from .memory_tools import (
    BindPlayerCharacterArgs,
    CreateCharacterArgs,
    MemoryTools,
    SessionControlArgs,
    StartGameArgs,
    UpdateCharacterTagsArgs,
    UpdateSceneArgs,
    UpdateWorldTagsArgs,
    has_campaign_background,
)
from .rule_tools import ExecuteRuleArgs, ListRulesArgs, RegisterRuleArgs, RuleTools
from .rulebook_tools import QueryCoreRulesArgs, RulebookTools
from .spatial_tools import (
    CheckAttackVectorArgs,
    CreateGridArgs,
    MoveEntityArgs,
    PlaceEntityArgs,
    SpatialTools,
)
from .turn_tools import TurnControlArgs, TurnTools


ToolHandler = Callable[..., Awaitable[Dict[str, Any]]]


class EmptyArgs(BaseModel):
    pass


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
                description="列出当前已经注册的规则。默认返回摘要；需要入参/出参时按 tag 查 detail，不要重复同参数查询。",
                model=ListRulesArgs,
                handler=rule_tools.list_rules,
            ),
            "query_core_rules": make_tool(
                name="query_core_rules",
                description="查询 DND 2024 核心规则和 DM 指引摘要；用于动作经济、战斗、状态、伤害治疗、通用施法、通用装备、共同故事、桌面边界、即兴答复、后果和 DM 裁定。只返回少量只读规则卡，不写入跑团状态；数值、骰子和随机结果仍需 execute_rule。",
                model=QueryCoreRulesArgs,
                handler=rulebook_tools.query_core_rules,
            ),
            "create_character": make_tool(
                name="create_character",
                description="创建一个无模式 Tag 角色卡；player_id 为空时绑定当前发言人。开场后不能覆盖旧卡；原角色已死亡/退场时可用于创建后继角色。",
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
            "start_game": make_tool(
                name="start_game",
                description="当玩家要求开始游戏、开场或进入剧情时使用。先检查背景、角色、开场介绍和跌宕剧情骨架是否足够；足够才正式开场并锁定剧情主干。开场后仍允许新玩家加入。",
                model=StartGameArgs,
                handler=memory_tools.start_game,
            ),
            "session_control": make_tool(
                name="session_control",
                description="会话控制工具：查询状态、备份存档、列出备份、在当前档为空时恢复上一个非空备份、重开当前会话、压缩记忆、查看最近调试记录。重开/清空存档必须先获取确认码，再用 confirm_reset 和 confirm_token 二次确认。",
                model=SessionControlArgs,
                handler=memory_tools.session_control,
            ),
            "search_external_memory": make_tool(
                name="search_external_memory",
                description="按需检索 Honcho 外置记忆，用于玩家询问旧事件、旧关系、玩家偏好、上一章 recap、未解决伏笔或角色长期倾向。返回内容只作非权威回忆线索；不得用它覆盖 HP、物品、位置、轮次、规则、骰子或工具结果。",
                model=SearchExternalMemoryArgs,
                handler=external_memory_tools.search_external_memory,
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
                description="控制战斗轮动状态：场面结算、角色回合、行动顺序、本轮乱序行动记录、推进下一建议行动者、120 秒超时、无人响应自动保守行动。",
                model=TurnControlArgs,
                handler=turn_tools.turn_control,
            ),
            "cycle_control": make_tool(
                name="cycle_control",
                description='显式结束当前叙事周期。MVP 仅支持 action="end_cycle"；不要用完成文本或猜测来结束周期。',
                model=CycleControlArgs,
                handler=cycle_tools.cycle_control,
            ),
            "generate_map_svg": make_tool(
                name="generate_map_svg",
                description="当玩家明确或上下文明显需要视觉地图、战场示意、地形草图或 SVG 输出时使用；用独立 LLM 子上下文生成 SVG 并保存为文件。只生成视觉层，不改变物理网格、坐标、移动或视线事实。",
                model=GenerateMapSvgArgs,
                handler=map_tools.generate_map_svg,
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
            if "cycle_control" not in names:
                names.append("cycle_control")
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

        session = self.repository.load_session(session_id)
        allowed = self._with_llm_decided_tools(self._allowed_tool_names(mode, message=message), message=message)
        if _post_game_tool_scope(session, message):
            allowed = self._with_llm_decided_tools(_post_game_tool_names(message), message=message)
        if not has_campaign_background(session):
            allowed = self._background_first_tool_names(allowed, message=message)
        allowed = self._prune_diagnostic_tools(allowed, message=message)
        if "cycle_control" not in allowed:
            allowed.append("cycle_control")
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
                "start_game",
                "register_rule",
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
            if _contains_any(text, BATTLE_RESOLUTION_TERMS):
                return [
                    "get_battle_snapshot",
                    "turn_control",
                    "query_core_rules",
                    "execute_rule",
                    "update_scene",
                    "update_character_tags",
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
                    "turn_control",
                    "query_core_rules",
                    "move_entity",
                    "check_attack_vector",
                    "execute_rule",
                    "update_scene",
                    "update_character_tags",
                    "session_control",
                    "estimate_token_usage",
                ]
                if _contains_any(text, RULE_AUTHORING_LIKELY_TERMS):
                    tools.insert(6, "register_rule")
                return tools
            if _contains_any(text, TURN_FLOW_TERMS):
                return [
                    "get_battle_snapshot",
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
                "execute_rule",
                "register_rule",
                "update_character_tags",
                "bind_player_character",
                "update_scene",
                "start_game",
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
            "start_game",
            "register_rule",
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
    def _with_llm_decided_tools(names: list[str], message: str = "") -> list[str]:
        """Expose expensive/optional tools only when the message makes them useful."""
        selected = list(names)
        text = (message or "").strip().lower()
        if text and _contains_any(text, EXTERNAL_MEMORY_TERMS):
            selected.append("search_external_memory")
        if "generate_map_svg" in selected:
            return list(dict.fromkeys(selected))
        if message and _looks_text_only_request(message):
            return list(dict.fromkeys(selected))
        return list(dict.fromkeys([*selected, "generate_map_svg"]))

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
        for name in names:
            if name in {"update_world_tags", "query_core_rules", "session_control", "estimate_token_usage"} and name not in allowed:
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


DIAGNOSTIC_TERMS = ("token", "tokens", "上下文", "压缩", "调试", "debug", "日志", "消耗", "预算", "audit")
EXTERNAL_MEMORY_TERMS = (
    "honcho",
    "外置记忆",
    "长期记忆",
    "还记得",
    "记得",
    "回忆",
    "以前",
    "之前",
    "上次",
    "上一章",
    "前情",
    "recap",
    "关系",
    "偏好",
    "伏笔",
    "旧事",
    "过去",
)
MAP_SETUP_TERMS = ("创建地图", "重置地图", "生成地图", "放置", "摆放", "开战棋", "布置地图", "设置地图")
CHARACTER_PROFILE_TERMS = (
    "人物卡",
    "角色卡",
    "建立角色",
    "创建角色",
    "建角色",
    "新角色",
    "新角色加入",
    "换新角色",
    "换角色",
    "重建角色",
    "重建人物",
    "新号",
    "补位",
    "替补",
    "后继角色",
    "重新加入",
    "重新进团",
    "重新入团",
    "角色死了",
    "角色死亡",
    "死亡",
    "阵亡",
    "已死",
    "退场",
    "退休",
    "加入一个角色",
    "帮我建立角色",
    "职业",
    "种族",
    "专长",
    "风格",
    "装备",
    "能力加入",
    "补充",
    "默认战斗行为",
    "战斗习惯",
    "默认攻击",
    "默认行动",
    "行动策略",
    "战斗策略",
    "请记住",
    "主武器",
    "常用法术",
    "次要法术",
    "次级法术",
)
BATTLE_JOIN_TERMS = (
    "我加入",
    "我要加入",
    "加入队伍",
    "加入战场",
    "加入一个角色",
    "建立角色",
    "创建角色",
    "建角色",
    "新角色",
    "新角色加入",
    "换新角色",
    "换角色",
    "重建角色",
    "新号",
    "补位",
    "替补",
    "后继角色",
    "重新加入",
    "重新进团",
    "重新入团",
    "角色死了",
    "角色死亡",
    "死亡",
    "阵亡",
    "已死",
    "退场",
    "退休",
    "参战",
    "绑定角色",
    "为我绑定",
    "排入战队",
    "排入战斗",
)
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
BATTLE_RESOLUTION_TERMS = (
    "结算战斗",
    "战斗结算",
    "结束战斗",
    "结束遭遇",
    "遭遇结束",
    "结束本场",
    "战斗结束",
    "清算战场",
    "收尾战斗",
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
RULE_QUERY_TERMS = (
    "规则列表",
    "有哪些规则",
    "已有规则",
    "规则详情",
    "怎么判定",
    "怎么骰",
    "骰子规则",
    "dnd",
    "DND",
    "规则书",
    "核心规则",
    "优势",
    "劣势",
    "豁免",
    "命中",
    "倒地",
    "束缚",
    "中毒",
    "目盲",
    "躲藏",
    "附赠动作",
    "反应",
    "借机攻击",
    "机会攻击",
    "临时生命",
    "生命值归零",
)
DM_GUIDANCE_TERMS = (
    "dm职责",
    "dm是做什么",
    "城主职责",
    "地下城主",
    "称职dm",
    "称职的dm",
    "共同故事",
    "不是竞争",
    "不要对抗玩家",
    "公平裁定",
    "灵活裁定",
    "临时裁定",
    "怎么裁定",
    "即兴",
    "即兴答复",
    "yes and",
    "no but",
    "可以而且",
    "不能但是",
    "后果",
    "失败推进",
    "成功代价",
    "部分成功",
    "桌面安全",
    "相互尊重",
    "硬边界",
    "软边界",
    "不舒服",
    "越界",
    "尊重玩家",
    "了解玩家",
    "玩家偏好",
    "游戏性",
    "娱乐性",
    "叙事",
    "简短叙述",
    "氛围描写",
    "战斗叙述",
    "战斗描写",
    "战术信息",
)
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
    "附赠动作",
    "反应",
    "借机攻击",
    "机会攻击",
    "优势",
    "劣势",
    "倒地",
    "束缚",
    "中毒",
    "目盲",
    "生命值归零",
    "侦察",
    "观察",
    "查看",
    "搜索",
    "调查",
    "寻找",
    "警戒",
    "守望",
    "潜伏",
    "潜行",
    "询问",
    "打听",
    "沟通",
    "分享",
    "索要",
    "索取",
    "饮用",
    "喝",
    "草药",
    "凝神花",
    "补给",
    "消耗",
    "英勇启发",
    "资源",
    "投掷",
    "扔",
    "扔进",
    "推",
    "拖",
    "拉",
    "擒抱",
    "抓取",
    "强制位移",
    "推入",
    "推下",
    "投进",
    "要害",
    "古井",
    "挥砍",
    "砍",
    "斩",
    "劈",
    "刺",
    "击",
    "跳起",
    "听",
    "盯",
    "发现",
    "注意",
    "检定",
    "判定",
    "骰",
    "装填",
    "待射",
    "目标",
    "敌",
    "怪",
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
            "圆满落幕",
            "圆满结束",
            "正式落幕",
            "危机已正式解除",
            "危机已落下帷幕",
            "暂无冲突",
            "当前冲突：暂无",
        )
    ):
        return True
    lowered = str(message or "").strip().lower()
    if not lowered:
        return False
    return any(
        term in lowered
        for term in (
            "全局结算",
            "结束游戏",
            "个人结局",
            "后日谈",
            "尾声",
            "谁最菜",
            "谁最强",
            "评价",
            "评估",
            "职业等级",
            "传奇等级",
            "休息一会",
            "背景剧情描述",
            "下一段冒险",
            "下一次冒险",
            "下次冒险",
            "下个冒险",
            "下回冒险",
            "沉睡直到",
            "沉睡到下",
            "休眠直到",
            "休眠到下",
            "直到下次",
            "无人可以打扰",
        )
    )


def _post_game_tool_names(message: str) -> list[str]:
    text = str(message or "").strip().lower()
    names = ["query_core_rules", "session_control", "estimate_token_usage"]
    scene_or_rest_terms = (
        "背景",
        "间幕",
        "休息",
        "休整",
        "沉睡",
        "休眠",
        "后日谈",
        "尾声",
        "结局",
        "下一段冒险",
        "下一次冒险",
        "下次冒险",
        "下个冒险",
        "下回冒险",
        "直到下次",
    )
    reset_terms = (
        "结束游戏",
        "重开",
        "清空",
        "reset",
        "confirm_reset",
    )
    post_game_action_terms = (
        "行动",
        "检定",
        "判定",
        "裁定",
        "摧毁",
        "破坏",
        "清理",
        "整理",
        "采集",
        "收集",
        "种植",
        "育苗",
        "帮忙",
        "辅助",
        "托管",
        "挂机",
        "默认",
        "预设",
        "跟随",
        "扫射",
        "开火",
        "挥剑",
        "挥动",
        "注入",
        "蛊惑",
        "说服",
        "保护费",
        "恢复",
        "疗伤",
        "短休",
        "长休",
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
        for name in ("execute_rule", "update_scene", "update_character_tags"):
            if name not in names:
                names.insert(0, name)
    elif any(
        term in text
        for term in (
            "状态",
            "负面",
            "buff",
            "debuff",
            "资源",
            "生命",
            "背包",
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

TEXT_ONLY_TERMS = (
    "token",
    "上下文",
    "压缩",
    "调试",
    "debug",
    "日志",
    "规则列表",
    "有哪些规则",
    "已有规则",
    "规则详情",
    "dm职责",
    "城主职责",
    "即兴",
    "后果",
    "桌面安全",
    "玩家偏好",
    "游戏性",
    "娱乐性",
    "叙事",
    "战斗叙述",
    "状态",
    "当前状态",
    "行动顺序",
    "顺序",
    "队列",
    "轮次",
    "回合",
    "谁行动",
    "轮到谁",
    "人物卡",
    "角色卡",
    "属性",
    "status",
    "initiative",
    "turn order",
    "order",
    "queue",
    "list",
    "rules",
    "rule list",
)

VISUAL_REQUEST_TERMS = (
    "画",
    "绘制",
    "生成地图",
    "地图",
    "示意图",
    "站位图",
    "俯视",
    "可视化",
    "标出来",
    "svg",
    "map",
    "draw",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _looks_text_only_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    if _contains_any(text, VISUAL_REQUEST_TERMS):
        return False
    return _contains_any(text, TEXT_ONLY_TERMS)


def _looks_like_delegated_opening_seed(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    setup_terms = (
        "开始游戏",
        "正式开始",
        "开局",
        "开场",
        "进入剧情",
        "进入正片",
        "补完后开始",
        "补全后开始",
        "智能补完",
        "不用多问",
        "直接开始",
        "故事",
        "剧本",
        "副本",
    )
    if not any(term in text for term in setup_terms):
        return False
    buckets = 0
    if any(term in text for term in ("异界", "异世界", "穿越", "重生", "末世", "废土", "核战", "修仙", "仙侠", "文明", "文明重建", "科幻", "奇幻", "玄幻", "现代", "赛博", "克苏鲁", "悬疑", "武侠", "中世纪", "历史", "dnd", "coc", "d20")):
        buckets += 1
    if any(term in text for term in ("经营", "种田", "后宫", "宫斗", "调查", "求生", "冒险", "恐怖", "轻松", "日常", "荒诞", "宏大", "悲剧", "失败", "黑暗", "热血", "温馨")):
        buckets += 1
    if any(term in text for term in ("我是", "我们是", "扮演", "担任", "店长", "队长", "领主", "学生", "调查员", "佣兵", "冒险者")):
        buckets += 1
    if any(term in text for term in ("咖啡馆", "店", "酒馆", "城市", "村庄", "王国", "宫廷", "学院", "宗门", "地下城", "空间站", "港口", "领地")):
        buckets += 1
    if any(term in text for term in ("店员", "猫娘", "贵族", "敌人", "怪物", "组织", "势力", "公司", "教团", "军团", "帮派", "派系")):
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
