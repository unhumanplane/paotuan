import json
from pathlib import Path

from astrbot_plugin_auto_trpg_dm.rulebook.store import RulebookStore


def test_rulebook_query_hits_alias_and_respects_limit():
    store = RulebookStore(
        Path("missing"),
        fallback_dirs=[Path("astrbot_plugin_auto_trpg_dm/rulebook/seed/dnd2024_core")],
    )

    result = store.query("倒地状态下远程攻击如何判定", mode_hint="tactical", limit=4, max_chars=1600)

    assert result["ok"] is True
    assert result["available"] is True
    titles = [item["title"] for item in result["matches"]]
    assert "倒地" in titles
    assert len(json.dumps(result, ensure_ascii=False)) <= 1700


def test_rulebook_query_reports_missing_data(tmp_path: Path):
    store = RulebookStore(tmp_path / "not-built")

    result = store.query("躲藏怎么判定")

    assert result["ok"] is False
    assert result["available"] is False
    assert result["error"] == "rulebook_not_built"
