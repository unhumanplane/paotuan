#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPS = Path('/volume1/docker/hermes/paotuan')
STATE_PATH = OPS / 'monitor_state.json'
DEPLOY_STATE_PATH = OPS / 'state.json'
REPORT_PATH = OPS / 'reports' / 'latest.md'
HISTORY_PATH = OPS / 'reports' / 'history.jsonl'
REPO_API = 'https://api.github.com/repos/unhumanplane/paotuan'
DATA = Path(os.environ.get('HERMES_HOME') or '/volume1/docker/hermes/data')
if not (DATA / 'cron').exists():
    DATA = Path(__file__).resolve().parents[1]
CRON_JOBS_PATH = DATA / 'cron' / 'jobs.json'
CRON_OUTPUT_ROOT = DATA / 'cron' / 'output'
JOB_NAME_HINT = 'paotuan repo steward'
DEFAULT_NOTIFY_URL = os.environ.get('PAOTUAN_NOTIFY_URL', 'http://127.0.0.1:8767/notify')
DEFAULT_NOTIFY_GROUP_ID = os.environ.get('PAOTUAN_NOTIFY_GROUP_ID', '1101538762')
DEFAULT_NOTIFY_SECRET_PATH = Path(
    os.environ.get('HERMES_CODER_BRIDGE_SECRET_PATH') or OPS / 'secrets' / 'coder_bridge_secret'
)
DIRECT_NOTIFY_ENABLED = os.environ.get('PAOTUAN_STEWARD_DIRECT_NOTIFY', '1').lower() not in {
    '0',
    'false',
    'no',
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def read_tail(path: Path, limit: int = 7000) -> str:
    if not path.exists():
        return ''
    data = path.read_bytes()
    return data[-limit:].decode('utf-8', errors='replace')


def latest_history() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {}
    last = ''
    try:
        for line in HISTORY_PATH.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.strip():
                last = line.strip()
        return json.loads(last) if last else {}
    except Exception:
        return {}


def gh_get(path: str) -> Any:
    req = urllib.request.Request(
        REPO_API + path,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'paotuan-hermes-steward',
            'X-GitHub-Api-Version': '2022-11-28',
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))


def fetch_prs() -> tuple[list[dict[str, Any]], str | None]:
    try:
        prs = gh_get('/pulls?state=all&sort=updated&direction=desc&per_page=10')
    except urllib.error.HTTPError as e:
        return [], f'GitHub PR API HTTP {e.code}'
    except Exception as e:
        return [], f'GitHub PR API error: {type(e).__name__}: {e}'
    normalized = []
    for pr in prs:
        normalized.append({
            'number': pr.get('number'),
            'title': pr.get('title') or '',
            'state': pr.get('state') or '',
            'draft': bool(pr.get('draft')),
            'merged_at': pr.get('merged_at'),
            'updated_at': pr.get('updated_at') or '',
            'head': ((pr.get('head') or {}).get('sha') or '')[:12],
            'base': ((pr.get('base') or {}).get('ref') or ''),
            'user': ((pr.get('user') or {}).get('login') or ''),
            'url': pr.get('html_url') or '',
        })
    return normalized, None




def is_transient_git_remote_failure(context: dict[str, Any], history: dict[str, Any], deploy_state: dict[str, Any]) -> bool:
    if str(history.get('status') or '') != 'failed':
        return False
    combined = '\n'.join(str(context.get(k, '')) for k in ('deploy_output_tail', 'latest_report_tail'))
    combined += '\n' + str(history.get('error') or '')
    if 'git ls-remote' not in combined and 'Empty reply from server' not in combined:
        return False
    return bool(deploy_state.get('last_deployed_commit')) and not bool(deploy_state.get('restart_pending'))


def short_commit(value: Any) -> str:
    text = str(value or '').strip()
    return text[:12] if text else 'unknown'


