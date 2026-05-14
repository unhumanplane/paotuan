import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone


def _install_fake_astrbot_modules():
    if "astrbot.core.agent.tool" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    core = types.ModuleType("astrbot.core")
    agent = types.ModuleType("astrbot.core.agent")
    run_context = types.ModuleType("astrbot.core.agent.run_context")
    tool = types.ModuleType("astrbot.core.agent.tool")
    astr_agent_context = types.ModuleType("astrbot.core.astr_agent_context")

    class FakeContextWrapper:
        def __class_getitem__(cls, item):
            return cls

    class FakeFunctionTool:
        def __class_getitem__(cls, item):
            return cls

        def validate_parameters(self):
            return None

    class FakeToolSet:
        def __init__(self, tools):
            self.tools = tools

    class FakeAstrAgentContext:
        pass

    run_context.ContextWrapper = FakeContextWrapper
    tool.FunctionTool = FakeFunctionTool
    tool.ToolSet = FakeToolSet
    astr_agent_context.AstrAgentContext = FakeAstrAgentContext

    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.core"] = core
    sys.modules["astrbot.core.agent"] = agent
    sys.modules["astrbot.core.agent.run_context"] = run_context
    sys.modules["astrbot.core.agent.tool"] = tool
    sys.modules["astrbot.core.astr_agent_context"] = astr_agent_context


_install_fake_astrbot_modules()

from astrbot_plugin_auto_trpg_dm.core.ambient_image import AmbientImageConfig
from astrbot_plugin_auto_trpg_dm.core.map_core import DEFAULT_STRICT_LOCAL_MAP_ID, save_active_strict_grid
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.router import (
    IntentRouter,
    _adjudication_completeness_guard,
    _extract_llm_usage_summary,
    _is_diagnostic_request,
    _should_record_narrative_trace,
)


def test_extract_llm_usage_summary_reads_openai_cached_tokens():
    summary = _extract_llm_usage_summary(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 64},
            },
            "completion_text": "raw answer should not be copied",
        }
    )

    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 20
    assert summary["total_tokens"] == 120
    assert summary["cached_tokens"] == 64
    assert summary["cache_hit_ratio_pct"] == 64.0
    assert "completion_text" not in summary


def test_fact_check_correction_does_not_become_narrative_trace():
    assert _should_record_narrative_trace(
        "不是，DM前面记错了，她们不是已经在打雇佣兵吗？",
        "她们还在老柳树下谈着，佣兵还没碰上一根手指头。",
    ) is False


def test_extract_llm_usage_summary_reads_object_usage():
    class Usage:
        prompt_tokens = "160"
        completion_tokens = 40
        prompt_tokens_details = {"cached_tokens": "120"}

    class Response:
        usage = Usage()

    summary = _extract_llm_usage_summary(Response())

    assert summary["prompt_tokens"] == 160
    assert summary["completion_tokens"] == 40
    assert summary["cached_tokens"] == 120
    assert summary["cache_hit_ratio_pct"] == 75.0


def test_diagnostic_request_excludes_fact_check_corrections():
    assert _is_diagnostic_request("查日志，DM记错了，修正剧情") is False
    assert _is_diagnostic_request("看一下日志和token消耗") is True


def test_extract_llm_usage_summary_reads_anthropic_cache_fields():
    summary = _extract_llm_usage_summary(
        {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 30,
                "cache_read_input_tokens": 150,
                "cache_creation_input_tokens": 25,
            }
        }
    )

    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 30
    assert summary["cache_read_input_tokens"] == 150
    assert summary["cache_creation_input_tokens"] == 25
    assert summary["cache_hit_ratio_pct"] == 75.0


def test_start_game_arg_repair_coerces_json_string_outline():
    repaired = IntentRouter._repair_tool_args(
        "start_game",
        {
            "opening_intro": "开场文字",
            "campaign_outline": '{"act_1":"导火索","act_2":"升级","act_3":"高潮"}',
            "scene_patch": "废弃枢纽站里传来爪刃声。",
        },
        "开始游戏",
    )

    assert repaired["campaign_outline"]["act_1"] == "导火索"
    assert repaired["scene_patch"]["summary"] == "废弃枢纽站里传来爪刃声。"


