from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.tools.memory_tools import (
    filter_runtime_character_tags_after_start,
    infer_tags_from_text,
    validate_character_card_party_balance,
    validate_character_card_payload,
)


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


def test_rejects_nuclear_material_character_card():
    result = validate_character_card_payload(
        name="U235石头人",
        summary="一个主要由矿物组成的类人生物，矿物元素含铀，可以维持可控临界态。",
        tags=[],
        require_name=True,
    )

    assert result
    assert result["error"] == "character_card_unreasonable"
    assert any("战略级资源" in reason for reason in result["reasons"])


def test_nuclear_material_card_exceeds_low_power_party_baseline():
    session = GameSession.new("group")
    session.characters["pc_bird"] = Character(
        id="pc_bird",
        name="小型原始鸟",
        player_id="p1",
        summary="一只谨慎的小型原始鸟，擅长低空侦察。",
        tags=[TagValue(key="体型", value="小型")],
    )

    result = validate_character_card_party_balance(
        session,
        "pc_stone",
        name="石头人-235",
        summary="身体含有浓缩铀和裂变反应堆结构，可进入可控裂变。",
        tags=[],
    )

    assert result
    assert result["error"] == "character_card_power_mismatch"
    assert "铀" in result["candidate_profile"]["matched_terms"]


def test_blocks_post_start_permanent_mutation_power_tags():
    allowed, blocked = filter_runtime_character_tags_after_start(
        [
            {
                "key": "当前状态",
                "value": "辐射合成代谢已激活，裂变能量转化为有机质，现在可以获得更强更有效的有益进化。",
                "layer": "status",
            },
            {
                "key": "伤势",
                "value": "口腔仍有烫伤，进食受影响。",
                "layer": "status",
            },
        ]
    )

    assert [item["key"] for item in allowed] == ["伤势"]
    assert [item["key"] for item in blocked] == ["当前状态"]
