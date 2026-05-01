import asyncio
import json

import pytest

from astrbot_plugin_auto_trpg_dm.core.cycle_state_machine import CycleStateMachine
from astrbot_plugin_auto_trpg_dm.core.environment_agent import RecorderAgent
from astrbot_plugin_auto_trpg_dm.core.models import CycleState, GameSession


class FakeRepository:
    def __init__(self):
        self.sessions = {}
        self.audit = []

    def load_session(self, session_id):
        return self.sessions.get(session_id, GameSession(session_id=session_id))

    def save_session(self, session):
        self.sessions[session.session_id] = session

    def append_audit(self, session_id, record):
        self.audit.append({"session_id": session_id, **record})


class FakeLlmResponse:
    def __init__(self, text):
        self.completion_text = text


class FakeAstrContext:
    def __init__(self, response_text):
        self._response_text = response_text

    async def llm_generate(self, **kwargs):
        await asyncio.sleep(0)
        return FakeLlmResponse(self._response_text)


@pytest.fixture
def fake_repo():
    return FakeRepository()


class TestBuildUserPrompt:
    def test_contains_cycle_id_and_snapshot(self, fake_repo):
        session = GameSession(session_id="test")
        session.current_cycle_id = 5
        session.characters["pc_warrior"] = session.characters.get("pc_warrior") or type(
            "Character", (), {"id": "pc_warrior", "name": "战士", "player_id": "p1", "summary": ""}
        )()
        # Direct assignment since Character isn't imported easily here
        from astrbot_plugin_auto_trpg_dm.core.models import Character
        session.characters["pc_warrior"] = Character(
            id="pc_warrior", name="战士", player_id="p1"
        )
        ra = RecorderAgent(astr_context=None, repository=fake_repo)
        prompt = ra._build_user_prompt(session, session.ra_cycle_input)
        assert "cycle_id" in prompt
        assert "战士" in prompt


class TestParseJson:
    def test_parses_plain_json(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        result = ra._parse_json('{"summary": "test"}')
        assert result["summary"] == "test"

    def test_parses_markdown_code_block(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        result = ra._parse_json('```json\n{"summary": "test"}\n```')
        assert result["summary"] == "test"

    def test_parses_markdown_without_language(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        result = ra._parse_json('```\n{"summary": "test"}\n```')
        assert result["summary"] == "test"

    def test_raises_on_invalid_json(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        with pytest.raises(json.JSONDecodeError):
            ra._parse_json("not json")


class TestNormalizeSummary:
    def test_normalizes_all_fields(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        parsed = {
            "summary": "战斗结束",
            "character_status": {"pc1": {"hp": 10}},
            "enemy_status": {"orc": {"hp": 0}},
            "world_changes": {"scene_updates": {}},
            "rules_triggered": ["attack"],
            "dm_narrative_aligned": False,
            "discrepancies": ["HP mismatch"],
        }
        result = ra._normalize_summary(parsed, cycle_id=3)
        assert result["cycle_id"] == 3
        assert result["summary"] == "战斗结束"
        assert result["character_status"]["pc1"]["hp"] == 10
        assert result["dm_narrative_aligned"] is False
        assert result["discrepancies"] == ["HP mismatch"]

    def test_uses_defaults_for_missing_fields(self):
        ra = RecorderAgent(astr_context=None, repository=None)
        result = ra._normalize_summary({}, cycle_id=1)
        assert result["cycle_id"] == 1
        assert result["summary"] == ""
        assert result["dm_narrative_aligned"] is True
        assert result["discrepancies"] == []


class TestResolveCycle:
    async def test_successful_resolution(self, fake_repo):
        llm_json = json.dumps(
            {
                "summary": "战士攻击了兽人",
                "character_status": {"pc_warrior": {"hp": 14}},
                "enemy_status": {"orc": {"hp": 0, "alive": False}},
                "world_changes": {},
                "rules_triggered": ["melee_attack"],
                "dm_narrative_aligned": True,
                "discrepancies": [],
            },
            ensure_ascii=False,
        )
        ctx = FakeAstrContext(llm_json)
        ra = RecorderAgent(astr_context=ctx, repository=fake_repo)

        session = GameSession(session_id="resolve_test")
        session.current_cycle_id = 2
        session.cycle_state = CycleState.CYCLE_RESOLVING
        session.audit_buffer.cycle_id = 2
        fake_repo.sessions["resolve_test"] = session

        result = await ra.resolve_cycle("resolve_test", session)

        assert result["ok"] is True
        assert len(session.environment_summaries) == 1
        summary = session.environment_summaries[0]
        assert summary["cycle_id"] == 2
        assert summary["summary"] == "战士攻击了兽人"
        assert summary["rules_triggered"] == ["melee_attack"]
        # State should have transitioned through TRANSITION to ACTIVE with new cycle
        assert session.cycle_state == CycleState.CYCLE_ACTIVE
        assert session.current_cycle_id == 3  # incremented by start_new_cycle
        assert len(session.audit_buffer.actions) == 0  # buffer reset

    async def test_invalid_json_fallback(self, fake_repo):
        ctx = FakeAstrContext("not valid json")
        ra = RecorderAgent(astr_context=ctx, repository=fake_repo)

        session = GameSession(session_id="resolve_test")
        session.current_cycle_id = 1
        session.cycle_state = CycleState.CYCLE_RESOLVING
        fake_repo.sessions["resolve_test"] = session

        result = await ra.resolve_cycle("resolve_test", session)

        assert result["ok"] is False
        assert result["error"] == "ra_llm_failed"
        assert len(session.environment_summaries) == 0

    async def test_no_context_raises(self, fake_repo):
        ra = RecorderAgent(astr_context=None, repository=fake_repo)

        session = GameSession(session_id="resolve_test")
        session.current_cycle_id = 1
        session.cycle_state = CycleState.CYCLE_RESOLVING
        fake_repo.sessions["resolve_test"] = session

        result = await ra.resolve_cycle("resolve_test", session)

        assert result["ok"] is False
        assert result["error"] == "ra_llm_failed"
