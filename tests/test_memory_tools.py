from astrbot_plugin_auto_trpg_dm.tools.memory_tools import infer_tags_from_text


def test_infer_compact_character_tags():
    tags = infer_tags_from_text("职业法师 武器双持斧 种族矮人")

    by_key = {item["key"]: item["value"] for item in tags}
    assert by_key["职业"] == "法师"
    assert by_key["武器"] == "双持斧"
    assert by_key["种族"] == "矮人"


def test_infer_comma_separated_character_tags():
    tags = infer_tags_from_text("职业法师，专长近战双斧，次要火焰法术，火球术，点燃武器，常用装备锯齿双斧，棉布娃娃，厕纸")

    by_key = {item["key"]: item["value"] for item in tags}
    assert by_key["职业"] == "法师"
    assert by_key["专长"] == "近战双斧"
    assert by_key["次要能力"] == ["火焰法术", "火球术", "点燃武器"]
    assert "锯齿双斧" in by_key["常用装备"]


def test_infer_style_tag():
    tags = infer_tags_from_text("补充风格“酗酒矮人战斗法师”")

    by_key = {item["key"]: item["value"] for item in tags}
    assert by_key["风格"] == "酗酒矮人战斗法师"
