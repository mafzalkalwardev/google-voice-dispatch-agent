"""Resource-aware parallel slot limits copied from the Auto Dialer."""
from __future__ import annotations


def system_ram_gb() -> float:
    try:
        import psutil

        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        return 8.0


def chrome_process_count() -> int:
    try:
        import psutil

        count = 0
        for proc in psutil.process_iter(["name"]):
            try:
                if (proc.info.get("name") or "").lower() in ("chrome.exe", "chrome"):
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


def recommended_slots(requested: int) -> int:
    requested = max(1, min(int(requested or 1), 15))
    ram = system_ram_gb()
    chrome = chrome_process_count()
    if ram < 10 or chrome >= 20:
        return min(requested, 1)
    if ram < 16 or chrome >= 10:
        return min(requested, 2)
    if ram < 24:
        return min(requested, 5)
    if ram < 32:
        return min(requested, 10)
    return requested


def effective_requested_slots(requested: int, *, force_requested_slots: bool = False) -> int:
    requested = max(1, min(int(requested or 1), 15))
    if force_requested_slots:
        return requested
    return recommended_slots(requested)
