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

