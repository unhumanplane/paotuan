from __future__ import annotations

from .models import GameMode, GameSession


class GameModeStateMachine:
    """Small deterministic guardrail before the LLM sees tools."""

    CHARACTER_HINTS = ("角色", "车卡", "人物卡", "创建人物", "属性", "背景")
    RULE_HINTS = ("规则", "机制", "检定", "伤害公式", "判定方式", "怎么骰")
    BATTLE_HINTS = ("战棋", "地图", "坐标", "移动", "格", "攻击距离", "视线", "回合", "轮动", "行动顺序", "下一位")
    RESOLUTION_HINTS = ("结算", "场面结算", "结果", "命中", "伤害", "消耗", "豁免", "继续", "跳过", "无人响应")

    def detect(self, session: GameSession, message: str) -> GameMode:
        text = message.strip().lower()
        battle = session.battle or {}
        turn = battle.get("turn") if isinstance(battle.get("turn"), dict) else {}
        if battle.get("active") or turn.get("active"):
            return GameMode.TACTICAL
        if any(hint in text for hint in self.BATTLE_HINTS):
            return GameMode.TACTICAL
        if session.mode == GameMode.CHARACTER_CREATION and not self._looks_finished(text):
            return GameMode.CHARACTER_CREATION
        if session.mode == GameMode.RULE_AUTHORING and not self._looks_finished(text):
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
