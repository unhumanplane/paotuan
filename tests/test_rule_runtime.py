from pathlib import Path

from astrbot_plugin_auto_trpg_dm.rules.python_runtime import PythonRuleRuntime


def test_register_and_execute_rule(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path, timeout_seconds=3)
    code = """
def calculate(skill, difficulty):
    total = roll("1d20") + skill
    return {
        "total": total,
        "success": total >= difficulty,
    }
""".strip()
    registered = runtime.register_rule(
        rule_name="skill_check",
        description="basic skill check",
        code_string=code,
        input_schema={"skill": "number", "difficulty": "number"},
    )
    assert registered["ok"] is True
    executed = runtime.execute_rule("skill_check", {"skill": 3, "difficulty": 10})
    assert executed["ok"] is True
    assert "total" in executed["result"]
    assert executed["rolls"][0]["expression"] == "1d20"


def test_rejects_import(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path)
    result = runtime.register_rule(
        rule_name="bad",
        description="bad",
        code_string="import os\ndef calculate():\n    return 1",
    )
    assert result["ok"] is False
    assert result["error"] == "validation_failed"


def test_rejects_attribute_escape(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path)
    result = runtime.register_rule(
        rule_name="bad_attr",
        description="bad",
        code_string="def calculate(x):\n    return x.__class__",
    )
    assert result["ok"] is False
    assert result["error"] == "validation_failed"


def test_rejects_shadowing_roll_helper(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path)
    code = """
def calculate():
    roll = roll("1d20")
    return {"total": roll}
""".strip()

    result = runtime.register_rule(
        rule_name="shadow_roll",
        description="bad shadowing",
        code_string=code,
    )

    assert result["ok"] is False
    assert result["error"] == "validation_failed"
    assert "reserved" in result["reason"]
    assert "roll" in result["reason"]


def test_rejects_calculate_argument_shadowing_roll(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path)
    code = """
def calculate(roll):
    return {"total": roll}
""".strip()

    result = runtime.register_rule(
        rule_name="arg_shadow_roll",
        description="bad arg shadowing",
        code_string=code,
    )

    assert result["ok"] is False
    assert result["error"] == "validation_failed"
    assert "reserved" in result["reason"]
    assert "roll" in result["reason"]


def test_rejects_undefined_local_name(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path)
    code = """
def calculate(**kwargs):
    damage_dice = kwargs.get("damage_dice_count", 2)
    return {"dice": damage_dice_count}
""".strip()

    result = runtime.register_rule(
        rule_name="undefined_damage_dice_count",
        description="bad undefined variable",
        code_string=code,
        input_schema={"damage_dice_count": "number"},
    )

    assert result["ok"] is False
    assert result["error"] == "validation_failed"
    assert "undefined name" in result["reason"]
    assert "damage_dice_count" in result["reason"]


def test_allows_kwargs_get_and_safe_helpers(tmp_path: Path):
    runtime = PythonRuleRuntime(tmp_path, timeout_seconds=3)
    code = """
def calculate(**kwargs):
    damage_dice_count = kwargs.get("damage_dice_count", 2)
    total = roll(str(damage_dice_count) + "d6")
    return {"total": total}
""".strip()

    registered = runtime.register_rule(
        rule_name="damage_roll",
        description="valid helper usage",
        code_string=code,
        input_schema={"damage_dice_count": "number"},
    )
    assert registered["ok"] is True

    executed = runtime.execute_rule("damage_roll", {"damage_dice_count": 2})
    assert executed["ok"] is True
    assert executed["rolls"][0]["expression"] == "2d6"

