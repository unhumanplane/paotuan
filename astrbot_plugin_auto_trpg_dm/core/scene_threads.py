from __future__ import annotations

from collections.abc import Mapping
from typing import Any


THREAD_REOPEN_STATUSES = {"active", "open", "reopened"}
THREAD_ACTIVE_STATUSES = {"", "active", "open", "reopened"}
THREAD_SOFT_EXIT_STATUSES = {"blocked", "deferred"}
THREAD_CLOSED_STATUSES = {"resolved", "failed_forward", "archived", "closed", "retired"}
THREAD_TERMINAL_STATUSES = {"retired"}
# Threads in these states should not remain the current focus.
THREAD_LEAVE_FOCUS_STATUSES = THREAD_CLOSED_STATUSES | THREAD_SOFT_EXIT_STATUSES
THREAD_OBSOLETE_STATUSES = THREAD_CLOSED_STATUSES | {
    "inactive",
    "completed",
    "complete",
    "done",
    "cancelled",
    "canceled",
    "superseded",
}


def normalize_thread_status(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "failure_forward": "failed_forward",
        "fail_forward": "failed_forward",
        "failed": "failed_forward",
        "paused": "deferred",
        "blocked_waiting": "blocked",
        "complete": "resolved",
        "completed": "resolved",
        "done": "resolved",
        "cancelled": "archived",
        "canceled": "archived",
    }
    return aliases.get(text, text or default)


def thread_status(thread: Mapping[str, Any] | None, *, default: str = "") -> str:
    if not isinstance(thread, Mapping):
        return default
    return normalize_thread_status(thread.get("status"), default=default)


def thread_is_active(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_ACTIVE_STATUSES


def thread_is_reopen_status(value: Any) -> bool:
    return normalize_thread_status(value) in THREAD_REOPEN_STATUSES


def thread_is_soft_exit(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_SOFT_EXIT_STATUSES


def thread_is_closed(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_CLOSED_STATUSES


def thread_is_terminal(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_TERMINAL_STATUSES


def thread_is_obsolete(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_OBSOLETE_STATUSES


def thread_can_leave_focus(thread: Mapping[str, Any] | None) -> bool:
    return thread_status(thread) in THREAD_LEAVE_FOCUS_STATUSES