def _match_first(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.M)
        if match:
            return ' '.join(match.group(1).strip().split())
    return ''


def commit_subject_from_report(report: str) -> str:
    return _match_first(
        [
            r'checked out [0-9a-f]{7,40}\s+(.+)$',
            r'HEAD is now at [0-9a-f]{7,40}\s+(.+)$',
        ],
        report,
    )


def pytest_summary_from_report(report: str) -> str:
    return _match_first([r'(\d+\s+passed(?:,\s*\d+\s+\w+)?\s+in\s+[0-9.]+s)'], report)


def build_deploy_success_notification(history: dict[str, Any], latest_report: str) -> str:
    commit = short_commit(history.get('commit'))
    subject = commit_subject_from_report(latest_report)
    title = f'main 已部署到 NAS：{commit}'
    if subject:
        title += f'（{subject}）'

    checks = []
    if re.search(r'OK\s+compileall', latest_report):
        checks.append('compileall 通过')
    pytest_summary = pytest_summary_from_report(latest_report)
    if pytest_summary:
        checks.append(f'pytest {pytest_summary}')
    if 'AstrBot plugin reload API returned status=ok' in latest_report:
        checks.append('AstrBot Dashboard 热加载成功')
    if 'plugin log confirmed hot reload' in latest_report or 'plugin_initialized' in latest_report:
        checks.append('插件独立日志已确认初始化')

    lines = ['【Paotuan 部署成功】', title]
    if checks:
        lines.append('验证：' + '；'.join(checks) + '。')
    lines.append('说明：本次未重启 AstrBot 容器。')
    return '\n'.join(lines)


def build_pr_monitor_error_notification(error: str) -> str:
    return '\n'.join(
        [
            '【Paotuan PR 监控异常】',
            f'GitHub PR API 暂时不可用：{error}',
            '部署轮询会继续按计划重试；如果连续出现，再检查 NAS 到 GitHub 的网络或代理。',
        ]
    )


def read_notify_secret(path: Path = DEFAULT_NOTIFY_SECRET_PATH) -> str:
    value = path.read_text(encoding='utf-8').strip() if path.exists() else ''
    if not value:
        raise RuntimeError(f'notify secret missing: {path}')
    return value


def post_notify(
    text: str,
    *,
    url: str = DEFAULT_NOTIFY_URL,
    group_id: str = DEFAULT_NOTIFY_GROUP_ID,
    secret_path: Path = DEFAULT_NOTIFY_SECRET_PATH,
    timeout: int = 15,
) -> dict[str, Any]:
    secret = read_notify_secret(secret_path)
    payload = {'group_id': str(group_id), 'text': text}
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Hermes-Coder-Signature': signature,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        status = exc.code
    try:
        parsed = json.loads(raw) if raw else {}
    except Exception:
        parsed = {'error': raw[:500]}
    parsed.setdefault('http_status', status)
    return parsed


