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


def test_deterministic_repair_does_not_retire_actor_from_policy_completion():
    session = GameSession.new("group")
    session.mode = GameMode.CHARACTER_CREATION
    session.scene["_game_started"] = True
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.player_character_map = {"p1": "pc_yaka"}
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "summary": "雅卡在客舱里补充个人资料。",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
        }
    }

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message="补充设定，我的卡是男性角色。",
        completion="开场后角色卡已锁定。如果确实想换一个男性角色，需要等当前角色退场后再创建新卡入场。",
        tool_results=[],
    )

    assert any(item["type"] == "mode" for item in result["applied"])
    assert not any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert not any(item["type"] == "closed_character_scene_threads" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_yaka"]
    assert not session.characters["pc_yaka"].tags


def test_deterministic_repair_does_not_retire_actor_from_retirement_backstory():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.player_character_map = {"p1": "pc_zhagu"}
    session.scene["active_scene_thread_id"] = "character:pc_zhagu"
    session.scene["scene_threads"] = {
        "character:pc_zhagu": {
            "summary": "扎古正在餐厅和水手喝酒聊天。",
            "current_objective": "与马驼子、秦海生等机舱水手喝酒聊天",
            "participants": ["pc_zhagu"],
            "active_character_id": "pc_zhagu",
        }
    }

    player_message = "讲述自己在海上的经历，极地，热带，现代海盗，提前“退休”后的渔夫生活，为什么复出，在海上漂泊的人都是迷失的灵魂"
    completion = "扎古靠在吧台边，讲起跑远洋、买旧渔船和为什么接受史东邀请复出的往事。"
    rejected_backstory_tool = [
        {
            "tool": "update_character_tags",
            "args": {
                "character_id": "pc_zhagu",
                "tags": [{"key": "退役生活", "value": "买旧渔船在近海打渔", "layer": "notes"}],
            },
            "result": {"ok": False, "error": "character_card_locked_after_start"},
        }
    ]

    assert not continuity_audit_should_run(
        session,
        player_message=player_message,
        completion=completion,
        tool_results=[],
    )

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message=player_message,
        completion=completion,
        tool_results=rejected_backstory_tool,
    )

    assert not any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert not any(item["type"] == "closed_character_scene_threads" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_zhagu"]
    assert not session.characters["pc_zhagu"].tags


def test_named_actor_comma_retire_message_still_marks_terminal():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.player_character_map = {"p1": "pc_zhagu"}
    session.scene["scene_threads"] = {
        "character:pc_zhagu": {
            "summary": "扎古在餐厅和水手喝酒聊天。",
            "participants": ["pc_zhagu"],
            "active_character_id": "pc_zhagu",
        }
    }

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message="扎古，退场",
        completion="扎古已退场。",
        tool_results=[],
    )

    assert any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert session.scene["scene_threads"]["character:pc_zhagu"]["status"] == "closed"
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_zhagu"].tags}
    assert "已退场" in tags[("status", "退场状态")]


def test_named_actor_comma_retirement_message_still_marks_terminal():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.player_character_map = {"p1": "pc_zhagu"}
    session.scene["scene_threads"] = {
        "character:pc_zhagu": {
            "summary": "扎古在餐厅和水手喝酒聊天。",
            "participants": ["pc_zhagu"],
            "active_character_id": "pc_zhagu",
        }
    }

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message="扎古，退休",
        completion="扎古正式退休，离开这趟探险。",
        tool_results=[],
    )

    assert any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert session.scene["scene_threads"]["character:pc_zhagu"]["status"] == "closed"


def test_audit_patch_does_not_retire_actor_from_retirement_backstory():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.player_character_map = {"p1": "pc_zhagu"}
    session.scene["scene_threads"] = {
        "character:pc_zhagu": {
            "summary": "扎古正在餐厅和水手喝酒聊天。",
            "participants": ["pc_zhagu"],
            "active_character_id": "pc_zhagu",
        }
    }
    payload = {
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_zhagu",
                    "tags": [{"key": "退场状态", "value": "已退场", "layer": "status"}],
                }
            ],
            "scene_threads": [
                {"thread_id": "character:pc_zhagu", "patch": {"status": "closed", "summary": "扎古已退场。"}}
            ],
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="讲述自己在海上的经历，提前“退休”后的渔夫生活，为什么复出",
        completion="扎古讲起自己退休后又复出的经历。",
        tool_results=[],
    )

    assert not any(item.get("type") in {"character_tags", "scene_thread"} for item in result["applied"])
    assert {item["reason"] for item in result["rejected"]} == {"missing_terminal_evidence"}
    assert "status" not in session.scene["scene_threads"]["character:pc_zhagu"]
    assert not session.characters["pc_zhagu"].tags


