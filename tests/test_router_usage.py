import asyncio
import json
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
from astrbot_plugin_auto_trpg_dm.core.modes import GameModeStateMachine
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.core.router import (
    IntentRouter,
    _adjudication_completeness_guard,
    _actor_equipment_final_reply_guard,
    _character_card_final_reply_guard,
    _extract_llm_usage_summary,
    _llm_no_tool_call_flags,
    _llm_request_shape,
    _is_diagnostic_request,
    _maybe_close_concluded_turn,
    _reset_confirmation_output_guard,
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


def test_llm_request_shape_marks_call_purpose_without_affecting_tool_flag():
    shape = _llm_request_shape(
        {
            "prompt": "retry final text",
            "contexts": [{"role": "assistant", "content": "tool result"}],
            "system_prompt": "dm",
            "_call_purpose": "final_response_tool_loop",
        }
    )

    assert shape["call_purpose"] == "final_response_tool_loop"
    assert shape["tool_enabled"] is False
    assert shape["contexts_count"] == 1


def test_llm_no_tool_call_flags_separate_auxiliary_from_final_followup():
    final_shape = _llm_request_shape(
        {
            "prompt": "final text",
            "system_prompt": "dm",
            "_call_purpose": "final_response_tool_loop",
        }
    )
    audit_shape = _llm_request_shape(
        {
            "prompt": "audit",
            "system_prompt": "continuity",
            "_call_purpose": "continuity_audit",
        }
    )
    tool_shape = _llm_request_shape(
        {
            "prompt": "tool loop",
            "system_prompt": "dm",
            "func_tool": object(),
            "_call_purpose": "dm_tool_loop",
        }
    )

    assert _llm_no_tool_call_flags(final_shape) == (True, False)
    assert _llm_no_tool_call_flags(audit_shape) == (False, True)
    assert _llm_no_tool_call_flags(tool_shape) == (False, False)


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


def test_router_uses_detected_mode_for_routing_without_persisting_keyword_mode():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.mode = GameMode.NARRATIVE
    message = GameModeStateMachine.CHARACTER_HINTS[0]
    astr_context = FakeAstrContext("Character setup routing did not persist mode.")
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent(message)))
    saved = repository.load_session("group-1")
    handled = [
        item
        for item in repository.last_audit_records("group-1", limit=20)
        if item.get("type") == "message_handled"
    ]

    assert "Character setup routing" in reply
    assert handled[-1]["mode"] == GameMode.CHARACTER_CREATION.value
    assert saved.mode == GameMode.NARRATIVE


def test_router_serializes_narrative_llm_for_same_session():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.scene["_game_started"] = True
    active_calls = 0
    max_active_calls = 0

    async def run_case():
        release_first = asyncio.Event()

        class BlockingAstrContext(FakeAstrContext):
            async def llm_generate(self, **kwargs):
                nonlocal active_calls, max_active_calls
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
                self.calls.append(kwargs)
                try:
                    if len(self.calls) == 1:
                        await release_first.wait()
                        return FakeLlmResponse("first done")
                    return FakeLlmResponse("second done")
                finally:
                    active_calls -= 1

        astr_context = BlockingAstrContext("first done", "second done")
        router = IntentRouter(
            astr_context=astr_context,
            repository=repository,
            tool_registry=FakeToolRegistry(),
            continuity_auditor_enabled=False,
        )
        first = asyncio.create_task(router.handle_message(FakeEvent("我调查控制台")))
        await asyncio.sleep(0)
        second = asyncio.create_task(router.handle_message(FakeEvent("我查看日志")))
        await asyncio.sleep(0.05)
        assert len(astr_context.calls) == 1
        release_first.set()
        return await asyncio.gather(first, second), astr_context

    replies, astr_context = asyncio.run(run_case())

    assert replies == ["first done", "second done"]
    assert len(astr_context.calls) == 2
    assert max_active_calls == 1


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


def test_extract_llm_usage_summary_reads_nested_provider_raw_response_object():
    class Raw:
        usage = {
            "prompt_tokens": 300,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 240},
        }

    class Metadata:
        raw_response = Raw()

    class Response:
        response_metadata = Metadata()

    summary = _extract_llm_usage_summary(Response())

    assert summary["prompt_tokens"] == 300
    assert summary["completion_tokens"] == 50
    assert summary["cached_tokens"] == 240
    assert summary["cache_hit_ratio_pct"] == 80.0


def test_start_game_arg_repair_coerces_json_string_outline():
    repaired = IntentRouter._repair_tool_args(
        "start_game",
        {
            "title": "底巢清剿：锈蚀圣堂",
            "opening_intro": "开场文字",
            "campaign_outline": '{"act_1":"导火索","act_2":"升级","act_3":"高潮"}',
            "scene_patch": "废弃枢纽站里传来爪刃声。",
        },
        "开始游戏",
    )

    assert repaired["title"] == "底巢清剿：锈蚀圣堂"
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

    assert "建议角色方向：" in reply
    assert "**" not in reply
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


