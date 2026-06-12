import asyncio
import json

from astrbot_plugin_auto_trpg_dm.core.continuity_auditor import (
    ContinuityAuditor,
    apply_continuity_audit_patches,
    apply_deterministic_continuity_repairs,
    continuity_audit_should_run,
    normalize_active_scene_thread,
    normalize_entity_anchor_continuity,
    normalize_resolved_combat_continuity,
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


def test_deterministic_repair_does_not_mark_terminal_without_llm_audit():
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
    assert not any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert not any(item["type"] == "closed_character_scene_threads" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_latatos"]
    assert session.scene["active_scene_thread_id"] == "character:pc_latatos"
    assert not session.characters["pc_latatos"].tags


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

    assert continuity_audit_should_run(
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


def test_named_actor_comma_retire_message_waits_for_llm_audit():
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

    assert not any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_zhagu"]
    assert not session.characters["pc_zhagu"].tags


def test_named_actor_comma_retirement_message_waits_for_llm_audit():
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

    assert not any(item["type"] == "character_terminal_tags" for item in result["applied"])
    assert "status" not in session.scene["scene_threads"]["character:pc_zhagu"]
    assert not session.characters["pc_zhagu"].tags


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
        "issues": [
            {
                "severity": "high",
                "problem": "扎古已退场。",
                "evidence": ["扎古已退场。"],
                "repair": "关闭线程。",
            }
        ],
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


def test_llm_audit_patch_can_mark_terminal_without_player_consent():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.player_character_map = {"p1": "pc_zhagu"}
    session.scene["scene_threads"] = {
        "character:pc_zhagu": {
            "summary": "扎古正冲向甲板守卫。",
            "participants": ["pc_zhagu"],
            "active_character_id": "pc_zhagu",
        }
    }
    payload = {
        "issues": [
            {
                "severity": "high",
                "problem": "扎古已被驱逐离船，但角色线程仍是 active。",
                "evidence": ["扎古被守卫制服铐走，安保宣布他被驱逐下船，无法再参与本次航行。"],
                "repair": "写入退场状态并关闭扎古角色线程。",
            }
        ],
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_zhagu",
                    "tags": [{"key": "退场状态", "value": "被驱逐下船，无法再参与本次航行", "layer": "status"}],
                }
            ],
            "scene_threads": [
                {
                    "thread_id": "character:pc_zhagu",
                    "patch": {
                        "status": "closed",
                        "summary": "扎古被守卫制服并驱逐下船，无法再参与本次航行。",
                    },
                }
            ],
        },
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="我继续冲向守卫。",
        completion="守卫把扎古制服铐走，安保宣布他被驱逐下船，无法再参与本次航行。",
        tool_results=[],
    )

    assert any(item.get("type") == "character_tags" for item in result["applied"])
    assert any(item.get("type") == "scene_thread" for item in result["applied"])
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_zhagu"].tags}
    assert tags[("status", "退场状态")] == "被驱逐下船，无法再参与本次航行"
    assert session.scene["scene_threads"]["character:pc_zhagu"]["status"] == "closed"


def test_llm_audit_patch_rejects_terminal_self_attestation():
    session = GameSession.new("group")
    session.mode = GameMode.NARRATIVE
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
        "issues": [
            {
                "severity": "high",
                "problem": "雅卡已退场。",
                "evidence": ["雅卡已退场。"],
                "repair": "关闭线程。",
            }
        ],
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_yaka",
                    "tags": [{"key": "退场状态", "value": "已退场", "layer": "status"}],
                }
            ],
            "scene_threads": [
                {"thread_id": "character:pc_yaka", "patch": {"status": "closed", "summary": "雅卡已退场。"}}
            ],
        },
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="我查看电脑数据。",
        completion="雅卡打开电脑，继续调查船内传感器。",
        tool_results=[],
    )

    assert not any(item.get("type") in {"character_tags", "scene_thread"} for item in result["applied"])
    assert {item["reason"] for item in result["rejected"]} == {"missing_terminal_evidence"}
    assert "status" not in session.scene["scene_threads"]["character:pc_yaka"]
    assert not session.characters["pc_yaka"].tags


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
        "issues": [
            {
                "severity": "high",
                "problem": "拉塔托丝已离开当前故事。",
                "evidence": ["拉塔托丝已退场。"],
                "repair": "写入退场状态。",
            }
        ],
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


