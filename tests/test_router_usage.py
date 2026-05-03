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
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.router import IntentRouter, _extract_llm_usage_summary


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
