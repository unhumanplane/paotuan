from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CampaignTemplate:
    key: str
    title: str
    genre: str
    default_tone: str
    default_location: str
    default_factions: Tuple[str, ...]
    default_ruleset: str
    premise_frame: str
    focus_axes: Tuple[str, str]
    keywords: Tuple[str, ...]
    summary: str = ""
    recommended_players: str = "3-5 人"
    quickstart_preferences: str = ""
    list_tags: Tuple[str, ...] = ()
    opening_scene: str = ""
    current_objective: str = ""
    current_pressure: str = ""


TEMPLATES: Tuple[CampaignTemplate, ...] = (
    CampaignTemplate(
        key="underhive_rusted_chapel",
        title="底巢清剿：锈蚀圣堂",
        genre="grimdark_sci_fi",
        default_tone="哥特军事恐怖、硬核清剿、狭窄空间压迫、失联倒计时和异端污染",
        default_location="第七底巢、锈蚀圣堂、尸体悬挂的教堂穹顶、失真扩音器覆盖的废弃礼拜区",
        default_factions=("帝国清剿小队", "失联侦察队", "被剥去身份牌的尸体", "盘踞圣堂的异端污染源"),
        default_ruleset="homebrew_warhammer40k_adaptation；风险、命中、伤害、弹药、通讯窗口和精神压力用 d20/伤害骰裁定，避免堆砌规则书细节",
        premise_frame=(
            "小队奉命进入第七底巢调查侦察队失联。最后传回的画面里，教堂穹顶下挂满被剥去身份牌的尸体，"
            "本该死寂的扩音器正在反复播放帝国圣歌。"
        ),
        focus_axes=("硬核清剿", "哥特恐怖调查"),
        keywords=(
            "锈蚀圣堂",
            "第七底巢",
            "底巢清剿",
            "底巢",
            "清剿",
            "记录核心",
            "侦察队",
            "扩音器",
            "帝国圣歌",
            "身份牌",
            "轨道干扰",
            "圣堂",
        ),
        summary="进入第七底巢的废弃圣堂，寻找失联侦察队的记录核心；尸体、圣歌和通讯倒计时正在把清剿变成一场恐怖调查。",
        recommended_players="2-5 人",
        quickstart_preferences="硬核但克制血腥，战术清剿和哥特恐怖调查均衡，重点放在通讯倒计时、弹药/伤势代价和记录核心线索。",
        list_tags=("40K风", "底巢清剿", "倒计时"),
        opening_scene=(
            "你的小队奉命进入第七底巢，调查一支侦察队的失联。最后传回的画面里，教堂穹顶下挂满了被剥去身份牌的尸体，"
            "而本该死寂的扩音器正在反复播放帝国圣歌。"
        ),
        current_objective="找到失联侦察队的记录核心。",
        current_pressure="底巢通讯将在两小时后被轨道干扰彻底切断。",
    ),
    CampaignTemplate(
        key="grimdark_underhive_purge",
        title="哥特科幻底巢清剿",
        genre="grimdark_sci_fi",
        default_tone="哥特军事恐怖、克制高压、重视火力、资源消耗和任务代价",
        default_location="帝国巢都底巢、废弃维护层与污染排水区",
        default_factions=("帝国作战小队", "底巢幸存者", "基因窃取者教派或异端巢穴"),
        default_ruleset="homebrew_warhammer40k_adaptation；风险、命中、伤害和资源消耗用 d20/伤害骰裁定，避免堆砌规则书细节",
        premise_frame="一支小队奉命进入失联底巢区，清剿异端污染源，同时确认失踪信标、幸存者证词和异常生物活动。",
        focus_axes=("战术清剿", "恐怖调查"),
        keywords=("战锤", "40k", "warhammer", "底巢", "巢都", "下巢", "极限战士", "阿斯塔特", "星际战士", "基因窃取者", "清剿"),
    ),
    CampaignTemplate(
        key="fog_harbor_investigation",
        title="雾港悬疑调查",
        genre="modern_occult_mystery",
        default_tone="潮湿、克制、悬疑、逐步逼近真相",
        default_location="暴雨海港、废弃灯塔、雾中码头与旧航海档案室",
        default_factions=("港务署", "失踪船员家属", "隐瞒事故的旧利益团体"),
        default_ruleset="以 d20 检定为基础；调查、潜行、交涉和危险处置需要投骰，超自然只通过可见线索逐步揭示",
        premise_frame="玩家因调查、办案、委托或滞留来到海港，失踪船只空船返航，旧灯塔在暴雨夜重新亮起。",
        focus_axes=("线索调查", "海港惊悚"),
        keywords=("雾港", "海港", "港口", "灯塔", "渔船", "游艇", "大雾", "南极", "航海", "克苏鲁", "调查", "悬疑"),
    ),
    CampaignTemplate(
        key="urban_occult_delivery",
        title="都市怪谈委托",
        genre="urban_occult",
        default_tone="现代都市、怪谈边缘、轻规则、重现场选择",
        default_location="夜间城市、旧校舍、便利店、居民楼与无人电梯",
        default_factions=("普通市民", "民间怪谈社群", "试图掩盖异常的本地组织"),
        default_ruleset="以 d20 检定为基础；社交、调查、逃脱和仪式干预需要投骰，异常规则先从玩家可见现象中建立",
        premise_frame="一份夜间委托或失踪事件把玩家卷入都市异常，普通地点开始出现不该存在的门、声音或重复时间。",
        focus_axes=("都市调查", "怪谈惊悚"),
        keywords=("都市", "怪谈", "现代", "灵异", "夜班", "外卖", "快递", "学校", "校园", "教室", "电梯", "便利店"),
    ),
    CampaignTemplate(
        key="space_train_mystery",
        title="星际列车密室",
        genre="space_opera_mystery",
        default_tone="冷冽科幻、密室悬疑、心理压力、有限资源",
        default_location="跃迁列车、边境星域轨道、封闭车厢与不存在的终点站",
        default_factions=("列车安保", "乘客群像", "边境公司代表", "未知信号源"),
        default_ruleset="以 d20 检定为基础；工程、调查、社交、战斗和危机处置需要投骰，空间事实以工具状态为准",
        premise_frame="列车或飞船在跃迁后偏离航线，收到来自不存在终点的求救讯号，乘员记忆开始出现矛盾。",
        focus_axes=("密室调查", "太空危机"),
        keywords=("太空", "星际", "星环", "列车", "空间站", "跃迁", "飞船", "密室", "终点站", "边境星域"),
    ),
    CampaignTemplate(
        key="cyber_neon_debt_heist",
        title="霓虹债务夜奔",
        genre="cyberpunk_heist",
        default_tone="赛博朋克、快节奏、肮脏交易、霓虹雨夜和高压追逃",
        default_location="企业下城区、黑诊所、无人货运站、雨夜高架与地下数据市场",
        default_factions=("欠债的玩家小队", "企业追债部队", "黑市中间人", "被偷走身份的客户"),
        default_ruleset="以 d20 检定为基础；黑客、潜入、交涉、枪战、载具追逐和义体过载需要投骰，债务与热度作为压力资源",
        premise_frame="玩家欠下一笔不该存在的债，被迫在一夜内截获一份活体身份密钥；目标、雇主和追兵都在撒谎。",
        focus_axes=("潜入抢劫", "都市追逃"),
        keywords=("赛博", "霓虹", "黑客", "义体", "企业", "债务", "抢劫", "夜奔", "高架", "下城区"),
        recommended_players="2-5 人",
        quickstart_preferences="电影级高压，潜入抢劫和都市追逃均衡，规则轻量但保留热度与资源压力。",
    ),
    CampaignTemplate(
        key="xianxia_sect_trial",
        title="仙门试炼山海变",
        genre="xianxia_trial_mystery",
        default_tone="仙侠宗门、少年意气、试炼危机、山海异变与门规压力",
        default_location="云台宗外门、禁林试炼场、断碑山道与封印裂隙",
        default_factions=("外门弟子", "戒律堂", "山海异兽", "暗中篡改试炼的内门势力"),
        default_ruleset="以 d20 检定为基础；身法、术法、识阵、斗法、疗伤和灵力消耗需要投骰，修为优势必须可解释且有限",
        premise_frame="宗门试炼本应只是入门考核，却因封印裂隙提前失控；玩家既要活下来，也要弄清是谁改动了试炼阵。",
        focus_axes=("宗门试炼", "异变调查"),
        keywords=("修仙", "仙侠", "宗门", "试炼", "外门", "内门", "灵力", "山海", "异兽", "阵法"),
        recommended_players="3-6 人",
        quickstart_preferences="中高烈度，少年热血和异变调查均衡，术法表现可以潇洒但资源与风险要落盘。",
    ),
    CampaignTemplate(
        key="cozy_tavern_mystery",
        title="暖炉酒馆小镇奇案",
        genre="cozy_fantasy_mystery",
        default_tone="温暖奇幻、低烈度、轻松调查、小镇人情与一点点危险",
        default_location="雪夜边境小镇、暖炉酒馆、面包房、钟楼和结冰河岸",
        default_factions=("酒馆常客", "镇卫队", "旅行商队", "不愿说实话的邻里"),
        default_ruleset="以 d20 检定为基础；调查、手艺、交涉、追踪和小规模冲突需要投骰，伤亡尺度默认克制",
        premise_frame="雪夜里镇上的节庆奖杯失踪，钟楼却响起不该存在的第十三声；所有人都像隐瞒了一个善意或尴尬的秘密。",
        focus_axes=("轻松调查", "小镇人情"),
        keywords=("温馨", "轻松", "酒馆", "小镇", "奇案", "雪夜", "暖炉", "节庆", "日常", "治愈"),
        recommended_players="2-5 人",
        quickstart_preferences="低到中烈度，轻松调查和小镇人情为主，允许幽默但保留清晰线索与小代价。",
    ),
    CampaignTemplate(
        key="steam_court_clockwork",
        title="蒸汽宫廷钟表阴谋",
        genre="steampunk_court_intrigue",
        default_tone="蒸汽宫廷、礼仪刀锋、机械奇观、阴谋和倒计时",
        default_location="雾都王宫、钟表塔、地下锅炉厅、贵族舞会与飞艇停泊坪",
        default_factions=("王室侍从与贵族", "钟表师公会", "秘密警察", "飞艇舰队军官"),
        default_ruleset="以 d20 检定为基础；礼仪、调查、机械操作、潜入、决斗和追逐需要投骰，公开身份与丑闻是关键资源",
        premise_frame="王储加冕前夜，王宫主钟提前敲响了明天的钟声；玩家必须在舞会、锅炉和密信之间找出谁在偷走时间。",
        focus_axes=("宫廷社交", "机械阴谋"),
        keywords=("蒸汽", "宫廷", "贵族", "钟表", "阴谋", "舞会", "飞艇", "王宫", "礼仪", "机械"),
        recommended_players="3-5 人",
        quickstart_preferences="中烈度，宫廷社交和机械阴谋均衡，战斗少而关键，失败会带来名誉与时间压力。",
    ),
    CampaignTemplate(
        key="wasteland_supply_run",
        title="废土补给远行",
        genre="post_apocalyptic_survival",
        default_tone="干燥、紧绷、资源稀缺、选择有代价",
        default_location="废土公路、废弃补给站、辐射风暴区与临时聚落",
        default_factions=("幸存者车队", "补给站居民", "掠夺者", "旧世界设施残留"),
        default_ruleset="以 d20 检定为基础；补给、车辆、伤病、谈判和战斗都要记录有限资源与风险",
        premise_frame="玩家护送关键补给穿越危险公路，途中必须在时间、燃料、安全和陌生求助之间做选择。",
        focus_axes=("生存资源", "公路冲突"),
        keywords=("废土", "末世", "核战", "荒野", "补给", "车队", "公路", "求生", "辐射", "聚落", "掠夺者"),
    ),
    CampaignTemplate(
        key="island_ruins_expedition",
        title="孤岛遗迹探险",
        genre="expedition_mystery",
        default_tone="冒险、危险、古代遗迹、逐步揭开异常",
        default_location="未知小岛、沉没遗迹、热带密林、潮汐洞穴与临时营地",
        default_factions=("探险队", "当地向导", "秘密资助方", "守护遗迹的敌对势力"),
        default_ruleset="以 d20 检定为基础；探索、攀爬、潜水、破译、交涉和战斗需要投骰，遗迹真相分阶段揭示",
        premise_frame="一次海上或考古委托把玩家带到地图外的小岛，遗迹信号、陌生文字和敌对行动同时出现。",
        focus_axes=("遗迹探索", "邪教冲突"),
        keywords=("小岛", "孤岛", "遗迹", "探险", "考古", "海上", "潜水", "神秘语言", "邪教", "墓室", "导航仪"),
    ),
    CampaignTemplate(
        key="low_magic_border",
        title="低魔边境冒险",
        genre="low_magic_frontier",
        default_tone="克制、危险、重选择后果、方便快速开场",
        default_location="边境港镇、近海航道、旧堡与荒野路口",
        default_factions=("港务行会", "旧贵族私兵", "海盗残党", "沉默教团"),
        default_ruleset="以 d20 检定为基础；概率、风险和对抗行动必须投骰",
        premise_frame="一份异常委托把玩家带到边境地点，第一幕从失踪人员、封锁现场或异常货物开始。",
        focus_axes=("冒险探索", "势力冲突"),
        keywords=("低魔", "边境", "酒馆", "王国", "村庄", "冒险者", "委托", "地下城", "中世纪", "dnd"),
    ),
)

