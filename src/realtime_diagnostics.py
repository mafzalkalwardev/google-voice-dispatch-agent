from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from src.paths import runtime_base


DIAGNOSTICS_PATH = runtime_base() / "logs" / "realtime_diagnostics.json"


def default_diagnostics() -> dict[str, Any]:
    return {
        "updated_at": time.time(),
        "state": "IDLE",
        "loop_iteration": 0,
        "capture_device": "",
        "capture_device_index": None,
        "capture_device_name": "",
        "capture_mode": "",
        "capture_rms": 0.0,
        "vad_threshold": 0.0,
        "vad_detected": False,
        "speech_started_at": "",
        "speech_ended_at": "",
        "speech_duration_seconds": 0.0,
        "captured_speech_path": "",
        "last_stt_text": "",
        "last_empty_stt_reason": "",
        "last_tts_text": "",
        "last_tts_duration_seconds": 0.0,
        "silence_seconds": 0.0,
    }


def write_live_diagnostics(
    data: dict[str, Any],
    path: Path = DIAGNOSTICS_PATH,
) -> None:
    payload = default_diagnostics()
    payload.update(data)
    payload["updated_at"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_live_diagnostics(path: Path = DIAGNOSTICS_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_diagnostics()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_diagnostics()
    payload = default_diagnostics()
    if isinstance(data, dict):
        payload.update(data)
    return payload