def test_audit_patch_applies_evidence_backed_low_risk_status_tag():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.characters["pc_zhagu"].upsert_tags(
        [{"key": "当前所在", "value": "音速号客舱", "layer": "status"}]
    )
    session.player_character_map["p1"] = "pc_zhagu"
    payload = {
        "issues": [
            {
                "severity": "low",
                "problem": "扎古的当前位置标签滞后。",
                "evidence": ["DM 回复显示扎古已经进入公共休息室。"],
                "repair": "把当前所在更新为公共休息室。",
            }
        ],
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_zhagu",
                    "tags": [{"key": "当前所在", "value": "公共休息室", "layer": "status"}],
                }
            ]
        },
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="和他们喝些酒，联络感情。",
        completion="几分钟后，扎古和三位水手、轮机员围坐在公共休息室靠舷窗的圆桌边。",
        tool_results=[],
    )

    assert any(item["type"] == "character_tags" for item in result["applied"])
    assert not any(item.get("key") == "当前所在" for item in result["rejected"])
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_zhagu"].tags}
    assert tags[("status", "当前所在")] == "公共休息室"


def test_audit_patch_rejects_low_risk_status_tag_without_evidence():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_zhagu"] = Character(id="pc_zhagu", name="扎古", player_id="p1")
    session.characters["pc_zhagu"].upsert_tags(
        [{"key": "当前所在", "value": "音速号客舱", "layer": "status"}]
    )
    session.player_character_map["p1"] = "pc_zhagu"
    payload = {
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_zhagu",
                    "tags": [{"key": "当前所在", "value": "公共休息室", "layer": "status"}],
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="我继续整理工具。",
        completion="扎古还在客舱里整理工具包。",
        tool_results=[],
    )

    assert not any(item["type"] == "character_tags" for item in result["applied"])
    assert any(item.get("key") == "当前所在" for item in result["rejected"])
    tags = {(tag.layer, tag.key): tag.value for tag in session.characters["pc_zhagu"].tags}
    assert tags[("status", "当前所在")] == "音速号客舱"


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


def test_audit_scene_patch_can_time_qualify_npc_known_fact_with_tool_evidence():
    session = GameSession.new("group")
    session.scene["entity_facts"] = {
        "npc_shidong": {
            "entity_type": "npc",
            "name": "史东",
            "current_status": "已确认生还；当前所在不明",
            "historical_facts": ["曾在中央控制室观察ROV操作，时间点早于C4爆炸。"],
        }
    }
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "npcs": [{"id": "npc_shidong", "name": "史东", "known_facts": ["站在中央控制室观察ROV操作"]}],
            "open_hooks": [{"id": "hook_where_is_shidong", "text": "史东是否随船沉没？", "status": "open"}],
        }
    }
    payload = {
        "safe_patches": {
            "scene": {
                "npcs": [
                    {
                        "id": "npc_shidong",
                        "name": "史东",
                        "status": "已确认生还；当前所在不明",
                        "known_facts": ["曾在中央控制室观察ROV操作，时间点早于C4爆炸。"],
                    }
                ],
                "open_hooks": [
                    {
                        "id": "hook_where_is_shidong",
                        "text": "史东已确认生还但当前所在不明——他通过何种路线逃生？",
                        "status": "open",
                        "visibility": "observed",
                    }
                ],
            }
        }
    }
    tool_results = [
        {
            "tool": "clarify_entity_timeline",
            "result": {
                "ok": True,
                "entity_fact": session.scene["entity_facts"]["npc_shidong"],
                "scene_thread_id": "character:pc_yaka",
            },
        }
    ]

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="史东到底什么情况",
        completion="史东已确认生还。",
        tool_results=tool_results,
    )

    thread = session.scene["scene_threads"]["character:pc_yaka"]

    assert any(item.get("type") == "scene" for item in result["applied"])
    assert thread["npcs"][0]["known_facts"] == ["曾在中央控制室观察ROV操作，时间点早于C4爆炸。"]
    assert "是否随船沉没" not in thread["open_hooks"][0]["text"]


