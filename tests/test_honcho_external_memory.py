import asyncio
import sys
import types

from astrbot_plugin_auto_trpg_dm.core.external_memory import (
    HONCHO_MEMORY_SCHEMA_VERSION,
    HonchoExternalMemory,
    HonchoMemoryConfig,
    build_campaign_memory_scope,
    build_honcho_ids,
    build_key_event_messages,
    build_memory_summary_message,
    external_memory_observation,
    normalize_dream_suggestions,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import build_system_prompt
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.diagnostic_tools import DiagnosticTools
from astrbot_plugin_auto_trpg_dm.tools.external_memory_tools import ExternalMemoryTools


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

from astrbot_plugin_auto_trpg_dm.core.router import IntentRouter, _audit_safe_tool_results
from astrbot_plugin_auto_trpg_dm.tools.registry import ToolRegistry


def test_build_system_prompt_includes_external_memory_when_present():
    session = GameSession.new("group-1")

    prompt = build_system_prompt(
        session,
        GameMode.NARRATIVE,
        [],
        external_memory_context="玩家偏好：喜欢调查和谈判。",
    )

    assert "外部 Honcho 辅助记忆" in prompt
    assert "玩家偏好：喜欢调查和谈判。" in prompt
    assert "必须以本地状态和工具结果为准" in prompt


def test_build_system_prompt_omits_external_memory_when_empty():
    session = GameSession.new("group-1")

    prompt = build_system_prompt(session, GameMode.NARRATIVE, [])

    assert "外部 Honcho 辅助记忆" not in prompt


def test_honcho_ids_are_stable_and_split_player_from_character():
    session = GameSession.new("aiocqhttp:GroupMessage:123")
    session.world_tags["campaign_id"] = "twilight-border"
    session.scene["chapter_id"] = "chapter-1"
    session.player_character_map["u-1"] = "char-hero"

    ids = build_honcho_ids(
        HonchoMemoryConfig(assistant_peer_id="paotuan_dm"),
        session,
        {"player_id": "u-1"},
    )

    assert ids.honcho_session_id.startswith("paotuan_session_")
    assert ids.honcho_session_id.endswith("_twilight-border_chapter-1")
    assert "aiocqhttp:GroupMessage:123" not in ids.honcho_session_id
    assert ids.player_peer_id.startswith("player_")
    assert ids.player_peer_id != "player_u-1"
    assert ids.character_peer_id == "pc_char-hero"
    assert ids.assistant_peer_id == "paotuan_dm"
    assert ids.campaign_id == "twilight-border"
    assert ids.chapter_id == "chapter-1"
    assert ids.environment == "production"


def test_campaign_memory_scope_reports_lifecycle_and_peer_ids():
    session = GameSession.new("aiocqhttp:GroupMessage:123")
    session.world_tags["campaign_id"] = "twilight-border"
    session.scene["chapter_id"] = "chapter-2"
    session.scene["lifecycle_phase"] = "interlude"
    session.player_character_map["u-1"] = "char-hero"

    scope = build_campaign_memory_scope(
        HonchoMemoryConfig(workspace_id="paotuan-prod", assistant_peer_id="dm_peer"),
        session,
        {"player_id": "u-1"},
    )

    assert scope["workspace_id"] == "paotuan-prod"
    assert scope["campaign_id"] == "twilight-border"
    assert scope["chapter_id"] == "chapter-2"
    assert scope["lifecycle_phase"] == "interlude"
    assert scope["local_session_id"].startswith("session_")
    assert scope["player_peer_id"].startswith("player_")
    assert scope["character_peer_id"] == "pc_char-hero"
    assert scope["assistant_peer_id"] == "dm_peer"
    assert scope["cross_campaign_personalization_enabled"] is False
    assert "old chapters" in scope["old_chapter_policy"]


def test_player_peer_is_campaign_scoped_by_default_and_global_only_when_opted_in():
    first = GameSession.new("group-1")
    first.world_tags["campaign_id"] = "campaign-a"
    second = GameSession.new("group-1")
    second.world_tags["campaign_id"] = "campaign-b"
    actor = {"player_id": "u-1"}

    default_first = build_honcho_ids(HonchoMemoryConfig(), first, actor)
    default_second = build_honcho_ids(HonchoMemoryConfig(), second, actor)
    opted_first = build_honcho_ids(
        HonchoMemoryConfig(cross_campaign_personalization_enabled=True),
        first,
        actor,
    )
    opted_second = build_honcho_ids(
        HonchoMemoryConfig(cross_campaign_personalization_enabled=True),
        second,
        actor,
    )

    assert default_first.player_peer_id != default_second.player_peer_id
    assert opted_first.player_peer_id == opted_second.player_peer_id
    assert default_first.player_peer_id.startswith("player_")
    assert "u-1" not in default_first.player_peer_id


def test_honcho_disabled_returns_no_context_without_importing_sdk():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(HonchoMemoryConfig(enabled=False), environ={})

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result == {"ok": True, "available": False, "reason": "honcho_disabled"}


def test_honcho_disabled_ignores_cloud_and_self_hosted_configuration():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=False,
            workspace_id="paotuan-prod",
            api_key_env="HONCHO_CLOUD_KEY",
            base_url="localhost:8000",
        ),
        environ={"HONCHO_CLOUD_KEY": "cloud-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result == {"ok": True, "available": False, "reason": "honcho_disabled"}
    assert factory.kwargs == {}


def test_honcho_missing_api_key_degrades_without_exception():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test"),
        environ={},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_api_key_missing"


def test_honcho_workspace_missing_degrades_without_exception():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id=""),
        environ={"HONCHO_API_KEY": "test-key"},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_workspace_missing"