def test_router_cleans_menu_like_guidance_before_return_and_audit():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.scene["_game_started"] = True
    astr_context = FakeAstrContext(
        "门缝里透出冷蓝色的光，里面有人压低声音提到巡逻换岗。\n\n"
        "你可以选择：\n"
        "1. 继续偷听\n"
        "2. 敲门试探\n"
        "3. 直接离开"
    )
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我调查门缝")))
    records = repository.last_audit_records("group-1", limit=20)
    handled = [item for item in records if item.get("type") == "message_handled"]
    cleanup = [item for item in records if item.get("type") == "outbound_menu_guidance_cleaned"]

    assert "冷蓝色的光" in reply
    assert "你可以选择" not in reply
    assert "继续偷听" not in reply
    assert handled[-1]["completion"] == reply
    assert cleanup[-1]["removed_blocks"] == 1
    assert "original_hash" in cleanup[-1]
    assert "cleaned_hash" in cleanup[-1]


def test_router_preserves_setup_suggestions_before_game_start():
    repository = InMemoryRepository()
    astr_context = FakeAstrContext(
        "好的，背景已准备就绪。\n\n"
        "**《夜语者》**\n\n"
        "**背景设定：**\n"
        "一座不眠的现代都市，新京市。\n\n"
        "**建议角色方向：**\n"
        "- 通灵者\n"
        "- 隐秘社团成员\n"
        "- 被诅咒之人"
    )
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("来一个现代背景的跑团：")))
    records = repository.last_audit_records("group-1", limit=20)

    assert "**建议角色方向：**" in reply
    assert "通灵者" in reply
    assert not any(item.get("type") == "outbound_menu_guidance_cleaned" for item in records)


def test_router_skips_cleanup_for_diagnostic_completion():
    repository = InMemoryRepository()
    completion = "Token 粗算：1. prompt=100；2. completion=20；3. total=120。"
    astr_context = FakeAstrContext(completion)
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("debug token 详细")))
    records = repository.last_audit_records("group-1", limit=20)

    assert reply == completion
    assert not any(item.get("type") == "outbound_menu_guidance_cleaned" for item in records)


def test_router_semantic_judge_deletes_ambiguous_tail_menu():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.scene["_game_started"] = True
    astr_context = FakeAstrContext(
        "门后的锁孔里透出蓝光，金属链条在里面轻轻晃动。\n\n"
        "你是指：研究机关？还是询问守卫？或者同时？",
        '{"classification":"closed_player_options","action":"delete_candidate","confidence":0.91,"reason":"候选文本是：你是指：研究机关？还是询问守卫？或者同时？"}',
        '{"ok":true,"needs_repair":false,"issues":[],"safe_patches":{},"player_correction":""}',
    )
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我看看门")))
    records = repository.last_audit_records("group-1", limit=30)
    reviewed = [item for item in records if item.get("type") == "outbound_menu_guidance_semantic_reviewed"]
    cleaned = [item for item in records if item.get("type") == "outbound_menu_guidance_cleaned"]
    handled = [item for item in records if item.get("type") == "message_handled"]

    assert len(astr_context.calls) == 3
    assert reply == "门后的锁孔里透出蓝光，金属链条在里面轻轻晃动。"
    assert "你是指" not in reply
    assert reviewed[-1]["classification"] == "closed_player_options"
    assert reviewed[-1]["action"] == "delete_candidate"
    assert "candidate_hash" in reviewed[-1]
    assert "candidate_text" not in reviewed[-1]
    assert "研究机关" not in reviewed[-1]["reason"]
    assert cleaned[-1]["semantic_classification"] == "closed_player_options"
    assert handled[-1]["completion"] == reply
    assert any(item.get("type") == "continuity_audit_reviewed" for item in records)


