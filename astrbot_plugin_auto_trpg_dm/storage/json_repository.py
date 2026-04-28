from __future__ import annotations

import json
import re
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

    def save_session(self, session: GameSession) -> None:
        session.updated_at = utc_now_iso()
        path = self._session_path(session.session_id)
        path.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value)
        return safe.strip("._") or "default"
