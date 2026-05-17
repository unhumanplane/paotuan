from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import GameSession
from .prompt_projection import project_dm_prompt_value
from .scene_hooks import format_scene_tracking_status, project_visible_scene_value
from .timeline import timeline_status_text, timeline_view


DEFAULT_ROUTE_PREFIXES = ("/auto_trpg_dm", "/astrbot_plugin_auto_trpg_dm")


class AutoTrpgAdminWeb:
    """Read-only Dashboard API for inspecting live TRPG DM state."""

    def __init__(self, repository: Any):
        self.repository = repository

    def register_routes(self, context: Any, route_prefixes: tuple[str, ...] = DEFAULT_ROUTE_PREFIXES) -> int:
        if not hasattr(context, "register_web_api"):
            return 0
        routes = [
            ("dm/web/status", self.get_status, ["GET"], "Auto TRPG DM status"),
            ("dm/web/sessions", self.get_sessions, ["GET"], "Auto TRPG DM sessions"),
            (
                "dm/web/sessions/<session_key>/snapshot",
                self.get_session_snapshot,
                ["GET"],
                "Auto TRPG DM session snapshot",
            ),
            (
                "dm/web/sessions/<session_key>/audit",
                self.get_session_audit,
                ["GET"],
                "Auto TRPG DM session audit",
            ),
            (
                "dm/web/sessions/<session_key>/backups",
                self.get_session_backups,
                ["GET"],
                "Auto TRPG DM session backups",
            ),
        ]
        registered = 0
        for prefix in route_prefixes:
            normalized_prefix = "/" + str(prefix or "").strip("/")
            for suffix, handler, methods, description in routes:
                context.register_web_api(f"{normalized_prefix}/{suffix}", handler, methods, description)
                registered += 1
        return registered

    async def get_status(self, **_kwargs: Any) -> dict[str, Any]:
        sessions = self._list_session_records()
        active_battles = sum(1 for item in sessions if item["summary"].get("battle_active"))
        return _json_ok(
            {
                "session_count": len(sessions),
                "active_battle_count": active_battles,
                "audit_file_count": _count_files(getattr(self.repository, "audit_dir", None), "*.jsonl*"),
                "backup_file_count": _count_files(getattr(self.repository, "data_dir", Path()), "save_backups/**/*.json"),
                "plugin_log_available": self.repository.plugin_log_path().exists(),
            }
        )

    async def get_sessions(self, **_kwargs: Any) -> dict[str, Any]:
        return _json_ok([item["summary"] for item in self._list_session_records()])

    async def get_session_snapshot(self, session_key: str = "", **_kwargs: Any) -> dict[str, Any]:
        record = self._session_record(session_key)
        if not record:
            return _json_error("session_not_found", 404)
        session = record["session"]
        snapshot = _project_session_snapshot(session)
        snapshot["session_key"] = record["key"]
        return _json_ok(snapshot)

    async def get_session_audit(self, session_key: str = "", **_kwargs: Any) -> dict[str, Any]:
        record = self._session_record(session_key)
        if not record:
            return _json_error("session_not_found", 404)
        session = record["session"]
        records = self.repository.last_audit_records(session.session_id, limit=40)
        return _json_ok([_project_audit_record(item) for item in records[-40:]])

    async def get_session_backups(self, session_key: str = "", **_kwargs: Any) -> dict[str, Any]:
        record = self._session_record(session_key)
        if not record:
            return _json_error("session_not_found", 404)
        session = record["session"]
        backups = self.repository.list_session_backups(session.session_id, limit=20)
        return _json_ok([_project_backup_item(item) for item in backups])

    def _session_record(self, session_key: str) -> dict[str, Any] | None:
        key = _safe_session_key(session_key)
        for record in self._list_session_records():
            if record["key"] == key:
                return record
        return None

    def _list_session_records(self) -> list[dict[str, Any]]:
        saves_dir = getattr(self.repository, "saves_dir", None)
        if not isinstance(saves_dir, Path) or not saves_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(saves_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    continue
                if not data.get("session_id"):
                    data["session_id"] = path.stem
                session = GameSession.from_dict(data)
            except Exception:
                continue
            summary = _project_session_summary(session, path)
            records.append({"key": path.stem, "path": path, "session": session, "summary": summary})
        return records


def _json_ok(data: Any = None, message: str = "") -> Any:
    return _json_response({"status": "ok", "data": data, "message": message})


def _json_error(message: str, code: int = 400) -> Any:
    return _json_response({"status": "error", "data": None, "message": message}, code=code)


def _json_response(payload: dict[str, Any], code: int = 200) -> Any:
    try:
        from quart import jsonify

        response = jsonify(payload)
        response.status_code = code
        return response
    except Exception:
        return payload


def _project_session_summary(session: GameSession, path: Path) -> dict[str, Any]:
    scene = session.scene or {}
    battle = session.compact_snapshot().get("battle", {})
    return {
        "session_key": path.stem,
        "session_id": session.session_id,
        "title": session.title,
        "mode": session.mode.value,
        "cycle_state": session.cycle_state.value,
        "timeline": timeline_view(session.timeline),
        "timeline_text": timeline_status_text(session.timeline),
        "updated_at": session.updated_at,
        "created_at": session.created_at,
        "character_count": len(session.characters),
        "participant_count": len(session.participants),
        "battle_active": bool((session.battle or {}).get("active")),
        "current_cycle_id": session.current_cycle_id,
        "current_objective": _compact_text(scene.get("current_objective") or scene.get("summary") or "", 220),
        "battle_turn": (battle.get("turn") or {}) if isinstance(battle, dict) else {},
    }


def _project_session_snapshot(session: GameSession) -> dict[str, Any]:
    snapshot = session.compact_snapshot()
    scene = snapshot.get("scene") if isinstance(snapshot.get("scene"), dict) else {}
    world_tags = snapshot.get("world_tags") if isinstance(snapshot.get("world_tags"), dict) else {}
    visible_scene = project_visible_scene_value(scene, depth=4, text_limit=360, item_limit=20) or {}
    visible_world = project_dm_prompt_value(world_tags, depth=4, text_limit=360)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "mode": session.mode.value,
        "cycle_state": session.cycle_state.value,
        "timeline": timeline_view(session.timeline),
        "timeline_text": timeline_status_text(session.timeline),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "characters": snapshot.get("characters", []),
        "participants": snapshot.get("participants", []),
        "player_character_map": snapshot.get("player_character_map", {}),
        "visible_world_tags": visible_world if isinstance(visible_world, dict) else {},
        "visible_scene": visible_scene if isinstance(visible_scene, dict) else {},
        "scene_tracking_status": format_scene_tracking_status(scene),
        "memory_summary": _compact_text(snapshot.get("memory_summary") or "", 1200),
        "rules": snapshot.get("rules", []),
        "rule_sets": project_dm_prompt_value(snapshot.get("rule_sets", {}), depth=3, text_limit=320),
        "battle": snapshot.get("battle", {}),
        "current_cycle_id": session.current_cycle_id,
        "environment_summaries": project_dm_prompt_value(
            snapshot.get("environment_summaries", []),
            depth=4,
            text_limit=420,
        ),
    }