DEFAULT_TEMPLATE = TEMPLATES[-1]

SETUP_TERMS = (
    "来一个",
    "开一个",
    "开个",
    "开一场",
    "新团",
    "跑团",
    "团",
    "剧本",
    "副本",
    "故事",
    "开场",
    "开局",
    "开始游戏",
    "进入剧情",
    "带我们跑",
    "主持",
    "生成",
    "自动生成",
    "智能补完",
)
CHARACTER_ONLY_TERMS = ("角色卡", "人物卡", "建卡", "随机创建角色", "随机建卡")
DELEGATE_PREFERENCE_TERMS = (
    "不用多问",
    "不要多问",
    "直接开始",
    "开始吧",
    "你来定",
    "你定吧",
    "你决定",
    "随便定",
    "默认",
    "按你判断",
    "交给你",
)
INTENSITY_TERMS = (
    "硬核",
    "高烈度",
    "低烈度",
    "烈度",
    "电影级",
    "克制",
    "温和",
    "轻松",
    "残酷",
    "伤亡",
    "死亡",
    "血腥",
    "压抑",
    "黑暗",
    "pg",
    "r级",
)
PLAYSTYLE_TERMS = (
    "战术",
    "清剿",
    "调查",
    "恐怖",
    "惊悚",
    "社交",
    "政治",
    "阴谋",
    "解谜",
    "探索",
    "生存",
    "资源",
    "战斗",
    "群像",
    "沙盒",
    "轻规则",
    "规则书",
    "叙事",
    "剧情",
    "均衡",
    "平衡",
)
PREFERENCE_MODIFIER_TERMS = ("偏", "更想", "别太", "不要太", "少一点", "多一点", "都要", "均衡", "平衡")
READONLY_OR_CONTROL_TERMS = (
    "status",
    "状态",
    "token",
    "tokens",
    "备份",
    "地图",
    "重开",
    "重置",
    "暂停",
    "resume",
)
PRESET_LIST_TERMS = (
    "预设剧本",
    "预设团",
    "预设",
    "内置剧本",
    "内置团",
    "模板库",
    "剧本库",
    "有什么剧本",
    "有哪些剧本",
    "有什么团",
    "有哪些团",
    "可选剧本",
    "可选团本",
    "剧本列表",
    "团本列表",
    "推荐剧本",
    "推荐几个剧本",
    "开箱即玩",
)
PRESET_SELECTION_TERMS = (
    "预设",
    "内置",
    "选",
    "选择",
    "载入",
    "就跑",
)
PRESET_TEMPLATE_SELECTION_CONTEXT_TERMS = (
    "剧本模板",
    "预设模板",
    "模板库",
    "套模板",
    "用模板",
    "选模板",
    "选择模板",
    "载入模板",
)
EXPLICIT_PRESET_TERMS = PRESET_SELECTION_TERMS + (
    "跑这个",
    "跑那个",
    "用这个",
    "用那个",
    "跑暖炉",
    "跑雾港",
    "跑锈蚀",
    "跑底巢",
    "跑霓虹",
    "跑仙门",
    "跑蒸汽",
    "跑废土",
    "跑孤岛",
    "跑低魔",
    "跑 ",
    "跑1",
    "跑2",
    "跑3",
    "跑4",
    "跑5",
    "跑6",
    "跑7",
    "跑8",
    "跑9",
)
PRESET_START_TERMS = (
    "开始",
    "开始游戏",
    "开场",
    "开局",
    "进入剧情",
    "进入正片",
    "直接跑",
    "马上跑",
)
CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}
CUSTOM_CAMPAIGN_BRIEF_KEY = "custom_player_brief"
CUSTOM_CAMPAIGN_BRIEF_TITLE = "玩家自定义剧本"
LLM_CAMPAIGN_KEY = "llm_generated_campaign"
LLM_CAMPAIGN_TITLE = "LLM 原创剧本"
CUSTOM_CAMPAIGN_BRIEF_SECTION_TERMS = (
    "时代背景",
    "基本概括",
    "玩家组成",
    "玩家可选",
    "友方npc",
    "友方NPC",
    "敌对npc",
    "敌对NPC",
    "模组限定",
    "玩家优势",
    "剧情按照这个",
    "新剧本",
)


