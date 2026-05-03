from astrbot_plugin_auto_trpg_dm.core.outbound_cleanup import (
    apply_semantic_menu_judgment,
    cleanup_menu_like_guidance,
)


def test_removes_tail_numbered_action_menu_for_normal_action():
    text = """门缝里透出冷蓝色的光，里面有人压低声音提到“巡逻换岗”。

你可以选择：
1. 继续偷听
2. 敲门试探
3. 直接离开"""

    result = cleanup_menu_like_guidance(text, "我调查门缝")

    assert result.changed is True
    assert "冷蓝色的光" in result.text
    assert "你可以选择" not in result.text
    assert "继续偷听" not in result.text


def test_replaces_abrupt_menu_only_reply_with_paraphrased_open_world_reminder():
    text = """你可以选择：
1. 调查门缝
2. 敲门
3. 离开"""

    result = cleanup_menu_like_guidance(text, "我停在门前")

    assert result.changed is True
    assert result.replacement_used is True
    assert "你可以直接描述想做的事" not in result.text
    assert "开放世界中没有选项束缚你" not in result.text
    assert "想尝试" in result.text or "角色想" in result.text
    assert "后果" in result.text


def test_preserves_numbered_turn_order():
    text = """第 2 轮，阶段：character_turn。
建议行动/超时锚点：斥候（pc_owner），持有人：Alice。
1. 斥候（当前）：未行动
2. 法师：已行动"""

    result = cleanup_menu_like_guidance(text, "战斗顺序")

    assert result.changed is False
    assert result.text == text


def test_preserves_player_roster():
    text = """玩家登记：
1. Alice（u-1） -> 斥候 [pc_owner]
2. Bob（u-2） -> 未绑定角色"""

    result = cleanup_menu_like_guidance(text, "玩家列表")

    assert result.changed is False
    assert result.text == text


def test_preserves_dice_check_output():
    text = """骰子检定：撬开门锁
规则：skill_check v1
掷骰：1d20=[14]+3 => 17
结果：total=17；success=True；DC 15"""

    result = cleanup_menu_like_guidance(text, "我撬锁")

    assert result.changed is False
    assert result.text == text


def test_preserves_rule_or_diagnostic_output():
    text = """Token 粗算：快照 1200 字，约 600 token。
prompt=820；tool schema=300；audit 最近 3 条。"""

    result = cleanup_menu_like_guidance(text, "debug token 详细")

    assert result.changed is False
    assert result.text == text


def test_explicit_help_request_softens_numbered_menu():
    text = """你可以选择：
1. 观察门缝里的光
2. 回忆巡逻换岗规律
3. 找同伴分头行动"""

    result = cleanup_menu_like_guidance(text, "我卡住了，给点建议")

    assert result.changed is True
    assert "1." not in result.text
    assert "你可以选择" not in result.text
    assert "线索" in result.text or "目标" in result.text


def test_explicit_help_request_softens_hidden_question_menu():
    text = "门后有脚步声停住了。是继续偷听？敲门试探？还是转身离开？"

    result = cleanup_menu_like_guidance(text, "我卡住了，给点建议")

    assert result.changed is True
    assert "还是" not in result.text
    assert "线索" in result.text or "目标" in result.text


def test_removes_hidden_question_chain_action_menu():
    text = "门后有脚步声停住了。是继续偷听？敲门试探？还是转身离开？"

    result = cleanup_menu_like_guidance(text, "我调查门缝")

    assert result.changed is True
    assert result.text == "门后有脚步声停住了。"


def test_removes_hidden_next_step_sentence_menu():
    text = "门后的火光忽明忽暗。下一步？调查门缝。敲门试探。绕开这扇门。"

    result = cleanup_menu_like_guidance(text, "我停在门前")

    assert result.changed is True
    assert result.text == "门后的火光忽明忽暗。"


def test_removes_hidden_multiline_next_step_menu():
    text = """门后的火光忽明忽暗。
下一步？
调查门缝。
敲门试探。
绕开这扇门。"""

    result = cleanup_menu_like_guidance(text, "我停在门前")

    assert result.changed is True
    assert result.text == "门后的火光忽明忽暗。"


def test_removes_hidden_framed_how_to_handle_menu():
    text = "守卫的脚步已经逼近门口。你打算怎么处理这件事？交涉，绕路，还是强闯？"

    result = cleanup_menu_like_guidance(text, "我躲在门边")

    assert result.changed is True
    assert result.text == "守卫的脚步已经逼近门口。"