def test_router_semantic_judge_keeps_necessary_clarification():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.scene["_game_started"] = True
    completion = "雾里有两道身影。\n\n你是指左边披斗篷的人？还是右边拿灯的人？"
    astr_context = FakeAstrContext(
        completion,
        '{"classification":"necessary_clarification","action":"keep","confidence":0.88,"reason":"asks target identity"}',
        '{"ok":true,"needs_repair":false,"issues":[],"safe_patches":{},"player_correction":""}',
    )
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我盯着那个人")))
    records = repository.last_audit_records("group-1", limit=30)
    reviewed = [item for item in records if item.get("type") == "outbound_menu_guidance_semantic_reviewed"]

    assert len(astr_context.calls) == 3
    assert reply == completion
    assert reviewed[-1]["classification"] == "necessary_clarification"
    assert reviewed[-1]["action"] == "keep"
    assert not any(
        item.get("type") == "outbound_menu_guidance_cleaned"
        and item.get("semantic_classification") == "necessary_clarification"
        for item in records
    )
    assert any(item.get("type") == "continuity_audit_reviewed" for item in records)


def test_router_projects_tool_results_before_returning_to_dm_context():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["generate_map_svg"]
        tools_call_args = [{"title": "北门", "prompt": "raw prompt"}]
        tool_calls = []

    class RawResultToolExecutor:
        async def execute(self, tool_name, args):
            return {
                "ok": True,
                "message": "地图已生成。",
                "file_path": "D:/runtime/maps/north.svg",
                "url": "https://example.invalid/north.svg",
                "raw_svg": "raw secret svg",
                "debug": "debug trace",
            }

    class FakeLoopLlm:
        def __init__(self):
            self.second_call_contexts = []

        async def __call__(self, **kwargs):
            if "func_tool" in kwargs:
                return FakeToolCallResponse()
            self.second_call_contexts = kwargs["contexts"]
            return FakeLlmResponse("最终叙事。")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=RawResultToolExecutor(),
            session_id="group-1",
            raw_player_message="我要看地图",
        )
        return result, llm.second_call_contexts

    result, contexts = asyncio.run(run_case())
    tool_context = contexts[-1]["content"]

    assert result.completion_text == "最终叙事。"
    assert "本轮工具返回（已投影" in tool_context
    assert "地图已生成" in tool_context
    assert "raw secret svg" not in tool_context
    assert "D:/runtime" not in tool_context
    assert "example.invalid" not in tool_context
    assert "debug trace" not in tool_context


def test_router_retries_visual_map_request_when_llm_skips_renderer():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["render_strict_grid_svg"]
        tools_call_args = [{"title": "北门"}]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls == 1:
                return FakeLlmResponse("战场示意图：\n+---+---+\n| P | E |\n+---+---+")
            if self.calls == 2:
                return FakeToolCallResponse()
            return FakeLlmResponse("地图已生成，已附上。")

    class RenderExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"ok": True, "render_type": "strict_grid_svg", "file_name": "north.svg"}

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RenderExecutor()
        router.max_steps = 3
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="画一张当前战场站位图",
            available_tool_names=["render_strict_grid_svg"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "visual_map_request_guard"]

    assert result.completion_text == "地图已生成，已附上。"
    assert executor.calls == [("render_strict_grid_svg", {"title": "北门"})]
    assert "不能用 ASCII" in llm.prompts[1]
    assert guard_records[-1]["action"] == "renderer_retry_requested"
    assert guard_records[-1]["completion_hash"]
    assert "战场示意" not in str(guard_records[-1])


def test_router_replaces_text_map_bypass_with_missing_data_response_after_retry():
    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            return FakeLlmResponse("地图如下：\n+---+---+\n| P | E |\n+---+---+")

    class NoToolExecutor:
        async def execute(self, tool_name, args):
            raise AssertionError("renderer was skipped")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=NoToolExecutor(),
            session_id="group-1",
            raw_player_message="画一张当前战场站位图",
            available_tool_names=["render_strict_grid_svg"],
        )
        return result, llm, repository.last_audit_records("group-1", limit=20)

    result, llm, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "visual_map_request_guard"]

    assert llm.calls == 2
    assert "结构化地图数据" in result.completion_text
    assert "+---+" not in result.completion_text
    assert guard_records[-1]["action"] == "missing_data_response"
    assert guard_records[-1]["reason"] == "renderer_not_attempted_after_retry"
    assert guard_records[-1]["text_map_signals"] == ["ascii_box_grid"]