def test_normalize_entity_anchor_continuity_retires_stale_informant_hooks():
    session = GameSession.new("group")
    session.scene.update(
        {
            "summary": "队伍抵达 B-07，但线人不在这个区域，下落仍然悬着。",
            "stakes": "再拖一轮，敌人可能把线人从南端防火门后转移走。",
            "open_hooks": [
                {"id": "hook_informant_missing", "text": "线人可能还在南端防火门后。", "status": "open"}
            ],
            "entity_facts": {
                "npc_informant": {
                    "entity_type": "npc",
                    "name": "失踪线人",
                    "current_status": "已救出；随队抵达 B-07 冷藏库区域",
                    "current_location": "B-07 冷藏库外侧作业车旁",
                    "criticality": "critical",
                    "custody": "with_party",
                    "aliases": ["线人"],
                    "historical_facts": ["凯德在值班室解绑线人。"],
                    "unknowns": ["撤离路线仍需确认"],
                    "authoritative_events": ["event_informant_rescued", "event_informant_escorted"],
                }
            },
            "scene_threads": {
                "character:pc_kade": {
                    "summary": "凯德在冷藏库外，线人下落不明，可能还在门后。",
                    "npcs": [{"id": "npc_informant", "name": "失踪线人", "known_facts": ["可能还在门后"]}],
                    "open_hooks": [{"id": "hook_informant_missing", "text": "线人是否还在门后？", "status": "open"}],
                }
            },
        }
    )

    result = normalize_entity_anchor_continuity(session)

    assert result["changed"] is True
    assert "已过期" in session.scene["summary"]
    assert "已过期" in session.scene["stakes"]
    assert session.scene["open_hooks"][0]["status"] == "resolved"
    assert "已过期" in session.scene["open_hooks"][0]["text"]
    thread = session.scene["scene_threads"]["character:pc_kade"]
    assert "已过期" in thread["summary"]
    assert thread["open_hooks"][0]["status"] == "resolved"
    assert thread["npcs"][0]["status"] == "已救出；随队抵达 B-07 冷藏库区域"
    assert thread["npcs"][0]["location"] == "B-07 冷藏库外侧作业车旁"
    assert thread["npcs"][0]["custody"] == "with_party"


def test_normalize_entity_anchor_continuity_handles_item_and_door_anchors():
    session = GameSession.new("group")
    session.scene.update(
        {
            "current_objective": "先确认黑曜圣匣是否还在祭坛上，再决定是否开门。",
            "open_hooks": [
                {"id": "hook_relic", "text": "黑曜圣匣是否还没拿到？", "status": "open"},
                {"id": "hook_door", "text": "北侧防火门是否仍然关闭？", "status": "open"},
            ],
            "entity_facts": {
                "item_relic": {
                    "entity_type": "item",
                    "name": "黑曜圣匣",
                    "current_status": "已获得并交由凯德保管",
                    "criticality": "critical",
                    "holder": "pc_kade",
                    "aliases": ["圣匣"],
                    "authoritative_events": ["event_relic_acquired"],
                },
                "door_north_fire": {
                    "entity_type": "door",
                    "name": "北侧防火门",
                    "current_status": "已打开；可通行",
                    "criticality": "key",
                    "aliases": ["防火门"],
                    "authoritative_events": ["event_north_door_opened"],
                },
            },
        }
    )

    result = normalize_entity_anchor_continuity(session)

    assert result["changed"] is True
    assert "已过期" in session.scene["current_objective"]
    assert session.scene["open_hooks"][0]["status"] == "resolved"
    assert session.scene["open_hooks"][1]["status"] == "resolved"
    assert "已获得并交由凯德保管" in session.scene["open_hooks"][0]["text"]
    assert "已打开；可通行" in session.scene["open_hooks"][1]["text"]


