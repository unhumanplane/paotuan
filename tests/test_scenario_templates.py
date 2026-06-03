from astrbot_plugin_auto_trpg_dm.core.scenario_templates import (
    build_campaign_preset_patch,
    build_campaign_preference_question,
    build_campaign_seed_patch,
    campaign_preference_gaps,
    campaign_preset_start_requested,
    format_campaign_preset_list,
    format_campaign_preset_loaded_reply,
    looks_like_campaign_preset_list_request,
    looks_like_campaign_generation_request,
    looks_like_campaign_preference_answer,
    looks_like_custom_campaign_brief,
    select_campaign_preset,
    should_ask_campaign_preferences,
)


def test_generic_campaign_request_does_not_auto_select_preset():
    text = "来一个跑团同好研讨会社团剧本"

    assert looks_like_campaign_generation_request(text) is True
    assert select_campaign_preset(text) is None


def test_warhammer_seed_asks_preferences_without_auto_template_matching():
    text = "开一个战锤40K底巢清剿团，我是极限战士喷火兵，队里还有一个技术军士。"

    question = build_campaign_preference_question(text, None)

    assert looks_like_campaign_generation_request(text) is True
    assert should_ask_campaign_preferences(text) is True
    assert "烈度" in question
    assert "LLM" in question
    assert "不自动套预设剧本" in question
    assert "哥特科幻底巢清剿" not in question


def test_seed_with_style_preferences_does_not_need_followup_question():
    text = "开一个硬核战锤40K底巢清剿团，战术清剿和恐怖调查均衡，别太多规则书细节。"

    assert campaign_preference_gaps(text) == []
    assert should_ask_campaign_preferences(text) is False


def test_preference_answer_is_accepted_without_being_new_campaign_seed():
    text = "硬核，但不要太血腥；战术和恐怖均衡，规则细节少一点。"

    assert looks_like_campaign_preference_answer(text) is True
    assert looks_like_campaign_generation_request(text) is False


def test_campaign_seed_patch_without_direct_preset_uses_llm_original_source():
    patch = build_campaign_seed_patch(
        "开一个战锤40K底巢清剿团，我是极限战士喷火兵。",
        preference_text="硬核，战术和恐怖均衡，别太多规则书细节。",
    )

    assert patch["genre"] == "LLM 原创跑团"
    assert patch["campaign_generation"]["source"] == "llm_generated_campaign"
    assert patch["campaign_contract"]["template_key"] == "llm_generated_campaign"
    assert "硬核" in patch["tone"]
    assert "玩家一句话种子" in patch["campaign_background"]
    assert "不要套用预设库" in patch["campaign_generation"]["opening_instruction"]
    assert "模板骨架" not in patch["campaign_background"]


def test_structured_custom_campaign_brief_uses_player_source_instead_of_low_magic_template():
    text = (
        "来一盘新游戏，剧情按照这个来搞:新剧本\n"
        "时代背景：明朝\n"
        "基本概括：老徐是锦衣卫百户，官方身份是三宝船队随员，真实任务是寻访建文余孽。"
        "舰队抵达伊朗沿岸后，当地长老提到十几年前有个自称史东的东方人路过，号称桃源公。\n"
        "玩家组成：明朝船队随员、西方背景雇佣兵、中东背景雇佣兵。\n"
        "友方NPC组成：锦衣卫百户老徐、本地部落猎手、通译、挑夫一队。\n"
        "敌对NPC组成：波斯山贼、桃源教普通信众、桃源教低级教徒、具备低魔超自然能力的高级祭司、史东。\n"
        "模组限定：武器严格遵守时代特征，没有通译时不同语言背景只能简单交流。"
    )

    question = build_campaign_preference_question(text, None)
    patch = build_campaign_seed_patch(text, preference_text="硬核一些吧")

    assert looks_like_custom_campaign_brief(text) is True
    assert should_ask_campaign_preferences(text) is True
    assert "自定义剧本" in question
    assert "低魔边境冒险" not in question
    assert patch["campaign_generation"]["source"] == "player_custom_brief"
    assert patch["campaign_contract"]["template_key"] == "custom_player_brief"
    assert "三宝船队" in patch["campaign_background"]
    assert "桃源教" in patch["campaign_background"]
    assert "默认边境港镇" in patch["location"]
    assert "一份异常委托把玩家带到边境地点" not in patch["campaign_background"]