def test_preserves_scene_question_chain_that_is_not_player_action_menu():
    text = "门后的声音越来越近。是守卫靠近？门自己打开？还是你的错觉？"

    result = cleanup_menu_like_guidance(text, "我调查门缝")

    assert result.changed is False
    assert result.text == text


def test_replaces_menu_only_target_clarification_options():
    text = "你是要攻击左边的守卫？攻击右边的守卫？还是攻击门上的锁？"

    result = cleanup_menu_like_guidance(text, "我攻击他")

    assert result.changed is True
    assert "左边的守卫" not in result.text
    assert "具体目标" in result.text
    assert "当前局势" not in result.text


def test_help_request_preserves_visible_target_list():
    text = """你可以选择目标：
1. 左边的守卫
2. 右边的弩手
3. 门上的锁"""

    result = cleanup_menu_like_guidance(text, "我能攻击谁")

    assert result.changed is False
    assert result.text == text


def test_help_request_preserves_visible_target_question_list():
    text = "你是要攻击左边的守卫？攻击右边的弩手？还是门上的锁？"

    result = cleanup_menu_like_guidance(text, "我能攻击谁")

    assert result.changed is False
    assert result.text == text


def test_removes_target_clarification_options_after_context():
    text = "雾里有两道身影，门上也挂着一把锁。你是要攻击左边的守卫？攻击右边的守卫？还是攻击门上的锁？"

    result = cleanup_menu_like_guidance(text, "我攻击他")

    assert result.changed is True
    assert result.text == "雾里有两道身影，门上也挂着一把锁。"
    assert "攻击左边" not in result.text


def test_returns_semantic_candidate_for_ambiguous_tail_choice_prompt():
    text = "门后的锁孔里透出蓝光。\n\n你是指：研究机关？还是询问守卫？或者同时？"

    result = cleanup_menu_like_guidance(text, "我看看门")

    assert result.changed is False
    assert result.semantic_candidate is not None
    assert result.semantic_candidate.text.startswith("你是指")
    assert "或者" in result.semantic_candidate.signals


def test_semantic_candidate_uses_tail_fragment_when_final_paragraph_is_long():
    prefix = "铁链轻轻晃动，锁孔里的蓝光沿着门缝铺开。" * 12
    text = f"{prefix}你是指：研究机关？还是询问守卫？或者同时？"

    result = cleanup_menu_like_guidance(text, "我看看门")

    assert result.changed is False
    assert result.semantic_candidate is not None
    assert result.semantic_candidate.text == "你是指：研究机关？还是询问守卫？或者同时？"


def test_semantic_candidate_is_limited_to_tail_or_latter_half():
    text = """你是指：研究机关？还是询问守卫？或者同时？

门后的锁孔里透出蓝光。

铁链在里面轻轻晃了一下。"""

    result = cleanup_menu_like_guidance(text, "我看看门")

    assert result.changed is False
    assert result.semantic_candidate is None


def test_semantic_candidate_does_not_use_entire_completion():
    text = "你是指：研究机关？还是询问守卫？或者同时？"

    result = cleanup_menu_like_guidance(text, "我看看门")

    assert result.changed is False
    assert result.semantic_candidate is None


def test_applies_semantic_delete_without_rewriting_body():
    text = "门后的锁孔里透出蓝光。\n\n你是指：研究机关？还是询问守卫？或者同时？"
    result = cleanup_menu_like_guidance(text, "我看看门")

    cleaned = apply_semantic_menu_judgment(text, result.semantic_candidate, "delete_candidate")

    assert cleaned.changed is True
    assert cleaned.text == "门后的锁孔里透出蓝光。"
    assert "研究机关" not in cleaned.text


def test_unclear_numbered_block_is_preserved():
    text = """你记下三条客观线索：
1. 门缝有蓝光
2. 地面有血迹
3. 屋内有人低语"""

    result = cleanup_menu_like_guidance(text, "我观察现场")

    assert result.changed is False
    assert result.text == text