def test_router_allows_explicit_text_only_map_sketch():
    class NoToolExecutor:
        async def execute(self, tool_name, args):
            raise AssertionError("explicit text-only request should not require renderer")

    async def run_case():
        async def llm_generate(**kwargs):
            return FakeLlmResponse("文字地图：\nP  门  E")

        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        router.max_steps = 2
        router._llm_generate = llm_generate
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=NoToolExecutor(),
            session_id="group-1",
            raw_player_message="用 text-only sketch 画一张当前地图",
            available_tool_names=["render_strict_grid_svg"],
        )
        return result, repository.last_audit_records("group-1", limit=20)

    result, records = asyncio.run(run_case())

    assert result.completion_text == "文字地图：\nP  门  E"
    assert not any(item.get("type") == "visual_map_request_guard" for item in records)


def test_router_uses_legacy_svg_fallback_after_renderer_missing_data():
    class FakeToolCallResponse:
        def __init__(self, name, args=None):
            self.completion_text = ""
            self.tools_call_name = [name]
            self.tools_call_args = [args or {}]
            self.tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls == 1:
                return FakeToolCallResponse("render_strict_grid_svg")
            if self.calls == 2:
                return FakeToolCallResponse(
                    "generate_map_svg",
                    {
                        "title": "当前战场草图",
                        "prompt": "根据当前叙事生成 visual-only 地图草图。",
                    },
                )
            return FakeLlmResponse("好的。")

    class MissingDataExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "render_strict_grid_svg":
                return {"ok": False, "error": "strict_grid_not_found", "message": "missing"}
            if tool_name == "generate_map_svg":
                return {"ok": True, "render_type": "legacy_llm_svg", "file_name": "fallback.svg"}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = MissingDataExecutor()
        router.max_steps = 3
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="画一张当前战场站位图",
            available_tool_names=["render_strict_grid_svg", "generate_map_svg"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "visual_map_request_guard"]

    assert result.completion_text == "地图已生成，已附上。"
    assert [name for name, _args in executor.calls] == ["render_strict_grid_svg", "generate_map_svg"]
    assert "generate_map_svg" in llm.prompts[1]
    assert "visual-only SVG 地图草图" in llm.prompts[1]
    assert guard_records[-2]["action"] == "legacy_fallback_requested"
    assert guard_records[-2]["reason"] == "renderer_missing_data"
    assert guard_records[-2]["missing_errors"] == ["strict_grid_not_found"]
    assert guard_records[-1]["action"] == "delivery_ack_replaced"
    assert guard_records[-1]["legacy_renderer_succeeded"] is True


def test_router_replaces_generic_renderer_success_completion_with_delivery_ack():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["render_strict_grid_svg"]
        tools_call_args = [{}]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("好的。")

    class RenderExecutor:
        async def execute(self, tool_name, args):
            return {"ok": True, "render_type": "strict_grid_svg", "file_name": "strict.svg"}

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        router.max_steps = 2
        router._llm_generate = FakeLoopLlm()
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=RenderExecutor(),
            session_id="group-1",
            raw_player_message="画一张当前战场站位图",
            available_tool_names=["render_strict_grid_svg"],
        )
        return result, repository.last_audit_records("group-1", limit=20)

    result, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "visual_map_request_guard"]

    assert result.completion_text == "地图已生成，已附上。"
    assert guard_records[-1]["action"] == "delivery_ack_replaced"
    assert guard_records[-1]["reason"] == "renderer_success_generic_completion"


