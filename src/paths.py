from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "IndusDispatchAgent"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Root for bundled/read-only application resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent.parent


def _frozen_sidecar_runtime() -> Path | None:
    """Writable runtime folder next to a local/portable frozen EXE, when present."""
    exe_dir = Path(sys.executable).resolve().parent
    candidates = (exe_dir, exe_dir.parent)
    for candidate in candidates:
        if (candidate / ".env").exists():
            return candidate
        if (candidate / "chrome_profiles").exists() and (candidate / "dialer_config.json").exists():
            return candidate
    return None


def runtime_base() -> Path:
    """Writable runtime data directory for config, logs, contacts, audio, profiles."""
    override = os.getenv("INDUS_AGENT_HOME")
    if override:
        base = Path(override).expanduser()
    elif is_frozen():
        sidecar = _frozen_sidecar_runtime()
        if sidecar is not None:
            base = sidecar
        else:
            local_app_data = os.getenv("LOCALAPPDATA")
            base = Path(local_app_data) / APP_NAME if local_app_data else Path.home() / APP_NAME
    else:
        base = resource_root()

    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def runtime_path(*parts: str) -> Path:
    path = runtime_base().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def ensure_runtime_dirs() -> Path:
    base = runtime_base()
    for name in ("audio", "data", "logs", "logs/transcripts", "chrome_profiles"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return base
