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

_LEGACY_HEADERS = {"timestamp", "phone", "name", "status", "notes"}


def _row_from_values(values: list[str]) -> dict:
    row = {key: "" for key in _HEADERS}
    for index, key in enumerate(_HEADERS):
        if index < len(values):
            row[key] = values[index]
    return row


def ensure_call_log_header(path: Path | None = None) -> None:
    """Upgrade legacy 5-column call_logs.csv to the full header row."""
    log_path = Path(path) if path else CALL_LOG_FILE
    if not log_path.exists():
        return
    text = log_path.read_text(encoding="utf-8")
    if not text.strip():
        return
    first_line = text.splitlines()[0]
    reader = csv.reader([first_line])
    header = next(reader, [])
    if set(h.strip() for h in header if h.strip()) >= set(_HEADERS):
        return
    if set(h.strip() for h in header if h.strip()) != _LEGACY_HEADERS and len(header) >= len(_HEADERS):
        return

    rows: list[dict] = []
    with log_path.open(newline="", encoding="utf-8") as f:
        raw = list(csv.reader(f))
    if not raw:
        return
    data_rows = raw[1:]
    for values in data_rows:
        if not values or not any(v.strip() for v in values):
            continue
        if len(values) >= len(_HEADERS):
            rows.append(_row_from_values(values))
        elif len(values) >= 5:
            rows.append({
                "timestamp": values[0],
                "phone": values[1],
                "name": values[2],
                "status": values[3],
                "outcome": values[4] if len(values) > 4 else values[3],
                "started_at": "",
                "connected_at": "",
                "voicemail_detected_at": "",
                "ended_at": "",
                "connected_duration_s": "",
                "total_duration_s": "",
                "notes": values[4] if len(values) == 5 else " | ".join(values[4:]),
            })
    with log_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def read_call_logs(limit: int | None = 200, path: Path | None = None) -> list[dict]:
    """Read call logs newest-first with correct columns even for legacy files."""
    log_path = Path(path) if path else CALL_LOG_FILE
    if not log_path.exists():
        return []
    ensure_call_log_header(log_path)
    rows: list[dict] = []
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = set(reader.fieldnames or [])
            if fieldnames >= set(_HEADERS):
                for row in reader:
                    rows.append({key: row.get(key, "") for key in _HEADERS})
            else:
                f.seek(0)
                raw = list(csv.reader(f))
                for values in raw[1:]:
                    if values:
                        rows.append(_row_from_values(values))
    except Exception:
        return []
    rows.reverse()
    if limit is not None:
        rows = rows[:limit]
    return rows


def call_log_stats(path: Path | None = None) -> dict:
    rows = read_call_logs(limit=None, path=path)
    total = len(rows)
    connected = 0
    voicemail = 0
    for row in rows:
        notes = (row.get("notes") or "").lower()
        if row.get("voicemail_detected_at") or "voicemail" in notes:
            voicemail += 1
        elif row.get("connected_at"):
            connected += 1
    return {"total": total, "connected": connected, "voicemail": voicemail}


class CallLogger:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CALL_LOG_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_header()
        else:
            ensure_call_log_header(self.path)

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
        ensure_call_log_header(self.path)
        with open(self.path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_HEADERS)
            writer.writerow(row)