def looks_like_campaign_preset_list_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    return _contains_any(normalized, PRESET_LIST_TERMS)


def format_campaign_preset_list() -> str:
    lines = ["可以，下面这些是开箱即玩的预设剧本。回“跑 2 号”或直接说标题，我就载入对应开场脚手架："]
    for index, template in enumerate(TEMPLATES, start=1):
        tags = " / ".join(template.list_tags or template.focus_axes)
        lines.append(
            f"{index}. 《{template.title}》：{_template_summary(template)}"
            f"（{tags}，{template.recommended_players}）"
        )
    lines.append("也可以直接给一句新方向；我会让 LLM 原创补成可开场剧本，不自动套这些预设。")
    return "\n".join(lines)


def select_campaign_preset(text: str) -> Optional[CampaignTemplate]:
    normalized = _normalize(text)
    if not normalized or looks_like_campaign_preset_list_request(text):
        return None
    if looks_like_custom_campaign_brief(text):
        return None
    explicit_preset = _contains_any(normalized, EXPLICIT_PRESET_TERMS) or _contains_any(
        normalized,
        PRESET_TEMPLATE_SELECTION_CONTEXT_TERMS,
    )
    if not explicit_preset:
        return None
    selected_index = _selected_preset_number(normalized)
    if selected_index is not None and 1 <= selected_index <= len(TEMPLATES):
        return TEMPLATES[selected_index - 1]

    best_template: Optional[CampaignTemplate] = None
    best_score = 0
    for template in TEMPLATES:
        score = _preset_selection_score(normalized, template)
        if score > best_score:
            best_template = template
            best_score = score
    if not best_template:
        return None
    has_selection_context = _looks_like_preset_selection_context(normalized)
    concise_title_pick = _looks_like_direct_preset_title(normalized, best_template, best_score)
    if has_selection_context or concise_title_pick:
        return best_template
    return None


