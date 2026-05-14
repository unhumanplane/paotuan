import asyncio
import json

from astrbot_plugin_auto_trpg_dm.core.continuity_auditor import (
    ContinuityAuditor,
    apply_continuity_audit_patches,
    apply_deterministic_continuity_repairs,
    continuity_audit_should_run,
    normalize_active_scene_thread,
)
from astrbot_plugin_auto_trpg_dm.core.models import Character, GameMode, GameSession


class FakeResponse:
    def __init__(self, completion_text):
        self.completion_text = completion_text


class FakeLlm:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.text)


def test_deterministic_repair_closes_retired_actor_thread_and_restores_narrative_mode():
    session = GameSession.new("group")
    session.mode = GameMode.CHARACTER_CREATION
    session.scene["_game_started"] = True
    session.characters["pc_latatos"] = Character(id="pc_latatos", name="拉塔托丝", player_id="p1")
    session.characters["pc_laofei"] = Character(id="pc_laofei", name="老肥", player_id="p2")
    session.player_character_map = {"p1": "pc_latatos", "p2": "pc_laofei"}
    session.scene["active_scene_thread_id"] = "character:pc_latatos"
    session.scene["scene_threads"] = {
        "character:pc_latatos": {
            "summary": "拉塔托丝仍在马厩区域。",
            "participants": ["pc_latatos"],
            "active_character_id": "pc_latatos",
            "updated_at": "2026-05-14T04:02:00+00:00",
        },
        "character:pc_laofei": {
            "summary": "老肥在酒馆等天亮。",
            "location": "酒馆",
            "participants": ["pc_laofei"],
            "active_character_id": "pc_laofei",
            "updated_at": "2026-05-14T03:22:00+00:00",
        },
    }

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message="去哪里不重要，算是角色退场了",
        completion="拉塔托丝消失在夜色里，角色已退场。",
        tool_results=[],
    )

    assert any(item["type"] == "mode" for item in result["applied"])
    assert session.mode == GameMode.NARRATIVE
    assert session.scene["scene_threads"]["character:pc_latatos"]["status"] == "closed"
    assert session.scene["active_scene_thread_id"] == "character:pc_laofei"
    assert session.scene["summary"] == "老肥在酒馆等天亮。"
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_latatos"].tags}
    assert "已退场" in tags[("status", "退场状态")]


def test_continuity_audit_should_run_for_denial_against_completed_ritual_fact():
    session = GameSession.new("group")
    session.scene["current_conflict"] = "黑暗仪式完成——部分成功；小地主被诅咒标记。"

    assert continuity_audit_should_run(
        session,
        player_message="集中精神试一下诅咒中感知位置的能力",
        completion="还没下咒呢，感知不到。",
        tool_results=[],
    )


def test_continuity_audit_skips_plain_state_queries():
    session = GameSession.new("group")

    assert not continuity_audit_should_run(
        session,
        player_message="当前我的状态",
        completion="你在酒馆。",
        tool_results=[],
    )


def test_llm_auditor_returns_payload_without_dm_context():
    response = {
        "ok": True,
        "needs_repair": True,
        "issues": [{"severity": "high", "problem": "active thread 已退场", "evidence": ["退场"], "repair": "关闭线程"}],
        "safe_patches": {"mode": "narrative"},
        "player_correction": "上一句关于 active 状态的描述已校正。",
    }
    fake = FakeLlm(json.dumps(response, ensure_ascii=False))
    session = GameSession.new("group")
    session.scene["_game_started"] = True

    result = asyncio.run(
        ContinuityAuditor(fake, "default", max_tokens=1200).run(
            session,
            actor={"player_id": "p1"},
            player_message="退场",
            completion="已退场。",
            tool_results=[],
        )
    )

    assert result["ok"] is True
    assert result["payload"]["needs_repair"] is True
    assert fake.calls[0]["contexts"] == []
    assert fake.calls[0]["max_tokens"] == 1200


def test_audit_patch_applies_only_terminal_character_tags_with_evidence():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_latatos"] = Character(id="pc_latatos", name="拉塔托丝", player_id="p1")
    session.player_character_map["p1"] = "pc_latatos"
    payload = {
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_latatos",
                    "tags": [
                        {"key": "退场状态", "value": "已退场", "layer": "status"},
                        {"key": "职业", "value": "传奇刺客", "layer": "identity"},
                    ],
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="去哪里不重要，算是角色退场了",
        completion="拉塔托丝已退场。",
        tool_results=[],
    )

    assert any(item["type"] == "character_tags" for item in result["applied"])
    assert any(item.get("reason") == "only_terminal_status_tags_are_auto_applied" for item in result["rejected"])
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_latatos"].tags}
    assert tags[("status", "退场状态")] == "已退场"
    assert ("identity", "职业") not in tags


def test_normalize_active_scene_thread_ignores_closed_active_thread():
    session = GameSession.new("group")
    session.scene["active_scene_thread_id"] = "character:pc_esmeralda"
    session.scene["scene_threads"] = {
        "character:pc_esmeralda": {
            "summary": "艾斯米拉达已退场。",
            "status": "closed",
            "updated_at": "2026-05-14T03:58:00+00:00",
        },
        "character:pc_laofei": {
            "summary": "老肥在酒馆等天亮。",
            "location": "酒馆",
            "updated_at": "2026-05-14T03:20:00+00:00",
        },
    }

    result = normalize_active_scene_thread(session)

    assert result["changed"] is True
    assert session.scene["active_scene_thread_id"] == "character:pc_laofei"
    assert session.scene["summary"] == "老肥在酒馆等天亮。"


def test_normalize_active_scene_thread_closes_legacy_terminal_thread_text():
    session = GameSession.new("group")
    session.scene["active_scene_thread_id"] = "character:pc_esmeralda"
    session.scene["scene_threads"] = {
        "character:pc_esmeralda": {
            "summary": "艾斯米拉达已退场，离开小镇，不再与本地故事交织。",
            "current_objective": "无活跃主线目标——艾斯米拉达已退场",
            "updated_at": "2026-05-14T03:58:00+00:00",
        },
        "character:pc_laofei": {
            "summary": "老肥在酒馆等天亮。",
            "location": "酒馆",
            "updated_at": "2026-05-14T03:20:00+00:00",
        },
    }

    result = normalize_active_scene_thread(session)

    assert result["changed"] is True
    assert result["closed_thread_ids"] == ["character:pc_esmeralda"]
    assert session.scene["scene_threads"]["character:pc_esmeralda"]["status"] == "closed"
    assert session.scene["active_scene_thread_id"] == "character:pc_laofei"