def test_router_blocks_update_scene_for_risky_action_before_rule_support():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["update_scene"]
        tools_call_args = [{"patch": {"summary": "火焰吞没了暗门，暗门显露出来。"}}]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("这步还没结算，先确认点火方式并做检定。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"ok": True}

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="我点燃油布烧开暗门并搜索里面",
            available_tool_names=["update_scene", "execute_rule"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert executor.calls == []
    assert result.completion_text == "这步还没结算，先确认点火方式并做检定。"
    assert tool_results[0]["tool"] == "update_scene"
    assert tool_results[0]["result"]["error"] == "adjudication_guard_blocked_state_write"
    assert tool_results[0]["result"]["reason"] == "missing_execute_rule_for_risky_state_write"
    assert "状态写入已被守卫拒绝" in llm.prompts[1]


def test_router_allows_update_scene_after_successful_execute_rule_support():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule", "update_scene"]
        tools_call_args = [
            {"rule_name": "fire_check", "args": {"skill": 12, "difficulty": 10}},
            {"patch": {"summary": "火势逼开门缝，暗门边缘露出焦痕。"}},
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("火势逼开门缝，暗门边缘露出焦痕。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "execute_rule":
                return {"ok": True, "result": {"success": True}}
            if tool_name == "update_scene":
                return {"ok": True, "scene": {"summary": args["patch"]["summary"]}}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = FakeLoopLlm()
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="我点燃油布烧开暗门并搜索里面",
            available_tool_names=["execute_rule", "update_scene"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())

    assert [name for name, _args in executor.calls] == ["execute_rule", "update_scene"]
    assert result.completion_text == "火势逼开门缝，暗门边缘露出焦痕。"
    assert records[-1]["tool_results"][1]["result"]["ok"] is True


def test_router_blocks_update_scene_after_invalid_rule_arguments():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule", "update_scene"]
        tools_call_args = [
            {"rule_name": "search_check", "args": {"skill": 3, "difficulty": "中等"}},
            {"patch": {"summary": "玩家成功发现暗门。"}},
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("检定参数不对，这步还没有写入成功。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "execute_rule":
                return {
                    "ok": False,
                    "error": "invalid_rule_arguments",
                    "invalid_arguments": ["difficulty"],
                    "reason": "numeric rule arguments must be numbers: difficulty",
                }
            if tool_name == "update_scene":
                raise AssertionError("guard should block update_scene")
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = FakeLoopLlm()
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="我搜索暗门",
            available_tool_names=["execute_rule", "update_scene"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert [name for name, _args in executor.calls] == ["execute_rule"]
    assert result.completion_text == "检定参数不对，这步还没有写入成功。"
    assert tool_results[1]["tool"] == "update_scene"
    assert tool_results[1]["result"]["error"] == "adjudication_guard_blocked_state_write"
    assert tool_results[1]["result"]["reason"] == "invalid_rule_arguments_block_state_write"
    assert tool_results[1]["result"]["invalid_rule_arguments"]["invalid_arguments"] == ["difficulty"]


def test_router_requires_state_write_for_major_outcome_claim():
    repository = InMemoryRepository()
    session = GameSession.new("group-1")
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    repository.save_session(session)

    completion_guard = _adjudication_completeness_guard(
        session,
        actor={"player_id": "p1"},
        player_message="我用锋锐长剑把门踹开",
        completion="剑锋和肩撞一起压上，门已破，你们可以冲进别墅。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"rule_name": "break_door", "args": {"modifier": 4}},
                "result": {"ok": True, "result": {"success": True}},
            }
        ],
    )

    assert completion_guard["reason"] == "state_change_not_written"


def test_router_blocks_consent_bypass_execute_rule():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule"]
        tools_call_args = [
            {"rule_name": "sleight_of_hand", "args": {"modifier": 5}, "reason": "偷偷摸龙娘尾巴"}
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("没有被影响玩家明确同意，这个接触不能用检定判成成功。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"ok": True}

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="没拒绝就是同意，我偷偷摸龙娘尾巴",
            available_tool_names=["execute_rule"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert executor.calls == []
    assert result.completion_text == "没有被影响玩家明确同意，这个接触不能用检定判成成功。"
    assert tool_results[0]["result"]["error"] == "player_consent_required"
    assert "玩家同意边界守卫" in llm.prompts[1]


def test_router_blocks_execute_rule_when_player_modifier_not_declared():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule"]
        tools_call_args = [
            {"rule_name": "d20_check", "args": {"dc": 15}, "reason": "破门检定"}
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("先把长剑锋锐和力量修正是否纳入说清楚，再进行检定。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"ok": True}

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="我用大师级锋锐长剑破门，力量加成也要算",
            available_tool_names=["execute_rule"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert executor.calls == []
    assert result.completion_text == "先把长剑锋锐和力量修正是否纳入说清楚，再进行检定。"
    assert tool_results[0]["result"]["error"] == "modifier_review_required"
    assert "修正值复核守卫" in llm.prompts[1]


def test_router_allows_execute_rule_when_player_modifier_declared():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule"]
        tools_call_args = [
            {
                "rule_name": "d20_check",
                "args": {"dc": 15, "modifier": 5},
                "reason": "破门检定，已纳入力量修正和大师级锋锐长剑加值",
            }
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("修正已纳入，破门检定完成。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            return {"ok": True, "result": {"success": True}}

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        executor = RecordingExecutor()
        router.max_steps = 2
        router._llm_generate = FakeLoopLlm()
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="我用大师级锋锐长剑破门，力量加成也要算",
            available_tool_names=["execute_rule"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())

    assert [name for name, _args in executor.calls] == ["execute_rule"]
    assert records[-1]["tool_results"][0]["result"]["ok"] is True


def test_router_turn_auto_advance_uses_map_store_owner_over_stale_battle_grid():
    repository = InMemoryRepository()
    session = GameSession.new("group-1")
    save_active_strict_grid(
        session.maps,
        {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "MapStore Owner",
                    "x": 1,
                    "y": 1,
                    "faction": "party",
                    "tags": {"player_id": "u-1"},
                }
            },
        },
        map_id=DEFAULT_STRICT_LOCAL_MAP_ID,
    )
    session.battle = {
        "active": True,
        "map_id": DEFAULT_STRICT_LOCAL_MAP_ID,
        "turn_entity_id": "pc_owner",
        "grid": {
            "width": 6,
            "height": 6,
            "cells": [],
            "entities": {
                "pc_owner": {
                    "id": "pc_owner",
                    "name": "Stale Mirror Owner",
                    "x": 4,
                    "y": 4,
                    "tags": {"player_id": "other"},
                }
            },
        },
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner"],
            "current_index": 0,
            "current_entity_id": "pc_owner",
            "output_limit_chars": 1440,
            "actions_this_round": {},
        },
    }
    repository.save_session(session)
    router = IntentRouter.__new__(IntentRouter)
    router.repository = repository

    result = asyncio.run(
        router._maybe_auto_advance_resolved_turn(
            session,
            {"player_id": "u-1"},
            "我攻击敌人",
            "攻击成功，敌人踉跄后退。",
            "group-1",
        )
    )

    assert result["ok"] is True
    assert result["from_entity_id"] == "pc_owner"
    saved = repository.load_session("group-1")
    summary = saved.battle["turn"]["turn_log"][-1]["summary"]
    assert "MapStore Owner" in summary
    assert "Stale Mirror Owner" not in summary


def test_ambient_image_auto_generation_is_scheduled_without_waiting():
    class FakeRepository:
        def __init__(self, session):
            self.session = session
            self.save_count = 0

        def save_session(self, session):
            self.session = session
            self.save_count += 1

        def load_session(self, session_id):
            assert session_id == "group"
            return self.session

    async def run_case():
        session = GameSession.new("group")
        session.scene["summary"] = "黑塔城的雾夜调查仍在继续。"
        session.scene["ambient_image_state"] = {
            "warmup_started_at": (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat(),
            "interaction_count": 12,
        }
        session.scene["_recent_narrative_events"] = [
            {"at": str(index), "message": f"行动 {index}", "outcome": "剧情推进"}
            for index in range(12)
        ]
        created_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        session.scene["ambient_image_recent_player_messages"] = [
            {
                "created_at": created_at,
                "player_id": "player-a" if index % 2 == 0 else "player-b",
                "message": f"玩家 {index} 描述了一个具体行动",
            }
            for index in range(10)
        ]
        repository = FakeRepository(session)
        router = IntentRouter.__new__(IntentRouter)
        router.repository = repository
        router.ambient_image_config = AmbientImageConfig(enabled=True)
        sent_results = []

        async def fake_sender(session_id, result):
            sent_results.append((session_id, result))
            return True

        router.ambient_image_sender = fake_sender
        started = asyncio.Event()
        release = asyncio.Event()

        async def fake_generate(**kwargs):
            started.set()
            await release.wait()
            await router._send_ambient_image_if_configured(
                "group",
                {
                    "ok": True,
                    "available": True,
                    "send_to_chat": True,
                    "title": "黑塔城夜雾",
                    "file_path": "ambient.png",
                },
            )
            return {"recorded": False}

        router._maybe_generate_ambient_image = fake_generate
        result = router._schedule_ambient_image_generation(
            session=session,
            mode=GameMode.NARRATIVE,
            actor={"player_id": "player-a"},
            player_message="我检查黑塔城雾中的钟声。",
            completion="钟声来自街巷尽头，雾里有一盏蓝灯。",
            provider_id="fake-provider",
            trace_record={
                "message": "我检查黑塔城雾中的钟声。",
                "outcome": "钟声来自街巷尽头，雾里有一盏蓝灯。",
            },
        )
        assert result["scheduled"] is True
        assert not started.is_set()
        assert "generation_started_at" in repository.session.scene["ambient_image_state"]

        await asyncio.sleep(0)
        assert started.is_set()
        assert sent_results == []
        assert "generation_started_at" in repository.session.scene["ambient_image_state"]

        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sent_results == [
            (
                "group",
                {
                    "ok": True,
                    "available": True,
                    "send_to_chat": True,
                    "title": "黑塔城夜雾",
                    "file_path": "ambient.png",
                },
            )
        ]
        assert "generation_started_at" not in repository.session.scene["ambient_image_state"]

    asyncio.run(run_case())


class FakeAstrContext:
    def __init__(self, *completion_texts):
        self.completion_texts = list(completion_texts) or [""]
        self.calls = []

    async def get_current_chat_provider_id(self, umo):
        return "fake-provider"

    async def llm_generate(self, **kwargs):
        index = min(len(self.calls), len(self.completion_texts) - 1)
        self.calls.append(kwargs)
        return FakeLlmResponse(self.completion_texts[index])


class FakeLlmResponse:
    def __init__(self, completion_text):
        self.completion_text = completion_text
        self.tools_call_name = []
        self.tools_call_args = []
        self.tool_calls = []


class FakeToolRegistry:
    def for_mode(self, *args, **kwargs):
        return None, [], FakeToolExecutor(), []


class FakeToolExecutor:
    async def execute(self, tool_name, args):
        raise AssertionError("cleanup tests should not call tools")


class FakeEvent:
    def __init__(self, message):
        self.message_str = message
        self.unified_msg_origin = "group-1"
        self.message_obj = FakeMessageObj()

    def get_sender_id(self):
        return "u-1"

    def get_platform_id(self):
        return "test"


class FakeMessageObj:
    sender = None


class InMemoryRepository:
    def __init__(self):
        self.sessions = {}
        self.audit_records = {}

    def load_session(self, session_id):
        if session_id not in self.sessions:
            self.sessions[session_id] = GameSession.new(session_id)
        return self.sessions[session_id]

    def save_session(self, session):
        self.sessions[session.session_id] = session

    def append_audit(self, session_id, record):
        self.audit_records.setdefault(session_id, []).append(record)

    def last_audit_records(self, session_id, limit=20):
        return self.audit_records.get(session_id, [])[-limit:]