def test_audit_scene_patch_projects_pressure_clock_and_stakes_to_thread():
    session = GameSession.new("group")
    session.scene.update(
        {
            "current_conflict": "旧状态：隘口已全面警戒，哨兵仍在放箭。",
            "stakes": "旧状态：隘口随时增援。",
            "pressure_clock": {
                "label": "隘口警觉",
                "tick": 6,
                "max": 4,
                "description": "旧状态：哨兵锁定凯德位置。",
                "status": "active",
            },
            "scene_threads": {
                "character:pc_kade": {
                    "summary": "凯德在隘口侧翼。",
                    "participants": ["pc_kade"],
                    "active_character_id": "pc_kade",
                    "current_conflict": "旧状态：隘口已全面警戒，哨兵仍在放箭。",
                    "stakes": "旧状态：隘口随时增援。",
                    "pressure_clock": {
                        "label": "隘口警觉",
                        "tick": 6,
                        "max": 4,
                        "description": "旧状态：哨兵锁定凯德位置。",
                        "status": "active",
                    },
                }
            },
        }
    )
    payload = {
        "safe_patches": {
            "scene": {
                "current_conflict": "后山隘口暂未触发新战斗；报信人逃脱只代表后续增援风险。",
                "stakes": "后山隘口存在后续增援风险，但当前前哨战斗已经收束。",
                "pressure_clock": {
                    "label": "隘口增援风险",
                    "tick": 1,
                    "max": 4,
                    "description": "报信人逃脱只代表后续增援风险，隘口当前未全面开战。",
                    "status": "watching",
                },
            }
        }
    }
    tool_results = [
        {
            "tool": "update_scene",
            "args": {"scene_thread_id": "character:pc_kade"},
            "result": {
                "ok": True,
                "scene_thread_id": "character:pc_kade",
                "current_conflict": "后山隘口暂未触发新战斗；报信人逃脱只代表后续增援风险。",
                "stakes": "后山隘口存在后续增援风险，但当前前哨战斗已经收束。",
                "pressure_clock": {
                    "label": "隘口增援风险",
                    "tick": 1,
                    "max": 4,
                    "description": "报信人逃脱只代表后续增援风险，隘口当前未全面开战。",
                    "status": "watching",
                },
            },
        }
    ]

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="隘口也重复",
        completion="更正：隘口当前只是增援风险。",
        tool_results=tool_results,
    )

    thread = session.scene["scene_threads"]["character:pc_kade"]
    assert any(item.get("type") == "scene" for item in result["applied"])
    assert any(item.get("type") == "scene_thread_projection" for item in result["applied"])
    assert session.scene["pressure_clock"]["tick"] == 1
    assert thread["pressure_clock"]["tick"] == 1
    assert "全面警戒" not in thread["current_conflict"]
    assert "当前前哨战斗已经收束" in thread["stakes"]


def test_audit_scene_thread_patch_can_sync_evidence_backed_pressure_clock_without_status_change():
    session = GameSession.new("group")
    session.scene.update(
        {
            "scene_threads": {
                "character:pc_moyu": {
                    "summary": "墨鱼在隘口侧翼。",
                    "participants": ["pc_moyu"],
                    "active_character_id": "pc_moyu",
                    "pressure_clock": {
                        "label": "隘口警觉",
                        "tick": 3,
                        "max": 4,
                        "description": "旧状态：哨兵持续搜索墨鱼。",
                    },
                }
            }
        }
    )
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_moyu",
                    "patch": {
                        "pressure_clock": {
                            "label": "隘口增援风险",
                            "tick": 1,
                            "max": 4,
                            "description": "报信人逃脱只代表后续增援风险，隘口当前未全面开战。",
                        }
                    },
                }
            ]
        }
    }
    tool_results = [
        {
            "tool": "update_scene",
            "args": {"scene_thread_id": "character:pc_moyu"},
            "result": {
                "ok": True,
                "scene_thread_id": "character:pc_moyu",
                "pressure_clock": {
                    "label": "隘口增援风险",
                    "tick": 1,
                    "max": 4,
                    "description": "报信人逃脱只代表后续增援风险，隘口当前未全面开战。",
                },
            },
        }
    ]

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="同步墨鱼线程压力钟",
        completion="更正：隘口当前只是增援风险。",
        tool_results=tool_results,
    )

    thread = session.scene["scene_threads"]["character:pc_moyu"]
    assert any(item.get("type") == "scene_thread" for item in result["applied"])
    assert not result["rejected"]
    assert thread["pressure_clock"]["tick"] == 1
    assert thread["pressure_clock"]["description"] == "报信人逃脱只代表后续增援风险，隘口当前未全面开战。"
    assert "status" not in thread