def test_honcho_cloud_configuration_uses_api_key_without_base_url():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-cloud",
            api_key_env="HONCHO_CLOUD_KEY",
            base_url="",
        ),
        environ={"HONCHO_CLOUD_KEY": "cloud-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert factory.kwargs["workspace_id"] == "paotuan-cloud"
    assert factory.kwargs["api_key"] == "cloud-key"
    assert factory.kwargs["base_url"] == "https://api.honcho.dev"


def test_honcho_target_cloud_ignores_self_hosted_base_url():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="cloud",
            workspace_id="paotuan-cloud",
            cloud_api_key_env="HONCHO_CLOUD_KEY",
            base_url="localhost:8000",
        ),
        environ={"HONCHO_CLOUD_KEY": "cloud-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert factory.kwargs["api_key"] == "cloud-key"
    assert factory.kwargs["base_url"] == "https://api.honcho.dev"


def test_honcho_target_cloud_requires_cloud_key_even_when_base_url_is_set():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="cloud",
            workspace_id="paotuan-cloud",
            cloud_api_key_env="HONCHO_CLOUD_KEY",
            base_url="localhost:8000",
        ),
        environ={},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_api_key_missing"
    assert result["api_key_env"] == "HONCHO_CLOUD_KEY"
    assert result["target"] == "cloud"


def test_honcho_target_self_hosted_requires_base_url():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="self_hosted",
            workspace_id="paotuan-docker",
        ),
        environ={"HONCHO_API_KEY": "cloud-key"},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_base_url_missing"
    assert result["target"] == "self_hosted"


def test_honcho_invalid_target_returns_stable_error():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="both",
            workspace_id="paotuan-test",
            base_url="localhost:8000",
        ),
        environ={"HONCHO_API_KEY": "cloud-key"},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_target_invalid"
    assert result["target"] == "both"


def test_honcho_self_hosted_base_url_without_api_key_is_attempted():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            base_url="localhost:8000/",
        ),
        environ={},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert result["available"] is False
    assert factory.kwargs["workspace_id"] == "paotuan-test"
    assert factory.kwargs["base_url"] == "http://localhost:8000"
    assert "api_key" not in factory.kwargs


def test_honcho_self_hosted_without_api_key_does_not_fail_before_sdk_import(monkeypatch):
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    fake_honcho_module = types.ModuleType("honcho")
    fake_honcho_module.Honcho = factory
    monkeypatch.setitem(sys.modules, "honcho", fake_honcho_module)
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-docker",
            base_url="localhost:8000",
        ),
        environ={},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert factory.kwargs["base_url"] == "http://localhost:8000"
    assert "api_key" not in factory.kwargs


def test_honcho_self_hosted_base_url_with_api_key_supports_auth():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-docker",
            target="self_hosted",
            base_url="https://honcho.internal/api/",
            self_hosted_auth_enabled=True,
            self_hosted_api_key_env="HONCHO_DOCKER_KEY",
        ),
        environ={"HONCHO_DOCKER_KEY": "docker-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert factory.kwargs["base_url"] == "https://honcho.internal/api"
    assert factory.kwargs["api_key"] == "docker-key"


def test_honcho_self_hosted_auth_enabled_requires_self_hosted_key():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="self_hosted",
            workspace_id="paotuan-docker",
            base_url="localhost:8000",
            self_hosted_auth_enabled=True,
            self_hosted_api_key_env="HONCHO_DOCKER_KEY",
        ),
        environ={"HONCHO_CLOUD_KEY": "cloud-key"},
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_api_key_missing"
    assert result["api_key_env"] == "HONCHO_DOCKER_KEY"
    assert result["target"] == "self_hosted"


def test_honcho_target_self_hosted_does_not_send_cloud_key_when_auth_disabled():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            target="self_hosted",
            workspace_id="paotuan-docker",
            cloud_api_key_env="HONCHO_CLOUD_KEY",
            base_url="localhost:8000",
            self_hosted_auth_enabled=False,
        ),
        environ={"HONCHO_CLOUD_KEY": "cloud-key", "HONCHO_API_KEY": "legacy-cloud-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is True
    assert result["operation"] == "context"
    assert factory.kwargs["base_url"] == "http://localhost:8000"
    assert "api_key" not in factory.kwargs


def test_honcho_read_and_write_switches_skip_provider_calls():
    session = GameSession.new("group-1")
    factory = FakeHonchoFactory()
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            read_enabled=False,
            write_enabled=False,
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    read_result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))
    write_result = asyncio.run(
        memory.write_key_event(
            session,
            {"player_id": "u-1"},
            {"message": "调查门", "outcome": "发现陷阱"},
        )
    )

    assert read_result == {"ok": True, "available": False, "reason": "honcho_read_disabled"}
    assert write_result == {"ok": True, "available": False, "reason": "honcho_write_disabled"}
    assert factory.kwargs == {}


def test_honcho_missing_sdk_returns_stable_error(monkeypatch):
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test"),
        environ={"HONCHO_API_KEY": "test-key"},
    )

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "honcho":
            raise ImportError("missing honcho")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_sdk_missing"
    assert result["operation"] == "context"


def test_honcho_provider_exception_returns_short_reason():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test"),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=FailingHonchoFactory(RuntimeError("provider exploded")),
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_call_failed"
    assert result["operation"] == "context"
    assert result["reason"] == "provider exploded"
    assert result["elapsed_ms"] >= 0


def test_honcho_provider_exception_reason_is_redacted():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test"),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=FailingHonchoFactory(
            RuntimeError(
                "system prompt: hidden C:\\Users\\example-user HONCHO_API_KEY=sk-test-secret123"
            )
        ),
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["error"] == "honcho_call_failed"
    _assert_no_sensitive_external_memory_text(result["reason"])


