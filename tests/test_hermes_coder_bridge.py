import asyncio
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
        job_timeout_seconds=1800,
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
        game_data_dir=str(tmp_path / "game_data"),
        game_export_dir=str(tmp_path / "exports"),
        game_log_tail_bytes=400,
        game_audit_tail_bytes=400,
        game_reply_chars=3200,
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


def test_format_coder_job_reply_prefixes_nonzero_exit(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    bridge.max_notify_chars = 200

    reply = bridge._format_coder_job_reply(2, "failed")

    assert reply.startswith("【Hermes /coder 异常退出：2】")
    assert "failed" in reply


def test_session_for_group_uses_notify_whitelist(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")

    assert bridge._session_for_group("1101538762") == "default:GroupMessage:1101538762"

    try:
        bridge._session_for_group("123")
    except ValueError as exc:
        assert "whitelist" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_game_log_request_detection_is_conservative():
    assert bridge_mod.is_game_log_request("获取最新游戏日志")
    assert bridge_mod.is_game_log_request("导出 auto_trpg_dm.log")
    assert not bridge_mod.is_game_log_request("执行日志复盘并修复问题")
    assert not bridge_mod.is_game_log_request("审查两个未合并 PR")


def test_build_game_log_reply_uses_requested_group_audit_and_exports(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    data_dir = bridge.game_data_dir
    (data_dir / "logs").mkdir(parents=True)
    (data_dir / "audit").mkdir(parents=True)
    (data_dir / "saves").mkdir(parents=True)
    (data_dir / "logs" / "auto_trpg_dm.log").write_text(
        "2026-05-18 10:00:00 plugin-old\n2026-05-18 10:01:00 plugin-tail\n",
        encoding="utf-8",
    )
    (data_dir / "audit" / "default_GroupMessage_676453921.jsonl").write_text(
        '{"event":"audit-tail","session":"676453921"}\n',
        encoding="utf-8",
    )
    (data_dir / "saves" / "default_GroupMessage_676453921.json").write_text(
        '{"session":"676453921"}\n',
        encoding="utf-8",
    )

    reply = bridge._build_game_log_reply(
        {
            "prompt": "获取 676453921 最新游戏日志",
            "group_id": "1101538762",
            "session_id": "default:GroupMessage:1101538762",
        }
    )

    assert "auto_trpg_dm.log" in reply
    assert "default_GroupMessage_676453921.jsonl" in reply
    assert "default_GroupMessage_676453921.json" in reply
    assert "plugin-tail" in reply
    assert "audit-tail" in reply
    exports = list(bridge.game_export_dir.glob("game_logs_*_1101538762.txt"))
    assert len(exports) == 1
    assert "audit-tail" in exports[0].read_text(encoding="utf-8")


def test_coder_serves_game_log_request_without_starting_hermes(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        (bridge.game_data_dir / "logs").mkdir(parents=True)
        (bridge.game_data_dir / "logs" / "auto_trpg_dm.log").write_text("plugin-tail\n", encoding="utf-8")

        async def fake_read_signed_json(_request):
            return {
                "prompt": "获取最新游戏日志",
                "group_id": "1101538762",
                "message_id": "msg-log",
                "session_id": "default:GroupMessage:1101538762",
            }, None

        def fake_run_hermes(*_args):
            raise AssertionError("run_hermes should not be called for game log requests")

        def fake_json_response(data, *args, **kwargs):
            del args
            return {"data": data, "status": kwargs.get("status")}

        monkeypatch.setattr(bridge, "_read_signed_json", fake_read_signed_json)
        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge_mod, "json_response", fake_json_response)

        response = await bridge.coder(object())

        assert response["status"] is None
        assert response["data"]["ok"] is True
        assert response["data"]["accepted"] is False
        assert "plugin-tail" in response["data"]["reply"]

    asyncio.run(run_case())


def test_run_coder_job_posts_result_to_astrbot(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        captured = {}

        def fake_run_hermes(prompt, timeout, workdir):
            captured["prompt"] = prompt
            captured["timeout"] = timeout
            captured["workdir"] = workdir
            return 0, "审查完成"

        def fake_post(api_key, session, text):
            captured["api_key"] = api_key
            captured["session"] = session
            captured["text"] = text
            return {"ok": True}

        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge, "_post_astrbot_message", fake_post)

        await bridge._run_coder_job(
            {"group_id": "1101538762", "message_id": "msg1"},
            "prompt",
        )

        assert captured["timeout"] == 1800
        assert captured["session"] == "default:GroupMessage:1101538762"
        assert captured["text"] == "审查完成"

    asyncio.run(run_case())


def test_background_accepted_reply_constant_is_defined():
    assert bridge_mod.DEFAULT_BACKGROUND_ACCEPTED_REPLY
    assert "后台" in bridge_mod.DEFAULT_BACKGROUND_ACCEPTED_REPLY
