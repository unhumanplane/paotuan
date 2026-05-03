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


def test_cleanup_is_idempotent():
    text = "门后有脚步声停住了。是继续偷听？敲门试探？还是转身离开？"

    first = cleanup_menu_like_guidance(text, "我调查门缝")
    second = cleanup_menu_like_guidance(first.text, "我调查门缝")

    assert first.changed is True
    assert second.text == first.text
