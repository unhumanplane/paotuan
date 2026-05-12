from tests.test_dm_ack_and_outputs import _install_fake_astrbot_modules

_install_fake_astrbot_modules()

from astrbot_plugin_auto_trpg_dm.core.models import GameSession
from astrbot_plugin_auto_trpg_dm.main import _campaign_plot_locked, _looks_like_plot_rewrite_request


def _started_session():
    session = GameSession.new("s")
    session.scene["_game_started"] = True
    session.scene["_plot_locked"] = True
    session.world_tags["_plot_locked"] = True
    return session


def test_in_character_request_to_find_hidden_culprit_is_not_plot_rewrite():
    session = _started_session()
    text = (
        "我这样和老执事说“尊敬的执事，十一税我已经准备好了，正打算今天送来。"
        "另外我还想续一下酒馆的经营者责任保险。不过……我这次来其实有件十万火急的事想请您帮忙——"
        "我的酒馆被人投毒了，现场留下了敌人的脚印。不知道能否劳烦您为我做一次预言，帮我找出这个幕后黑手？”"
    )

    assert _campaign_plot_locked(session)
    assert not _looks_like_plot_rewrite_request(text)


def test_out_of_character_hidden_culprit_rewrite_is_still_blocked():
    assert _looks_like_plot_rewrite_request("其实幕后黑手是镇长，真相是他自导自演，改一下剧情")
