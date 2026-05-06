#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path("/volume1/docker/hermes")
OPS = ROOT / "paotuan"
DATA = Path(os.environ.get("HERMES_HOME") or ROOT / "data")
DEFAULT_JOB_ID = os.environ.get("PAOTUAN_STEWARD_JOB_ID", "156c7611abe3")
DEFAULT_OUTPUT_DIR = DATA / "cron" / "output" / DEFAULT_JOB_ID
DEFAULT_STATE_PATH = OPS / "notify_cron_output_state.json"
DEFAULT_SECRET_PATH = OPS / "secrets" / "coder_bridge_secret"
DEFAULT_NOTIFY_URL = "http://127.0.0.1:8767/notify"
DEFAULT_GROUP_ID = os.environ.get("PAOTUAN_NOTIFY_GROUP_ID", "1101538762")
MAX_SCAN_FILES = 200


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
    }


def notification_key(fingerprint: dict[str, Any]) -> str:
    return f"{fingerprint['name']}:{fingerprint['sha256']}"


def extract_section(text: str, heading: str) -> str:
    marker = f"\n## {heading}\n"
    if marker not in text:
        if text.startswith(f"## {heading}\n"):
            body = text.split("\n", 1)[1]
        else:
            return ""
    else:
        body = text.split(marker, 1)[1]
    next_heading = body.find("\n## ")
    if next_heading >= 0:
        body = body[:next_heading]
    return body.strip()


def strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def clean_response(text: str) -> str:
    text = text.strip()
    text = text.replace("\r\n", "\n")
    footer = "\nTo stop or manage this job,"
    if footer in text:
        text = text.split(footer, 1)[0].rstrip()
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def notification_from_output(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    response = clean_response(extract_section(text, "Response"))
    if response:
        normalized = response.strip().upper()
        if normalized == "[SILENT]" or normalized == "(NO RESPONSE GENERATED)":
            return None
        return f"【Paotuan 轮询通知】\n{response}"

    error = strip_code_fence(extract_section(text, "Error"))
    if error:
        return f"【Paotuan 轮询异常】\n{error}"
    return None


def recent_output_files(output_dir: Path, limit: int) -> list[Path]:
    if not output_dir.exists():
        return []
    files = [path for path in output_dir.glob("*.md") if path.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime_ns)
    return files[-limit:]


def collect_pending(output_dir: Path, state: dict[str, Any], limit: int) -> list[tuple[Path, dict[str, Any], str]]:
    sent = state.setdefault("sent", {})
    pending: list[tuple[Path, dict[str, Any], str]] = []
    for path in recent_output_files(output_dir, limit):
        try:
            text = notification_from_output(path)
            if not text:
                continue
            fingerprint = file_fingerprint(path)
            key = notification_key(fingerprint)
            if key in sent:
                continue
            pending.append((path, fingerprint, text))
        except Exception as exc:
            state.setdefault("scan_errors", []).append({"file": str(path), "error": str(exc), "at": int(time.time())})
    return pending


def bootstrap_existing(output_dir: Path, state_path: Path, limit: int) -> int:
    state = {
        "bootstrapped_at": int(time.time()),
        "sent": {},
    }
    count = 0
    for path in recent_output_files(output_dir, limit):
        try:
            if not notification_from_output(path):
                continue
            fingerprint = file_fingerprint(path)
            state["sent"][notification_key(fingerprint)] = {
                **fingerprint,
                "bootstrapped": True,
                "sent_at": int(time.time()),
            }
            count += 1
        except Exception:
            continue
    save_json(state_path, state)
    return count


def read_secret(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not value:
        raise RuntimeError(f"notify secret missing: {path}")
    return value


def post_notify(url: str, secret: str, group_id: str, text: str, timeout: int) -> dict[str, Any]:
    payload = {"group_id": group_id, "text": text}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Hermes-Coder-Signature": signature,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    try:
        parsed = json.loads(raw) if raw else {}
    except Exception:
        parsed = {"error": raw[:500]}
    parsed.setdefault("http_status", status)
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=os.environ.get("PAOTUAN_CRON_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    parser.add_argument("--state-path", default=os.environ.get("PAOTUAN_NOTIFY_STATE_PATH", str(DEFAULT_STATE_PATH)))
    parser.add_argument("--secret-path", default=os.environ.get("HERMES_CODER_BRIDGE_SECRET_PATH", str(DEFAULT_SECRET_PATH)))
    parser.add_argument("--notify-url", default=os.environ.get("PAOTUAN_NOTIFY_URL", DEFAULT_NOTIFY_URL))
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--scan-limit", type=int, default=int(os.environ.get("PAOTUAN_NOTIFY_SCAN_LIMIT", str(MAX_SCAN_FILES))))
    parser.add_argument("--timeout-seconds", type=int, default=int(os.environ.get("PAOTUAN_NOTIFY_TIMEOUT_SECONDS", "15")))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    state_path = Path(args.state_path)
    if not state_path.exists():
        count = bootstrap_existing(output_dir, state_path, args.scan_limit)
        print(f"Bootstrapped {count} existing cron output(s); future outputs will notify.")
        print(json.dumps({"wakeAgent": False, "bootstrapped": count}, ensure_ascii=False))
        return 0

    state = load_json(state_path, {"sent": {}})
    pending = collect_pending(output_dir, state, args.scan_limit)
    if not pending:
        state["last_checked_at"] = int(time.time())
        save_json(state_path, state)
        print("No pending Paotuan cron notifications.")
        print(json.dumps({"wakeAgent": False, "sent": 0}, ensure_ascii=False))
        return 0

    secret = "" if args.dry_run else read_secret(Path(args.secret_path))
    sent_count = 0
    errors: list[dict[str, Any]] = []
    for path, fingerprint, text in pending:
        try:
            if args.dry_run:
                result = {"ok": True, "dry_run": True}
            else:
                result = post_notify(args.notify_url, secret, str(args.group_id), text, args.timeout_seconds)
            if result.get("ok") is not True:
                errors.append({"file": path.name, "error": result.get("error") or result})
                continue
            state.setdefault("sent", {})[notification_key(fingerprint)] = {
                **fingerprint,
                "sent_at": int(time.time()),
                "group_id": str(args.group_id),
            }
            sent_count += 1
        except Exception as exc:
            errors.append({"file": path.name, "error": str(exc)})

    state["last_checked_at"] = int(time.time())
    state["last_errors"] = errors[-10:]
    save_json(state_path, state)
    print(f"Sent {sent_count} Paotuan cron notification(s).")
    if errors:
        print(json.dumps({"notify_errors": errors[-5:]}, ensure_ascii=False))
    print(json.dumps({"wakeAgent": False, "sent": sent_count, "errors": len(errors)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
