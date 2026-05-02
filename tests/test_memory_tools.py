import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.storage.json_repository import JsonGameRepository
from astrbot_plugin_auto_trpg_dm.tools.memory_tools import (
    MemoryTools,
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


def test_allows_standard_astartes_when_not_claiming_army_or_mythic_power():
    result = validate_character_card_payload(
        name="极限战士喷火兵",
        summary="极限战士第五连的阿斯塔特修士，装备钷素喷火器和动力拳套，执行底巢清剿任务。",
        tags=[
            {"key": "阵营", "value": "Ultramarines", "layer": "identity"},
            {"key": "装备", "value": "钷素喷火器、动力拳套、动力甲", "layer": "equipment"},
            {"key": "弱点", "value": "重甲限制机动性，烟尘和狭窄地形会影响视野", "layer": "status"},
        ],
        require_name=True,
    )

    assert result is None


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


def test_start_game_accepts_json_string_outline_and_text_scene(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags.update(
        {
            "genre": "grimdark_sci_fi",
            "tone": "军事恐怖",
            "starting_premise": "极限战士清剿底巢基因窃取者巢穴。",
        }
    )
    session.world_tags["_background_ready"] = True
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"})
    result = asyncio.run(
        tools.start_game(
            opening_intro="底巢警报在头盔中尖啸，你踏入废弃枢纽站，黑暗管廊里传来爪刃刮擦金属的声音。热雾从破裂管道里涌出，鸟卜仪同时捕捉到一个高速逼近的异形信号。",
            player_guidance="你可以侦查、喷火压制，或呼叫队友封锁侧翼。",
            campaign_outline='{"act_1":"斥候突袭暴露巢穴入口","act_2":"深入底巢发现教派仪式","act_3":"摧毁节点或撤离呼叫支援"}',
            scene_patch="底巢废弃枢纽站，第一只斥候正从黑暗中扑出。",
        )
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    assert saved.scene["_game_started"] is True
    assert "底巢废弃枢纽站" in saved.scene["summary"]


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


def test_dead_bound_character_owner_can_rejoin_with_new_character(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_old"] = Character(
        id="pc_old",
        name="旧角色",
        player_id="p1",
        summary="已经参加开场的角色。",
        tags=[TagValue(key="生命状态", value="确认死亡", layer="status")],
    )
    session.player_character_map["p1"] = "pc_old"
    repository.save_session(session)

    tools = MemoryTools(
        repository,
        "group",
        actor={"player_id": "p1"},
        message="/dm 我的角色死了，我用新角色重新加入",
    )
    result = asyncio.run(
        tools.create_character(
            character_id="pc_new",
            name="新角色",
            summary="同队伍水平的后继调查员，正在附近寻找失踪同伴。",
            tags=[{"key": "身份", "value": "后继调查员", "layer": "identity"}],
        )
    )

    assert result["ok"] is True
    assert result["bound_player_id"] == "p1"
    assert result["rejoin_replacement"]["previous_character_id"] == "pc_old"
    saved = repository.load_session("group")
    assert saved.player_character_map["p1"] == "pc_new"
    old_tags = {(tag.layer, tag.key): tag.value for tag in saved.characters["pc_old"].tags}
    new_tags = {(tag.layer, tag.key): tag.value for tag in saved.characters["pc_new"].tags}
    assert old_tags[("relations", "后继角色")] == "pc_new"
    assert new_tags[("relations", "前任角色")] == "pc_old"


def test_alive_bound_character_owner_cannot_rejoin_after_start(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_old"] = Character(
        id="pc_old",
        name="旧角色",
        player_id="p1",
        summary="仍在行动的角色。",
        tags=[TagValue(key="当前状态", value="受伤但仍能行动", layer="status")],
    )
    session.player_character_map["p1"] = "pc_old"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 我想换新角色")
    result = asyncio.run(
        tools.create_character(
            character_id="pc_new",
            name="新角色",
            summary="同队伍水平的后继调查员。",
            tags=[],
        )
    )

    assert result["ok"] is False
    assert result["error"] == "character_card_locked_after_start"
    assert repository.load_session("group").player_character_map["p1"] == "pc_old"


def test_rejoin_does_not_allow_overwriting_dead_character(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_old"] = Character(
        id="pc_old",
        name="旧角色",
        player_id="p1",
        tags=[TagValue(key="生命状态", value="确认死亡", layer="status")],
    )
    session.player_character_map["p1"] = "pc_old"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 重新加入")
    result = asyncio.run(
        tools.create_character(
            character_id="pc_old",
            name="复写旧角色",
            summary="试图覆盖旧角色。",
            tags=[],
        )
    )

    assert result["ok"] is False
    assert result["error"] == "character_card_locked_after_start"


def test_terminal_rejoin_can_bind_existing_unowned_successor(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_old"] = Character(
        id="pc_old",
        name="旧角色",
        player_id="p1",
        tags=[TagValue(key="当前状态", value="永久退场", layer="status")],
    )
    session.characters["pc_successor"] = Character(
        id="pc_successor",
        name="后继者",
        player_id="",
        summary="同队伍水平的后继角色。",
    )
    session.player_character_map["p1"] = "pc_old"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 我绑定这个后继角色重新加入")
    result = asyncio.run(tools.bind_player_character(character_id="pc_successor"))

    assert result["ok"] is True
    saved = repository.load_session("group")
    assert saved.player_character_map["p1"] == "pc_successor"
    assert saved.characters["pc_successor"].player_id == "p1"
