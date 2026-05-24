from astrbot_plugin_auto_trpg_dm.core.memory import MemoryCompressor
from astrbot_plugin_auto_trpg_dm.core.models import GameSession


def test_memory_compressor_summary_marks_itself_as_historical_not_current_state():
    session = GameSession.new("group")
    session.title = "灯塔疑云"
    session.scene["summary"] = "队伍已经进入灯塔。"

    summary = MemoryCompressor().build_summary(session)

    assert summary.startswith("说明：这是历史压缩摘要，不代表当前事实；当前会话快照和本轮工具结果优先。")
    assert "团名：灯塔疑云" in summary
    assert "当前场景：队伍已经进入灯塔。" in summary
