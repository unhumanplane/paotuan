from astrbot_plugin_auto_trpg_dm.core.scene_threads import (
    normalize_thread_status,
    thread_can_leave_focus,
    thread_is_closed,
    thread_is_obsolete,
    thread_is_soft_exit,
)


def test_scene_thread_status_aliases_and_lifecycle_groups():
    assert normalize_thread_status("failure-forward") == "failed_forward"
    assert normalize_thread_status("completed") == "resolved"
    assert normalize_thread_status("paused") == "deferred"

    assert thread_is_closed({"status": "failed_forward"}) is True
    assert thread_is_obsolete({"status": "failed_forward"}) is True
    assert thread_can_leave_focus({"status": "failed_forward"}) is True

    assert thread_is_soft_exit({"status": "blocked"}) is True
    assert thread_can_leave_focus({"status": "blocked"}) is True
    assert thread_is_closed({"status": "blocked"}) is False
    assert thread_is_obsolete({"status": "blocked"}) is False