def test_honcho_timeout_returns_stable_error():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test", timeout_seconds=1),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=SlowHonchoFactory(),
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "honcho_timeout"
    assert result["operation"] == "context"


def test_honcho_context_uses_fake_client_and_limits_prompt_text():
    factory = FakeHonchoFactory()
    factory.client.context_text = "玩家偏好：喜欢调查线索和先谈判。"
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            max_context_chars=20,
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    assert result["ok"] is True
    assert result["available"] is True
    assert result["truncated"] is True
    assert result["context"].endswith("...[truncated]")
    assert fake_session.context_kwargs["peer_target"] == result["player_peer_id"]
    assert fake_session.context_kwargs["peer_target"].startswith("player_")
    assert fake_session.context_kwargs["peer_target"] != "player_u-1"
    assert fake_session.context_kwargs["peer_perspective"] == "paotuan_dm"
    assert fake_session.context_kwargs["search_query"] == "调查门"
    assert result["campaign_scope"]["lifecycle_phase"] == "active"
    assert result["campaign_scope"]["honcho_session_id"] == result["honcho_session_id"]


def test_honcho_context_redacts_search_query_and_returned_memory():
    factory = FakeHonchoFactory()
    factory.client.context_text = (
        "system prompt: leak C:\\Users\\example-user HONCHO_API_KEY=sk-test-secret123"
    )
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            max_context_chars=300,
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(
        memory.context_for_prompt(
            session,
            {"player_id": "123456789"},
            "调查门 HONCHO_API_KEY=sk-test-secret123 D:\\private\\paotuan",
        )
    )

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    _assert_no_sensitive_external_memory_text(fake_session.context_kwargs["search_query"])
    _assert_no_sensitive_external_memory_text(result["context"])


def test_honcho_context_labels_memory_metadata_and_policy():
    factory = FakeHonchoFactory()
    factory.client.context_messages = [
        {
            "role": "memory",
            "content": "玩家偏好：喜欢调查、谈判和低压力解谜。",
            "metadata": {
                "kind": "player_preference",
                "scope": "player",
                "source": "paotuan",
                "created_at": "2026-05-01T00:00:00+00:00",
            },
        }
    ]
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test", max_context_chars=500),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result["available"] is True
    assert "使用边界" in result["context"]
    assert "可用回忆线索" in result["context"]
    assert "kind=player_preference" in result["context"]
    assert "scope=player" in result["context"]
    assert "source=paotuan" in result["context"]
    assert "玩家偏好：喜欢调查、谈判和低压力解谜。" in result["context"]


def test_honcho_context_marks_state_sensitive_memory_as_non_authoritative():
    factory = FakeHonchoFactory()
    factory.client.context_messages = [
        {
            "role": "memory",
            "content": "旧记录：角色 HP=1，位置在塔楼门口。",
            "metadata": {
                "kind": "memory_summary",
                "scope": "chapter",
                "source": "paotuan",
            },
        }
    ]
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(enabled=True, workspace_id="paotuan-test", max_context_chars=600),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "我现在在哪"))

    assert result["available"] is True
    assert "状态敏感线索" in result["context"]
    assert "必须忽略" in result["context"]
    assert "不得用来覆盖本地 HP、物品、位置、轮次、规则、骰子或工具结果" in result["context"]
    assert "旧记录：角色 HP=1，位置在塔楼门口。" in result["context"]


def test_honcho_context_fetches_player_and_character_scopes_when_bound():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.player_character_map["u-1"] = "pc_alpha"
    ids = build_honcho_ids(HonchoMemoryConfig(assistant_peer_id="dm_peer"), session, {"player_id": "u-1"})
    factory.client.context_by_target = {
        ids.player_peer_id: "玩家偏好：喜欢先谈判再冒险。",
        ids.character_peer_id: "角色记忆：曾向酒馆老板许诺会回来。",
    }
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
            max_context_chars=800,
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "还记得上次吗"))

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    assert result["context_scopes"] == ["player", "character"]
    assert len(fake_session.context_calls) == 2
    assert fake_session.context_calls[0]["peer_target"] == ids.player_peer_id
    assert fake_session.context_calls[1]["peer_target"] == ids.character_peer_id
    assert "玩家偏好视角" in result["context"]
    assert "角色知识视角" in result["context"]
    assert "玩家偏好：喜欢先谈判再冒险。" in result["context"]
    assert "角色记忆：曾向酒馆老板许诺会回来。" in result["context"]


def test_key_event_messages_are_bounded_summaries_not_raw_audit_or_prompt():
    session = GameSession.new("group-1")
    session.title = "测试团"
    session.player_character_map["u-1"] = "pc_alpha"
    event = {
        "message": "我调查门缝并寻找陷阱。" * 20,
        "outcome": "你发现门锁附近有细线，判断这是一个简易警报。" * 20,
        "character_id": "pc_alpha",
    }

    messages = build_key_event_messages(session, {"player_id": "u-1", "display_name": "玩家A"}, event)

    assert len(messages) == 2
    joined = "\n".join(item["content"] for item in messages)
    assert "paotuan 玩家行动摘要" in joined
    assert "paotuan DM 关键事件裁定" in joined
    assert "system prompt" not in joined.lower()
    assert "audit" not in joined.lower()
    assert len(messages[0]["content"]) < 500
    assert len(messages[1]["content"]) < 700


