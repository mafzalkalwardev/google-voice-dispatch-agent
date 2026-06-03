"""Tests for call_logs.csv read/migrate helpers."""
from pathlib import Path

from src.call_log import (
    _HEADERS,
    call_log_stats,
    ensure_call_log_header,
    read_call_logs,
)


def test_read_legacy_header_with_full_rows(tmp_path: Path) -> None:
    log = tmp_path / "call_logs.csv"
    log.write_text(
        "timestamp,phone,name,status,notes\n"
        "2026-06-02 16:25:14,+17085681794,Farman Ali,ENDED,ENDED,"
        "2026-06-02T16:24:14,2026-06-02T16:24:32,2026-06-02T16:24:41,"
        "2026-06-02T16:25:14,42.1,60.0,voicemail left\n",
        encoding="utf-8",
    )
    rows = read_call_logs(limit=5, path=log)
    assert len(rows) == 1
    assert rows[0]["phone"] == "+17085681794"
    assert rows[0]["connected_duration_s"] == "42.1"
    assert rows[0]["total_duration_s"] == "60.0"
    assert "voicemail" in rows[0]["notes"].lower()


def test_ensure_call_log_header_upgrades_file(tmp_path: Path) -> None:
    log = tmp_path / "call_logs.csv"
    log.write_text(
        "timestamp,phone,name,status,notes\n"
        "2026-06-01 11:09:31,+1,Test,ENDED,ok\n",
        encoding="utf-8",
    )
    ensure_call_log_header(log)
    text = log.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(_HEADERS)


def test_call_log_stats_voicemail(tmp_path: Path) -> None:
    log = tmp_path / "call_logs.csv"
    log.write_text(
        ",".join(_HEADERS) + "\n"
        "2026-06-02 16:25:14,+1,A,ENDED,ENDED,,,2026-06-02T16:24:41,,42,60,vm\n"
        "2026-06-02 16:22:40,+1,B,ENDED,ENDED,,2026-06-02T16:22:02,,,30,45,answered\n",
        encoding="utf-8",
    )
    stats = call_log_stats(path=log)
    assert stats["total"] == 2
    assert stats["voicemail"] == 1
    assert stats["connected"] == 1
