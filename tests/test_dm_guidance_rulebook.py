from pathlib import Path

from astrbot_plugin_auto_trpg_dm.rulebook.store import RulebookStore


def test_dm_guidance_query_hits_improvisation_and_consequences():
    store = RulebookStore(
        Path("missing"),
        fallback_dirs=[Path("astrbot_plugin_auto_trpg_dm/rulebook/seed/dnd2024_core")],
    )

    result = store.query("DM如何即兴裁定失败后果", mode_hint="narrative", limit=4, max_chars=1600)

    assert result["ok"] is True
    categories = {item["category"] for item in result["matches"]}
    ids = {item["id"] for item in result["matches"]}
    assert "dm_guidance" in categories
    assert ids.intersection(
        {
            "dmg2024.guidance.improvisation",
            "dmg2024.guidance.consequences",
            "dmg2024.guidance.fair_flexible",
        }
    )


def test_dm_guidance_query_hits_table_safety():
    store = RulebookStore(
        Path("missing"),
        fallback_dirs=[Path("astrbot_plugin_auto_trpg_dm/rulebook/seed/dnd2024_core")],
    )

    result = store.query("玩家说这个内容不舒服越界时 DM 怎么办", mode_hint="narrative", limit=3)

    assert result["ok"] is True
    assert result["matches"]
    assert result["matches"][0]["category"] == "dm_guidance"


def test_dm_guidance_query_hits_combat_narration():
    store = RulebookStore(
        Path("missing"),
        fallback_dirs=[Path("astrbot_plugin_auto_trpg_dm/rulebook/seed/dnd2024_core")],
    )

    result = store.query("战斗中怎样叙述命中和伤害", mode_hint="tactical", limit=4)

    assert result["ok"] is True
    assert any(item["id"] == "dmg2024.guidance.combat_narration" for item in result["matches"])
