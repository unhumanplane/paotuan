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
        coder_hermes_home=str(tmp_path / "coder_hermes_home"),
        coder_reasoning_effort="xhigh",
        session_state_path=str(tmp_path / "coder_sessions.json"),
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
        game_export_send_dir=str(tmp_path / "astrbot_exports"),
        game_log_tail_bytes=400,
        game_audit_tail_bytes=400,
        game_reply_chars=3200,
        plugin_review_script=str(tmp_path / "review_plugin_logs.sh"),
        plugin_review_reply="review accepted",
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


def test_format_coder_job_reply_hides_hermes_cli_noise_but_keeps_answer(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    raw_output = (
        "session_id: 20260522_145511_88a39a\n"
        "↻ Resumed session 20260522_145511_88a39a (1 user message, 16 total messages)\n"
        "Session 20260523_104954_80db38 found but has no messages. Starting fresh.\n"
        "\x1b[32m✓ Worktree created:\x1b[0m /volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-629d2c77\n"
        "  Branch: hermes/hermes-629d2c77\n"
        "我已读到 docs/dev_plan.md。\n"
        "\n"
        "它是“双 Agent 架构开发计划”，当前状态是“设计已完成，待进入开发”。\n"
        "\x1b[32m✓ Worktree cleaned up: /volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-629d2c77\x1b[0m\n"
    )

    reply = bridge._format_coder_job_reply(0, raw_output)

    assert reply.startswith("我已读到 docs/dev_plan.md。")
    assert "双 Agent 架构开发计划" in reply
    assert "session_id:" not in reply
    assert "20260522_145511_88a39a" not in reply
    assert "Resumed session" not in reply
    assert "has no messages" not in reply
    assert "Starting fresh" not in reply
    assert "Worktree created" not in reply
    assert "Worktree cleaned up" not in reply
    assert "Branch:" not in reply
    assert "\x1b[" not in reply


def test_update_session_state_extracts_session_id_from_unsanitized_output(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    raw_output = (
        "session_id: 20260522_145511_88a39a\n"
        "\x1b[32m✓ Worktree created:\x1b[0m /tmp/worktree\n"
        "  Branch: hermes/hermes-629d2c77\n"
        "我已读到 docs/dev_plan.md。\n"
    )
    reply = bridge._format_coder_job_reply(0, raw_output)

    bridge._update_session_state(
        "1101538762",
        {"prompt": "读一下 dev_plan"},
        0,
        raw_output,
        reply,
    )

    state = bridge_mod.load_json_state(bridge.session_state_path)
    session = state["sessions"]["1101538762"]
    assert session["hermes_session_id"] == "20260522_145511_88a39a"
    assert "Worktree created" not in session["last_result"]


def test_update_session_state_clears_session_id_on_failure(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    raw_output = (
        "session_id: 20260522_145511_88a39a\n"
        "↻ Resumed session 20260522_145511_88a39a (1 user message, 16 total messages)\n"
        "API call failed after 3 retries: Non-streaming API call timed out after 300s with no response (threshold: 300s)\n"
    )
    reply = bridge._format_coder_job_reply(1, raw_output)

    bridge._update_session_state(
        "1101538762",
        {"prompt": "timeout case"},
        1,
        raw_output,
        reply,
    )

    state = bridge_mod.load_json_state(bridge.session_state_path)
    session = state["sessions"]["1101538762"]
    assert session["hermes_session_id"] == ""
    assert session["last_result"] == "[失败] Hermes /coder 超时"
    assert "Resumed session" not in session["last_result"]


def test_format_coder_job_reply_hides_review_diff_blocks(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    raw_output = (
        "┊ review diff\n"
        "a//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/tests/test_cycle_buffer.py → b//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/tests/test_cycle_buffer.py\n"
        "@@ -130,8 +130,32 @@\n"
        "     assert session.timeline[\"last_advanced_cycle_id\"] == 2\n"
        "\n"
        "-def test_append_cycle_action_sanitizes_raw_grid_from_ra_tool_input():\n"
        "-    session = GameSession.new(\"group\")\n"
        "+def test_append_cycle_action_keeps_latest_50_actions_without_leaking_overflow_to_ra_summary():\n"
        "+    session = GameSession.new(\"group\")\n"
        "  ┊ review diff\n"
        "a//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/astrbot_plugin_auto_trpg_dm/core/cycle_buffer.py → b//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/astrbot_plugin_auto_trpg_dm/core/cycle_buffer.py\n"
        "@@ -6,6 +6,9 @@\n"
        "+CYCLE_BUFFER_ACTION_LIMIT = 50\n"
        "\n"
        "我检查并修复了 cycle buffer 的 RA 输入膨胀问题，测试已通过。\n"
    )

    reply = bridge._format_coder_job_reply(0, raw_output)

    assert reply == "我检查并修复了 cycle buffer 的 RA 输入膨胀问题，测试已通过。"
    assert "review diff" not in reply
    assert ".worktrees" not in reply
    assert "@@" not in reply
    assert "CYCLE_BUFFER_ACTION_LIMIT" not in reply


def test_format_coder_job_reply_falls_back_to_session_answer_when_stdout_is_only_diff(tmp_path):
    bridge = _bridge(tmp_path, groups="1101538762")
    session_id = "20260523_093935_a1b2c3d4"
    session_file = bridge.coder_hermes_home / "sessions" / f"session_{session_id}.json"
    session_file.parent.mkdir(parents=True)
    session_file.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "assistant", "content": ""},
                    {
                        "role": "assistant",
                        "content": "已根据 docs/dev_plan.md 继续处理 D4。\n\n验证：\n- pytest -q 通过：736 passed",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw_output = (
        f"session_id: {session_id}\n"
        "┊ review diff\n"
        "a//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/tests/test_cycle_buffer.py → b//volume1/docker/hermes/paotuan/work/paotuan/.worktrees/hermes-08f79dea/tests/test_cycle_buffer.py\n"
        "@@ -130,8 +130,32 @@\n"
        "+CYCLE_BUFFER_ACTION_LIMIT = 50\n"
    )

    reply = bridge._format_coder_job_reply(0, raw_output)

    assert reply.startswith("已根据 docs/dev_plan.md")
    assert "736 passed" in reply
    assert "review diff" not in reply
    assert ".worktrees" not in reply


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

    result = bridge._build_game_log_result(
        {
            "prompt": "获取 676453921 最新游戏日志",
            "group_id": "1101538762",
            "session_id": "default:GroupMessage:1101538762",
        }
    )
    reply = result["reply"]

    assert "auto_trpg_dm.log" in reply
    assert "default_GroupMessage_676453921.jsonl" in reply
    assert "default_GroupMessage_676453921.json" in reply
    assert "plugin-tail" in reply
    assert "audit-tail" in reply
    exports = list(bridge.game_export_dir.glob("game_logs_*_1101538762.txt"))
    assert len(exports) == 1
    assert "audit-tail" in exports[0].read_text(encoding="utf-8")
    assert result["files"] == [
        {
            "path": str(bridge.game_export_send_dir / exports[0].name),
            "name": exports[0].name,
        }
    ]


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
        assert response["data"]["files"][0]["name"].startswith("game_logs_")

    asyncio.run(run_case())


def test_run_coder_job_posts_result_to_astrbot(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        captured = {}

        def fake_run_hermes(prompt, timeout, workdir, hermes_home=None, reasoning_effort="", resume_session_id="", session_source=""):
            captured["prompt"] = prompt
            captured["timeout"] = timeout
            captured["workdir"] = workdir
            captured["hermes_home"] = hermes_home
            captured["reasoning_effort"] = reasoning_effort
            captured["resume_session_id"] = resume_session_id
            captured["session_source"] = session_source
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
        assert captured["workdir"] == tmp_path
        assert captured["hermes_home"] == tmp_path / "coder_hermes_home"
        assert captured["reasoning_effort"] == "xhigh"
        assert captured["resume_session_id"] == ""
        assert captured["session_source"] == "paotuan-coder-1101538762"
        assert captured["session"] == "default:GroupMessage:1101538762"
        assert captured["text"] == "审查完成"

    asyncio.run(run_case())


def test_run_coder_job_resumes_saved_session_and_splits_long_notify(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        bridge.max_notify_chars = 80
        session_file = bridge.coder_hermes_home / "sessions" / "session_20260522_120000_deadbeef.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(json.dumps({"messages": [{"role": "assistant", "content": "old result"}]}), encoding="utf-8")
        bridge_mod.save_json_state(
            bridge.session_state_path,
            {
                "sessions": {
                    "1101538762": {
                        "hermes_session_id": "20260522_120000_deadbeef",
                        "last_prompt": "old prompt",
                        "last_result": "old result",
                    }
                }
            },
        )
        captured = {"texts": []}

        def fake_run_hermes(prompt, timeout, workdir, hermes_home=None, reasoning_effort="", resume_session_id="", session_source=""):
            captured["prompt"] = prompt
            captured["resume_session_id"] = resume_session_id
            captured["session_source"] = session_source
            return 0, "20260522_130000_a1b2c3d4\n" + ("输出行\n" * 40)

        def fake_post(api_key, session, text):
            captured["texts"].append(text)
            return {"ok": True}

        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge, "_post_astrbot_message", fake_post)

        await bridge._run_coder_job(
            {"group_id": "1101538762", "message_id": "msg1", "prompt": "new prompt"},
            bridge_mod.build_prompt({"prompt": "new prompt", "group_id": "1101538762"}, bridge._session_context_for_group("1101538762")),
        )

        assert captured["resume_session_id"] == "20260522_120000_deadbeef"
        assert captured["session_source"] == "paotuan-coder-1101538762"
        assert "old prompt" in captured["prompt"]
        assert len(captured["texts"]) > 1
        assert captured["texts"][0].startswith("[1/")
        state = bridge_mod.load_json_state(bridge.session_state_path)
        assert state["sessions"]["1101538762"]["hermes_session_id"] == "20260522_130000_a1b2c3d4"
        assert "new prompt" in state["sessions"]["1101538762"]["last_prompt"]

    asyncio.run(run_case())


def test_run_coder_job_skips_empty_saved_session_and_starts_fresh(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        session_file = bridge.coder_hermes_home / "sessions" / "session_20260523_104954_80db38.json"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(json.dumps({"messages": []}), encoding="utf-8")
        bridge_mod.save_json_state(
            bridge.session_state_path,
            {
                "sessions": {
                    "1101538762": {
                        "hermes_session_id": "20260523_104954_80db38",
                        "last_returncode": 0,
                        "last_result": "previous ok",
                    }
                }
            },
        )
        captured = {}

        def fake_run_hermes(prompt, timeout, workdir, hermes_home=None, reasoning_effort="", resume_session_id="", session_source=""):
            captured["resume_session_id"] = resume_session_id
            return 0, "20260523_120000_a1b2c3d4\nfresh ok"

        def fake_post(api_key, session, text):
            captured["text"] = text
            return {"ok": True}

        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge, "_post_astrbot_message", fake_post)

        await bridge._run_coder_job(
            {"group_id": "1101538762", "message_id": "msg-empty", "prompt": "continue"},
            "prompt",
        )

        assert captured["resume_session_id"] == ""
        assert captured["text"] == "fresh ok"

    asyncio.run(run_case())


def test_run_coder_job_skips_failed_session_and_starts_fresh(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        bridge_mod.save_json_state(
            bridge.session_state_path,
            {
                "sessions": {
                    "1101538762": {
                        "hermes_session_id": "20260522_145511_88a39a",
                        "last_returncode": 1,
                        "last_result": "API call failed after 3 retries: Non-streaming API call timed out after 300s with no response (threshold: 300s)",
                    }
                }
            },
        )
        captured = {}

        def fake_run_hermes(prompt, timeout, workdir, hermes_home=None, reasoning_effort="", resume_session_id="", session_source=""):
            captured["resume_session_id"] = resume_session_id
            captured["prompt"] = prompt
            return 0, "20260523_010500_a1b2c3d4\nfresh success"

        def fake_post(api_key, session, text):
            captured["text"] = text
            return {"ok": True}

        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge, "_post_astrbot_message", fake_post)

        await bridge._run_coder_job(
            {"group_id": "1101538762", "message_id": "msg2", "prompt": "fresh please"},
            bridge_mod.build_prompt({"prompt": "fresh please", "group_id": "1101538762"}, bridge._session_context_for_group("1101538762")),
        )

        assert captured["resume_session_id"] == ""
        assert "fresh success" in captured["text"]
        state = bridge_mod.load_json_state(bridge.session_state_path)
        assert state["sessions"]["1101538762"]["hermes_session_id"] == "20260523_010500_a1b2c3d4"

    asyncio.run(run_case())


def test_background_accepted_reply_constant_is_defined():
    assert bridge_mod.DEFAULT_BACKGROUND_ACCEPTED_REPLY
    assert "后台" in bridge_mod.DEFAULT_BACKGROUND_ACCEPTED_REPLY



def test_plugin_review_request_detection_targets_review_fix_prompts():
    assert bridge_mod.is_plugin_review_request("不要让你自己审阅，让插件自己调用审阅，实现自动修复版")
    assert bridge_mod.is_plugin_review_request("审阅插件日志并自动修复")
    assert not bridge_mod.is_plugin_review_request("获取最新游戏日志")
    assert not bridge_mod.is_plugin_review_request("审查两个未合并 PR")


def test_coder_starts_plugin_review_script_without_running_generic_hermes(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        bridge.plugin_review_script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        captured = {}

        async def fake_read_signed_json(_request):
            return {
                "prompt": "审阅插件日志并自动修复",
                "group_id": "1101538762",
                "message_id": "msg-review",
                "session_id": "default:GroupMessage:1101538762",
            }, None

        async def fake_run_plugin_review_job(payload, hermes_prompt):
            captured["payload"] = payload
            captured["prompt"] = hermes_prompt

        def fake_run_hermes(*_args):
            raise AssertionError("generic run_hermes should not be called for plugin review requests")

        def fake_json_response(data, *args, **kwargs):
            del args
            return {"data": data, "status": kwargs.get("status")}

        monkeypatch.setattr(bridge, "_read_signed_json", fake_read_signed_json)
        monkeypatch.setattr(bridge, "_run_plugin_review_job", fake_run_plugin_review_job)
        monkeypatch.setattr(bridge_mod, "run_hermes", fake_run_hermes)
        monkeypatch.setattr(bridge_mod, "json_response", fake_json_response)

        response = await bridge.coder(object())

        await asyncio.sleep(0)
        assert response["status"] is None
        assert response["data"]["ok"] is True
        assert response["data"]["accepted"] is True
        assert response["data"]["reply"] == "review accepted"
        assert captured["payload"]["message_id"] == "msg-review"
        assert "审阅插件日志并自动修复" in captured["prompt"]

    asyncio.run(run_case())


def test_run_plugin_review_script_passes_owner_request_env(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_mod, "OPS", tmp_path)
    script = tmp_path / "review_plugin_logs.sh"
    script.write_text("#!/bin/sh\nprintf '%s' \"$PAOTUAN_REVIEW_REQUEST\"\n", encoding="utf-8")
    script.chmod(0o755)

    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "owner request"

    def fake_run(cmd, cwd, env, text, stdout, stderr, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env_request"] = env["PAOTUAN_REVIEW_REQUEST"]
        captured["timeout"] = timeout
        return FakeCompleted()

    monkeypatch.setattr(bridge_mod.subprocess, "run", fake_run)

    returncode, output = bridge_mod.run_plugin_review_script(script, "owner request", 5)

    assert returncode == 0
    assert output == "owner request"
    assert captured["env_request"] == "owner request"
    assert captured["cwd"] == str(bridge_mod.OPS)


def test_build_prompt_includes_session_context():
    prompt = bridge_mod.build_prompt({"prompt": "new request", "group_id": "1101538762"}, "old context")

    assert "old context" in prompt
    assert "new request" in prompt


def test_run_plugin_review_job_posts_result_to_astrbot(tmp_path, monkeypatch):
    async def run_case():
        bridge = _bridge(tmp_path, groups="1101538762")
        captured = {}

        def fake_run_review(script_path, user_prompt, timeout, hermes_home=None, reasoning_effort=""):
            captured["script_path"] = script_path
            captured["user_prompt"] = user_prompt
            captured["timeout"] = timeout
            captured["hermes_home"] = hermes_home
            captured["reasoning_effort"] = reasoning_effort
            return 0, "审阅脚本完成"

        def fake_post(api_key, session, text):
            captured["api_key"] = api_key
            captured["session"] = session
            captured["text"] = text
            return {"ok": True}

        monkeypatch.setattr(bridge_mod, "run_plugin_review_script", fake_run_review)
        monkeypatch.setattr(bridge, "_post_astrbot_message", fake_post)

        await bridge._run_plugin_review_job(
            {"group_id": "1101538762", "message_id": "msg-review"},
            "review prompt",
        )

        assert captured["timeout"] == 1800
        assert captured["hermes_home"] == tmp_path / "coder_hermes_home"
        assert captured["reasoning_effort"] == "xhigh"
        assert captured["user_prompt"] == "review prompt"
        assert captured["session"] == "default:GroupMessage:1101538762"
        assert captured["text"] == "审阅脚本完成"

    asyncio.run(run_case())


def test_prepare_coder_hermes_home_overrides_reasoning_without_touching_main(tmp_path, monkeypatch):
    main_home = tmp_path / "data"
    coder_home = tmp_path / "data-coder"
    main_home.mkdir()
    (main_home / "config.yaml").write_text(
        "model:\n  default: gpt-5.5\nagent:\n  max_turns: 90\n  reasoning_effort: high\ncron:\n  enabled: true\n",
        encoding="utf-8",
    )
    (main_home / ".env").write_text("OPENAI_API_KEY=masked\n", encoding="utf-8")
    (main_home / "SOUL.md").write_text("persona\n", encoding="utf-8")
    monkeypatch.setattr(bridge_mod, "MAIN_HERMES_HOME", main_home)

    bridge_mod.prepare_coder_hermes_home(coder_home, "xhigh")

    assert "reasoning_effort: high" in (main_home / "config.yaml").read_text(encoding="utf-8")
    coder_config = (coder_home / "config.yaml").read_text(encoding="utf-8")
    assert "reasoning_effort: xhigh" in coder_config
    assert "cron:" in coder_config
    assert (coder_home / ".env").exists()


def test_run_hermes_uses_coder_home_and_reasoning(tmp_path, monkeypatch):
    main_home = tmp_path / "data"
    coder_home = tmp_path / "data-coder"
    workdir = tmp_path / "repo"
    main_home.mkdir()
    workdir.mkdir()
    (main_home / "config.yaml").write_text("agent:\n  reasoning_effort: high\n", encoding="utf-8")
    monkeypatch.setattr(bridge_mod, "MAIN_HERMES_HOME", main_home)
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "done"

    def fake_run(cmd, cwd, env, text, stdout, stderr, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env_home"] = env["HERMES_HOME"]
        captured["stale_timeout"] = env["HERMES_API_CALL_STALE_TIMEOUT"]
        captured["timeout"] = timeout
        return FakeCompleted()

    monkeypatch.setattr(bridge_mod.subprocess, "run", fake_run)

    returncode, output = bridge_mod.run_hermes("hello", 5, workdir, coder_home, "xhigh", "", "paotuan-coder-test")

    assert returncode == 0
    assert output == "done"
    assert captured["cmd"] == [
        "hermes",
        "chat",
        "--accept-hooks",
        "--worktree",
        "--yolo",
        "--pass-session-id",
        "-Q",
        "--source",
        "paotuan-coder-test",
        "-q",
        "hello",
    ]
    assert captured["cwd"] == str(workdir)
    assert captured["env_home"] == str(coder_home)
    assert captured["stale_timeout"] == "900"
    assert "reasoning_effort: xhigh" in (coder_home / "config.yaml").read_text(encoding="utf-8")


def test_run_hermes_resumes_saved_session(tmp_path, monkeypatch):
    main_home = tmp_path / "data"
    coder_home = tmp_path / "data-coder"
    workdir = tmp_path / "repo"
    main_home.mkdir()
    workdir.mkdir()
    (main_home / "config.yaml").write_text("agent:\n  reasoning_effort: high\n", encoding="utf-8")
    monkeypatch.setattr(bridge_mod, "MAIN_HERMES_HOME", main_home)
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = "done"

    def fake_run(cmd, cwd, env, text, stdout, stderr, timeout):
        captured["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(bridge_mod.subprocess, "run", fake_run)

    bridge_mod.run_hermes("hello", 5, workdir, coder_home, "xhigh", "20260522_120000_deadbeef", "ignored-source")

    assert captured["cmd"] == [
        "hermes",
        "chat",
        "--accept-hooks",
        "--worktree",
        "--yolo",
        "--pass-session-id",
        "--resume",
        "20260522_120000_deadbeef",
        "-Q",
        "-q",
        "hello",
    ]


def test_command_env_switches_home_without_losing_main_node_bin(tmp_path, monkeypatch):
    main_home = tmp_path / "data"
    coder_home = tmp_path / "data-coder"
    main_home.mkdir()
    monkeypatch.setattr(bridge_mod, "MAIN_HERMES_HOME", main_home)

    env = bridge_mod.command_env(coder_home)

    assert env["HERMES_HOME"] == str(coder_home)
    assert env["HERMES_NODE_BIN"] == str(coder_home / "node" / "bin")
    assert env["HERMES_API_CALL_STALE_TIMEOUT"] == "900"
    assert str(coder_home / "node" / "bin") in env["PATH"]
