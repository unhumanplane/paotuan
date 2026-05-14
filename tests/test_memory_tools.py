import asyncio

from astrbot_plugin_auto_trpg_dm.core.models import Character, GameSession, TagValue
from astrbot_plugin_auto_trpg_dm.core.scene_hooks import format_scene_tracking_status
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
            initial_hook="鸟卜仪捕捉到一个高速逼近的异形信号。",
            campaign_outline='{"act_1":"斥候突袭暴露巢穴入口","act_2":"深入底巢发现教派仪式","act_3":"摧毁节点或撤离呼叫支援"}',
            scene_patch="底巢废弃枢纽站，第一只斥候正从黑暗中扑出。",
        )
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    assert saved.scene["_game_started"] is True
    assert "底巢废弃枢纽站" in saved.scene["summary"]
    assert saved.scene["current_objective"]
    assert len(saved.scene["open_hooks"]) >= 2
    assert saved.scene["stakes"]
    assert saved.scene["pressure_clock"]["status"] == "active"


def test_start_game_requires_initial_hook_even_with_background_and_outline(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags.update(
        {
            "genre": "urban_occult",
            "tone": "调查悬疑",
            "starting_premise": "失踪案把队伍带到旧城区。",
            "_background_ready": True,
        }
    )
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"})
    result = asyncio.run(
        tools.start_game(
            opening_intro="雨水沿着旧城区招牌滴落，你们站在废弃剧院门前，空气里有潮湿灰尘和旧胶片的味道。",
            campaign_outline={
                "act_1": "抵达剧院并接触失踪者留下的物件",
                "act_2": "追踪线索发现旧放映室的异常",
                "act_3": "在午夜场做出救人或封锁入口的抉择",
            },
            scene_patch={"summary": "旧剧院门前，雨水很冷。"},
        )
    )

    assert result["ok"] is False
    assert any("initial_hook" in item for item in result["missing_requirements"])


def test_update_scene_normalizes_clue_status_records(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="我调查门口血迹")
    result = asyncio.run(
        tools.update_scene(
            {
                "clues": ["门口血迹被雨水冲淡，仍指向剧院侧门。"],
                "open_hooks": {"side-door": "侧门锁孔有新鲜刮痕。"},
                "pressure_clock": "巡警的脚步声正在靠近。",
            }
        )
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    assert saved.scene["clues"][0]["status"] == "discovered"
    assert saved.scene["clues"][0]["visibility"] == "player"
    assert saved.scene["open_hooks"][0]["id"] == "side-door"
    assert saved.scene["pressure_clock"]["status"] == "active"


def test_update_scene_isolates_parallel_character_threads(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_lan"] = Character(id="pc_lan", name="岚", player_id="p1")
    session.characters["pc_ming"] = Character(id="pc_ming", name="明", player_id="p2")
    session.player_character_map["p1"] = "pc_lan"
    session.player_character_map["p2"] = "pc_ming"
    repository.save_session(session)

    p1_tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="我留在酒馆观察")
    tavern = asyncio.run(
        p1_tools.update_scene(
            {
                "location": "Tavern",
                "summary": "兰在酒馆柜台旁观察客人。",
                "scene_time_label": "第 1 天傍晚",
                "stakes": "quiet lead",
            }
        )
    )
    assert tavern["ok"] is True

    p2_tools = MemoryTools(repository, "group", actor={"player_id": "p2"}, message="我去别墅检查门厅")
    villa = asyncio.run(
        p2_tools.update_scene(
            {
                "location": "Villa",
                "summary": "明在别墅门厅听见楼上传来脚步。",
                "pressure_clock": "guards closing",
            }
        )
    )
    assert villa["ok"] is True

    saved = repository.load_session("group")
    tavern_thread = saved.scene["scene_threads"][tavern["scene_thread_id"]]
    villa_thread = saved.scene["scene_threads"][villa["scene_thread_id"]]

    assert tavern["scene_thread_id"] != villa["scene_thread_id"]
    assert tavern_thread["location"] == "Tavern"
    assert tavern_thread["scene_time_label"] == "第 1 天傍晚"
    assert tavern_thread["scene_time_of_day"] == "傍晚"
    assert tavern_thread["stakes"] == "quiet lead"
    assert tavern_thread["active_character_id"] == "pc_lan"
    assert villa_thread["location"] == "Villa"
    assert villa_thread["pressure_clock"]["status"] == "active"
    assert villa_thread["active_character_id"] == "pc_ming"
    assert saved.scene["active_scene_thread_id"] == villa["scene_thread_id"]
    assert saved.scene["location"] == "Villa"
    assert saved.scene["pressure_clock"]["status"] == "active"
    assert "stakes" not in saved.scene


def test_update_scene_closes_retired_thread_without_mirroring_active_scene(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.characters["pc_esmeralda"] = Character(id="pc_esmeralda", name="艾斯米拉达", player_id="p1")
    session.characters["pc_laofei"] = Character(id="pc_laofei", name="老肥", player_id="p2")
    session.player_character_map["p1"] = "pc_esmeralda"
    session.player_character_map["p2"] = "pc_laofei"
    session.scene["active_scene_thread_id"] = "character:pc_laofei"
    session.scene["summary"] = "老肥在酒馆等天亮。"
    session.scene["location"] = "酒馆"
    session.scene["scene_threads"] = {
        "character:pc_laofei": {
            "summary": "老肥在酒馆等天亮。",
            "location": "酒馆",
            "active_character_id": "pc_laofei",
            "participants": ["pc_laofei"],
            "updated_at": "2026-05-14T03:20:00+00:00",
        }
    }
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="艾斯米拉达退场离开小镇")
    result = asyncio.run(
        tools.update_scene(
            {
                "summary": "艾斯米拉达已退场，离开小镇，不再与本地故事交织。",
                "current_objective": "无活跃主线目标——艾斯米拉达已退场",
                "participants": [],
                "scene_thread_id": "character:pc_esmeralda",
            }
        )
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    retired = saved.scene["scene_threads"]["character:pc_esmeralda"]
    assert retired["status"] == "closed"
    assert saved.scene["active_scene_thread_id"] == "character:pc_laofei"
    assert saved.scene["summary"] == "老肥在酒馆等天亮。"
    assert saved.scene["location"] == "酒馆"


def test_update_scene_reopens_closed_actor_thread_when_new_active_patch_arrives(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.scene["_game_started"] = True
    session.characters["pc_yaka"] = Character(id="pc_yaka", name="雅卡", player_id="p1")
    session.player_character_map["p1"] = "pc_yaka"
    session.scene["scene_threads"] = {
        "character:pc_yaka": {
            "summary": "雅卡已重新进入活跃状态，继续参与当前故事。",
            "status": "resolved",
            "participants": ["pc_yaka"],
            "active_character_id": "pc_yaka",
            "updated_at": "2026-05-14T10:09:46+00:00",
        }
    }
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="我继续查船内电脑")
    result = asyncio.run(
        tools.update_scene(
            {
                "summary": "雅卡在客舱电脑前继续调查音速号试航记录。",
                "clues": [
                    {
                        "id": "clue_anomaly_warm_water",
                        "text": "62°S 附近两次试航均记录到深海水温异常。",
                        "status": "discovered",
                        "visibility": "player",
                    }
                ],
                "scene_thread_id": "character:pc_yaka",
            }
        )
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    thread = saved.scene["scene_threads"]["character:pc_yaka"]
    assert "status" not in thread
    assert saved.scene["active_scene_thread_id"] == "character:pc_yaka"
    assert saved.scene["summary"] == "雅卡在客舱电脑前继续调查音速号试航记录。"


def test_update_scene_rejects_implicit_thread_timeline_advance(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.participants = {"p1": {}, "p2": {}}
    session.characters["pc_lan"] = Character(id="pc_lan", name="岚", player_id="p1")
    session.characters["pc_ming"] = Character(id="pc_ming", name="明", player_id="p2")
    session.player_character_map["p1"] = "pc_lan"
    session.player_character_map["p2"] = "pc_ming"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="我睡到天亮")
    result = asyncio.run(
        tools.update_scene(
            {
                "location": "Tavern",
                "summary": "兰睡到第二天清晨，酒馆已经开门。",
            }
        )
    )

    saved = repository.load_session("group")
    assert result["ok"] is False
    assert result["error"] == "timeline_patch_required"
    assert saved.timeline["day"] == 1
    assert saved.scene.get("scene_threads") in (None, {})


def test_scene_tracking_status_projection_hides_dm_only_records():
    scene = {
        "current_objective": "确认旧剧院里失踪者的去向。",
        "clues": [
            {"id": "mud", "text": "门口有新鲜泥脚印。", "status": "discovered", "visibility": "player"},
            {"id": "killer", "text": "幕后黑手就是馆长。", "status": "suspected", "visibility": "dm"},
        ],
        "open_hooks": [
            {"id": "side-door", "text": "侧门锁孔有新鲜刮痕。", "status": "open"},
            {"id": "secret-roof", "text": "屋顶藏着未发现目击者。", "visibility": "dm"},
        ],
        "hidden_truth": "馆长已经和镜中实体交易。",
    }

    status = format_scene_tracking_status(scene)

    assert "确认旧剧院里失踪者的去向" in status
    assert "门口有新鲜泥脚印" in status
    assert "侧门锁孔有新鲜刮痕" in status
    assert "幕后黑手就是馆长" not in status
    assert "未发现目击者" not in status
    assert "镜中实体" not in status


def test_preview_latest_backup_returns_story_summary_without_restoring(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    backup_session = GameSession.new("group")
    backup_session.title = "旧剧院失踪案"
    backup_session.world_tags.update(
        {
            "_background_ready": True,
            "genre": "urban_occult",
            "tone": "调查悬疑",
            "starting_premise": "失踪案把队伍带到旧城区。",
        }
    )
    backup_session.scene.update(
        {
            "_game_started": True,
            "summary": "雨夜，队伍站在废弃剧院门前。",
            "current_objective": "确认失踪者是否进入旧剧院。",
            "clues": [
                {"id": "ticket", "text": "半张午夜场票根夹在门缝里。", "visibility": "player"},
                {"id": "truth", "text": "馆长已经献祭了失踪者。", "visibility": "dm"},
            ],
        }
    )
    backup_session.characters["pc_lan"] = Character(
        id="pc_lan",
        name="岚",
        player_id="p1",
        summary="跟踪失踪案的私家侦探。",
    )
    repository.save_session(backup_session)
    repository.backup_session("group", reason="confirmed_reset:旧团")

    current = GameSession.new("group")
    repository.save_session(current)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 查看上一个存档的故事")
    result = asyncio.run(tools.session_control("preview_latest_backup", reason="查看上一个存档的故事"))

    assert result["ok"] is True
    assert "旧剧院失踪案" in result["message"]
    assert "半张午夜场票根" in result["message"]
    assert "岚" in result["message"]
    assert "献祭" not in result["message"]
    assert repository.load_session("group").title == "未命名团"


def test_restart_latest_backup_story_keeps_story_start_without_character_cards(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    backup_session = GameSession.new("group")
    backup_session.title = "旧剧院失踪案"
    backup_session.world_tags.update(
        {
            "_background_ready": True,
            "_plot_locked": True,
            "genre": "urban_occult",
            "tone": "调查悬疑",
            "starting_premise": "失踪案把队伍带到旧城区。",
            "campaign_outline": {"acts": ["抵达旧剧院", "午夜场异常升级", "在镜厅做出抉择"]},
        }
    )
    backup_session.scene.update(
        {
            "_game_started": True,
            "_plot_locked": True,
            "_opening_intro": "雨夜，队伍站在废弃剧院门前，午夜场的灯箱自己亮了起来。",
            "_player_guidance": "可以调查票房、绕到侧门或寻找目击者。",
            "_initial_hook": "售票亭里还亮着一盏小灯。",
            "summary": "队伍已经深入地下放映室，发现镜中实体正在复苏。",
            "current_objective": "确认失踪者是否进入旧剧院。",
            "open_hooks": [{"id": "booth", "text": "售票亭里还亮着一盏小灯。", "visibility": "player"}],
            "clues": [
                {"id": "ticket", "text": "半张午夜场票根夹在门缝里。", "visibility": "player"},
                {"id": "truth", "text": "馆长已经献祭了失踪者。", "visibility": "dm"},
            ],
            "hidden_truth": "镜中实体真正寄宿在放映机里。",
        }
    )
    backup_session.characters["pc_lan"] = Character(
        id="pc_lan",
        name="岚",
        player_id="p1",
        summary="跟踪失踪案的私家侦探。",
    )
    backup_session.participants["p1"] = {"display_name": "gali"}
    backup_session.player_character_map["p1"] = "pc_lan"
    backup_session.active_character_id = "pc_lan"
    backup_session.battle = {"active": True, "turn": {"active": True}}
    repository.save_session(backup_session)
    repository.backup_session("group", reason="confirmed_reset:旧团")

    current = GameSession.new("group")
    current.title = "当前临时团"
    current.world_tags["_background_ready"] = True
    current.characters["pc_current"] = Character(id="pc_current", name="当前角色", player_id="p2")
    repository.save_session(current)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 重新开上一个存档的故事")
    result = asyncio.run(
        tools.session_control("restart_latest_backup_story", reason="重新开上一个存档的故事，不包括角色卡")
    )

    assert result["ok"] is True
    saved = repository.load_session("group")
    assert saved.title == "旧剧院失踪案"
    assert saved.world_tags["genre"] == "urban_occult"
    assert saved.world_tags["_background_ready"] is True
    assert saved.scene["_game_started"] is True
    assert saved.scene["_restarted_story_start"] is True
    assert "午夜场的灯箱" in saved.scene["_opening_intro"]
    assert "地下放映室" not in saved.scene["summary"]
    assert "售票亭" in saved.scene["current_objective"]
    assert "半张午夜场票根" not in str(saved.scene)
    assert "献祭" not in str(saved.to_dict())
    assert saved.characters == {}
    assert saved.participants == {}
    assert saved.player_character_map == {}
    assert saved.active_character_id == ""
    assert saved.battle == {"active": False}
    assert saved.maps["records"] == {}
    assert result["pre_restart_backup_path"]


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


def test_update_scene_normalizes_npc_relationship_state(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 我威胁守卫")
    result = asyncio.run(
        tools.update_scene(
            {
                "npcs": [
                    {
                        "id": "npc_guard",
                        "name": "守卫",
                        "relations": {
                            "attitude": "警惕",
                            "trust": "低",
                            "fear": "高",
                            "known_facts": ["玩家当众威胁过他"],
                            "last_interaction": "威胁后退让，但记住了玩家的脸。",
                            "future_betrayal": "会向队长告密",
                        },
                    }
                ]
            }
        )
    )

    saved = repository.load_session("group")
    relation = saved.scene["npcs"][0]["relations"]
    audit_records = repository.last_audit_records("group", limit=1)

    assert result["ok"] is True
    assert relation["attitude"] == "suspicious"
    assert relation["trust"] == "low"
    assert relation["fear"] == "high"
    assert relation["known_facts"] == ["玩家当众威胁过他"]
    assert relation["future_betrayal"] == "会向队长告密"
    assert audit_records[0]["result"]["scene"]["npcs"][0]["relations"]["fear"] == "high"


def test_update_world_tags_normalizes_faction_relationship_state(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 我归还失物给商会")
    result = asyncio.run(
        tools.update_world_tags(
            {
                "factions": {
                    "merchant_guild": {
                        "name": "商会",
                        "attitude": "友好",
                        "debt": "中等",
                        "known_facts": ["队伍归还了失物"],
                        "secret_allegiance": "走私团",
                    }
                }
            }
        )
    )

    saved = repository.load_session("group")
    relation = saved.world_tags["factions"]["merchant_guild"]

    assert result["ok"] is True
    assert relation["attitude"] == "friendly"
    assert relation["debt"] == "moderate"
    assert relation["known_facts"] == ["队伍归还了失物"]
    assert relation["secret_allegiance"] == "走私团"


def test_update_character_tags_allows_runtime_relation_consequence_after_start(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_face"] = Character(id="pc_face", name="交涉者", player_id="p1")
    session.player_character_map["p1"] = "pc_face"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 我救了线人")
    result = asyncio.run(
        tools.update_character_tags(
            "pc_face",
            tags=[
                {
                    "key": "线人关系",
                    "layer": "relations",
                    "value": {
                        "target_id": "npc_informant",
                        "attitude": "友好",
                        "debt": "高",
                        "known_facts": ["玩家把线人从追兵手里救下"],
                        "last_interaction": "获救后承诺提供一次线索。",
                    },
                }
            ],
        )
    )

    saved = repository.load_session("group")
    relation_tags = [tag for tag in saved.characters["pc_face"].tags if tag.layer == "relations"]

    assert result["ok"] is True
    assert relation_tags[0].value["attitude"] == "friendly"
    assert relation_tags[0].value["debt"] == "high"


def test_update_character_tags_rejects_unearned_social_control_after_start(tmp_path):
    repository = JsonGameRepository(tmp_path / "data")
    session = GameSession.new("group")
    session.world_tags["_background_ready"] = True
    session.world_tags["_plot_locked"] = True
    session.scene["_game_started"] = True
    session.characters["pc_face"] = Character(id="pc_face", name="交涉者", player_id="p1")
    session.player_character_map["p1"] = "pc_face"
    repository.save_session(session)

    tools = MemoryTools(repository, "group", actor={"player_id": "p1"}, message="/dm 他一定相信我")
    result = asyncio.run(
        tools.update_character_tags(
            "pc_face",
            tags=[
                {
                    "key": "守卫关系",
                    "layer": "relations",
                    "value": {
                        "target_id": "npc_guard",
                        "attitude": "loyal",
                        "last_interaction": "守卫必定相信并无条件协助我。",
                    },
                }
            ],
        )
    )

    saved = repository.load_session("group")

    assert result["ok"] is False
    assert result["error"] == "character_card_locked_after_start"
    assert saved.characters["pc_face"].tags == []