def test_router_marks_repair_required_when_continuity_patch_rejected():
    repository = InMemoryRepository()
    session = repository.load_session("group-1")
    session.scene["_game_started"] = True
    audit_payload = {
        "ok": True,
        "needs_repair": True,
        "issues": [
            {
                "severity": "high",
                "problem": "Audit found an unsupported retirement fact.",
                "evidence": ["The player says Kade is still present."],
                "repair": "Do not retire Kade without an existing character record.",
            }
        ],
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "missing-kade",
                    "tags": [{"key": "退场状态", "value": "已退场", "layer": "status"}],
                }
            ]
        },
        "player_correction": "",
    }
    astr_context = FakeAstrContext(
        "Kade has permanently left the current story.",
        json.dumps(audit_payload, ensure_ascii=False),
    )
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
    )

    reply = asyncio.run(router.handle_message(FakeEvent("你又记错了，Kade 还没有退场")))
    saved = repository.load_session("group-1")
    records = repository.last_audit_records("group-1", limit=30)
    reviewed = [item for item in records if item.get("type") == "continuity_audit_reviewed"]

    assert "Kade has permanently left" in reply
    assert saved.scene["_repair_required"]["source"] == "continuity_auditor"
    assert saved.scene["_repair_required"]["reason"] == "rejected_safe_patches"
    assert saved.scene["_repair_required"]["rejected"][0]["reason"] == "missing_character"
    assert reviewed[-1]["repair_required"]["reason"] == "rejected_safe_patches"


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


def test_router_accepts_final_response_tool_as_loop_completion():
    class FirstToolCallResponse:
        completion_text = ""
        tools_call_name = ["session_control"]
        tools_call_args = [{"action": "status"}]
        tool_calls = []

    class FinalToolCallResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [{"reply": "当前状态稳定，可以继续行动。"}]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FirstToolCallResponse()
            return FinalToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "session_control":
                return {"ok": True, "status": "stable"}
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
        router.max_steps = 4
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="现在怎样",
            available_tool_names=["session_control", "final_response"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())

    assert result.completion_text == "当前状态稳定，可以继续行动。"
    assert llm.calls == 2
    assert [name for name, _args in executor.calls] == ["session_control", "final_response"]
    assert result.tool_results[-1]["tool"] == "final_response"
    assert records[-1]["tool_results"][0]["tool"] == "final_response"


def test_reset_confirmation_output_guard_blocks_fabricated_llm_token():
    reply = _reset_confirmation_output_guard(
        "确定重置",
        "确认码：RESET-DEFAULT-GROUPMESSAGE-676453921-171709\n发送这条确认码我会清空当前存档。",
        [],
    )

    assert "不能由叙事回复生成确认码" in reply
    assert "重开当前团" in reply


def test_reset_confirmation_output_guard_allows_tool_generated_token():
    reply = _reset_confirmation_output_guard(
        "确定重置",
        "确认码：RESET-ABC123",
        [
            {
                "tool": "session_control",
                "result": {
                    "ok": False,
                    "action": "reset_confirmation_required",
                    "confirm_token": "RESET-ABC123",
                },
            }
        ],
    )

    assert reply == ""


def test_router_retries_tool_role_response_instead_of_returning_repr():
    class FirstToolCallResponse:
        completion_text = ""
        tools_call_name = ["resolve_check"]
        tools_call_args = [{"check": "acrobatics", "dc": 13}]
        tool_calls = []

    class ToolRoleResponse:
        role = "tool"
        completion_text = ""
        tools_call_name = []
        tools_call_args = []
        tool_calls = []

        def __str__(self):
            return "LLMResponse(role='tool', result_chain=MessageChain(...))"

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs.get("prompt", ""))
            if self.calls == 1:
                return FirstToolCallResponse()
            if self.calls == 2:
                return ToolRoleResponse()
            return FakeLlmResponse("你贴着窗下滚入震天雷，屋内爆响后守军阵脚大乱。")

    class ResolveExecutor:
        async def execute(self, tool_name, args):
            assert tool_name == "resolve_check"
            return {"ok": True, "total": 15, "dc": 13, "outcome": "success"}

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        router.max_steps = 4
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=ResolveExecutor(),
            session_id="group-1",
            raw_player_message="滚震天雷进石屋",
            available_tool_names=["resolve_check"],
        )
        return result, llm

    result, llm = asyncio.run(run_case())

    assert "LLMResponse" not in result.completion_text
    assert "震天雷" in result.completion_text
    assert llm.calls == 3
    assert "工具角色/空消息" in llm.prompts[-1]


def test_router_falls_back_from_tool_role_response_at_max_steps():
    class FirstToolCallResponse:
        completion_text = ""
        tools_call_name = ["resolve_check"]
        tools_call_args = [{"check": "acrobatics", "dc": 13}]
        tool_calls = []

    class ToolRoleResponse:
        role = "tool"
        completion_text = ""
        tools_call_name = []
        tools_call_args = []
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FirstToolCallResponse()
            return ToolRoleResponse()

    class ResolveExecutor:
        async def execute(self, tool_name, args):
            return {"ok": True, "total": 15, "dc": 13, "outcome": "success"}

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        router.max_steps = 2
        router._llm_generate = llm
        router.repository = repository
        return await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=ResolveExecutor(),
            session_id="group-1",
            raw_player_message="滚震天雷进石屋",
            available_tool_names=["resolve_check"],
        )

    result = asyncio.run(run_case())

    assert "LLMResponse" not in result.completion_text
    assert "本轮结算已完成" in result.completion_text
    assert "resolve_check" in result.completion_text


