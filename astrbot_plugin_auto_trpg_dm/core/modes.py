from __future__ import annotations

from .combat_lifecycle import combat_lifecycle_active
from .models import GameMode, GameSession


class GameModeStateMachine:
    """Small deterministic guardrail before the LLM sees tools."""

    CHARACTER_HINTS = ("角色", "车卡", "人物卡", "创建人物", "属性", "背景")
    RULE_HINTS = ("规则", "机制", "检定", "伤害公式", "判定方式", "怎么骰")
    BATTLE_HINTS = ("战棋", "地图", "坐标", "移动", "格", "攻击距离", "视线", "回合", "轮动", "行动顺序", "下一位")
    RESOLUTION_HINTS = ("结算", "场面结算", "结果", "命中", "伤害", "消耗", "豁免", "继续", "跳过", "无人响应")
    LIVE_ACTION_HINTS = (
        "攻击",
        "施法",
        "治疗",
        "移动",
        "侦察",
        "侦查",
        "搜索",
        "调查",
        "问",
        "询问",
        "打听",
        "安抚",
        "索取",
        "取走",
        "浇",
        "潜行",
        "防御",
        "闪避",
        "掩护",
        "发动",
        "触发",
        "命中",
        "伤害",
        "检定",
        "判定",
        "过检定",
        "骰",
        "尝试",
        "使用",
        "点燃",
        "敌人",
        "怪物",
        "动作如潮",
        "顺劈斩",
    )

    def detect(self, session: GameSession, message: str) -> GameMode:
        text = message.strip().lower()
        battle = session.battle or {}
        turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
        if combat_lifecycle_active(session):
            return GameMode.TACTICAL
        if any(hint in text for hint in self.BATTLE_HINTS):
            return GameMode.TACTICAL
        if self._campaign_started(session) and self._looks_like_terminal_exit_statement(text):
            return GameMode.NARRATIVE
        if self._campaign_started(session) and self._looks_like_live_action(text):
            return GameMode.RESOLUTION
        if self._looks_like_character_request(text):
            return GameMode.CHARACTER_CREATION
        if self._looks_like_start_request(text) and not battle.get("active"):
            return GameMode.NARRATIVE
        if session.mode == GameMode.CHARACTER_CREATION and not self._campaign_started(session) and not self._looks_finished(text):
            return GameMode.CHARACTER_CREATION
        if session.mode == GameMode.RULE_AUTHORING and not self._looks_finished(text) and not self._has_background_ready(session):
            return GameMode.RULE_AUTHORING
        if any(hint in text for hint in self.CHARACTER_HINTS):
            return GameMode.CHARACTER_CREATION
        if any(hint in text for hint in self.RULE_HINTS):
            return GameMode.RULE_AUTHORING
        if any(hint in text for hint in self.RESOLUTION_HINTS):
            return GameMode.RESOLUTION
        if not session.characters and any(token in text for token in ("我是", "我想扮演", "职业")):
            return GameMode.CHARACTER_CREATION
        return GameMode.NARRATIVE

    @staticmethod
    def _looks_finished(text: str) -> bool:
        return any(token in text for token in ("完成", "就这样", "开始", "进入剧情", "开局", "回合结束", "结束回合", "下一位", "下一个"))

    @staticmethod
    def _looks_like_character_request(text: str) -> bool:
        if not text:
            return False
        if any(token in text for token in ("角色", "人物卡", "角色卡", "车卡", "建卡", "创建人物", "绑定角色")):
            return True
        if any(token in text for token in ("我是", "我想扮演", "我扮演", "职业", "身份")) and not any(
            token in text for token in ("背景=", "世界观", "题材", "剧本")
        ):
            return True
        return False

    @staticmethod
    def _looks_like_terminal_exit_statement(text: str) -> bool:
        if not text:
            return False
        terminal_terms = ("退场", "退休", "离队", "不再扮演", "角色结束", "角色结局")
        rejoin_terms = ("新角色", "建卡", "创建人物", "创建角色", "绑定角色", "换新角色", "重新加入", "重新进团")
        background_terms = (
            "背景",
            "经历",
            "往事",
            "生活",
            "复出",
            "退役",
            "退休后",
            "退休后的",
            "提前退休",
            "提前“退休”",
            '提前"退休"',
            "渔夫生活",
            "讲述",
            "聊",
            "回忆",
            "曾经",
            "以前",
            "过去",
        )
        if any(term in text for term in rejoin_terms):
            return False
        explicit_terms = ("退场", "退休", "离队", "角色退场", "角色结束", "角色结局", "不再扮演")
        if text.strip(" ，,。.!！?？:：;；").endswith(explicit_terms) or "算是角色退场" in text:
            return True
        if any(term in text for term in background_terms) and not any(
            term in text for term in ("已退场", "确认退场", "永久退场", "角色已退场")
        ):
            return False
        return any(term in text for term in terminal_terms)

    @staticmethod
    def _looks_like_start_request(text: str) -> bool:
        return any(token in text for token in ("开始游戏", "正式开始", "开场", "开局", "进入剧情", "进入正片"))

    @staticmethod
    def _has_background_ready(session: GameSession) -> bool:
        world_tags = dict(session.world_tags or {})
        if world_tags.get("_background_ready") is True:
            return True
        meaningful = [
            value
            for key, value in world_tags.items()
            if not str(key).startswith("_") and str(value).strip() not in {"", "{}", "[]", "None"}
        ]
        return len(meaningful) >= 2

    @staticmethod
    def _campaign_started(session: GameSession) -> bool:
        scene = dict(session.scene or {})
        world_tags = dict(session.world_tags or {})
        return bool(scene.get("_game_started") or scene.get("_legacy_live_campaign") or world_tags.get("_plot_locked") is True)

    @classmethod
    def _looks_like_live_action(cls, text: str) -> bool:
        if not text:
            return False
        return any(hint in text for hint in cls.LIVE_ACTION_HINTS)