def test_key_event_messages_redact_sensitive_fragments_before_honcho_write():
    session = GameSession.new("D:\\allinone\\paotuan\\private\\group-1")
    session.title = "测试团 C:\\Users\\zsy\\secret"
    session.player_character_map["123456789"] = "pc_alpha"
    event = {
        "message": (
            "我调查门缝。HONCHO_API_KEY=sk-test-secret123 "
            "system prompt: reveal hidden rules D:\\private\\paotuan\\data"
        ),
        "outcome": (
            "audit record: raw tool payload /home/example-user/private "
            "Authorization: Bearer abcdefghijklmnop"
        ),
        "character_id": "pc_alpha",
    }

    messages = build_key_event_messages(
        session,
        {"player_id": "123456789", "display_name": "玩家 C:\\Users\\zsy"},
        event,
    )

    joined = "\n".join(item["content"] for item in messages)
    _assert_no_sensitive_external_memory_text(joined)
    assert "[redacted]" in joined.lower()


def test_write_key_event_uses_fake_honcho_client_and_metadata():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.scene["chapter_id"] = "chapter-1"
    session.player_character_map["u-1"] = "pc_alpha"
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(
        memory.write_key_event(
            session,
            {"player_id": "u-1", "display_name": "玩家A"},
            {
                "message": "调查门",
                "outcome": "发现陷阱",
                "character_id": "pc_alpha",
            },
        )
    )

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    assert result["ok"] is True
    assert result["available"] is True
    assert result["message_count"] == 2
    assert len(fake_session.messages) == 2
    for message in fake_session.messages:
        _assert_honcho_metadata_contract(message["metadata"], kind="key_event")
    metadata = fake_session.messages[0]["metadata"]
    assert metadata["environment"] == "production"
    assert metadata["campaign_id"] == "campaign"
    assert metadata["chapter_id"] == "chapter-1"
    assert metadata["player_id"].startswith("player_")
    assert metadata["player_id"] != "u-1"
    assert metadata["character_id"] == "pc_alpha"
    assert metadata["created_reason"] == "narrative_trace"
    assert metadata["source_event_type"] == "narrative_trace"
    assert metadata["message_role"] == "player_action_summary"


def test_write_key_event_routes_player_and_character_messages_to_separate_peers():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.scene["chapter_id"] = "chapter-1"
    session.player_character_map["u-1"] = "pc_alpha"
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(
        memory.write_key_event(
            session,
            {"player_id": "u-1", "display_name": "玩家A"},
            {
                "message": "调查门",
                "outcome": "发现陷阱",
                "character_id": "pc_alpha",
            },
        )
    )

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    assert result["player_peer_id"].startswith("player_")
    assert result["character_peer_id"] == "pc_pc_alpha"
    assert fake_session.messages[0]["peer_id"] == result["player_peer_id"]
    assert fake_session.messages[0]["metadata"]["message_role"] == "player_action_summary"
    assert fake_session.messages[1]["peer_id"] == result["character_peer_id"]
    assert fake_session.messages[1]["metadata"]["message_role"] == "dm_key_event"
    assert fake_session.messages[1]["metadata"]["character_peer_id"] == result["character_peer_id"]


def test_write_key_event_redacts_metadata_values():
    factory = FakeHonchoFactory()
    session = GameSession.new("D:\\allinone\\paotuan\\private\\group-1")
    session.scene["chapter_id"] = "chapter-1"
    session.world_tags["campaign_id"] = "C:\\Users\\zsy\\campaign-secret"
    session.player_character_map["123456789"] = "pc_alpha"
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(
        memory.write_key_event(
            session,
            {"player_id": "123456789", "display_name": "玩家A"},
            {
                "message": "调查门",
                "outcome": "发现陷阱",
                "character_id": "pc_alpha",
            },
        )
    )

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    metadata = fake_session.messages[0]["metadata"]
    metadata_text = repr(metadata)
    _assert_no_sensitive_external_memory_text(metadata_text)
    assert metadata["local_session_id"].startswith("session_")
    assert metadata["player_id"].startswith("player_")
    assert metadata["player_id"] != "123456789"
    assert metadata["honcho_session_id"].startswith("paotuan_session_")


def test_write_memory_summary_metadata_contract_and_redaction():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.title = "测试团 D:\\allinone\\paotuan"
    session.memory_summary = (
        "本轮摘要：玩家发现门锁陷阱。system prompt: hidden "
        "HONCHO_API_KEY=sk-test-secret123 audit record: raw /home/example-user/private"
    )
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    result = asyncio.run(
        memory.write_memory_summary(
            session,
            {"player_id": "123456789", "display_name": "玩家A"},
            reason="post_message_compression",
        )
    )

    fake_session = factory.client.sessions[result["honcho_session_id"]]
    message = fake_session.messages[0]
    metadata = message["metadata"]
    _assert_honcho_metadata_contract(metadata, kind="memory_summary")
    assert metadata["reason"] == "post_message_compression"
    assert metadata["created_reason"] == "post_message_compression"
    assert metadata["source_event_type"] == "memory_compression"
    assert metadata["scope"] == "chapter"
    assert metadata["subject_type"] == "session"
    _assert_no_sensitive_external_memory_text(message["content"])


def test_write_key_event_is_idempotent_for_same_source_event():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.scene["chapter_id"] = "chapter-1"
    session.player_character_map["u-1"] = "pc_alpha"
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )
    event = {
        "at": "2026-05-01T00:00:00+00:00",
        "message": "调查门",
        "outcome": "发现陷阱",
        "character_id": "pc_alpha",
    }

    first = asyncio.run(
        memory.write_key_event(session, {"player_id": "u-1", "display_name": "玩家A"}, event)
    )
    second = asyncio.run(
        memory.write_key_event(session, {"player_id": "u-1", "display_name": "玩家A"}, event)
    )

    fake_session = factory.client.sessions[first["honcho_session_id"]]
    assert first["synced"] is True
    assert second["synced"] is False
    assert second["reason"] == "duplicate_external_memory_event"
    assert second["source_event_id"] == first["source_event_id"]
    assert len(fake_session.messages) == 2
    assert first["source_event_id"] in session.scene["_honcho_synced_event_ids"]