def test_preset_list_is_player_visible_and_contains_multiple_styles():
    reply = format_campaign_preset_list()

    assert looks_like_campaign_preset_list_request("有什么预设剧本") is True
    assert looks_like_campaign_preset_list_request("推荐几个剧本") is True
    assert looks_like_campaign_preset_list_request("预设列表") is True
    assert "《底巢清剿：锈蚀圣堂》" in reply
    assert "《霓虹债务夜奔》" in reply
    assert "《仙门试炼山海变》" in reply
    assert "《暖炉酒馆小镇奇案》" in reply
    assert "回“跑 2 号”" in reply


def test_preset_keywords_in_opening_seed_do_not_trigger_preset_fast_path():
    text = "开局部分不要搞关键词触发预设，按我刚才给的新剧本原创开场。"

    assert looks_like_campaign_preset_list_request(text) is False
    assert select_campaign_preset(text) is None
    assert looks_like_campaign_generation_request(text) is True


def test_long_opening_seed_mentions_preset_attack_defense_without_list_request():
    text = (
        "来一盘新游戏，剧情按照这个来搞:新剧本\n"
        "时代背景：架空近未来城邦。\n"
        "基本概括：玩家要在开局部分处理预设攻防演练留下的误报，"
        "但这只是世界内事件，不要触发任何内置预设。\n"
        "玩家组成：本地调查员和技术员。\n"
        "友方NPC组成：档案管理员、巡逻队长。\n"
        "敌对NPC组成：走私商、伪装成安保的内鬼。\n"
        "模组限定：没有超自然能力，所有冲突按现代调查处理。"
    )

    assert looks_like_custom_campaign_brief(text) is True
    assert looks_like_campaign_preset_list_request(text) is False
    assert select_campaign_preset(text) is None
    patch = build_campaign_seed_patch(text, preference_text="克制调查")
    assert patch["campaign_generation"]["source"] == "player_custom_brief"
    assert "预设攻防演练" in patch["campaign_background"]


def test_preset_selection_by_number_and_title():
    by_number = select_campaign_preset("跑 3 号")
    by_title = select_campaign_preset("就跑暖炉酒馆小镇奇案")
    not_direct_selection = select_campaign_preset("开一个战锤40K底巢清剿团")

    assert by_number is not None
    assert by_number.title == "雾港悬疑调查"
    assert by_title is not None
    assert by_title.key == "cozy_tavern_mystery"
    assert not_direct_selection is None


def test_preset_patch_marks_quickstart_scaffold():
    template = select_campaign_preset("跑暖炉酒馆小镇奇案")
    assert template is not None

    patch = build_campaign_preset_patch(template, request_text="跑 8 号")

    assert patch["campaign_generation"]["source"] == "preset_library"
    assert patch["campaign_generation"]["quickstart"] is True
    assert patch["campaign_preset"]["key"] == "cozy_tavern_mystery"
    assert "预设剧本" in format_campaign_preset_loaded_reply(template)
    assert campaign_preset_start_requested("跑 8 号开始") is True


def test_rusted_chapel_preset_preserves_objective_and_pressure():
    template = select_campaign_preset("就跑锈蚀圣堂")
    assert template is not None

    patch = build_campaign_preset_patch(template, request_text="就跑锈蚀圣堂")

    assert template.key == "underhive_rusted_chapel"
    assert patch["campaign_preset"]["current_objective"] == "找到失联侦察队的记录核心。"
    assert patch["campaign_preset"]["current_pressure"] == "底巢通讯将在两小时后被轨道干扰彻底切断。"
    assert "扩音器正在反复播放帝国圣歌" in patch["campaign_generation"]["opening_scene"]
    assert "记录核心" in patch["campaign_background"]
    assert "两小时" in patch["campaign_background"]
