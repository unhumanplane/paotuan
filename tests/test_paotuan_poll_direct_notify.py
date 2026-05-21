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


def test_deploy_failure_notification_is_deterministic_and_short():
    text = poll_mod.build_deploy_failure_notification(
        {
            "deploy_output_tail": "ignored",
            "latest_report_tail": "Permission denied: '__init__.cpython-312.pyc'",
        },
        {
            "commit": "8970c507772aaafa8c1f21204179a40ce7674a61",
            "reason": "hermes-cron",
        },
    )

    assert "[Paotuan deploy failed]" in text
    assert "8970c507772a" in text
    assert "hermes-cron" in text
    assert "Permission denied" in text
    assert "Hermes agent was not woken" in text
    assert len(text) < 700


def test_truncate_compacts_whitespace_and_limits_length():
    text = poll_mod._truncate(" one\n\n two   three " * 40, 80)

    assert "\n" not in text
    assert len(text) <= 80
    assert text.endswith("...")


def test_pr_attention_notification_is_lightweight():
    text = poll_mod.build_pr_attention_notification(
        [
            {
                "number": 27,
                "title": "feat: forensic dump system",
                "user": "HashVal",
                "state": "open",
                "head": "8fa7e020a9d8",
                "url": "https://github.com/unhumanplane/paotuan/pull/27",
            }
        ],
        reason="open PR was not reported before",
    )

    assert "[Paotuan PR attention]" in text
    assert "PR #27" in text
    assert "HashVal" in text
    assert "/coder" in text
    assert len(text) < 700
