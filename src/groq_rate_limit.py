"""Simple in-process Groq rate-limit guard for long batch runs."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

logger = logging.getLogger("GoogleVoiceAgent")

_lock = threading.Lock()
_timestamps: deque[float] = deque()


def acquire_slot(max_per_minute: int, *, block: bool = True) -> bool:
    """Wait until a request slot is available (rolling 60s window)."""
    if max_per_minute <= 0:
        return True
    while True:
        with _lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while _timestamps and _timestamps[0] < cutoff:
                _timestamps.popleft()
            if len(_timestamps) < max_per_minute:
                _timestamps.append(now)
                return True
            wait_s = max(0.1, 60.0 - (now - _timestamps[0]))
        if not block:
            return False
        logger.info("Groq rate guard: at %d/min cap, sleeping %.1fs", max_per_minute, wait_s)
        time.sleep(wait_s)
