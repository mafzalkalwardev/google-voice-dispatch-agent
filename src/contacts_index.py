"""SQLite-backed contacts index for fast pagination over large lists."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from src.contacts import load_contacts, normalize_phone
from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent")

BASE_DIR = runtime_base()
INDEX_DB = BASE_DIR / "logs" / "contacts_index.sqlite3"


def _connect() -> sqlite3.Connection:
    INDEX_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(INDEX_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS index_meta (
            file_key TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            built_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_key TEXT NOT NULL,
            phone TEXT NOT NULL,
            name TEXT NOT NULL,
            phone_norm TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_contacts_file ON contacts(file_key);
        CREATE INDEX IF NOT EXISTS idx_contacts_search ON contacts(search_text);
        """
    )


def _file_key(path: Path) -> str:
    p = path.resolve()
    return str(p)


def _meta_for_file(path: Path) -> tuple[str, float, int]:
    stat = path.stat()
    return _file_key(path), stat.st_mtime, stat.st_size


def is_index_current(path: Path) -> bool:
    if not path.exists() or not INDEX_DB.exists():
        return False
    key, mtime, size = _meta_for_file(path)
    with _connect() as conn:
        _init_schema(conn)
        row = conn.execute(
            "SELECT mtime, size, row_count FROM index_meta WHERE file_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return False
    return float(row["mtime"]) == mtime and int(row["size"]) == size


def build_index(path: Path, *, force: bool = False) -> int:
    """Load spreadsheet into SQLite. Returns row count."""
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Contacts file not found: {path}")

    key, mtime, size = _meta_for_file(path)
    if not force and is_index_current(path):
        with _connect() as conn:
            row = conn.execute(
                "SELECT row_count FROM index_meta WHERE file_key = ?",
                (key,),
            ).fetchone()
            return int(row["row_count"]) if row else 0

    t0 = time.monotonic()
    logger.info("Building contacts index for %s …", path.name)
    rows = load_contacts(path)
    built_at = time.strftime("%Y-%m-%d %H:%M:%S")

    with _connect() as conn:
        _init_schema(conn)
        conn.execute("DELETE FROM contacts WHERE file_key = ?", (key,))
        conn.execute("DELETE FROM index_meta WHERE file_key = ?", (key,))
        batch: list[tuple] = []
        for r in rows:
            phone = r.get("phone", "")
            name = r.get("name", "") or "Unknown"
            norm = normalize_phone(phone) or phone
            search = f"{name} {phone} {norm}".lower()
            batch.append((key, phone, name, norm, search))
        conn.executemany(
            "INSERT INTO contacts (file_key, phone, name, phone_norm, search_text) VALUES (?,?,?,?,?)",
            batch,
        )
        conn.execute(
            "INSERT INTO index_meta (file_key, mtime, size, row_count, built_at) VALUES (?,?,?,?,?)",
            (key, mtime, size, len(rows), built_at),
        )
        conn.commit()

    logger.info(
        "Contacts index built: %d rows in %.1fs",
        len(rows),
        time.monotonic() - t0,
    )
    return len(rows)


def query_contacts(
    path: Path,
    *,
    page: int = 1,
    per_page: int = 50,
    query: str = "",
    ensure_index: bool = True,
) -> dict:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"Contacts file not found: {path.name}")

    if ensure_index:
        build_index(path)

    key = _file_key(path)
    q = (query or "").strip().lower()
    per_page = max(1, min(200, int(per_page)))
    page = max(1, int(page))

    with _connect() as conn:
        _init_schema(conn)
        if q:
            like = f"%{q}%"
            where = "file_key = ? AND search_text LIKE ?"
            params: tuple = (key, like)
        else:
            where = "file_key = ?"
            params = (key,)

        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM contacts WHERE {where}",
            params,
        ).fetchone()["c"]
        pages = max(1, (total + per_page - 1) // per_page)
        if page > pages:
            page = pages
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""
            SELECT phone, name FROM contacts
            WHERE {where}
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (*params, per_page, offset),
        ).fetchall()

    start = offset + 1 if total else 0
    end = min(offset + per_page, total)
    return {
        "rows": [{"phone": r["phone"], "name": r["name"]} for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "start": start,
        "end": end,
    }


def get_total_count(path: Path) -> int:
    if is_index_current(path):
        key = _file_key(path)
        with _connect() as conn:
            row = conn.execute(
                "SELECT row_count FROM index_meta WHERE file_key = ?",
                (key,),
            ).fetchone()
            if row:
                return int(row["row_count"])
    return build_index(path)