def test_terminal_character_thread_cannot_reopen_from_stale_active_status_tags():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_feng"] = Character(id="pc_feng", name="风", player_id="p1")
    session.characters["pc_feng"].upsert_tags(
        [
            {"key": "当前所在", "value": "音速号餐厅", "layer": "status"},
            {"key": "最近行动", "value": "与贺远征交谈", "layer": "status"},
            {"key": "退场状态", "value": "已退场", "layer": "status"},
        ]
    )
    session.player_character_map = {"p1": "pc_feng"}
    session.scene["scene_threads"] = {
        "character:pc_feng": {
            "summary": "风已下船退场。",
            "status": "closed",
            "participants": ["pc_feng"],
            "active_character_id": "pc_feng",
        }
    }
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_feng",
                    "patch": {
                        "status": "",
                        "summary": "风在餐厅继续与贺远征交谈。",
                    },
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p2"},
        player_message="继续推进剧情",
        completion="风在餐厅继续与贺远征交谈。",
        tool_results=[
            {
                "tool": "update_character_tags",
                "args": {"character_id": "pc_feng", "tags": [{"key": "最近行动", "value": "继续交谈"}]},
                "result": {"ok": True},
            }
        ],
    )

    assert not any(item.get("type") == "scene_thread" for item in result["applied"])
    assert result["rejected"][0]["reason"] == "missing_reactivation_evidence"
    assert session.scene["scene_threads"]["character:pc_feng"]["status"] == "closed"


def test_terminal_character_thread_reopens_only_on_actor_reactivation_request():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_feng"] = Character(id="pc_feng", name="风", player_id="p1")
    session.characters["pc_feng"].upsert_tags(
        [
            {"key": "当前所在", "value": "音速号餐厅", "layer": "status"},
            {"key": "退场状态", "value": "已退场", "layer": "status"},
        ]
    )
    session.player_character_map = {"p1": "pc_feng"}
    session.scene["scene_threads"] = {
        "character:pc_feng": {
            "summary": "风已下船退场。",
            "status": "closed",
            "participants": ["pc_feng"],
            "active_character_id": "pc_feng",
        }
    }
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_feng",
                    "patch": {
                        "status": "",
                        "summary": "风恢复活跃，继续参与当前故事。",
                    },
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="我没退场，恢复活跃状态",
        completion="风恢复活跃，继续参与当前故事。",
        tool_results=[],
    )

    assert any(item.get("type") == "scene_thread" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_feng"]


def test_reactivation_request_reopens_false_retired_actor_threads():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.characters["pc_yaka"].upsert_tags(
        [{"key": "退场状态", "value": "已退场", "layer": "status"}]
    )
    session.player_character_map = {"p1": "pc_yaka"}
    session.scene["active_scene_thread_id"] = "character:pc_yaka"
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "summary": "雅卡已退场，不得再作为活跃角色参与当前故事。",
            "status": "retired",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
        }
    }

    result = apply_deterministic_continuity_repairs(
        session,
        actor={"player_id": "p1"},
        player_message="恢复活跃状态，我仍在参团",
        completion="收到，雅卡继续参与当前故事。",
        tool_results=[],
    )

    assert any(item["type"] == "character_reactivated" for item in result["applied"])
    thread = session.scene["scene_threads"]["character:pc_yaka"]
    assert "status" not in thread
    assert "继续作为活跃角色" in thread["summary"]
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_yaka"].tags}
    assert tags[("status", "退场状态")] == "活跃中"


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


def test_terminal_thread_patch_requires_external_evidence_not_patch_text():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.player_character_map = {"p1": "pc_yaka"}
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "summary": "雅卡正在客舱里查看电脑。",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
        }
    }
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_yaka",
                    "patch": {"status": "retired", "summary": "雅卡已退场，不得再参与当前故事。"},
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="我查看电脑数据",
        completion="雅卡打开电脑，继续调查船内传感器。",
        tool_results=[],
    )

    assert not any(item.get("type") == "scene_thread" for item in result["applied"])
    assert result["rejected"][0]["reason"] == "missing_terminal_evidence"
    assert "status" not in session.scene["scene_threads"]["character:pc_yaka"]


def test_audit_patch_can_reopen_actor_thread_with_reactivation_evidence():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.characters["pc_yaka"].upsert_tags(
        [{"key": "退场状态", "value": "活跃中", "layer": "status"}]
    )
    session.player_character_map = {"p1": "pc_yaka"}
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "summary": "旧记录显示雅卡离开了故事焦点。",
            "status": "resolved",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
        }
    }
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_yaka",
                    "patch": {"status": "active", "summary": "雅卡在客舱电脑前继续调查。"},
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="恢复活跃状态",
        completion="雅卡继续参与当前故事。",
        tool_results=[],
    )

    assert any(item["type"] == "scene_thread" for item in result["applied"])
    thread = session.scene["scene_threads"]["character:pc_yaka"]
    assert "status" not in thread
    assert thread["summary"] == "雅卡在客舱电脑前继续调查。"


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
