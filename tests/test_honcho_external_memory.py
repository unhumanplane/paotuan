import asyncio

from astrbot_plugin_auto_trpg_dm.core.external_memory import (
    HonchoExternalMemory,
    HonchoMemoryConfig,
    build_honcho_ids,
    build_key_event_messages,
)
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.core.prompts import build_system_prompt


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

    assert ids.honcho_session_id == "paotuan_aiocqhttp_GroupMessage_123_twilight-border_chapter-1"
    assert ids.player_peer_id == "player_u-1"
    assert ids.character_peer_id == "pc_char-hero"
    assert ids.assistant_peer_id == "paotuan_dm"
    assert ids.campaign_id == "twilight-border"
    assert ids.chapter_id == "chapter-1"
    assert ids.environment == "production"


def test_honcho_disabled_returns_no_context_without_importing_sdk():
    session = GameSession.new("group-1")
    memory = HonchoExternalMemory(HonchoMemoryConfig(enabled=False), environ={})

    result = asyncio.run(memory.context_for_prompt(session, {"player_id": "u-1"}, "调查门"))

    assert result == {"ok": True, "available": False, "reason": "honcho_disabled"}


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
    assert fake_session.context_kwargs["peer_target"] == "player_u-1"
    assert fake_session.context_kwargs["peer_perspective"] == "paotuan_dm"
    assert fake_session.context_kwargs["search_query"] == "调查门"


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
    assert fake_session.messages[0]["metadata"]["kind"] == "key_event"
    assert fake_session.messages[0]["metadata"]["environment"] == "production"
    assert fake_session.messages[0]["metadata"]["campaign_id"] == "campaign"
    assert fake_session.messages[0]["metadata"]["player_id"] == "u-1"
    assert fake_session.messages[0]["metadata"]["character_id"] == "pc_alpha"


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

    def add_peers(self, peers):
        self.peers.extend(peers)

    def add_messages(self, messages):
        self.messages.extend(messages)

    def context(self, **kwargs):
        self.context_kwargs = kwargs
        return FakeHonchoContext(self.client.context_text) if hasattr(self, "client") else FakeHonchoContext("")


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
    def __init__(self, text):
        self.text = text

    def to_openai(self, assistant=None):
        return [{"role": "system", "content": self.text}]