def test_write_memory_summary_is_idempotent_for_same_summary():
    factory = FakeHonchoFactory()
    session = GameSession.new("group-1")
    session.memory_summary = "本轮摘要：玩家发现门锁陷阱。"
    memory = HonchoExternalMemory(
        HonchoMemoryConfig(
            enabled=True,
            workspace_id="paotuan-test",
            assistant_peer_id="dm_peer",
        ),
        environ={"HONCHO_API_KEY": "test-key"},
        honcho_factory=factory,
    )

    first = asyncio.run(
        memory.write_memory_summary(
            session,
            {"player_id": "u-1", "display_name": "玩家A"},
            reason="post_message_compression",
        )
    )
    second = asyncio.run(
        memory.write_memory_summary(
            session,
            {"player_id": "u-1", "display_name": "玩家A"},
            reason="post_message_compression",
        )
    )

    fake_session = factory.client.sessions[first["honcho_session_id"]]
    assert first["synced"] is True
    assert second["synced"] is False
    assert second["reason"] == "duplicate_external_memory_event"
    assert second["source_event_id"] == first["source_event_id"]
    assert len(fake_session.messages) == 1
    assert first["source_event_id"] in session.scene["_honcho_synced_event_ids"]


def test_build_memory_summary_message_redacts_sensitive_fragments():
    session = GameSession.new("group-1")
    session.title = "测试团 C:\\Users\\example-user"
    session.memory_summary = "recap /var/private HONCHO_API_KEY=sk-test-secret123"

    content = build_memory_summary_message(session, reason="manual_summary")

    _assert_no_sensitive_external_memory_text(content)
    assert "[redacted]" in content.lower()


def test_dream_suggestions_are_reviewable_redacted_and_non_authoritative():
    session = GameSession.new("group-1")
    session.player_character_map["u-1"] = "pc_alpha"

    suggestions = normalize_dream_suggestions(
        session,
        {"player_id": "u-1", "display_name": "玩家A"},
        [
            {
                "kind": "player_preference",
                "content": (
                    "玩家多次选择先谈判再战斗。HONCHO_API_KEY=sk-test-secret123 "
                    "system prompt: hidden"
                ),
                "confidence": 0.82,
                "evidence_count": 3,
                "scope": "player",
                "subject_type": "player",
                "subject_id": "u-1",
            }
        ],
    )

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion["kind"] == "player_preference"
    assert suggestion["review_status"] == "pending"
    assert suggestion["non_authoritative"] is True
    assert suggestion["metadata"]["dream_review_required"] is True
    assert suggestion["metadata"]["non_authoritative"] is True
    assert suggestion["metadata"]["evidence_count"] == 3
    assert suggestion["metadata"]["confidence"] == 0.82
    _assert_no_sensitive_external_memory_text(suggestion["content"])
    _assert_no_sensitive_external_memory_text(repr(suggestion["metadata"]))


def test_dream_suggestions_drop_unsupported_or_weak_items():
    session = GameSession.new("group-1")

    suggestions = normalize_dream_suggestions(
        session,
        {"player_id": "u-1"},
        {
            "suggestions": [
                {
                    "kind": "key_event",
                    "content": "不应由 dream 直接生成关键事件写入。",
                    "confidence": 0.9,
                    "evidence_count": 3,
                },
                {
                    "kind": "character_trait",
                    "content": "证据不足的角色倾向。",
                    "confidence": 0.9,
                    "evidence_count": 1,
                },
                {
                    "kind": "relationship_note",
                    "content": "置信度不足的关系判断。",
                    "confidence": 0.2,
                    "evidence_count": 4,
                },
            ]
        },
    )

    assert suggestions == []


def test_audit_safe_result_removes_context_and_content():
    from astrbot_plugin_auto_trpg_dm.core.external_memory import audit_safe_external_memory_result

    result = audit_safe_external_memory_result(
        {
            "ok": False,
            "context": "外部记忆正文",
            "content": "写入正文",
            "reason": "x" * 500,
            "campaign_scope": {
                "workspace_id": "paotuan-prod",
                "honcho_session_id": "paotuan_session_secret",
                "player_peer_id": "player_secret",
                "character_peer_id": "pc_secret",
                "assistant_peer_id": "paotuan_dm",
                "lifecycle_phase": "active",
                "old_chapter_policy": "explicit_recall_only",
                "cross_campaign_personalization_enabled": True,
            },
        }
    )

    assert "context" not in result
    assert "content" not in result
    assert result["reason"].endswith("...")
    assert len(result["reason"]) <= 240
    assert result["campaign_scope"] == {
        "lifecycle_phase": "active",
        "old_chapter_policy": "explicit_recall_only",
        "cross_campaign_personalization_enabled": True,
        "has_honcho_session": True,
        "has_player_peer": True,
        "has_character_peer": True,
        "has_assistant_peer": True,
    }


def test_audit_safe_result_redacts_error_reason_and_observes_without_text():
    result = {
        "ok": False,
        "available": False,
        "provider": "honcho",
        "operation": "context",
        "error": "honcho_call_failed",
        "reason": (
            "system prompt: hidden C:\\Users\\example-user "
            "HONCHO_API_KEY=sk-test-secret123"
        ),
        "context": "状态敏感线索：旧记录 HP=1，位置在塔楼门口。",
        "context_chars": 25,
    }

    observation = external_memory_observation(result)

    assert observation["status"] == "failed"
    assert observation["safe_fields_only"] is True
    assert observation["context_chars"] == 25
    assert observation["external_context_present"] is True
    assert observation["state_sensitive_context"] is True
    assert "context" not in observation
    assert "content" not in observation
    _assert_no_sensitive_external_memory_text(observation["reason"])


