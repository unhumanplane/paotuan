from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.models import GameSession, utc_now_iso


class JsonGameRepository:
    def __init__(
        self,
        data_dir: Path,
        max_audit_bytes: int = 5_000_000,
        max_audit_backups: int = 3,
    ):
        self.data_dir = data_dir
        self.saves_dir = data_dir / "saves"
        self.audit_dir = data_dir / "audit"
        self.max_audit_bytes = max_audit_bytes
        self.max_audit_backups = max_audit_backups
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def load_session(self, session_id: str) -> GameSession:
        path = self._session_path(session_id)
        if not path.exists():
            return GameSession.new(session_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("session_id"):
            data["session_id"] = session_id
        return GameSession.from_dict(data)

    def save_session(self, session: GameSession, *, create_backup: bool = False, backup_reason: str = "") -> None:
        session.updated_at = utc_now_iso()
        path = self._session_path(session.session_id)
        if create_backup:
            self.backup_session(session.session_id, backup_reason or "before_save")
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def backup_session(self, session_id: str, reason: str = "", max_backups: int = 30) -> Path | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        backup_dir = self._backup_dir(session_id)
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_dir / f"{timestamp}.json"
        shutil.copy2(path, backup_path)
        meta = {
            "session_id": session_id,
            "source": str(path),
            "backup": str(backup_path),
            "reason": reason,
            "created_at": utc_now_iso(),
        }
        backup_path.with_suffix(".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._rotate_save_backups(backup_dir, max_backups=max_backups)
        return backup_path

    def list_session_backups(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        backup_dir = self._backup_dir(session_id)
        if not backup_dir.exists():
            return []
        paths = sorted(
            (item for item in backup_dir.glob("*.json") if not item.name.endswith(".meta.json")),
            key=lambda item: (item.stat().st_mtime, item.name),
            reverse=True,
        )
        backups: list[dict[str, Any]] = []
        for path in paths[: max(1, limit)]:
            meta_path = path.with_suffix(".meta.json")
            meta: dict[str, Any] = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    meta = {}
            backups.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                    "reason": str(meta.get("reason", "")),
                    "created_at": str(meta.get("created_at", "")),
                }
            )
        return backups

    def restore_session_backup(self, session_id: str, backup_path: str | Path) -> Path:
        source = Path(backup_path)
        if not source.exists():
            raise FileNotFoundError(str(source))
        backup_dir = self._backup_dir(session_id).resolve()
        resolved_source = source.resolve()
        if backup_dir not in resolved_source.parents:
            raise ValueError("backup_path_outside_session_backup_dir")
        target = self._session_path(session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved_source, target)
        return target

    def append_audit(self, session_id: str, record: dict[str, Any]) -> None:
        path = self.audit_dir / f"{self._safe_name(session_id)}.jsonl"
        self._rotate_audit_if_needed(path)
        record = {"at": utc_now_iso(), **record}
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def audit_path(self, session_id: str) -> Path:
        return self.audit_dir / f"{self._safe_name(session_id)}.jsonl"

    def plugin_log_path(self) -> Path:
        return self.data_dir / "logs" / "auto_trpg_dm.log"

    def maps_dir(self) -> Path:
        path = self.data_dir / "maps"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ambient_images_dir(self) -> Path:
        path = self.data_dir / "ambient_images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_path(self, session_id: str) -> Path:
        return self._session_path(session_id)

    def last_audit_records(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        path = self.audit_path(session_id)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, limit) :]
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def _session_path(self, session_id: str) -> Path:
        return self.saves_dir / f"{self._safe_name(session_id)}.json"

    def _backup_dir(self, session_id: str) -> Path:
        return self.data_dir / "save_backups" / self._safe_name(session_id)

    def _rotate_audit_if_needed(self, path: Path) -> None:
        if self.max_audit_bytes <= 0 or not path.exists():
            return
        if path.stat().st_size < self.max_audit_bytes:
            return
        for index in range(self.max_audit_backups, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            dst = path.with_name(f"{path.name}.{index + 1}")
            if index == self.max_audit_backups and src.exists():
                src.unlink()
            elif src.exists():
                src.replace(dst)
        path.replace(path.with_name(f"{path.name}.1"))

    @staticmethod
    def _rotate_save_backups(backup_dir: Path, max_backups: int) -> None:
        if max_backups <= 0:
            return
        backups = sorted(
            (item for item in backup_dir.glob("*.json") if not item.name.endswith(".meta.json")),
            key=lambda item: item.name,
            reverse=True,
        )
        for stale in backups[max_backups:]:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".meta.json").unlink(missing_ok=True)

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)
        return safe.strip("._") or "default"
