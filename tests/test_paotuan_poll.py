import importlib.util
from pathlib import Path


def _load_poll_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "paotuan_poll.py"
    spec = importlib.util.spec_from_file_location("paotuan_poll", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


poll_mod = _load_poll_module()


def test_build_deploy_success_notification_summarizes_report():
    report = """
[2026-05-10T15:00:40+00:00] checked out 97caafb00333 Containerize Hermes services on NAS
- OK compileall (0.58s)
424 passed in 6.24s
[2026-05-10T15:00:51+00:00] AstrBot plugin reload API returned status=ok
[2026-05-10T15:00:51+00:00] plugin log confirmed hot reload: plugin_initialized version=0.1.91
"""

    text = poll_mod.build_deploy_success_notification(
        {"commit": "97caafb0033398e26f106816ffa551471da9d434"},
        report,
    )

    assert "【Paotuan 部署成功】" in text
    assert "97caafb00333" in text
    assert "Containerize Hermes services on NAS" in text
    assert "compileall 通过" in text
    assert "pytest 424 passed in 6.24s" in text
    assert "AstrBot Dashboard 热加载成功" in text
    assert "未重启 AstrBot 容器" in text


def test_reportable_pr_changes_only_keeps_open_unmerged_prs():
    changes = [
        {"number": 1, "state": "closed", "merged_at": "2026-05-10T01:00:00Z"},
        {"number": 2, "state": "closed", "merged_at": None},
        {"number": 3, "state": "open", "merged_at": None},
    ]

    assert poll_mod.reportable_pr_changes(changes) == [{"number": 3, "state": "open", "merged_at": None}]


def test_pr_monitor_error_notification_is_deterministic():
    text = poll_mod.build_pr_monitor_error_notification("GitHub PR API HTTP 503")

    assert "【Paotuan PR 监控异常】" in text
    assert "GitHub PR API HTTP 503" in text
    assert "继续按计划重试" in text
