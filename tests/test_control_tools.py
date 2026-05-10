import asyncio
import json
from pathlib import Path
from uuid import uuid4

from astrbot_plugin_auto_trpg_dm.core.control_authority import (
    CONTROL_STATUS_HOSTED_BY_SYSTEM,
    CONTROLLER_TYPE_SYSTEM_HOST,
)
from astrbot_plugin_auto_trpg_dm.core.control_transfer import (
    CONTROL_ACTION_DELEGATE_TO_PLAYER,
    CONTROL_ACTION_RECLAIM,
    CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
)
from astrbot_plugin_auto_trpg_dm.core.hosted_action_policy import evaluate_hosted_action_policy
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.control_tools import ControlTools
from astrbot_plugin_auto_trpg_dm.tools.turn_tools import TurnTools


def _runtime_repo(label: str) -> JsonGameRepository:
    root = Path(".pytest-runtime") / f"{label}-{uuid4().hex}"
    return JsonGameRepository(root / "data")


def _repo_with_player_turn(label: str = "control_tools") -> JsonGameRepository:
    repo = _runtime_repo(label)
    session = GameSession.new("group")
    session.mode = GameMode.TACTICAL
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")
    session.characters["pc_next"] = Character(id="pc_next", name="Next PC", player_id="next")
    session.player_character_map["owner"] = "pc_owner"
    session.player_character_map["next"] = "pc_next"
    session.battle = {
        "active": True,
        "turn_entity_id": "pc_owner",
        "turn": {
            "active": True,
            "round": 1,
            "phase": "character_turn",
            "turn_order": ["pc_owner", "pc_next"],
            "current_index": 0,
            "current_entity_id": "pc_owner",
            "actions_this_round": {},
            "turn_log": [],
        },
    }
    repo.save_session(session)
    return repo


def test_control_authority_tool_delegate_allows_turn_tool_action_without_owner_change():
    repo = _repo_with_player_turn("control_tool_delegate")

    delegated = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action=CONTROL_ACTION_DELEGATE_TO_PLAYER,
            character_id="pc_owner",
            target_player_id="delegate",
            risk_ceiling="medium",
            consent_reference="private confirmation text should stay out of audit",
            audit_ref="delegate-auth-tool",
        )
    )
    allowed = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "delegate"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="代控角色采取防御姿态",
            advance_after=False,
        )
    )

    saved = repo.load_session("group")
    rendered_audit = json.dumps(repo.last_audit_records("group", 8), ensure_ascii=False)
    assert delegated["ok"] is True
    assert delegated["active_controller_id"] == "delegate"
    assert saved.characters["pc_owner"].player_id == "owner"
    assert allowed["ok"] is True
    assert saved.control_authority["records"]["pc_owner"]["audit_ref"] == "delegate-auth-tool"
    assert "private confirmation text should stay out of audit" not in rendered_audit


def test_control_authority_tool_reclaim_denies_old_delegate_through_turn_guard():
    repo = _repo_with_player_turn("control_tool_reclaim")
    tools = ControlTools(repo, "group", actor={"player_id": "owner"})
    asyncio.run(
        tools.control_authority(
            action=CONTROL_ACTION_DELEGATE_TO_PLAYER,
            character_id="pc_owner",
            target_player_id="delegate",
        )
    )

    reclaimed = asyncio.run(
        tools.control_authority(
            action=CONTROL_ACTION_RECLAIM,
            character_id="pc_owner",
            audit_ref="reclaim-auth-tool",
        )
    )
    denied_delegate = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "delegate"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="旧代控者不应再能行动",
            advance_after=False,
        )
    )
    allowed_owner = asyncio.run(
        TurnTools(repo, "group", actor={"player_id": "owner"}).turn_control(
            action="record_action",
            current_entity_id="pc_owner",
            summary="持有人收回后行动",
            advance_after=False,
        )
    )

    assert reclaimed["ok"] is True
    assert reclaimed["active_controller_id"] == "owner"
    assert denied_delegate["ok"] is False
    assert denied_delegate["error"] == "character_control_denied"
    assert denied_delegate["active_controller_id"] == "owner"
    assert allowed_owner["ok"] is True


def test_control_authority_tool_relinquish_feeds_hosted_action_policy():
    repo = _repo_with_player_turn("control_tool_host")

    result = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action=CONTROL_ACTION_RELINQUISH_TO_SYSTEM,
            character_id="pc_owner",
            risk_ceiling="medium",
            duration_type="until_scene_end",
            audit_ref="host-auth-tool",
        )
    )
    policy = evaluate_hosted_action_policy(
        repo.load_session("group"),
        "pc_owner",
        actor={"player_id": "__system__"},
        summary="保持掩护并防御",
    )

    assert result["ok"] is True
    assert result["active_controller_id"] == "__system__"
    assert result["controller_type"] == CONTROLLER_TYPE_SYSTEM_HOST
    assert result["status"] == CONTROL_STATUS_HOSTED_BY_SYSTEM
    assert policy["ok"] is True
    assert policy["hosted"] is True
    assert policy["controller_type"] == CONTROLLER_TYPE_SYSTEM_HOST
    assert policy["audit_ref"] == "host-auth-tool"


def test_control_authority_status_is_audited_but_does_not_save_session():
    session = GameSession.new("group")
    session.characters["pc_owner"] = Character(id="pc_owner", name="Owner PC", player_id="owner")
    repo = _CountingRepository(session)

    result = asyncio.run(
        ControlTools(repo, "group", actor={"player_id": "owner"}).control_authority(
            action="status",
            character_id="pc_owner",
        )
    )

    assert result["ok"] is True
    assert result["read_only"] is True
    assert repo.save_count == 0
    assert len(repo.audit_records) == 1
    assert repo.audit_records[0]["tool"] == "control_authority"


class _CountingRepository:
    def __init__(self, session: GameSession):
        self.session = session
        self.save_count = 0
        self.audit_records = []

    def load_session(self, session_id: str) -> GameSession:
        assert session_id == self.session.session_id
        return self.session

    def save_session(self, session: GameSession) -> None:
        self.save_count += 1
        self.session = session

    def append_audit(self, session_id: str, record: dict) -> None:
        assert session_id == self.session.session_id
        self.audit_records.append(record)
