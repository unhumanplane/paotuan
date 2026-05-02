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