def test_replaces_choose_focus_factual_clue_list():
    text = """你可以选择关注：
1. 门缝里透出冷蓝色光
2. 地面有新鲜血迹
3. 门内有人低语"""

    result = cleanup_menu_like_guidance(text, "我观察现场")

    assert result.changed is True
    assert "门缝里透出冷蓝色光" not in result.text
    assert "不寻常" in result.text
    assert "直接描述" in result.text


def test_preserves_open_question_without_options():
    text = "你好像注意到了不寻常的事情。接下来你要做什么？"

    result = cleanup_menu_like_guidance(text, "我观察现场")

    assert result.changed is False
    assert result.text == text


def test_review_feedback_preserves_allowed_preparation_and_clarification_examples():
    samples = [
        (
            "你想开明日方舟世界观的新团？好思路。这里有几个方向供你参考：\n"
            "1、感染者地下网络\n"
            "2、移动城邦危机\n"
            "3、罗德岛委托",
            "我想开明日方舟世界观的新团",
        ),
        (
            "你可以选择：\n"
            "1. 感染者地下网络\n"
            "2. 移动城邦危机\n"
            "3. 罗德岛委托",
            "我想开明日方舟世界观的新团",
        ),
        (
            "你发来的 \"GreedyStr\" 不太像角色行动意图。你想做什么呢？\n"
            "- 开一个新团？\n"
            "- 创建一个叫 GreedyStr 的角色加入后续探索？\n"
            "- 还是想查询当前存档状态？",
            "GreedyStr",
        ),
        (
            "**当前状态：战斗章节已完成。**\n\n"
            "你是想：\n"
            "1. **正式结束本次跑团**（存档保留，作为完结记录）——需要你二次确认\n"
            "2. **继续探索**巢室残骸——族长尸体可能有情报、储藏室可能有补给或线索\n"
            "3. **撤退返回地表**，向极限战士指挥部报告任务完成\n\n"
            "你的选择？",
            "战斗完成了吗",
        ),
        (
            "请选择：\n"
            "1. 正式结束本次跑团\n"
            "2. 继续探索巢室残骸\n"
            "3. 撤退返回地表",
            "战斗完成了吗",
        ),
        (
            '要我按这个方向给你建卡绑定吗？想把你的"机械贤者"设定成偏近战'
            "（动力武器+机仆突击）、偏远程（重型等离子/热熔+机仆火力掩护）"
            "还是偏技术（干扰战场、破解设施、操控虫巢机械）？",
            "我想建机械贤者",
        ),
        (
            "简单说：**先告诉我你想从哪条路绕进去，然后我们可以一拍拍来打这场突袭。** "
            "你现在手持两枚爆闪手雷，腰间喷火器和爆弹手枪都已就绪。选个方向。",
            "我想绕进去",
        ),
    ]

    for text, player_message in samples:
        result = cleanup_menu_like_guidance(text, player_message)

        assert result.changed is False
        assert result.text == text


def test_review_feedback_preserves_neutral_short_binary_prompt():
    text = "要走进去，还是先做别的准备？"

    result = cleanup_menu_like_guidance(text, "我看向通道")

    assert result.changed is False
    assert result.text == text


def test_preserves_normal_narrative_with_choose_wording():
    samples = [
        "你选择继续贴着墙推进，脚下的碎骨发出细小响声。",
        "如果你选择继续探索，巢室深处的动静会越来越清晰。",
    ]

    for text in samples:
        result = cleanup_menu_like_guidance(text, "我继续探索")

        assert result.changed is False
        assert result.text == text