def send_direct_notification(text: str, context: dict[str, Any], kind: str) -> bool:
    if not DIRECT_NOTIFY_ENABLED:
        context.setdefault('direct_notifications', []).append({'kind': kind, 'ok': False, 'disabled': True})
        return False
    try:
        result = post_notify(text)
        ok = result.get('ok') is True
        context.setdefault('direct_notifications', []).append(
            {
                'kind': kind,
                'ok': ok,
                'http_status': result.get('http_status'),
                'error': result.get('error') if not ok else None,
            }
        )
        return ok
    except Exception as exc:
        context.setdefault('direct_notifications', []).append(
            {'kind': kind, 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        )
        return False


def is_open_pr(pr: dict[str, Any]) -> bool:
    return pr.get('state') == 'open' and not pr.get('merged_at')


def reportable_pr_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [pr for pr in changes if is_open_pr(pr)]


def summarize_pr_changes(prs: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    old = state.get('prs', {}) if isinstance(state.get('prs'), dict) else {}
    changes = []
    current = {}
    for pr in prs:
        num = str(pr.get('number'))
        sig = '|'.join(str(pr.get(k, '')) for k in ('state', 'draft', 'merged_at', 'updated_at', 'head'))
        current[num] = sig
        if old.get(num) != sig:
            changes.append(pr)
    state['prs'] = current
    return changes


def current_cron_job_id() -> str | None:
    jobs_doc = load_json(CRON_JOBS_PATH, {})
    jobs = jobs_doc.get('jobs', []) if isinstance(jobs_doc, dict) else []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        script = str(job.get('script') or '')
        name = str(job.get('name') or '')
        if Path(script).name == 'paotuan_poll.py' or JOB_NAME_HINT in name:
            job_id = str(job.get('id') or '')
            if job_id:
                return job_id
    return None


def _reported_pr_numbers_from_text(text: str) -> set[str]:
    """Extract PR numbers from the human response area of a saved cron output.

    Saved cron outputs include the prompt, which may contain PR metadata that was
    never actually reviewed or delivered. Only count numbers in the response area.
    """
    if '## Response' in text:
        text = text.split('## Response', 1)[1]
    elif '## Error' in text:
        return set()
    found: set[str] = set()
    for start, end in re.findall(r'PR\s*#\s*(\d+)\s*[-~～—–]\s*#?\s*(\d+)', text, flags=re.I):
        try:
            a, b = int(start), int(end)
        except ValueError:
            continue
        if a <= b and b - a <= 50:
            found.update(str(n) for n in range(a, b + 1))
    for num in re.findall(r'PR\s*#\s*(\d+)', text, flags=re.I):
        found.add(str(int(num)))
    return found


def reported_pr_numbers(limit: int = 200) -> set[str]:
    job_id = current_cron_job_id()
    if not job_id:
        return set()
    output_dir = CRON_OUTPUT_ROOT / job_id
    if not output_dir.exists():
        return set()
    try:
        outputs = sorted(output_dir.glob('*.md'), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except Exception:
        return set()
    reported: set[str] = set()
    for path in outputs:
        try:
            reported.update(_reported_pr_numbers_from_text(path.read_text(encoding='utf-8', errors='replace')))
        except Exception:
            continue
    return reported


def find_unreported_open_prs(prs: list[dict[str, Any]], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover PRs that entered monitor state but never got a saved review.

    This covers restarts/timeouts where summarize_pr_changes() persisted the PR
    signature before the agent successfully produced/delivered a report. New or
    updated PRs in *changes* are already reported via pr_changes and are skipped
    here to avoid duplicates in the same run.
    """
    changed = {str(pr.get('number')) for pr in changes}
    reported = reported_pr_numbers()
    pending = []
    for pr in prs:
        num = str(pr.get('number'))
        if not num or num in changed or num in reported:
            continue
        if pr.get('state') == 'open':
            pending.append(pr)
    return pending


def main() -> int:
    OPS.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {})
    events: list[str] = []
    context: dict[str, Any] = {'checked_at': now()}

    # Deterministic deploy poll first. Exit 75 means lock/no-op-ish; keep monitoring alive.
    cmd = [str(OPS / 'bin' / 'paotuan_deploy.py'), '--poll', '--reason', 'hermes-cron']
    deploy_env = dict(os.environ)
    # Hermes cron pre-run scripts have their own outer timeout. Keep the GitHub
    # remote probe short here so transient GitHub stalls degrade to skipped
    # inside paotuan_deploy.py instead of paotuan_poll.py being killed.
    deploy_env.setdefault(
        'PATH',
        '/volume1/docker/hermes/install/hermes-agent/venv/bin:'
        '/volume1/docker/hermes/home/.local/bin:'
        '/volume1/docker/hermes/data/node/bin:'
        '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
    )
    deploy_env.setdefault('PAOTUAN_GIT_BIN', '/usr/bin/git')
    deploy_env.setdefault('PAOTUAN_GIT_REMOTE_ATTEMPTS', '1')
    deploy_env.setdefault('PAOTUAN_GIT_REMOTE_TIMEOUT', '20')
    deploy_env.setdefault('PAOTUAN_RESTART_TIMEOUT', '90')
    deploy_env.setdefault('PAOTUAN_DOCKER_RESTART_STOP_TIMEOUT', '20')
    deploy_env.setdefault('PAOTUAN_DOCKER_STATUS_TIMEOUT', '10')
    try:
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=130, env=deploy_env)
        deploy_output = proc.stdout or ''
        context['deploy_exit_code'] = proc.returncode
        context['deploy_output_tail'] = deploy_output[-5000:]
    except Exception as e:
        proc = None
        context['deploy_exit_code'] = 1
        context['deploy_output_tail'] = f'poll script exception: {type(e).__name__}: {e}'
        events.append('deploy_poll_exception')

    deploy_state = load_json(DEPLOY_STATE_PATH, {})
    history = latest_history()
    latest_report = read_tail(REPORT_PATH, 7000)
    context['deploy_state'] = deploy_state
    context['latest_history'] = history
    context['latest_report_tail'] = latest_report

    last_history_key = state.get('last_history_key')
    hist_key = None
    if history:
        hist_key = '|'.join(str(history.get(k, '')) for k in ('status', 'commit', 'finished_at', 'reason'))
        if hist_key != last_history_key:
            status = str(history.get('status') or 'unknown')
            # Notify on actual success/failure. Skipped reports are noisy unless there was an error in the poll itself.
            if status == 'failed' and is_transient_git_remote_failure(context, history, deploy_state):
                context['deploy_transient_network_failure_suppressed'] = True
            elif status == 'success':
                text = build_deploy_success_notification(history, latest_report)
                if send_direct_notification(text, context, 'deploy_success'):
                    context['deploy_direct_notification'] = text
                else:
                    events.append('deploy_success')
            elif status == 'failed':
                events.append('deploy_failed')
            elif status == 'skipped' and context.get('deploy_exit_code') not in (0, 75):
                events.append('deploy_poll_nonzero')
            state['last_history_key'] = hist_key

    prs, pr_error = fetch_prs()
    context['prs'] = prs
    if pr_error:
        context['pr_error'] = pr_error
        if state.get('last_pr_error') != pr_error:
            text = build_pr_monitor_error_notification(pr_error)
            if send_direct_notification(text, context, 'pr_monitor_error'):
                context['pr_monitor_direct_notification'] = text
            else:
                events.append('pr_monitor_error')
            state['last_pr_error'] = pr_error
    else:
        state.pop('last_pr_error', None)
        pr_changes = summarize_pr_changes(prs, state)
        open_pr_changes = reportable_pr_changes(pr_changes)
        if open_pr_changes:
            context['pr_changes'] = open_pr_changes
            events.append('pr_changed')
        pending_unreported = find_unreported_open_prs(prs, pr_changes)
        if pending_unreported:
            context['pending_unreported_prs'] = pending_unreported
            events.append('pr_unreported_pending')

    state['last_checked_at'] = context['checked_at']
    save_json(STATE_PATH, state)

    print('Paotuan steward poll context:')
    print(json.dumps(context, ensure_ascii=False, indent=2)[:14000])

    should_wake = bool(events)
    if should_wake:
        print('Events:', ', '.join(events))
    else:
        print('No meaningful PR or deploy event. Stay silent.')
    print(json.dumps({'wakeAgent': should_wake, 'events': events}, ensure_ascii=False))

    # Keep cron alive on deploy skip/lock. Real nonzero deploy events should wake the agent and be visible in context.
    code = int(context.get('deploy_exit_code') or 0)
    return 0 if code in (0, 75) else code


if __name__ == '__main__':
    raise SystemExit(main())
