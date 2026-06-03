"""Rotate large log/recording folders so 24/7 runs do not fill disk."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent")

BASE_DIR = runtime_base()


def rotate_logs_if_needed(
    max_transcript_mb: int = 500,
    max_recording_mb: int = 2000,
) -> None:
    """Move oversized log dirs to timestamped archives under logs/archive/."""
    archive_root = BASE_DIR / "logs" / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    for name, cap_mb in (("transcripts", max_transcript_mb), ("recordings", max_recording_mb)):
        folder = BASE_DIR / "logs" / name
        if not folder.exists():
            continue
        size_mb = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / (1024 * 1024)
        if size_mb < cap_mb:
            continue
        dest = archive_root / f"{name}_{stamp}"
        try:
            shutil.move(str(folder), str(dest))
            folder.mkdir(parents=True, exist_ok=True)
            logger.warning("Rotated %s (was %.0f MB) -> %s", name, size_mb, dest)
        except OSError as exc:
            logger.warning("Log rotation failed for %s: %s", name, exc)