def test_review_feedback_removes_in_game_closed_action_menus():
    samples = [
        (
            "下一步你想做什么？\n"
            "- **检查族长尸体**——泰伦生物的残骸可能残留有价值的情报或基因样本\n"
            "- **搜查巢室角落**——寻找储藏室、圣祠或通往地面的密道",
            "我站在巢室里",
            ["检查族长尸体", "搜查巢室角落"],
        ),
        (
            "**你想怎么做？** 沿着通道直接推进，还是先做其他准备？",
            "我看向通道",
            ["沿着通道直接推进", "先做其他准备"],
        ),
        (
            "**下一步，你怎么打算？**\n"
            "- 短暂修整，给爆弹手枪换弹，检查装备再推进\n"
            "- 直接踏入通道，保持警戒深入\n"
            "- 先侦查通道内部情况再做决定",
            "我看向通道",
            ["短暂修整", "直接踏入通道", "先侦查通道"],
        ),
        (
            "**你面前的选择：**\n"
            "- 🔥 趁基因窃取者还未完全恢复，逐个清理车间残余\n"
            "- 🔍 搜索暴君守卫身后的阴影区域——它守护着什么？\n"
            "- 🚪 快速搜索战利品后撤离，底巢深处的骚动意味着更强的敌人正在赶来",
            "我击退暴君守卫",
            ["逐个清理车间残余", "搜索暴君守卫", "快速搜索战利品"],
        ),
        (
            "**你需要决定下一步：**\n"
            "- 给爆弹手枪换弹，检查装备，然后深入阴影通道\n"
            "- 先侦察通道内部再推进\n"
            "- 或者用鸟卜仪扫描车间深处的热信号",
            "我站在车间里",
            ["给爆弹手枪换弹", "先侦察通道", "鸟卜仪扫描"],
        ),
        (
            "**这头野兽还没死。你选择：**\n"
            "- 切换动力拳套，趁它垂死给它最后一击\n"
            "- 退后换弹，拉开距离重新评估\n"
            "- 用喷火器再补一发，连它带那些恢复视觉的窃取者一起烧\n"
            "裁定补充：这步涉及风险、对抗或客观状态变化，但本轮还没有足够的骰子/战棋/状态工具结果支撑成功。",
            "我冲向野兽",
            ["切换动力拳套", "退后换弹", "用喷火器再补一发"],
        ),
        (
            "你现在直面暴君守卫，距离只有两步。喷火器刚完成喷射，你手里还握着它。下一步：\n\n"
            "**① 切换动力拳套充能**——扔掉喷火器，取下腿侧的动力拳套。\n"
            "**② 拔爆弹手枪近距处决**——松开喷火器，拔腿侧爆弹手枪。\n"
            "**③ 继续喷火压制**——扳机再扣，烈焰继续覆盖它。\n"
            "裁定补充：这步涉及风险、对抗或客观状态变化，但本轮还没有足够的骰子/战棋/状态工具结果支撑成功。",
            "我直面暴君守卫",
            ["切换动力拳套", "拔爆弹手枪", "继续喷火压制"],
        ),
        (
            "你旋身冲过门槛，动力甲伺服单元轰鸣，钷素喷火器的点火器咔嚓点燃——但这一轮的时间，只够你做**一件事**。"
            "你冲到暴君守卫身前大约五步时，需要选择：\n\n"
            "**① 钷素喷吐**——扳机扣下，烈焰洪流席卷失能的暴君守卫全身。\n\n"
            "**② 爆弹速射**——用火控急射十发爆弹打向胸颈。\n\n"
            "**③ 动力铁拳**——冲到近身，拳套充能。\n\n"
            "你要用哪个？",
            "我冲过门槛",
            ["钷素喷吐", "爆弹速射", "动力铁拳", "你要用哪个"],
        ),
        (
            "你的第二枚手雷还在右手，喷火器挂在腰侧。计划的第一步已经偏离了轨道——你现在要怎么办？\n"
            "- 趁闪光余波未散，直接强攻突入？\n"
            "- 或用第二枚手雷调整落点再投一次，争取覆盖主区域？\n"
            "- 或者换条思路，先找掩体重新评估？",
            "我丢手雷",
            ["直接强攻突入", "第二枚手雷调整落点", "先找掩体"],
        ),
        (
            "裁定补充：这步涉及风险、对抗或客观状态变化。\n"
            "① 切换动力拳套\n"
            "② 拔爆弹手枪\n"
            "③ 继续喷火压制\n"
            "你要用哪个？",
            "我直面暴君守卫",
            ["切换动力拳套", "拔爆弹手枪", "继续喷火压制", "你要用哪个"],
        ),
    ]

    for text, player_message, forbidden_terms in samples:
        result = cleanup_menu_like_guidance(text, player_message)

        assert result.changed is True
        if "裁定补充" in text:
            assert "裁定补充" in result.text
        assert result.text.strip() != "**"
        for term in forbidden_terms:
            assert term not in result.text


def test_cleanup_is_idempotent():
    text = "门后有脚步声停住了。是继续偷听？敲门试探？还是转身离开？"

    first = cleanup_menu_like_guidance(text, "我调查门缝")
    second = cleanup_menu_like_guidance(first.text, "我调查门缝")

    assert first.changed is True
    assert second.text == first.text
