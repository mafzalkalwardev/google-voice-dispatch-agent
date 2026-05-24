from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from src.paths import runtime_base

if TYPE_CHECKING:
    from src.call_session import CallSession

CALL_LOG_FILE = runtime_base() / "logs" / "call_logs.csv"
CALL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

_HEADERS = [
    "timestamp",
    "phone",
    "name",
    "status",
    "outcome",
    "started_at",
    "connected_at",
    "voicemail_detected_at",
    "ended_at",
    "connected_duration_s",
    "total_duration_s",
    "notes",
]


class CallLogger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CALL_LOG_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_header()

    def _write_header(self) -> None:
        with open(self.path, mode="w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(_HEADERS)

    def log(self, phone: str, name: str, status: str, notes: str = "") -> None:
        """Simple one-liner log (backward compatible)."""
        row = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phone": phone,
            "name": name,
            "status": status,
            "outcome": status,
            "started_at": "",
            "connected_at": "",
            "voicemail_detected_at": "",
            "ended_at": "",
            "connected_duration_s": "",
            "total_duration_s": "",
            "notes": notes,
        }
        self._append(row)

    def log_session(self, session: "CallSession", notes: str = "") -> None:
        """Full structured log from a completed CallSession."""
        d = session.to_log_dict()
        row = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "phone": d["phone"],
            "name": d["contact_name"],
            "status": d["state"],
            "outcome": d["outcome"] or d["state"],
            "started_at": d["started_at"],
            "connected_at": d["connected_at"],
            "voicemail_detected_at": d["voicemail_detected_at"],
            "ended_at": d["ended_at"],
            "connected_duration_s": d["connected_duration_s"] or "",
            "total_duration_s": d["total_duration_s"] or "",
            "notes": (d["notes"] + " | " + notes).strip(" |") if notes else d["notes"],
        }
        self._append(row)

    def _append(self, row: dict) -> None:
        with open(self.path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_HEADERS)
            writer.writerow(row)
