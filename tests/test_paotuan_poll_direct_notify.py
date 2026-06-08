import importlib.util
from pathlib import Path
import urllib.error


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


def test_ensure_git_ssh_env_sets_stable_deploy_key(tmp_path):
    key = tmp_path / "id_ed25519_paotuan"
    key.write_text("private-key-placeholder", encoding="utf-8")
    env = {}

    poll_mod.ensure_git_ssh_env(env, key)

    assert env["PAOTUAN_SSH_KEY"] == str(key)
    assert str(key) in env["GIT_SSH_COMMAND"]
    assert "IdentitiesOnly=yes" in env["GIT_SSH_COMMAND"]


def test_post_notify_retries_transient_gateway_error(tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.write_text("notify-secret", encoding="utf-8")
    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 502, "bad gateway", {}, None)
        return FakeResponse()

    monkeypatch.setattr(poll_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(poll_mod, "time", type("T", (), {"sleep": staticmethod(lambda seconds: None)}))

    result = poll_mod.post_notify(
        "hello",
        url="http://bridge/notify",
        group_id="1101538762",
        secret_path=secret,
        attempts=2,
    )

    assert result["ok"] is True
    assert result["http_status"] == 200
    assert len(calls) == 2