def _project_audit_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    projected = project_dm_prompt_value(record, depth=5, text_limit=600)
    projected = projected if isinstance(projected, dict) else {}
    return {
        "at": str(record.get("at", "")),
        "type": str(record.get("type", "")),
        "tool": str(record.get("tool", "")),
        "action": str(record.get("action", "")),
        "ok": _extract_ok(record),
        "error": _extract_error(record),
        "message": _extract_message(record),
        "record": projected,
    }


def _project_backup_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {
        "name": str(item.get("name", "")),
        "size": item.get("size", 0),
        "mtime": str(item.get("mtime", "")),
        "created_at": str(item.get("created_at", "")),
        "reason": _compact_text(item.get("reason") or "", 240),
    }


def _extract_ok(record: dict[str, Any]) -> bool | None:
    for value in (record.get("result"), record):
        if isinstance(value, dict) and isinstance(value.get("ok"), bool):
            return value.get("ok")
    return None


def _extract_error(record: dict[str, Any]) -> str:
    for value in (record.get("result"), record):
        if isinstance(value, dict) and value.get("error"):
            return _compact_text(value.get("error"), 160)
    return ""


def _extract_message(record: dict[str, Any]) -> str:
    for value in (record.get("result"), record):
        if isinstance(value, dict) and value.get("message"):
            return _compact_text(value.get("message"), 260)
    return ""


def _count_files(root: Any, pattern: str) -> int:
    if not isinstance(root, Path) or not root.exists():
        return 0
    return sum(1 for item in root.glob(pattern) if item.is_file())


def _safe_session_key(value: str) -> str:
    return Path(str(value or "").strip()).name


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
