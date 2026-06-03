"""Persist batch dial progress so reruns skip already-completed contacts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent")

BASE_DIR = runtime_base()
PROGRESS_FILE = BASE_DIR / "logs" / "batch_progress.json"


def contacts_fingerprint(contacts_path: Path) -> str:
    path = contacts_path.resolve()
    if not path.exists():
        return str(path)
    stat = path.stat()
    return f"{path}|{stat.st_size}|{int(stat.st_mtime)}"


def _load_raw() -> dict[str, Any]:
    if not PROGRESS_FILE.exists():
        return {}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read batch progress: %s", exc)
        return {}


def _save_raw(data: dict[str, Any]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_completed_phones(fingerprint: str) -> set[str]:
    data = _load_raw()
    entry = data.get("batches", {}).get(fingerprint, {})
    phones = entry.get("completed_phones", [])
    return {str(p) for p in phones}


def mark_completed(fingerprint: str, phone: str, index: int) -> None:
    data = _load_raw()
    batches = data.setdefault("batches", {})
    entry = batches.setdefault(fingerprint, {"completed_phones": [], "last_index": 0})
    phones: list[str] = entry.setdefault("completed_phones", [])
    if phone not in phones:
        phones.append(phone)
    entry["last_index"] = max(int(entry.get("last_index", 0)), int(index))
    entry["contacts_file"] = fingerprint
    _save_raw(data)


def filter_contacts(contacts: list[dict], fingerprint: str, *, resume: bool) -> list[dict]:
    if not resume:
        return contacts
    done = get_completed_phones(fingerprint)
    if not done:
        return contacts
    remaining = [c for c in contacts if c.get("phone") not in done]
    skipped = len(contacts) - len(remaining)
    if skipped:
        logger.info(
            "Resuming batch: skipping %d contact(s) already completed for this list",
            skipped,
        )
    return remaining


def reset_progress(fingerprint: str | None = None) -> None:
    data = _load_raw()
    if fingerprint is None:
        data.pop("batches", None)
    else:
        batches = data.get("batches", {})
        batches.pop(fingerprint, None)
    _save_raw(data)