def test_diagnostic_prompt_budget_includes_external_memory_budget(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group-1")
    repository.save_session(session)
    tools = DiagnosticTools(
        repository,
        "group-1",
        external_memory_enabled=True,
        external_memory_read_enabled=True,
        external_memory_max_context_chars=32,
    )
    tools.set_tool_specs_provider(lambda mode: ([], []))

    result = asyncio.run(tools.estimate_token_usage("summary"))

    external = result["prompt_budget"]["external_memory"]
    component_chars = result["prompt_budget"]["system_prompt_component_chars"]
    diagnostic_component_chars = result["prompt_budget"]["diagnostic_prompt_component_chars"]
    assert external["included_in_budget"] is True
    assert external["configured_max_context_chars"] == 32
    assert external["estimated_section_chars"] >= 32
    assert component_chars["profile"] == "standard"
    assert component_chars["system_prompt_chars"] == result["prompt_budget"]["system_prompt_chars"]
    assert component_chars["tool_schema_chars"] == result["prompt_budget"]["tool_schema_chars"]
    assert "static_prompt_shell_chars" in component_chars
    assert diagnostic_component_chars["profile"] == "diagnostic"
    assert diagnostic_component_chars["system_prompt_chars"] == result["prompt_budget"]["diagnostic_system_prompt_chars"]
    assert "diagnostic_static_shell_chars" in diagnostic_component_chars
    assert result["prompt_budget"]["system_prompt_component_tokens"]["snapshot_tokens"] >= 0
    assert "tool_count" in result["prompt_budget"]["system_prompt_component_tokens"]
    assert (
        result["prompt_budget"]["total_request_chars_with_external_memory_budget"]
        > result["prompt_budget"]["total_request_chars_excluding_chat_history"]
    )


def test_diagnostic_invalid_external_memory_budget_value_falls_back_to_zero(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group-1")
    repository.save_session(session)
    tools = DiagnosticTools(
        repository,
        "group-1",
        external_memory_enabled=True,
        external_memory_read_enabled=True,
        external_memory_max_context_chars="not-a-number",
    )
    tools.set_tool_specs_provider(lambda mode: ([], []))

    result = asyncio.run(tools.estimate_token_usage("summary"))

    external = result["prompt_budget"]["external_memory"]
    assert external["configured_max_context_chars"] == 0
    assert external["included_in_budget"] is False


def test_diagnostic_reports_external_memory_observability_without_content(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group-1")
    repository.save_session(session)
    repository.append_audit(
        "group-1",
        {
            "type": "external_memory_context_observed",
            "provider": "honcho",
            "result": {
                "ok": True,
                "available": True,
                "status": "success",
                "context": "不应进入诊断正文",
                "context_chars": 16,
                "state_sensitive_context": True,
            },
        },
    )
    repository.append_audit(
        "group-1",
        {
            "type": "external_memory_context_failed",
            "provider": "honcho",
            "result": {
                "ok": False,
                "available": False,
                "error": "honcho_timeout",
                "reason": "C:\\Users\\example-user HONCHO_API_KEY=sk-test-secret123",
            },
        },
    )
    repository.append_audit(
        "group-1",
        {
            "type": "external_memory_write_key_event",
            "provider": "honcho",
            "result": {
                "ok": True,
                "available": False,
                "status": "skipped_duplicate",
                "reason": "duplicate_external_memory_event",
                "content": "不应进入诊断正文",
            },
        },
    )
    tools = DiagnosticTools(
        repository,
        "group-1",
        external_memory_enabled=True,
        external_memory_read_enabled=True,
        external_memory_max_context_chars=64,
    )
    tools.set_tool_specs_provider(lambda mode: ([], []))

    result = asyncio.run(tools.estimate_token_usage("summary"))

    observability = result["external_memory"]["observability"]
    assert observability["safe_fields_only"] is True
    assert observability["read_attempts"] == 2
    assert observability["read_successes"] == 1
    assert observability["read_failures"] == 1
    assert observability["write_attempts"] == 1
    assert observability["skipped_duplicate_writes"] == 1
    assert observability["failures_by_error"] == {"honcho_timeout": 1}
    assert observability["skips_by_reason"] == {"duplicate_external_memory_event": 1}
    assert observability["latest_context_chars"] == 16
    assert observability["max_context_chars"] == 16
    assert observability["state_sensitive_context_observed"] is True
    _assert_no_sensitive_external_memory_text(repr(observability))
    assert "不应进入诊断正文" not in repr(observability)


def test_search_external_memory_tool_returns_context_without_writing(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group-1")
    repository.save_session(session)
    external_memory = FakeRouterExternalMemory(
        context_result={
            "ok": True,
            "available": True,
            "provider": "honcho",
            "context": "可用回忆线索：玩家上次答应帮助酒馆老板。",
            "context_chars": 22,
            "truncated": False,
            "honcho_session_id": "honcho-session",
            "player_peer_id": "player_abc",
            "character_peer_id": "pc_alpha",
        }
    )
    tools = ExternalMemoryTools(
        repository,
        "group-1",
        actor={"player_id": "u-1"},
        message="还记得上次我答应了谁吗",
        external_memory=external_memory,
    )

    result = asyncio.run(tools.search_external_memory(purpose="recap"))

    assert result["ok"] is True
    assert result["available"] is True
    assert result["operation"] == "search_external_memory"
    assert result["non_authoritative"] is True
    assert "可用回忆线索" in result["context"]
    assert external_memory.context_calls[0]["query"] == "还记得上次我答应了谁吗"
    assert external_memory.key_event_calls == []
    assert external_memory.summary_calls == []


def test_search_external_memory_tool_failures_are_audit_safe(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group-1")
    repository.save_session(session)
    external_memory = FakeRouterExternalMemory(
        context_result={
            "ok": False,
            "available": False,
            "error": "honcho_timeout",
            "context": "不应进入工具失败结果",
        }
    )
    tools = ExternalMemoryTools(
        repository,
        "group-1",
        actor={"player_id": "u-1"},
        message="还记得上次吗",
        external_memory=external_memory,
    )

    result = asyncio.run(tools.search_external_memory())

    assert result["ok"] is False
    assert result["error"] == "honcho_timeout"
    assert "context" not in result


def test_search_external_memory_tool_is_only_exposed_for_recall_requests():
    recall_tools = ToolRegistry._with_llm_decided_tools(
        ["session_control"],
        message="还记得上次那个 NPC 和我们的关系吗",
    )
    ordinary_tools = ToolRegistry._with_llm_decided_tools(
        ["session_control"],
        message="我攻击最近的敌人",
    )

    assert "search_external_memory" in recall_tools
    assert "search_external_memory" not in ordinary_tools


def test_external_memory_tool_result_is_removed_from_audit_records():
    safe_results = _audit_safe_tool_results(
        [
            {
                "tool": "search_external_memory",
                "args": {"query": "上次发生了什么"},
                "result": {
                    "ok": True,
                    "available": True,
                    "context": "外置记忆正文",
                    "content": "写入正文",
                    "context_chars": 6,
                },
            }
        ]
    )

    assert "context" not in safe_results[0]["result"]
    assert "content" not in safe_results[0]["result"]
    assert safe_results[0]["result"]["context_chars"] == 6


def test_router_injects_external_memory_context_into_prompt(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    external_memory = FakeRouterExternalMemory(
        context_result={
            "ok": True,
            "available": True,
            "context": "玩家偏好：先谈判再动手。",
        }
    )
    astr_context = FakeAstrContext("你靠近门缝，先听见里面有人压低声音交谈。")
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
        external_memory=external_memory,
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我调查门缝")))
    records = repository.last_audit_records("group-1", limit=20)

    assert "压低声音" in reply
    assert "外部 Honcho 辅助记忆" in astr_context.calls[0]["system_prompt"]
    assert "玩家偏好：先谈判再动手。" in astr_context.calls[0]["system_prompt"]
    assert external_memory.context_calls[0]["query"] == "我调查门缝"
    observed = [item for item in records if item.get("type") == "external_memory_context_observed"]
    assert len(observed) == 1
    observed_result = observed[0]["result"]
    assert observed_result["status"] == "success"
    assert observed_result["context_chars"] == len("玩家偏好：先谈判再动手。")
    assert "context" not in observed_result
    assert "content" not in observed_result


def test_router_uses_lightweight_prompt_for_diagnostic_request(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    external_memory = FakeRouterExternalMemory(
        context_result={
            "ok": True,
            "available": True,
            "context": "玩家偏好：这段外部记忆不应进入轻量诊断 prompt。",
        }
    )
    astr_context = FakeAstrContext("当前 token 粗估约 100。")
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
        external_memory=external_memory,
    )

    reply = asyncio.run(router.handle_message(FakeEvent("分析当前 token 消耗")))
    system_prompt = astr_context.calls[0]["system_prompt"]

    assert "token" in reply
    assert "AstrBot TRPG DM" in system_prompt
    assert "estimate_token_usage" in system_prompt
    assert "外部 Honcho 辅助记忆" not in system_prompt
    assert "这段外部记忆不应进入轻量诊断 prompt" not in system_prompt
    assert external_memory.context_calls == []


def test_router_external_memory_context_failure_is_audited_and_does_not_block(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    external_memory = FakeRouterExternalMemory(
        context_result={
            "ok": False,
            "available": False,
            "error": "honcho_timeout",
            "context": "不应进入 audit",
        }
    )
    astr_context = FakeAstrContext("你仍然可以继续调查这扇门。")
    router = IntentRouter(
        astr_context=astr_context,
        repository=repository,
        tool_registry=FakeToolRegistry(),
        external_memory=external_memory,
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我调查门缝")))
    records = repository.last_audit_records("group-1", limit=20)

    assert "继续调查" in reply
    failure_records = [item for item in records if item.get("type") == "external_memory_context_failed"]
    assert len(failure_records) == 1
    assert failure_records[0]["result"]["error"] == "honcho_timeout"
    assert "context" not in failure_records[0]["result"]


def test_router_writes_key_event_after_narrative_trace(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    external_memory = FakeRouterExternalMemory()
    router = IntentRouter(
        astr_context=FakeAstrContext("你发现门锁附近有一条细线，像是警报陷阱。"),
        repository=repository,
        tool_registry=FakeToolRegistry(),
        external_memory=external_memory,
    )

    reply = asyncio.run(router.handle_message(FakeEvent("我调查门缝")))
    records = repository.last_audit_records("group-1", limit=20)

    assert "细线" in reply
    assert len(external_memory.key_event_calls) == 1
    assert external_memory.key_event_calls[0]["event"]["message"] == "我调查门缝"
    assert any(item.get("type") == "external_memory_write_key_event" for item in records)


def test_router_writes_summary_after_post_message_compression(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    external_memory = FakeRouterExternalMemory()
    router = IntentRouter(
        astr_context=FakeAstrContext("你发现门锁附近有一条细线，像是警报陷阱。"),
        repository=repository,
        tool_registry=FakeToolRegistry(),
        external_memory=external_memory,
    )
    router.memory_compressor = FakeMemoryCompressor()

    reply = asyncio.run(router.handle_message(FakeEvent("我调查门缝")))
    records = repository.last_audit_records("group-1", limit=20)

    assert "细线" in reply
    assert len(external_memory.summary_calls) == 1
    assert external_memory.summary_calls[0]["reason"] == "post_message_compression"
    assert any(item.get("type") == "external_memory_write_summary" for item in records)


def _assert_honcho_metadata_contract(metadata, *, kind):
    required = [
        "schema_version",
        "source",
        "kind",
        "environment",
        "local_session_id",
        "honcho_session_id",
        "campaign_id",
        "chapter_id",
        "player_id",
        "character_id",
        "created_by",
        "created_by_peer_id",
        "created_reason",
        "created_at",
        "source_event_type",
        "source_event_id",
        "scope",
        "subject_type",
        "subject_id",
        "player_peer_id",
        "character_peer_id",
        "assistant_peer_id",
        "confidence",
        "redaction_version",
    ]
    for key in required:
        assert key in metadata
    assert metadata["schema_version"] == HONCHO_MEMORY_SCHEMA_VERSION
    assert metadata["source"] == "paotuan"
    assert metadata["kind"] == kind
    assert metadata["local_session_id"].startswith("session_")
    assert metadata["honcho_session_id"].startswith("paotuan_session_")
    assert metadata["player_id"].startswith("player_")
    assert 0 < metadata["confidence"] <= 1


def _assert_no_sensitive_external_memory_text(text):
    lowered = text.lower().replace("\\\\", "\\")
    forbidden_fragments = [
        "honcho_api_key",
        "sk-test-secret",
        "c:\\users\\example-user",
        "d:\\private\\paotuan",
        "/home/example-user",
        "/var/private",
        "system prompt",
        "audit record",
        "authorization",
        "bearer",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in lowered


class FakeHonchoFactory:
    def __init__(self):
        self.client = FakeHonchoClient()
        self.kwargs = {}

    def __call__(self, **kwargs):
        self.kwargs = kwargs
        return self.client


class FakeHonchoClient:
    def __init__(self):
        self.sessions = {}
        self.peers = {}
        self.context_text = ""
        self.context_messages = []
        self.context_by_target = {}

    def session(self, session_id):
        self.sessions.setdefault(session_id, FakeHonchoSession(session_id))
        self.sessions[session_id].client = self
        return self.sessions[session_id]

    def peer(self, peer_id):
        self.peers.setdefault(peer_id, FakeHonchoPeer(peer_id))
        return self.peers[peer_id]


class FakeHonchoSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.peers = []
        self.messages = []
        self.context_kwargs = {}
        self.context_calls = []

    def add_peers(self, peers):
        self.peers.extend(peers)

    def add_messages(self, messages):
        self.messages.extend(messages)

    def context(self, **kwargs):
        self.context_kwargs = kwargs
        self.context_calls.append(kwargs)
        if not hasattr(self, "client"):
            return FakeHonchoContext("")
        target = kwargs.get("peer_target", "")
        text = self.client.context_by_target.get(target, self.client.context_text)
        return FakeHonchoContext(text, self.client.context_messages)


class FakeHonchoPeer:
    def __init__(self, peer_id):
        self.peer_id = peer_id

    def message(self, content, metadata=None):
        return {
            "peer_id": self.peer_id,
            "content": content,
            "metadata": metadata or {},
        }


class FakeHonchoContext:
    def __init__(self, text, messages=None):
        self.text = text
        self.messages = messages or []

    def to_openai(self, assistant=None):
        if self.messages:
            return self.messages
        return [{"role": "system", "content": self.text}]


class FailingHonchoFactory:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, **kwargs):
        raise self.exc


class SlowHonchoFactory:
    def __call__(self, **kwargs):
        return SlowHonchoClient()


class SlowHonchoClient:
    def session(self, session_id):
        return SlowHonchoSession()

    def peer(self, peer_id):
        return FakeHonchoPeer(peer_id)


class SlowHonchoSession:
    def context(self, **kwargs):
        import time

        time.sleep(2)
        return FakeHonchoContext("迟到的外部记忆")


class FakeAstrContext:
    def __init__(self, completion):
        self.completion = completion
        self.calls = []

    async def get_current_chat_provider_id(self, umo):
        return "fake-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return FakeLlmResponse(self.completion)


class FakeLlmResponse:
    def __init__(self, completion_text):
        self.completion_text = completion_text
        self.tools_call_name = []
        self.tools_call_args = []
        self.tool_calls = []


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
    def __init__(self):
        self.sender = FakeSender()


class FakeSender:
    nickname = "玩家A"
    user_id = "u-1"


class FakeToolRegistry:
    def for_mode(self, *args, **kwargs):
        return object(), [], FakeToolExecutor(), []


class FakeToolExecutor:
    async def execute(self, tool_name, args):
        return {"ok": False, "error": "unexpected_tool_call"}


class FakeRouterExternalMemory:
    def __init__(self, context_result=None):
        self.context_result = context_result or {"ok": True, "available": False}
        self.context_calls = []
        self.key_event_calls = []
        self.summary_calls = []

    async def context_for_prompt(self, session, actor, query):
        self.context_calls.append({"session": session, "actor": actor, "query": query})
        return dict(self.context_result)

    async def write_key_event(self, session, actor, event):
        self.key_event_calls.append({"session": session, "actor": actor, "event": event})
        return {"ok": True, "available": True, "operation": "write_key_event"}

    async def write_memory_summary(self, session, actor, *, reason):
        self.summary_calls.append({"session": session, "actor": actor, "reason": reason})
        return {"ok": True, "available": True, "operation": "write_memory_summary"}


class FakeMemoryCompressor:
    max_snapshot_chars = 999999
    max_summary_chars = 999999

    def __init__(self):
        self.calls = 0

    def snapshot_chars(self, session):
        return 10

    def maybe_compress(self, session):
        self.calls += 1
        if self.calls == 2:
            session.memory_summary = "本轮摘要：玩家发现门锁陷阱。"
            return True
        return False
