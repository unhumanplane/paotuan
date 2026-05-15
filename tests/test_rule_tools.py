import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.rules.python_runtime import PythonRuleRuntime
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.rule_tools import RuleTools


RULE_CODE = """
def calculate(**kwargs):
    return {"total": 1}
"""


def _make_rule_tools(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    repository.save_session(GameSession.new("group"))
    runtime = PythonRuleRuntime(tmp_path / "rules")
    return RuleTools(repository, runtime, "group"), runtime


def test_list_rules_repeated_same_query_returns_lightweight_reuse(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    runtime.register_rule(
        "combat_check",
        "Resolve a combat check.",
        RULE_CODE,
        input_schema={"bonus": "attack bonus"},
        output_schema={"total": "check total"},
        tags=["combat"],
    )

    first = asyncio.run(tools.list_rules(detail_level="detail", tag="combat", limit=4))
    second = asyncio.run(tools.list_rules(detail_level="detail", tag="combat", limit=4))

    assert first["ok"] is True
    assert "details" in first
    assert second["ok"] is True
    assert second["rules_reused"] is True
    assert "rules" not in second


def test_list_rules_restricts_unfiltered_detail_for_large_rule_sets(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    for index in range(52):
        runtime.register_rule(
            f"rule_{index}",
            f"Resolve rule {index}.",
            RULE_CODE,
            input_schema={f"arg_{item}": "long parameter description" for item in range(12)},
            output_schema={f"out_{item}": "long output description" for item in range(12)},
            tags=["combat" if index % 2 == 0 else "exploration"],
        )

    result = asyncio.run(tools.list_rules(detail_level="detail", limit=16))

    assert result["ok"] is True
    assert result["detail_restricted"] is True
    assert len(result["rules"]["level_1"]["names"]) <= 32
    assert len(result["details"]) <= 4
    assert result["details_omitted"] >= 48
    assert "_omitted_keys" in result["details"][0]["input_schema"]


def test_execute_rule_does_not_inject_threshold_when_difficulty_exists(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    runtime.register_rule(
        "skill_check",
        "Resolve a skill check.",
        """
def calculate(skill, difficulty):
    return {"total": skill, "success": skill >= difficulty}
""".strip(),
        input_schema={"skill": "number", "difficulty": "number"},
    )

    result = asyncio.run(
        tools.execute_rule(
            "skill_check",
            args={"skill": 12, "difficulty": 10, "target": 15},
            reason="test difficulty alias handling",
        )
    )

    assert result["ok"] is True
    assert result["result"]["total"] == 12
    assert result["result"]["success"] is True
    audit = tools.repository.last_audit_records("group", limit=1)[0]
    assert "threshold" not in audit["input"]["args"]
    assert "target" not in audit["input"]["args"]


def test_execute_rule_drops_contextual_target_args_before_schema_validation(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    runtime.register_rule(
        "charge_shove",
        "Resolve a shove check.",
        """
def calculate(strength):
    return {"total": strength}
""".strip(),
        input_schema={"strength": "number"},
    )

    result = asyncio.run(
        tools.execute_rule(
            "charge_shove",
            args={
                "strength": 15,
                "target_aware": True,
                "target_size": "medium",
                "situation": "charging into a distracted mercenary",
            },
            reason="test contextual arg cleanup",
        )
    )

    assert result["ok"] is True
    assert result["result"]["total"] == 15
    audit = tools.repository.last_audit_records("group", limit=1)[0]
    assert audit["input"]["args"] == {"strength": 15}


def test_execute_rule_d20_normalizes_common_llm_fields_without_double_count(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    runtime.register_rule(
        "d20_skill_check",
        "Resolve a d20 skill check.",
        """
def calculate(bonus=0, dc=10):
    roll_total = roll("1d20")
    total = roll_total + bonus
    return {"roll": roll_total, "total": total, "dc": dc, "success": total >= dc}
""".strip(),
        input_schema={"bonus": "number", "dc": "number"},
    )

    result = asyncio.run(
        tools.execute_rule(
            "d20_skill_check",
            args={
                "ability": "intelligence",
                "skill": "investigation",
                "dc": 12,
                "bonus": 3,
                "modifier": 3,
                "proficiency": True,
                "advantage": False,
            },
            reason="test d20 cleanup",
        )
    )

    assert result["ok"] is True
    audit = tools.repository.last_audit_records("group", limit=1)[0]
    assert audit["input"]["args"] == {"dc": 12, "bonus": 3}
    assert "coerced_args" not in result
    assert result["result"]["total"] == result["result"]["roll"] + 3


def test_execute_rule_d20_maps_string_difficulty_and_sums_bonus_aliases(tmp_path):
    tools, runtime = _make_rule_tools(tmp_path)
    runtime.register_rule(
        "d20_skill_check",
        "Resolve a d20 skill check.",
        """
def calculate(bonus=0, dc=10):
    roll_total = roll("1d20")
    return {"roll": roll_total, "total": roll_total + bonus, "dc": dc}
""".strip(),
        input_schema={"bonus": "number", "dc": "number"},
    )

    result = asyncio.run(
        tools.execute_rule(
            "d20_skill_check",
            args={
                "difficulty": "hard",
                "ability_modifier": 2,
                "proficiency_bonus": 3,
                "skill": "demolition",
            },
            reason="test d20 aliases",
        )
    )

    assert result["ok"] is True
    audit = tools.repository.last_audit_records("group", limit=1)[0]
    assert audit["input"]["args"] == {"dc": 18, "bonus": 5}


def test_resolve_check_accepts_natural_llm_check_fields(tmp_path):
    tools, _runtime = _make_rule_tools(tmp_path)

    result = asyncio.run(
        tools.resolve_check(
            actor_id="pc_yaka",
            action="assemble C4 and hide it under underwater work notes",
            check_type="mechanical",
            ability="intelligence",
            skill="underwater demolition",
            difficulty="hard",
            bonus=3,
            proficiency=True,
            advantage=False,
            modifier_note="trained in underwater demolition",
            stakes="success prepares the charge; partial success leaves traces",
        )
    )

    assert result["ok"] is True
    assert result["tool"] == "resolve_check"
    assert result["dc"] == 18
    assert result["bonus"] == 3
    assert result["advantage"] == "normal"
    assert result["state_write_support"] is True
    assert result["check_id"].startswith("chk_")
    saved = tools.repository.load_session("group")
    pending = saved.scene["_pending_outputs"]
    assert pending[-1]["type"] == "dice_check"
    assert pending[-1]["rule_name"] == "resolve_check"


def test_resolve_check_sums_common_modifier_aliases_and_chinese_labels(tmp_path):
    tools, _runtime = _make_rule_tools(tmp_path)

    result = asyncio.run(
        tools.resolve_check(
            actor_name="雅卡",
            action="在水下安装炸药并掩盖痕迹",
            target_dc="",
            difficulty="困难",
            modifier=1,
            ability_modifier=2,
            proficiency_bonus=3,
            item_bonus=1,
            penalty=2,
            advantage="劣势",
            stakes="失败会留下明显痕迹",
        )
    )

    assert result["ok"] is True
    assert result["dc"] == 18
    assert result["bonus"] == 5
    assert result["advantage"] == "disadvantage"
    assert result["rolls"][0]["expression"] == "2d20kl1"
    assert result["result"]["total"] == result["result"]["roll"] + 5


def test_resolve_check_accepts_target_dc_and_disadvantage_alias(tmp_path):
    tools, _runtime = _make_rule_tools(tmp_path)

    result = asyncio.run(
        tools.resolve_check(
            action="force open the hatch quietly",
            target_dc=16,
            ability_modifier=4,
            proficiency_bonus=2,
            disadvantage=True,
        )
    )

    assert result["ok"] is True
    assert result["dc"] == 16
    assert result["bonus"] == 6
    assert result["advantage"] == "disadvantage"
