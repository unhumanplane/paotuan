import argparse
import importlib.util
import json
from pathlib import Path


def _load_bridge_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "hermes_coder_bridge.py"
    spec = importlib.util.spec_from_file_location("hermes_coder_bridge", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


bridge_mod = _load_bridge_module()


def _bridge(tmp_path, *, groups="", config_groups=None):
    secret_path = tmp_path / "coder_secret"
    secret_path.write_text("coder-secret", encoding="utf-8")
    api_key_path = tmp_path / "astrbot_api_key"
    api_key_path.write_text("api-key", encoding="utf-8")
    config_path = tmp_path / "coder_config.json"
    if config_groups is not None:
        config_path.write_text(
            json.dumps({"group_whitelist": config_groups}, ensure_ascii=False),
            encoding="utf-8",
        )
    args = argparse.Namespace(
        secret_path=str(secret_path),
        timeout_seconds=240,
        max_prompt_chars=4000,
        max_output_chars=12000,
        workdir=str(tmp_path),
        astrbot_api_url="http://astrbot/api/v1/im/message",
        astrbot_api_key_path=str(api_key_path),
        astrbot_api_timeout_seconds=10,
        astrbot_coder_config_path=str(config_path),
        notify_group_whitelist=groups,
        notify_session_template="default:GroupMessage:{group_id}",
        max_notify_chars=20,
    )
    return bridge_mod.CoderBridge(args)


def test_notify_whitelist_falls_back_to_astrbot_plugin_config(tmp_path):
    bridge = _bridge(tmp_path, config_groups=["1101538762"])

    group_id, session, text = bridge._resolve_notify_payload(
        {"group_id": "1101538762", "text": "部署完成"}
    )

    assert group_id == "1101538762"
    assert session == "default:GroupMessage:1101538762"
    assert text == "部署完成"


def test_notify_rejects_groups_outside_whitelist(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")

    try:
        bridge._resolve_notify_payload({"group_id": "123", "text": "hi"})
    except ValueError as exc:
        assert "whitelist" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_notify_derives_group_from_session_and_truncates_text(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")

    group_id, session, text = bridge._resolve_notify_payload(
        {"umo": "default:GroupMessage:1101538762", "message": "x" * 30}
    )

    assert group_id == "1101538762"
    assert session == "default:GroupMessage:1101538762"
    assert text == ("x" * 20) + "\n\n[已截断]"


def test_post_astrbot_message_uses_im_open_api_payload(tmp_path, monkeypatch):
    bridge = _bridge(tmp_path, groups="1101538762")
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"ok"}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["api_key"] = req.headers["X-api-key"]
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = bridge._post_astrbot_message("api-key", "default:GroupMessage:1101538762", "hello")

    assert result["ok"] is True
    assert captured["url"] == "http://astrbot/api/v1/im/message"
    assert json.loads(captured["body"].decode("utf-8")) == {
        "umo": "default:GroupMessage:1101538762",
        "message": "hello",
    }
    assert captured["api_key"] == "api-key"
    assert captured["timeout"] == 10
