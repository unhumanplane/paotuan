from astrbot_plugin_auto_trpg_dm.core.modes import GameModeStateMachine
from astrbot_plugin_auto_trpg_dm.core.models import GameMode, GameSession


def test_battle_state_forces_tactical_mode():
    session = GameSession.new("s")
    session.battle["active"] = True

    mode = GameModeStateMachine().detect(session, "我想回忆一下背景")

    assert mode == GameMode.TACTICAL


def test_character_hint_enters_character_creation():
    session = GameSession.new("s")

    mode = GameModeStateMachine().detect(session, "我是一个失忆医生")

    assert mode == GameMode.CHARACTER_CREATION

