import importlib.util
from pathlib import Path


def _load_notify_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "hermes_notify_cron_output.py"
    spec = importlib.util.spec_from_file_location("hermes_notify_cron_output", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


notify_mod = _load_notify_module()


def test_notification_from_response_section(tmp_path):
    output = tmp_path / "run.md"
    output.write_text(
        "# Cron Job: paotuan repo steward\n\n"
        "## Prompt\n\nignored\n\n"
        "## Response\n\nPR #9 已合并，部署成功。\n",
        encoding="utf-8",
    )

    assert notify_mod.notification_from_output(output) == "【Paotuan 轮询通知】\nPR #9 已合并，部署成功。"


def test_notification_skips_silent_response(tmp_path):
    output = tmp_path / "silent.md"
    output.write_text("## Response\n\n[SILENT]\n", encoding="utf-8")

    assert notify_mod.notification_from_output(output) is None


def test_notification_from_error_section_strips_fence(tmp_path):
    output = tmp_path / "failed.md"
    output.write_text("## Error\n\n```\nRuntimeError: bad key\n```\n", encoding="utf-8")

    assert notify_mod.notification_from_output(output) == "【Paotuan 轮询异常】\nRuntimeError: bad key"


def test_collect_pending_uses_fingerprint_state(tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    output = output_dir / "run.md"
    output.write_text("## Response\n\n第一次通知\n", encoding="utf-8")
    state = {"sent": {}}

    pending = notify_mod.collect_pending(output_dir, state, 10)

    assert len(pending) == 1
    path, fingerprint, text = pending[0]
    assert path == output
    assert fingerprint["name"] == "run.md"
    assert text == "【Paotuan 轮询通知】\n第一次通知"
    state["sent"][notify_mod.notification_key(fingerprint)] = fingerprint
    assert notify_mod.collect_pending(output_dir, state, 10) == []


def test_bootstrap_marks_existing_without_returning_pending(tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "old.md").write_text("## Response\n\n旧通知\n", encoding="utf-8")
    state_path = tmp_path / "state.json"

    count = notify_mod.bootstrap_existing(output_dir, state_path, 10)
    state = notify_mod.load_json(state_path, {})

    assert count == 1
    assert len(state["sent"]) == 1
    assert notify_mod.collect_pending(output_dir, state, 10) == []