def test_router_tool_argument_json_error_returns_safe_player_reply_without_tool_execution():
    class NoToolExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            raise AssertionError("bad tool JSON should not execute any tool")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        executor = NoToolExecutor()
        router.max_steps = 3
        router.repository = repository

        async def llm_generate(**kwargs):
            from astrbot_plugin_auto_trpg_dm.core.router import ToolArgumentJsonFallbackResponse

            return ToolArgumentJsonFallbackResponse("Unterminated string starting at: line 1 column 11")

        router._llm_generate = llm_generate
        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="玩家行动",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="射击援救队",
            available_tool_names=["execute_rule", "final_response"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())

    assert executor.calls == []
    assert "工具参数格式坏了" in result.completion_text
    assert "没有把未结算的结果写进存档" in result.completion_text
    fallback_records = [item for item in records if item.get("type") == "llm_tool_arguments_json_fallback"]
    assert fallback_records
    assert fallback_records[-1]["step"] == 1


def test_extract_tool_calls_recovers_function_argument_json_with_trailing_text():
    class ToolCallResponse:
        completion_text = ""
        tools_call_name = []
        tools_call_args = []
        tool_calls = [
            {
                "function": {
                    "name": "update_scene",
                    "arguments": '{"patch":{"summary":"control room searched"}}\nI will narrate this next.',
                }
            }
        ]

    calls = IntentRouter._extract_tool_calls(ToolCallResponse())

    assert calls == [{"name": "update_scene", "args": {"patch": {"summary": "control room searched"}}}]


def test_extract_tool_calls_recovers_named_argument_json_with_trailing_text():
    class ToolCallResponse:
        completion_text = ""
        tools_call_name = ["update_scene"]
        tools_call_args = ['{"patch":{"summary":"lantern dimmed"}}\nextra explanation']
        tool_calls = []

    calls = IntentRouter._extract_tool_calls(ToolCallResponse())

    assert calls == [{"name": "update_scene", "args": {"patch": {"summary": "lantern dimmed"}}}]


def test_extract_text_tool_calls_recovers_tool_payload_json_with_trailing_text():
    calls = IntentRouter._extract_text_tool_calls(
        '{"tool_calls":[{"name":"update_scene","args":{"patch":{"summary":"door barred"}}}]}'
        "\nNow I will continue the narration."
    )

    assert calls == [{"name": "update_scene", "args": {"patch": {"summary": "door barred"}}}]


def test_extract_tool_calls_keeps_malformed_argument_json_safe():
    class ToolCallResponse:
        completion_text = ""
        tools_call_name = []
        tools_call_args = []
        tool_calls = [{"function": {"name": "update_scene", "arguments": '{"patch": '}}]

    calls = IntentRouter._extract_tool_calls(ToolCallResponse())

    assert calls == [{"name": "update_scene", "args": {}}]


def test_character_card_final_reply_guard_blocks_unverified_elite_join():
    session = GameSession.new("group-1")
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_hans"] = Character(
        id="pc_hans",
        name="汉斯",
        player_id="p1",
        summary="普通船员出身的机械师，熟悉游艇基础维修。",
        tags=[TagValue(key="能力", value="维修、观察", layer="abilities")],
    )

    guard = _character_card_final_reply_guard(
        "加入新角色，我是一名刺客，通过潜艇被投放在船附近，夜间游泳上船",
        "可以。你的角色杰森·伯恩已经入场：受过专业训练的渗透与格斗专家，通过潜艇投放后夜间游泳登上音速号，已潜伏在船上伺机而动，装备顶级干式潜水服、隐藏的轻武器和工具。",
        [],
        session=session,
    )

    assert guard
    assert "character_card_power_mismatch" in guard["errors"]
    assert "不能直接通过" in guard["reply"]
    assert "潜入结果" in guard["reply"]


def _role_mix_equipment_guard_session():
    session = GameSession.new("group-1")
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_chen_dahu"] = Character(
        id="pc_chen_dahu",
        name="陈大虎",
        player_id="512469473",
        summary="福建镇海卫军户，长枪手。",
        tags=[
            TagValue(key="装备", value="精良级长枪一杆、腰刀一口、皮甲一副", layer="equipment"),
            TagValue(key="状态", value="随老徐队伍进山讨伐桃源公，已休整完毕。", layer="status"),
        ],
    )
    session.characters["pc_kade"] = Character(
        id="pc_kade",
        name="凯德",
        player_id="158988882",
        summary="锦衣卫弓弩手，携带神臂弩和千里眼。",
        tags=[
            TagValue(key="装备", value="神臂弩、普通箭、穿甲箭、麻药箭、毒箭、腰刀", layer="equipment"),
            TagValue(key="辅助工具", value="千里眼、测距工具", layer="equipment"),
        ],
    )
    session.player_character_map = {
        "512469473": "pc_chen_dahu",
        "158988882": "pc_kade",
    }
    return session


def test_actor_equipment_final_reply_guard_blocks_teammate_crossbow_on_current_actor():
    session = _role_mix_equipment_guard_session()

    guard = _actor_equipment_final_reply_guard(
        "在营地里试一下，反正能把箭捡回来，不会损耗",
        "你在营地靶场上站定，那把林鸢检定过的备用弩沉甸甸地托在手里，扣下扳机后箭羽擦过草靶。",
        [],
        session=session,
        actor={"player_id": "512469473", "display_name": "gali"},
    )

    assert guard["reason"] == "actor_equipment_final_reply_unverified"
    assert guard["actor_character_id"] == "pc_chen_dahu"
    assert guard["equipment_terms"] == ["备用弩"]
    assert "陈大虎" in guard["reply"]
    assert "精良级长枪" in guard["reply"]