def test_audit_scene_thread_patch_rejects_unbacked_pressure_clock_without_status_change():
    session = GameSession.new("group")
    session.scene["scene_threads"] = {
        "character:pc_moyu": {
            "summary": "墨鱼在隘口侧翼。",
            "participants": ["pc_moyu"],
            "active_character_id": "pc_moyu",
            "pressure_clock": {"label": "隘口警觉", "tick": 3, "max": 4},
        }
    }
    payload = {
        "safe_patches": {
            "scene_threads": [
                {
                    "thread_id": "character:pc_moyu",
                    "patch": {"pressure_clock": {"label": "凭空新增危机", "tick": 4, "max": 4}},
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="继续",
        completion="墨鱼观察隘口。",
        tool_results=[],
    )

    thread = session.scene["scene_threads"]["character:pc_moyu"]
    assert not any(item.get("type") == "scene_thread" for item in result["applied"])
    assert result["rejected"][0]["reason"] == "scene_thread_patch_not_evidence_backed"
    assert thread["pressure_clock"]["tick"] == 3


def test_audit_global_scene_repair_projects_shared_outpost_resolution_to_all_open_character_threads():
    session = GameSession.new("group")
    for character_id, name, player_id in (
        ("pc_kade", "凯德", "p1"),
        ("pc_yang_yongxin", "杨永信", "p2"),
        ("pc_yaka", "雅卡", "p3"),
    ):
        session.characters[character_id] = Character(id=character_id, name=name, player_id=player_id)
        session.player_character_map[player_id] = character_id
    session.scene.update(
        {
            "summary": "旧状态：前哨院内还有水井残敌，隘口增援已经抵达。",
            "current_conflict": "旧状态：水井残敌尚未补刀，杨永信还在等第二箭。",
            "scene_threads": {
                "character:pc_kade": {
                    "summary": "旧状态：凯德仍被院内残敌牵制。",
                    "current_conflict": "旧状态：水井残敌尚未补刀，杨永信还在等第二箭。",
                    "active_thread_summary": "旧派生：凯德仍被隘口哨兵拉弓瞄准。",
                    "active_thread_current_conflict": "旧派生：隘口哨兵正拉弓瞄准他所在方位。",
                    "active_thread_current_objective": "旧派生：从新位置侦察隘口敌情。",
                    "participants": ["pc_kade"],
                    "active_character_id": "pc_kade",
                },
                "character:pc_yang_yongxin": {
                    "summary": "旧状态：杨永信仍暴露在碎石坡上等待第二箭。",
                    "current_conflict": "旧状态：水井残敌尚未补刀，杨永信还在等第二箭。",
                    "participants": ["pc_yang_yongxin"],
                    "active_character_id": "pc_yang_yongxin",
                },
                "character:pc_yaka": {
                    "summary": "旧状态：雅卡还在和隘口增援混战。",
                    "current_conflict": "旧状态：隘口增援已经抵达。",
                    "participants": ["pc_yaka"],
                    "active_character_id": "pc_yaka",
                },
                "session": {
                    "status": "archived",
                    "summary": "更旧的第8轮前哨战斗。",
                },
            },
        }
    )
    payload = {
        "safe_patches": {
            "scene": {
                "summary": "第2天上午，前哨站院内战斗已结束，前哨站全院已肃清。",
                "current_conflict": "全院已肃清，无剩余敌人；当前主要风险来自已逃报信的哨兵、隘口陷阱和可能到来的桃源教增援。",
                "current_objective": "与队伍商议下一步行动计划。",
            }
        }
    }
    tool_results = [
        {
            "tool": "update_scene",
            "result": {
                "ok": True,
                "scene_thread_id": "character:pc_yang_yongxin",
                "summary": "第2天上午，前哨站院内战斗已结束，前哨站全院已肃清。",
                "current_conflict": "全院已肃清，无剩余敌人；当前主要风险来自已逃报信的哨兵、隘口陷阱和可能到来的桃源教增援。",
                "current_objective": "与队伍商议下一步行动计划。",
            },
        }
    ]

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p2"},
        player_message="隘口也打完了啊，你确认一下",
        completion="更正：前哨全院已肃清。",
        tool_results=tool_results,
    )

    projection = next(item for item in result["applied"] if item.get("type") == "scene_thread_projection")
    assert set(projection["thread_ids"]) == {"character:pc_kade", "character:pc_yang_yongxin", "character:pc_yaka"}
    assert "session" not in projection["thread_ids"]
    for thread_id in projection["thread_ids"]:
        thread = session.scene["scene_threads"][thread_id]
        assert "全院已肃清" in thread["current_conflict"]
        assert "水井残敌" not in json.dumps(thread, ensure_ascii=False)
        assert "第二箭" not in json.dumps(thread, ensure_ascii=False)
        assert "隘口增援已经抵达" not in json.dumps(thread, ensure_ascii=False)
    kade_thread = session.scene["scene_threads"]["character:pc_kade"]
    assert "active_thread_summary" not in kade_thread
    assert "active_thread_current_conflict" not in kade_thread
    assert "active_thread_current_objective" not in kade_thread


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


def test_normalize_active_scene_thread_does_not_close_from_terminal_text_alone():
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

    assert result["changed"] is False
    assert result["closed_thread_ids"] == []
    assert "status" not in session.scene["scene_threads"]["character:pc_esmeralda"]
    assert session.scene["active_scene_thread_id"] == "character:pc_esmeralda"


def test_normalize_active_scene_thread_coalesces_character_aliases():
    session = GameSession.new("group")
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.scene["active_scene_thread_id"] = "pc_yaka"
    session.scene["scene_threads"] = {
        "pc_yaka": {
            "summary": "旧别名线程：雅卡仍在客舱。",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
            "updated_at": "2026-05-14T13:40:00+00:00",
        },
        "character:pc_yaka": {
            "summary": "规范线程：雅卡在会议厅。",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
            "updated_at": "2026-05-14T13:58:00+00:00",
        },
    }

    result = normalize_active_scene_thread(session)

    assert result["changed"] is True
    assert result["merged_thread_aliases"] == [{"from": "pc_yaka", "to": "character:pc_yaka"}]
    assert "pc_yaka" not in session.scene["scene_threads"]
    assert session.scene["active_scene_thread_id"] == "character:pc_yaka"
    assert session.scene["scene_threads"]["character:pc_yaka"]["summary"] == "规范线程：雅卡在会议厅。"


def test_normalize_active_scene_thread_alias_merge_preserves_open_canonical_over_closed_alias():
    session = GameSession.new("group")
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.scene["active_scene_thread_id"] = "pc_yaka"
    session.scene["scene_threads"] = {
        "pc_yaka": {
            "summary": "旧别名线程：雅卡已退场。",
            "status": "closed",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
            "updated_at": "2026-05-14T13:58:00+00:00",
        },
        "character:pc_yaka": {
            "summary": "规范线程：雅卡在会议厅。",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
            "updated_at": "2026-05-14T13:40:00+00:00",
        },
    }

    result = normalize_active_scene_thread(session)

    assert result["changed"] is True
    assert "pc_yaka" not in session.scene["scene_threads"]
    thread = session.scene["scene_threads"]["character:pc_yaka"]
    assert "status" not in thread
    assert thread["summary"] == "规范线程：雅卡在会议厅。"
    assert session.scene["active_scene_thread_id"] == "character:pc_yaka"


def test_audit_patch_applies_low_risk_recent_action_result_tag():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    session.characters["pc_ahu"] = Character(id="pc_ahu", name="阿狐", player_id="p1")
    session.player_character_map = {"p1": "pc_ahu"}
    payload = {
        "safe_patches": {
            "character_tags": [
                {
                    "character_id": "pc_ahu",
                    "tags": [
                        {
                            "key": "最近行动结果",
                            "value": "震天雷滚入石屋后爆炸，守军阵脚大乱",
                            "layer": "status",
                        }
                    ],
                }
            ]
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="滚震天雷进石屋",
        completion="震天雷滚入石屋后爆炸，守军阵脚大乱。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"rule_name": "震天雷"},
                "result": {"ok": True, "message": "震天雷滚入石屋后爆炸，守军阵脚大乱"},
            }
        ],
    )

    assert not result["rejected"]
    assert any(item["type"] == "character_tags" for item in result["applied"])
    assert any(tag.key == "最近行动结果" and "震天雷" in tag.value for tag in session.characters["pc_ahu"].tags)


def test_audit_patch_scene_can_use_completion_as_tool_backed_evidence():
    session = GameSession.new("group")
    session.scene["_game_started"] = True
    payload = {
        "safe_patches": {
            "scene": {
                "summary": "震天雷在石屋内爆炸，屋内守军阵脚大乱",
                "current_conflict": "石屋守军被爆炸压制，局势转向清剿残敌",
            }
        }
    }

    result = apply_continuity_audit_patches(
        session,
        payload,
        actor={"player_id": "p1"},
        player_message="滚震天雷进石屋",
        completion="震天雷在石屋内爆炸，屋内守军阵脚大乱，局势转向清剿残敌。",
        tool_results=[
            {
                "tool": "execute_rule",
                "args": {"rule_name": "震天雷"},
                "result": {"ok": True, "message": "爆炸成功"},
            }
        ],
    )

    assert any(item["type"] == "scene" for item in result["applied"])
    assert session.scene["summary"] == "震天雷在石屋内爆炸，屋内守军阵脚大乱"
    assert session.scene["current_conflict"] == "石屋守军被爆炸压制，局势转向清剿残敌"



def test_normalize_resolved_combat_continuity_retires_stale_local_hooks():
    session = GameSession.new("group")
    session.scene.update(
        {
            "summary": "阿狐清空石屋，后院老徐和雅卡还在缠斗。",
            "current_objective": "确认石屋是否还有残敌，然后支援后院老徐和雅卡的战斗",
            "current_conflict": "石屋被震天雷内部引爆，前院肃清；后院战斗仍在继续",
            "stakes": "后院战斗需尽快收尾，否则史东寨将完成防御部署。",
            "last_resolution": {
                "outcome": "凯德确认：后院全部残敌已被杨永信和陈大虎合力清除，无剩余活敌。"
            },
            "open_hooks": [
                {"id": "backyard", "text": "后院仍有残敌和老徐缠斗"},
            ],
            "scene_threads": {
                "character:pc_yaka": {
                    "summary": "雅卡在后院接战中，老徐在左侧架刀。",
                    "current_conflict": "后院仍有沙袋垛后的持刀敌兵和老徐缠斗",
                    "participants": ["pc_yaka"],
                    "active_character_id": "pc_yaka",
                }
            },
        }
    )
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.characters["pc_yaka"].upsert_tags(
        [
            {
                "key": "当前位置",
                "value": "前院已被阿狐震天雷清空；后院仍有沙袋垛后的持刀敌兵和老徐缠斗",
                "layer": "status",
            }
        ]
    )

    result = normalize_resolved_combat_continuity(session)

    assert result["changed"] is True
    assert "后院" in result["locations"]
    assert "已过期" in session.scene["current_conflict"]
    assert "仍在继续" not in session.scene["current_conflict"]
    assert session.scene["open_hooks"][0]["status"] == "resolved"
    assert "仍有" not in session.scene["scene_threads"]["character:pc_yaka"]["current_conflict"]
    tag_value = session.characters["pc_yaka"].tags[0].value
    assert "已过期" in tag_value
    assert "缠斗" not in tag_value


def test_normalize_resolved_combat_continuity_does_not_touch_active_battle():
    session = GameSession.new("group")
    session.battle = {"active": True}
    session.scene["last_resolution"] = "后院残敌已清除一人。"
    session.scene["current_conflict"] = "后院战斗仍在继续"

    result = normalize_resolved_combat_continuity(session)

    assert result["changed"] is False
    assert session.scene["current_conflict"] == "后院战斗仍在继续"