def campaign_preset_start_requested(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    return _contains_any(normalized, PRESET_START_TERMS)


def build_campaign_preset_patch(template: CampaignTemplate, request_text: str = "") -> Dict[str, Any]:
    summary = _template_summary(template)
    scene_text = template.opening_scene or template.premise_frame
    seed = f"选择预设剧本《{template.title}》：{summary}；默认开场：{scene_text}"
    if template.current_objective:
        seed = f"{seed}；当前目标：{template.current_objective}"
    if template.current_pressure:
        seed = f"{seed}；当前压力：{template.current_pressure}"
    if str(request_text or "").strip():
        seed = f"{seed}；玩家进入语：{_short(request_text, 600)}"
    patch = build_campaign_seed_patch(
        seed,
        preference_text=_template_quickstart_preferences(template),
        template=template,
    )
    generation = dict(patch.get("campaign_generation") or {})
    generation.update(
        {
            "source": "preset_library",
            "status": "preset_loaded",
            "quickstart": True,
            "template_key": template.key,
            "template_title": template.title,
            "recommended_players": template.recommended_players,
            "opening_scene": template.opening_scene,
            "current_objective": template.current_objective,
            "current_pressure": template.current_pressure,
        }
    )
    patch["campaign_generation"] = generation
    contract = dict(patch.get("campaign_contract") or {})
    contract.update(
        {
            "genre": template.genre,
            "premise": _short(seed, 800),
            "tone": _short(_template_quickstart_preferences(template), 800),
            "template_key": template.key,
            "template_title": template.title,
            "source": "preset_library",
            "reset_previous_contract": True,
        }
    )
    patch["campaign_contract"] = contract
    patch["campaign_preset"] = {
        "key": template.key,
        "title": template.title,
        "summary": summary,
        "recommended_players": template.recommended_players,
        "quickstart_preferences": _template_quickstart_preferences(template),
        "opening_scene": template.opening_scene,
        "current_objective": template.current_objective,
        "current_pressure": template.current_pressure,
    }
    return patch


def format_campaign_preset_loaded_reply(template: CampaignTemplate) -> str:
    first_focus, second_focus = template.focus_axes
    return (
        f"已载入预设剧本《{template.title}》。"
        f"默认基调：{_template_quickstart_preferences(template)}"
        f"\n直接回一句角色身份并说“开始游戏”就能开场，例如：`/dm 我是负责{first_focus}的新人角色，开始游戏`。"
        f"也可以先补一句偏好，比如“更偏{second_focus}，烈度克制一点”。"
    )


def looks_like_campaign_generation_request(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _contains_any(normalized, CHARACTER_ONLY_TERMS) and not _contains_any(normalized, ("剧本", "跑团", "开场", "开局", "故事")):
        return False
    if not _contains_any(normalized, SETUP_TERMS):
        return False
    template, score = match_campaign_template_with_score(text)
    if score > 0:
        return True
    if "跑团" in normalized or "剧本" in normalized or "开场" in normalized or "新团" in normalized:
        return True
    return len(normalized) >= 18 and template is not None


def looks_like_custom_campaign_brief(text: str) -> bool:
    raw = str(text or "").strip()
    normalized = raw.lower()
    if len(normalized) < 120:
        return False
    section_hits = sum(1 for term in CUSTOM_CAMPAIGN_BRIEF_SECTION_TERMS if term.lower() in normalized)
    non_empty_lines = sum(1 for line in raw.splitlines() if line.strip())
    if section_hits >= 3:
        return True
    if section_hits >= 2 and non_empty_lines >= 3:
        return True
    if section_hits >= 1 and "玩家可选" in normalized and "npc" in normalized:
        return True
    return False


def match_campaign_template(text: str) -> CampaignTemplate:
    template, _score = match_campaign_template_with_score(text)
    return template or DEFAULT_TEMPLATE


def match_campaign_template_with_score(text: str) -> Tuple[Optional[CampaignTemplate], int]:
    normalized = _normalize(text)
    if not normalized:
        return DEFAULT_TEMPLATE, 0
    best = DEFAULT_TEMPLATE
    best_score = 0
    for template in TEMPLATES:
        score = sum(1 for keyword in template.keywords if keyword and keyword.lower() in normalized)
        if score > best_score:
            best = template
            best_score = score
    return best, best_score


def template_by_key(key: str) -> Optional[CampaignTemplate]:
    normalized = str(key or "").strip()
    for template in TEMPLATES:
        if template.key == normalized:
            return template
    return None


def campaign_preference_gaps(text: str, template: Optional[CampaignTemplate] = None) -> List[str]:
    normalized = _normalize(text)
    if not normalized or _contains_any(normalized, DELEGATE_PREFERENCE_TERMS):
        return []
    gaps: List[str] = []
    if not _contains_any(normalized, INTENSITY_TERMS):
        gaps.append("intensity")
    if not _contains_any(normalized, PLAYSTYLE_TERMS):
        gaps.append("playstyle")
    return gaps


def should_ask_campaign_preferences(text: str, template: Optional[CampaignTemplate] = None) -> bool:
    if not looks_like_campaign_generation_request(text):
        return False
    return bool(campaign_preference_gaps(text, template))


def looks_like_campaign_preference_answer(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _contains_any(normalized, READONLY_OR_CONTROL_TERMS):
        return False
    if looks_like_campaign_generation_request(text):
        return False
    if _contains_any(normalized, DELEGATE_PREFERENCE_TERMS):
        return True
    if _contains_any(normalized, INTENSITY_TERMS):
        return True
    if _contains_any(normalized, PLAYSTYLE_TERMS):
        return True
    return _contains_any(normalized, PREFERENCE_MODIFIER_TERMS)


def build_campaign_preference_question(text: str, template: Optional[CampaignTemplate] = None) -> str:
    if looks_like_custom_campaign_brief(text):
        return (
            "可以。你这段自定义剧本我会按原文当主设定，不套用预设或默认边境模板。"
            "开场前确认一下取向：烈度偏电影级冒险、硬核伤亡，还是克制一点？"
            "玩法更想偏战术推进、调查/社交，还是两者均衡？一句话回我就行。"
        )
    if template is None:
        return (
            "可以。这个新团我会让 LLM 按你的种子原创编写，不自动套预设剧本或默认低魔模板。"
            "开场前确认一下取向：烈度偏电影级冒险、硬核伤亡，还是克制一点？"
            "玩法更想偏战术推进、调查/社交、探索生存，还是几者均衡？一句话回我就行。"
        )
    chosen = template
    first_focus, second_focus = chosen.focus_axes
    return (
        f"可以。这个{chosen.title}我先按“{chosen.default_tone}”搭骨架。"
        f"开场前确认一下取向：烈度偏电影级冒险、硬核伤亡，还是克制一点？"
        f"玩法更想偏{first_focus}、{second_focus}，还是两者均衡？一句话回我就行。"
    )


def build_campaign_seed_patch(
    seed_text: str,
    preference_text: str = "",
    template: Optional[CampaignTemplate] = None,
) -> Dict[str, Any]:
    if looks_like_custom_campaign_brief(seed_text):
        return _build_custom_campaign_seed_patch(seed_text, preference_text=preference_text)
    if template is None:
        return _build_llm_campaign_seed_patch(seed_text, preference_text=preference_text)
    return _build_template_campaign_seed_patch(seed_text, preference_text=preference_text, template=template)


def _build_template_campaign_seed_patch(
    seed_text: str,
    preference_text: str = "",
    template: Optional[CampaignTemplate] = None,
) -> Dict[str, Any]:
    chosen = template or DEFAULT_TEMPLATE
    seed = _short(seed_text, 12000)
    preferences = _short(preference_text, 1200)
    tone_parts = [chosen.default_tone]
    if preferences:
        tone_parts.append(f"玩家取向：{preferences}")
    premise_parts = [f"玩家一句话种子：{seed}", f"模板骨架：{chosen.premise_frame}"]
    if preferences:
        premise_parts.insert(1, f"玩家风格取向：{preferences}")
    campaign_background = "；".join(part for part in premise_parts if part)
    return {
        "genre": chosen.genre,
        "tone": "；".join(tone_parts),
        "location": chosen.default_location,
        "factions": list(chosen.default_factions),
        "ruleset": chosen.default_ruleset,
        "starting_premise": _short(campaign_background, 6000),
        "campaign_background": campaign_background,
        "campaign_contract": {
            "genre": chosen.genre,
            "premise": _short(seed, 800),
            "tone": _short("；".join(tone_parts), 800),
            "template_key": chosen.key,
            "template_title": chosen.title,
        },
        "campaign_generation": {
            "source": "template_library",
            "status": "ready_for_opening",
            "template_key": chosen.key,
            "template_title": chosen.title,
            "seed": _short(seed, 800),
            "preferences": _short(preferences, 500),
            "opening_instruction": (
                "不要要求玩家上传或填写 Markdown；把预设模板当作内部脚手架，"
                "补齐开场介绍、initial_hook、玩家行动引导、三段式以上剧情骨架和公开 scene_patch，然后调用 start_game。"
            ),
        },
    }


def _build_llm_campaign_seed_patch(seed_text: str, preference_text: str = "") -> Dict[str, Any]:
    seed = _short(seed_text, 12000)
    preferences = _short(preference_text, 1200)
    tone_parts = ["LLM 原创生成；不套用预设模板；按玩家种子和取向补齐可开场、可裁定、可推进的剧本"]
    if preferences:
        tone_parts.append(f"玩家取向：{preferences}")
    campaign_background_parts = [f"玩家一句话种子：{seed}" if seed else "玩家授权 DM 原创生成一个新团"]
    if preferences:
        campaign_background_parts.insert(0, f"玩家风格取向：{preferences}")
    campaign_background = "；".join(campaign_background_parts)
    return {
        "genre": "LLM 原创跑团",
        "tone": "；".join(tone_parts),
        "location": "由 LLM 根据玩家种子原创生成；不得替换为默认边境港镇或未被明确选择的预设地点",
        "factions": ["由 LLM 根据玩家种子和风格取向原创整理玩家阵营、NPC、敌对势力和初始冲突"],
        "ruleset": "以 d20 检定为基础；概率、风险和对抗行动必须投骰，规则细节服务于当前剧本而不是预设模板。",
        "starting_premise": _short(campaign_background, 6000),
        "campaign_background": campaign_background,
        "campaign_contract": {
            "genre": "LLM 原创跑团",
            "premise": _short(seed, 800),
            "tone": _short("；".join(tone_parts), 800),
            "template_key": LLM_CAMPAIGN_KEY,
            "template_title": LLM_CAMPAIGN_TITLE,
            "source": "llm_generated_campaign",
        },
        "campaign_generation": {
            "source": "llm_generated_campaign",
            "status": "ready_for_opening",
            "template_key": LLM_CAMPAIGN_KEY,
            "template_title": LLM_CAMPAIGN_TITLE,
            "seed": _short(seed, 1200),
            "preferences": _short(preferences, 500),
            "preserve_player_seed": True,
            "opening_instruction": (
                "不要套用预设库、默认低魔边境、灰港镇或其他未被玩家明确选择的模板。"
                "把 seed 和 preferences 当作创作约束，由 LLM 原创补齐世界背景、开场介绍、initial_hook、"
                "玩家行动引导、三段式以上剧情骨架和公开 scene_patch，然后优先调用 start_game。"
            ),
        },
    }


def _build_custom_campaign_seed_patch(seed_text: str, preference_text: str = "") -> Dict[str, Any]:
    seed = _short(seed_text, 12000)
    preferences = _short(preference_text, 1200)
    tone_parts = ["以玩家自定义剧本原文为准；不套用预设模板；由 DM 按玩家取向补齐镜头、节奏和可裁定细节"]
    if preferences:
        tone_parts.append(f"玩家取向：{preferences}")
    campaign_background_parts = [f"玩家自定义剧本原文：{seed}"]
    if preferences:
        campaign_background_parts.insert(0, f"玩家风格取向：{preferences}")
    campaign_background = "；".join(campaign_background_parts)
    return {
        "genre": "player_custom_campaign",
        "tone": "；".join(tone_parts),
        "location": "按玩家自定义剧本原文指定；不得替换为默认边境港镇或其他预设地点",
        "factions": ["按玩家自定义剧本原文中的玩家阵营、友方 NPC、敌对 NPC 和势力关系整理"],
        "ruleset": "以 d20 检定为基础；严格遵守玩家模组限定、时代特征、语言限制、阵营锁定和超自然门槛。",
        "starting_premise": _short(campaign_background, 6000),
        "campaign_background": campaign_background,
        "campaign_contract": {
            "genre": "player_custom_campaign",
            "premise": _short(seed, 800),
            "tone": _short("；".join(tone_parts), 800),
            "template_key": CUSTOM_CAMPAIGN_BRIEF_KEY,
            "template_title": CUSTOM_CAMPAIGN_BRIEF_TITLE,
            "source": "player_custom_brief",
        },
        "campaign_generation": {
            "source": "player_custom_brief",
            "status": "ready_for_opening",
            "template_key": CUSTOM_CAMPAIGN_BRIEF_KEY,
            "template_title": CUSTOM_CAMPAIGN_BRIEF_TITLE,
            "seed": _short(seed, 1200),
            "preferences": _short(preferences, 500),
            "preserve_player_brief": True,
            "opening_instruction": (
                "以 seed 中的时代背景、基本概括、玩家组成、友方 NPC、敌对 NPC、模组限定和玩家优势为权威；"
                "不得替换成预设库、默认低魔边境、灰港镇或其他未由玩家指定的模板。"
                "把玩家原文整理成可开场背景，补齐开场介绍、initial_hook、玩家行动引导、"
                "三段式以上剧情骨架和公开 scene_patch，然后优先调用 start_game。"
            ),
        },
    }


def _template_summary(template: CampaignTemplate) -> str:
    return _short(template.summary or template.premise_frame, 96)


def _template_quickstart_preferences(template: CampaignTemplate) -> str:
    if template.quickstart_preferences:
        return template.quickstart_preferences
    first_focus, second_focus = template.focus_axes
    return f"中等烈度，{first_focus}和{second_focus}均衡，规则细节适中。"


def _selected_preset_number(text: str) -> Optional[int]:
    stripped = str(text or "").strip()
    pure_match = re.fullmatch(r"(?:第)?\s*(\d{1,2})\s*(?:号|个|本|号本)?", stripped)
    if pure_match:
        return int(pure_match.group(1))
    for word, number in sorted(CHINESE_NUMBERS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.fullmatch(rf"(?:第)?\s*{re.escape(word)}\s*(?:号|个|本|号本)?", stripped):
            return number
    if not _contains_any(stripped, ("第", "号", "选", "选择", "预设", "模板", "就跑", "跑")):
        return None
    match = re.search(r"(?:第|选|选择|预设|模板|跑|就跑)?\s*(\d{1,2})\s*(?:号|个|本|号本)?", stripped)
    if match:
        return int(match.group(1))
    for word, number in sorted(CHINESE_NUMBERS.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?:第|选|选择|预设|模板|跑|就跑)?\s*{re.escape(word)}\s*(?:号|个|本|号本)", stripped):
            return number
    return None


def _looks_like_preset_selection_context(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    if _contains_any(normalized, PRESET_SELECTION_TERMS):
        return True
    if _contains_any(normalized, PRESET_TEMPLATE_SELECTION_CONTEXT_TERMS):
        return True
    if normalized.startswith("跑") and not normalized.startswith("跑团"):
        return True
    if ("用" in normalized or "按" in normalized) and _contains_any(normalized, ("预设", "内置")):
        return True
    if _contains_any(normalized, ("就这个", "就那个", "用这个", "用那个")) and _contains_any(
        normalized,
        ("预设", "剧本", "团本"),
    ):
        return True
    return False


def _looks_like_direct_preset_title(text: str, template: CampaignTemplate, score: int) -> bool:
    normalized = _normalize(text)
    if not normalized or len(normalized) > 24 or score < 8:
        return False
    if _contains_any(normalized, ("开一个", "来一个", "来一盘", "新团", "新游戏", "跑团", "帮我", "给我", "剧情按照")):
        return False
    compact_text = re.sub(r"[\s《》:：，,。.!！?？、\-—_]+", "", normalized)
    compact_title = re.sub(r"[\s《》:：，,。.!！?？、\-—_]+", "", template.title.lower())
    if compact_text == compact_title:
        return True
    return len(compact_text) >= 4 and compact_text in compact_title


def _preset_selection_score(text: str, template: CampaignTemplate) -> int:
    score = 0
    title = template.title.lower()
    if title and title in text:
        score += 8
    for token in title.split():
        if token and token in text:
            score += 2
    for keyword in template.keywords:
        if keyword and keyword.lower() in text:
            score += 2
    compact_title = title.replace(" ", "")
    for size in (2, 3, 4):
        for start in range(0, max(0, len(compact_title) - size + 1)):
            piece = compact_title[start : start + size]
            if piece and piece in text:
                score += 1
    return score


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


def _contains_any(text: str, terms: Tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms if term)


def _short(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"