def test_actor_equipment_final_reply_guard_allows_recorded_actor_crossbow():
    session = _role_mix_equipment_guard_session()

    guard = _actor_equipment_final_reply_guard(
        "我架起神臂弩瞄准",
        "凯德把神臂弩架在岩石边，瞄准断墙后的哨兵。",
        [],
        session=session,
        actor={"player_id": "158988882", "display_name": "Kongdy"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_allows_non_possession_request():
    session = _role_mix_equipment_guard_session()

    guard = _actor_equipment_final_reply_guard(
        "问问营地里有没有多的弩",
        "营地里确实有备用弩，但陈大虎手里没有；如果要试射，需要先向老徐申请。",
        [],
        session=session,
        actor={"player_id": "512469473", "display_name": "gali"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_allows_tool_confirmed_temporary_loan():
    session = _role_mix_equipment_guard_session()

    guard = _actor_equipment_final_reply_guard(
        "我向老徐申请借一把备用弩试射",
        "老徐点头后把备用弩递给你，你把备用弩托在手里站到靶场前。",
        [
            {
                "tool": "update_character_tags",
                "args": {
                    "character_id": "pc_chen_dahu",
                    "tags": [
                        {
                            "key": "临时装备",
                            "value": "老徐同意临时借给陈大虎一把备用弩用于营地试射",
                            "layer": "status",
                        }
                    ],
                },
                "result": {
                    "ok": True,
                    "character_id": "pc_chen_dahu",
                    "updated_tags": [
                        {
                            "key": "临时装备",
                            "value": "老徐同意临时借给陈大虎一把备用弩用于营地试射",
                            "layer": "status",
                        }
                    ],
                },
            }
        ],
        session=session,
        actor={"player_id": "512469473", "display_name": "gali"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_allows_recorded_temporary_equipment_tag():
    session = _role_mix_equipment_guard_session()
    session.characters["pc_chen_dahu"].tags.append(
        TagValue(
            key="临时装备",
            value="林鸢将一把备用弩和数支军用弩箭交给陈大虎，陈大虎当前可携带并使用该备用弩。",
            layer="status",
        )
    )

    guard = _actor_equipment_final_reply_guard(
        "我端起备用弩试射",
        "你把备用弩托在手里，朝靶子扣下扳机。",
        [],
        session=session,
        actor={"player_id": "512469473", "display_name": "gali"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_does_not_use_active_character_for_unbound_player():
    session = _role_mix_equipment_guard_session()
    session.active_character_id = "pc_chen_dahu"

    guard = _actor_equipment_final_reply_guard(
        "我从队伍侧翼移动，准备等机会入场",
        "你背着弓赶到老徐队伍附近，等待合适的入场时机。",
        [],
        session=session,
        actor={"player_id": "23833769", "display_name": "风"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_ignores_enemy_archer_or_bow_target_narration():
    session = _role_mix_equipment_guard_session()
    session.characters["pc_yang_yongxin"] = Character(
        id="pc_yang_yongxin",
        name="杨永信",
        player_id="1298514181",
        summary="随船道士，擅长卜算和医术，略懂内炼功夫。",
        tags=[
            TagValue(key="主武器", value="伪装成拂尘的单手连枷", layer="equipment"),
            TagValue(key="副武器", value="单手剑（当法剑用）", layer="equipment"),
        ],
    )
    session.player_character_map["1298514181"] = "pc_yang_yongxin"

    guard = _actor_equipment_final_reply_guard(
        "老徐下命令开打了",
        "老徐的哨音一响，前队立刻开打；你压住左翼，盯紧哨塔上的弓箭手，等凯德射击后再近身推进。",
        [],
        session=session,
        actor={"player_id": "1298514181", "display_name": "阿卜杜拉阿合马"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_blocks_direct_unrecorded_bow_possession():
    session = _role_mix_equipment_guard_session()
    session.characters["pc_yang_yongxin"] = Character(
        id="pc_yang_yongxin",
        name="杨永信",
        player_id="1298514181",
        summary="随船道士，擅长卜算和医术，略懂内炼功夫。",
        tags=[TagValue(key="主武器", value="伪装成拂尘的单手连枷", layer="equipment")],
    )
    session.player_character_map["1298514181"] = "pc_yang_yongxin"

    guard = _actor_equipment_final_reply_guard(
        "老徐下命令开打了",
        "你背着弓从乱石后探出半身，准备朝哨塔射击。",
        [],
        session=session,
        actor={"player_id": "1298514181", "display_name": "阿卜杜拉阿合马"},
    )

    assert guard["reason"] == "actor_equipment_final_reply_unverified"
    assert guard["actor_character_id"] == "pc_yang_yongxin"
    assert guard["equipment_terms"] == ["弓"]


def test_actor_equipment_final_reply_guard_skips_late_join_character_card_request():
    session = _role_mix_equipment_guard_session()
    session.active_character_id = "pc_chen_dahu"

    guard = _actor_equipment_final_reply_guard(
        "我加入游戏，角色名字叫风，弓箭手，擅长精准射击，是后续赶来加入老徐他们这一队的援兵。",
        "你背着弓赶到老徐队伍附近，等待合适的入场时机。",
        [],
        session=session,
        actor={"player_id": "23833769", "display_name": "风"},
    )

    assert guard == {}


def test_actor_equipment_final_reply_guard_ignores_raw_text_only_update():
    session = _role_mix_equipment_guard_session()

    guard = _actor_equipment_final_reply_guard(
        "我想借一把备用弩试射",
        "你把备用弩托在手里，朝靶子扣下扳机。",
        [
            {
                "tool": "update_character_tags",
                "args": {
                    "character_id": "pc_chen_dahu",
                    "raw_text": "玩家想借一把备用弩试射",
                },
                "result": {
                    "ok": True,
                    "character_id": "pc_chen_dahu",
                    "updated_tags": [
                        {
                            "key": "最近行动",
                            "value": "玩家想借一把备用弩试射，但尚未确认领用。",
                            "layer": "status",
                        }
                    ],
                },
            }
        ],
        session=session,
        actor={"player_id": "512469473", "display_name": "gali"},
    )

    assert guard["equipment_terms"] == ["备用弩"]


def test_router_replaces_unverified_actor_equipment_final_response_tool():
    class FinalToolCallResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [
            {
                "reply": "你在营地靶场上站定，那把林鸢检定过的备用弩沉甸甸地托在手里。"
            }
        ]
        tool_calls = []

    class FakeLoopLlm:
        async def __call__(self, **kwargs):
            return FinalToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = _role_mix_equipment_guard_session()
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
            raw_player_message="在营地里试一下，反正能把箭捡回来，不会损耗",
            available_tool_names=["update_character_tags", "final_response"],
            actor={"player_id": "512469473", "display_name": "gali"},
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "actor_equipment_final_reply_guard"]

    assert [name for name, _args in executor.calls] == ["final_response"]
    assert "裁定修正" in result.completion_text
    assert "备用弩" in result.completion_text
    assert "沉甸甸地托在手里" not in result.completion_text
    assert guard_records[-1]["reason"] == "actor_equipment_final_reply_unverified"
    assert guard_records[-1]["actor_character_id"] == "pc_chen_dahu"
    assert guard_records[-1]["equipment_terms"] == ["备用弩"]


def test_router_does_not_rewrite_unbound_late_join_as_active_character_equipment():
    class FinalToolCallResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [
            {
                "reply": "你背着弓赶到老徐队伍附近，等待合适的入场时机。"
            }
        ]
        tool_calls = []

    class FakeLoopLlm:
        async def __call__(self, **kwargs):
            return FinalToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = _role_mix_equipment_guard_session()
        session.active_character_id = "pc_chen_dahu"
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
            raw_player_message="我加入游戏，角色名字叫风，弓箭手，擅长精准射击，是后续赶来加入老徐他们这一队的援兵。",
            available_tool_names=["bind_player_character", "final_response"],
            actor={"player_id": "23833769", "display_name": "风"},
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "actor_equipment_final_reply_guard"]

    assert [name for name, _args in executor.calls] == ["final_response"]
    assert result.completion_text == "你背着弓赶到老徐队伍附近，等待合适的入场时机。"
    assert "陈大虎" not in result.completion_text
    assert "裁定修正" not in result.completion_text
    assert guard_records == []


def test_router_replaces_unverified_character_final_response_tool():
    class FinalToolCallResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [
            {
                "reply": (
                    "可以。你的角色杰森·伯恩已经入场：受过专业训练的渗透与格斗专家，"
                    "通过潜艇投放后夜间游泳登上音速号，已潜伏在船上伺机而动。"
                    "装备顶级干式潜水服和隐藏的轻武器。"
                )
            }
        ]
        tool_calls = []

    class FakeLoopLlm:
        async def __call__(self, **kwargs):
            return FinalToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        session.characters["pc_hans"] = Character(
            id="pc_hans",
            name="汉斯",
            player_id="p1",
            summary="普通船员出身的机械师，熟悉游艇基础维修。",
            tags=[TagValue(key="能力", value="维修、观察", layer="abilities")],
        )
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
            raw_player_message="加入新角色，我是一名刺客，通过潜艇被投放在船附近，夜间游泳上船",
            available_tool_names=["bind_player_character", "final_response"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())
    guard_records = [item for item in records if item.get("type") == "character_card_final_reply_guard"]

    assert [name for name, _args in executor.calls] == ["final_response"]
    assert "还不能直接通过" in result.completion_text
    assert "已潜伏" not in result.completion_text
    assert guard_records[-1]["reason"] == "character_card_final_reply_unverified"
    assert "character_card_power_mismatch" in guard_records[-1]["errors"]


def test_router_replaces_final_response_after_explicit_tool_failure():
    class CreateCharacterCall:
        completion_text = ""
        tools_call_name = ["create_character"]
        tools_call_args = [{"name": "Kade", "summary": "elite SWAT gunner with M249 on a BearCat"}]
        tool_calls = []

    class FinalToolCallResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [{"reply": "Kade is now established on the BearCat with an M249."}]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return CreateCharacterCall()
            return FinalToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "create_character":
                return {
                    "ok": False,
                    "error": "character_card_power_mismatch",
                    "message": "character is too strong for this party",
                    "suggestion": "tone down the loadout",
                }
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        router = IntentRouter.__new__(IntentRouter)
        router.max_steps = 3
        router._llm_generate = FakeLoopLlm()
        router.repository = repository
        executor = RecordingExecutor()

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="player action",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="join as an elite SWAT gunner with M249 and BearCat",
            available_tool_names=["create_character", "final_response"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())

    assert [name for name, _args in executor.calls] == ["create_character", "final_response"]
    assert "M249" not in result.completion_text
    assert "BearCat" not in result.completion_text
    assert "character_card_power_mismatch" in records[-1]["errors"]
    assert records[-1]["type"] == "tool_failure_final_reply_guard"


def test_terminal_request_wording_does_not_close_battle_turn_locally():
    session = GameSession.new("group")
    session.battle = {
        "active": True,
        "turn": {
            "active": True,
            "phase": "character_turn",
            "current_entity_id": "pc-1",
            "current_index": 0,
        },
        "turn_entity_id": "pc-1",
    }

    result = _maybe_close_concluded_turn(session, "灞曠ず缁撶畻锛屾湰娆″埌姝ょ粨鏉?")

    assert result is None
    assert session.battle["active"] is True
    assert session.battle["turn"]["active"] is True
    assert session.battle["turn"]["phase"] == "character_turn"


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


def test_router_returns_llm_supplement_when_visual_map_tool_still_not_triggered_after_retry():
    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0
            self.prompts = []

        async def __call__(self, **kwargs):
            self.calls += 1
            self.prompts.append(kwargs["prompt"])
            if self.calls < 3:
                return FakeLlmResponse("地图如下：\n+---+---+\n| P | E |\n+---+---+")
            return FakeLlmResponse("工具没触发；我先补充可见情况，请提供结构化地图数据后再制图。")

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

    assert llm.calls == 3
    assert "补充当前可见信息" in llm.prompts[-1]
    assert result.completion_text == "工具没触发；我先补充可见情况，请提供结构化地图数据后再制图。"
    assert guard_records[-1]["action"] == "llm_supplement_requested"
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


def test_router_blocks_character_tags_for_risky_success_before_rule_support():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["update_character_tags"]
        tools_call_args = [
            {
                "character_id": "pc_kaide",
                "tags": [
                    {
                        "key": "当前状态",
                        "value": "科考站派船返回沉没区域搜索，确认只找到凯德一人",
                        "layer": "status",
                    }
                ],
            }
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
            return FakeLlmResponse("这步还没结算，先确认科考站是否愿意派船搜索。")

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
            raw_player_message="我说服科考站派船回音速号沉没区域搜索，只找到我一个人",
            available_tool_names=["update_character_tags", "resolve_check"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert executor.calls == []
    assert result.completion_text == "这步还没结算，先确认科考站是否愿意派船搜索。"
    assert tool_results[0]["tool"] == "update_character_tags"
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


def test_router_allows_update_scene_after_later_successful_resolve_check():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule", "resolve_check", "update_scene"]
        tools_call_args = [
            {"rule_name": "d20_skill_check", "args": {"ability": "intelligence", "proficiency": True, "dc": 12}},
            {"action": "组装炸药并遮掩痕迹", "dc": 12, "bonus": 3},
            {"patch": {"summary": "雅卡组装好了炸药，但留下了可被细查发现的痕迹。"}},
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("炸药已准备好，但客舱暗格里留下了草率遮掩的痕迹。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "execute_rule":
                return {
                    "ok": False,
                    "error": "invalid_rule_arguments",
                    "unknown_arguments": ["ability", "proficiency"],
                    "allowed_arguments": ["bonus", "dc"],
                    "reason": "unknown rule arguments: ability, proficiency",
                }
            if tool_name == "resolve_check":
                return {
                    "ok": True,
                    "tool": "resolve_check",
                    "check_id": "chk_test",
                    "state_write_support": True,
                    "result": {"total": 13, "dc": 12, "success": True},
                }
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
            raw_player_message="我组装好炸药，设定时间为5分钟，再把笔记塞回去遮掩",
            available_tool_names=["execute_rule", "resolve_check", "update_scene"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())
    tool_results = records[-1]["tool_results"]

    assert [name for name, _args in executor.calls] == ["execute_rule", "resolve_check", "update_scene"]
    assert tool_results[2]["result"]["ok"] is True
    assert result.completion_text == "炸药已准备好，但客舱暗格里留下了草率遮掩的痕迹。"


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


def test_completion_guard_skips_rejection_of_player_premise():
    repository = InMemoryRepository()
    session = GameSession.new("group-1")
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    repository.save_session(session)

    completion_guard = _adjudication_completeness_guard(
        session,
        actor={"player_id": "p1"},
        player_message="史东作为大BOSS，必须由他在中控室控制台上释放深潜器，重新检定史东能否活下来",
        completion=(
            "这个前提跟已记录的事实对不上。已确立的事实链：潜航器释放是在船尾底层潜航器库房完成，"
            "没有记录显示史东曾进入中控室；不能通过事后重新设定他在爆炸中心来重投。"
        ),
        tool_results=[],
    )

    assert completion_guard == {}


def test_completion_guard_allows_narrative_position_after_successful_check():
    repository = InMemoryRepository()
    session = GameSession.new("group-1")
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    repository.save_session(session)

    completion_guard = _adjudication_completeness_guard(
        session,
        actor={"player_id": "p1"},
        player_message="进门前按下炸弹启动键，将布包放在控制台下方，记录位置坐标，走向船尾深潜器。",
        completion="计时器从5:00跳到4:59，炸药包已经藏进控制台下方线缆槽。你记录坐标后走向船尾深潜器。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"rule_name": "d20_skill_check", "args": {"bonus": 2, "dc": 14}},
                "result": {"ok": True, "result": {"success": True, "total": 23, "dc": 14}},
            }
        ],
    )

    assert completion_guard == {}


def test_completion_guard_still_requires_spatial_tool_for_tactical_movement():
    repository = InMemoryRepository()
    session = GameSession.new("group-1")
    session.mode = GameMode.TACTICAL
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.battle = {"active": True}
    repository.save_session(session)

    completion_guard = _adjudication_completeness_guard(
        session,
        actor={"player_id": "p1"},
        player_message="我移动到B3绕到敌人背后。",
        completion="你已经绕到敌人背后，取得了更好的位置。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"rule_name": "acrobatics", "args": {"bonus": 2, "dc": 12}},
                "result": {"ok": True, "result": {"success": True}},
            }
        ],
    )

    assert completion_guard["reason"] == "missing_spatial_or_turn_tool_for_positioned_outcome"


def test_router_retries_final_response_risky_outcome_without_roll_support():
    class PrematureFinalResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [{"reply": "雅卡开枪命中哨兵，哨兵倒下。"}]
        tool_calls = []

    class FixedToolCallResponse:
        completion_text = ""
        tools_call_name = ["resolve_check", "update_scene", "final_response"]
        tools_call_args = [
            {"ability": "dexterity", "dc": 14, "reason": "雅卡射击哨兵"},
            {"patch": {"summary": "雅卡射击哨兵后，寨墙警戒被惊动。"}},
            {"reply": "枪声撕开夜色，哨兵被压制，寨墙上的火把开始晃动。"},
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
                return PrematureFinalResponse()
            return FixedToolCallResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "resolve_check":
                return {"ok": True, "outcome": "success", "total": 18, "dc": 14}
            if tool_name == "update_scene":
                return {"ok": True, "scene": args.get("patch", {})}
            if tool_name == "final_response":
                return {"ok": True, "reply": args.get("reply", "")}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
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
            raw_player_message="我射击寨墙上的哨兵",
            available_tool_names=["resolve_check", "update_scene", "final_response"],
        )
        return result, llm, executor, repository.last_audit_records("group-1", limit=20)

    result, llm, executor, records = asyncio.run(run_case())

    assert llm.calls == 2
    assert "完成度守卫" in llm.prompts[-1]
    assert [name for name, _args in executor.calls] == [
        "final_response",
        "resolve_check",
        "update_scene",
        "final_response",
    ]
    assert result.completion_text == "枪声撕开夜色，哨兵被压制，寨墙上的火把开始晃动。"
    assert not [record for record in records if record.get("type") == "adjudication_completeness_guard"]


def test_router_retries_final_response_after_soft_turn_contract_without_turn_control():
    class EncounterContractResponse:
        completion_text = ""
        tools_call_name = ["record_story_forge_encounter_contract"]
        tools_call_args = [
            {
                "encounter_decision": "soft_turns",
                "reason": "Both sides can react between focused actions.",
                "scene_goal": "Hold the stairwell entry.",
                "stakes": "Delay lets the guards regroup.",
                "action_economy": "one_actor_focus",
                "map_need": "sketch",
                "turn_order_source": "derived_scene",
                "recommended_next_tool": "turn_control",
            }
        ]
        tool_calls = []

    class PrematureFinalResponse:
        completion_text = ""
        tools_call_name = ["final_response"]
        tools_call_args = [{"reply": "The guards press in and the exchange is resolved in one rush."}]
        tool_calls = []

    class FixedTurnResponse:
        completion_text = ""
        tools_call_name = ["turn_control", "final_response"]
        tools_call_args = [
            {"action": "start_scene_resolution", "summary": "Soft turns start at the stairwell."},
            {"reply": "The stairwell is now under soft turns; one focused action at a time."},
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
                return EncounterContractResponse()
            if self.calls == 2:
                return PrematureFinalResponse()
            return FixedTurnResponse()

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "record_story_forge_encounter_contract":
                return {
                    "ok": True,
                    "contract_id": "enc:test",
                    "encounter_decision": "soft_turns",
                    "recommended_next_tool": "turn_control",
                }
            if tool_name == "turn_control":
                return {"ok": True, "phase": "scene_resolution", "current_entity_id": "pc_1"}
            if tool_name == "final_response":
                return {"ok": True, "reply": args.get("reply", "")}
            raise AssertionError(f"unexpected tool: {tool_name}")

    async def run_case():
        repository = InMemoryRepository()
        session = GameSession.new("group-1")
        session.world_tags["_plot_locked"] = True
        session.scene["_game_started"] = True
        repository.save_session(session)
        router = IntentRouter.__new__(IntentRouter)
        llm = FakeLoopLlm()
        executor = RecordingExecutor()
        router.max_steps = 4
        router._llm_generate = llm
        router.repository = repository

        result = await router._run_llm_tool_loop(
            chat_provider_id="fake-provider",
            system_prompt="system",
            initial_prompt="player action",
            toolset=object(),
            tool_executor=executor,
            session_id="group-1",
            raw_player_message="I hold the stairwell.",
            available_tool_names=["record_story_forge_encounter_contract", "turn_control", "final_response"],
        )
        return result, llm, executor

    result, llm, executor = asyncio.run(run_case())

    assert llm.calls == 3
    assert "Encounter Contract" in llm.prompts[-1]
    assert [name for name, _args in executor.calls] == [
        "record_story_forge_encounter_contract",
        "final_response",
        "turn_control",
        "final_response",
    ]
    assert result.completion_text == "The stairwell is now under soft turns; one focused action at a time."


def test_router_does_not_append_completeness_guard_after_successful_check_and_final_response():
    class FakeToolCallResponse:
        completion_text = ""
        tools_call_name = ["execute_rule", "execute_rule", "update_scene", "final_response"]
        tools_call_args = [
            {
                "rule_name": "d20_skill_check",
                "args": {"skill": "stealth", "advantage": False, "bonus": 2, "dc": 14},
                "reason": "非法参数复现旧模型第一次调用",
            },
            {
                "rule_name": "d20_skill_check",
                "args": {"bonus": 2, "dc": 14},
                "reason": "雅卡隐蔽启动C4并藏入控制台下方线缆槽",
            },
            {
                "patch": {
                    "summary": "C4炸药已暗中启动并藏于控制台下。",
                    "pressure_clock": {"label": "C4倒计时", "status": "active"},
                }
            },
            {
                "reply": "计时器从5:00跳到4:59，炸药包已经藏进控制台下方线缆槽。你记录坐标后走向船尾深潜器。"
            },
        ]
        tool_calls = []

    class FakeLoopLlm:
        def __init__(self):
            self.calls = 0

        async def __call__(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeToolCallResponse()
            return FakeLlmResponse("不应要求第二次补充。")

    class RecordingExecutor:
        def __init__(self):
            self.calls = []

        async def execute(self, tool_name, args):
            self.calls.append((tool_name, args))
            if tool_name == "execute_rule" and len(self.calls) == 1:
                return {
                    "ok": False,
                    "error": "invalid_rule_arguments",
                    "unknown_arguments": ["advantage", "skill"],
                    "allowed_arguments": ["bonus", "dc"],
                    "reason": "unknown rule arguments: advantage, skill",
                }
            if tool_name == "execute_rule":
                return {"ok": True, "result": {"success": True, "total": 23, "dc": 14}}
            if tool_name == "update_scene":
                return {"ok": True, "scene": {"summary": args["patch"]["summary"]}}
            if tool_name == "final_response":
                return {"ok": True, "reply": args["reply"]}
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
            raw_player_message="进门前按下炸弹启动键，将布包放在控制台下方，记录位置坐标，走向船尾深潜器。",
            available_tool_names=["execute_rule", "update_scene", "final_response"],
        )
        return result, executor, repository.last_audit_records("group-1", limit=20)

    result, executor, records = asyncio.run(run_case())

    assert [name for name, _args in executor.calls] == [
        "execute_rule",
        "execute_rule",
        "update_scene",
        "final_response",
    ]
    assert result.completion_text == "计时器从5:00跳到4:59，炸药包已经藏进控制台下方线缆槽。你记录坐标后走向船尾深潜器。"
    assert not [record for record in records if record.get("type") == "adjudication_completeness_guard"]


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


def test_state_write_guard_result_includes_next_tool_hint():
    from astrbot_plugin_auto_trpg_dm.core.router import _tool_call_requires_adjudication_support

    result = _tool_call_requires_adjudication_support(
        "我射击瞭望兵然后躲回掩体",
        [],
        tool_name="update_scene",
    )

    assert result["ok"] is False
    assert result["error"] == "adjudication_guard_blocked_state_write"
    assert result["reason"] == "missing_execute_rule_for_risky_state_write"
    assert "next_tool_hint" in result
    assert "resolve_check" in result["next_tool_hint"]
    assert "不要重复调用" in result["message"]


def test_narrative_attack_state_write_after_roll_does_not_require_spatial_tool():
    from astrbot_plugin_auto_trpg_dm.core.router import _tool_call_requires_adjudication_support

    session = GameSession.new("group-1")
    session.scene["_game_started"] = True
    result = _tool_call_requires_adjudication_support(
        "瞄准射击仓库屋檐下的弓箭手",
        [{"tool": "resolve_check", "result": {"ok": True, "outcome": "success"}}],
        session=session,
        tool_name="update_scene",
    )

    assert result == {}


def test_tactical_attack_state_write_still_requires_spatial_or_turn_tool_after_roll():
    from astrbot_plugin_auto_trpg_dm.core.router import _tool_call_requires_adjudication_support

    session = GameSession.new("group-1")
    session.scene["_game_started"] = True
    session.mode = GameMode.TACTICAL
    result = _tool_call_requires_adjudication_support(
        "瞄准射击仓库屋檐下的弓箭手",
        [{"tool": "resolve_check", "result": {"ok": True, "outcome": "success"}}],
        session=session,
        tool_name="update_scene",
    )

    assert result["ok"] is False
    assert result["reason"] == "missing_spatial_or_turn_tool_for_state_write"
