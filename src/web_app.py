"""
INDUS TRANSPORTS LLC — Dispatch Agent Operator Console
FastAPI + Jinja2 web frontend for the Google Voice dispatch agent.

Run with:
    python -m src.web_app
    # or
    uvicorn src.web_app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SRC_DIR / "templates"
STATIC_DIR = SRC_DIR / "static"
CALL_LOG_FILE = BASE_DIR / "logs" / "call_logs.csv"
CONFIG_FILE = BASE_DIR / "dialer_config.json"
DATA_DIR = BASE_DIR / "data"

logger = logging.getLogger("GoogleVoiceAgent.WebApp")

# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------

app = FastAPI(
    title="INDUS TRANSPORTS LLC — Dispatch Agent Console",
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_CONFIG_KEYS = [
    "contacts_file", "profile_name", "callback_number", "agent_name",
    "company_name", "company_website", "company_context",
    "groq_model", "loopback_device", "call_timeout", "call_max_duration",
    "capture_device", "tts_voice", "stt_model", "vad_threshold",
]


def _load_config() -> dict:
    """Read dialer_config.json, falling back to Config defaults."""
    base: dict = {}
    if CONFIG_FILE.exists():
        try:
            base = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fill in env/defaults via Config
    try:
        from src.config import Config  # type: ignore
        cfg = Config.load()
        merged = {
            "contacts_file": str(cfg.contacts_file),
            "profile_name": cfg.profile_name,
            "callback_number": cfg.callback_number,
            "agent_name": cfg.agent_name,
            "company_name": cfg.company_name,
            "company_website": cfg.company_website,
            "company_context": cfg.company_context,
            "groq_model": cfg.groq_model,
            "loopback_device": cfg.loopback_device,
            "call_timeout": cfg.call_timeout,
            "call_max_duration": cfg.call_max_duration,
            "capture_device": cfg.capture_device,
            "tts_voice": cfg.tts_voice,
            "stt_model": cfg.stt_model,
            "vad_threshold": cfg.vad_threshold,
        }
    except Exception:
        merged = {}
    merged.update(base)
    return merged


def _save_config(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.update({k: v for k, v in data.items() if k in _CONFIG_KEYS})
    CONFIG_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Run manager — wraps the CLI subprocess
# ---------------------------------------------------------------------------

class RunManager:
    """
    Manages a single `python -m src.main` subprocess.
    Thread-safe output buffer and SSE broadcast.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._lines: List[str] = []
        self._lock = threading.Lock()
        self._status: str = "idle"   # idle | running | stopping | completed | failed
        self._reader: Optional[threading.Thread] = None
        self._exit_code: Optional[int] = None
        # Async event to wake up SSE waiters
        self._new_data_event = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self, extra_args: List[str]) -> dict:
        if self.is_running:
            return {"ok": False, "error": "A run is already in progress"}
        with self._lock:
            self._lines = []
            self._exit_code = None
            self._status = "running"
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        cmd = [sys.executable, "-m", "src.main"] + extra_args
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(BASE_DIR),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            with self._lock:
                self._status = "failed"
            return {"ok": False, "error": str(exc)}
        self._reader = threading.Thread(
            target=self._read_output, daemon=True, name="RunOutputReader"
        )
        self._reader.start()
        return {"ok": True, "pid": self._process.pid}

    def stop(self) -> dict:
        if not self.is_running:
            with self._lock:
                self._status = "idle"
            return {"ok": True, "message": "No active run"}
        with self._lock:
            self._status = "stopping"
        try:
            self._process.terminate()
            self._process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self._process.kill()
        with self._lock:
            self._status = "stopped"
        return {"ok": True, "message": "Run stopped"}

    def get_state(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "is_running": self.is_running,
                "line_count": len(self._lines),
                "exit_code": self._exit_code,
            }

    def lines_since(self, index: int) -> List[str]:
        with self._lock:
            return list(self._lines[index:])

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_output(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        try:
            for raw in self._process.stdout:
                line = raw.rstrip("\n\r")
                with self._lock:
                    self._lines.append(line)
                self._signal_new_data()
        except Exception as exc:
            logger.debug("RunManager reader exception: %s", exc)
        finally:
            rc = self._process.wait()
            self._exit_code = rc
            with self._lock:
                if self._status not in ("stopping", "stopped"):
                    self._status = "completed" if rc == 0 else "failed"
            self._signal_new_data()

    def _signal_new_data(self) -> None:
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._new_data_event.set)
            except Exception:
                pass


run_manager = RunManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_call_logs(limit: int = 100) -> List[dict]:
    if not CALL_LOG_FILE.exists():
        return []
    rows = []
    try:
        with open(CALL_LOG_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(dict(row))
    except Exception:
        return []
    return list(reversed(rows))[:limit]


def _get_contacts_preview(contacts_file: Optional[str] = None) -> dict:
    path_str = contacts_file or _load_config().get("contacts_file", "")
    if not path_str:
        return {"rows": [], "total": 0, "error": "No contacts file configured"}
    p = Path(path_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    if not p.exists():
        return {"rows": [], "total": 0, "error": f"File not found: {p.name}"}
    try:
        from src.contacts import load_contacts  # type: ignore
        rows = load_contacts(p)
        return {"rows": rows[:15], "total": len(rows), "error": None}
    except Exception as exc:
        return {"rows": [], "total": 0, "error": str(exc)}


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    logs = _read_call_logs(limit=10)
    state = run_manager.get_state()
    return templates.TemplateResponse(request, "index.html", {
        "recent_logs": logs,
        "run_state": state,
    })


@app.get("/preflight", response_class=HTMLResponse)
async def preflight_page(request: Request):
    return templates.TemplateResponse(request, "preflight.html", {})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = _load_config()
    return templates.TemplateResponse(request, "settings.html", {"cfg": cfg})


@app.get("/contacts", response_class=HTMLResponse)
async def contacts_page(request: Request):
    cfg = _load_config()
    preview = _get_contacts_preview(cfg.get("contacts_file"))
    return templates.TemplateResponse(request, "contacts.html", {
        "contacts_file": cfg.get("contacts_file", ""),
        "preview": preview,
    })


@app.get("/audio", response_class=HTMLResponse)
async def audio_page(request: Request):
    cfg = _load_config()
    try:
        from src.voice_playback import list_audio_devices, probe_output_device  # type: ignore
        raw_devices = list_audio_devices()
        devices = []
        for d in raw_devices:
            entry = dict(d)
            if int(d.get("max_output_channels") or 0) > 0:
                ok, detail = probe_output_device(d["index"])
                entry["probe_ok"] = ok
                entry["probe_detail"] = detail
            else:
                entry["probe_ok"] = None   # input-only — not probed
                entry["probe_detail"] = ""
            devices.append(entry)
    except Exception:
        devices = []
    return templates.TemplateResponse(request, "audio.html", {
        "devices": devices,
        "loopback_device": cfg.get("loopback_device", "CABLE Input"),
        "capture_device": cfg.get("capture_device", "default"),
    })


@app.get("/run", response_class=HTMLResponse)
async def run_page(request: Request):
    cfg = _load_config()
    state = run_manager.get_state()
    return templates.TemplateResponse(request, "run.html", {
        "cfg": cfg,
        "run_state": state,
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    logs = _read_call_logs(limit=200)
    return templates.TemplateResponse(request, "logs.html", {"logs": logs})


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/preflight")
async def api_preflight():
    from src.preflight import run_all  # type: ignore
    cfg = _load_config()
    results = run_all(
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        contacts_file=Path(cfg.get("contacts_file", "")),
        profile_name=cfg.get("profile_name"),
        loopback_device=cfg.get("loopback_device"),
    )
    return [{"name": r.name, "status": r.status, "message": r.message} for r in results]


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    allowed = {k: v for k, v in body.items() if k in _CONFIG_KEYS}
    # Coerce numeric fields
    for f in ("call_timeout", "call_max_duration"):
        if f in allowed:
            try:
                allowed[f] = int(allowed[f])
            except (ValueError, TypeError):
                pass
    if "vad_threshold" in allowed:
        try:
            allowed["vad_threshold"] = float(allowed["vad_threshold"])
        except (ValueError, TypeError):
            pass
    _save_config(allowed)
    return {"ok": True, "saved": list(allowed.keys())}


@app.post("/api/contacts/upload")
async def api_upload_contacts(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="Must be .csv or .xlsx")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / ("contacts" + suffix)
    content = await file.read()
    dest.write_bytes(content)
    # Update config to point at new file
    _save_config({"contacts_file": str(dest)})
    # Preview
    try:
        from src.contacts import load_contacts  # type: ignore
        rows = load_contacts(dest)
        count = len(rows)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(dest)}
    return {"ok": True, "path": str(dest), "count": count}


@app.get("/api/audio/devices")
async def api_audio_devices():
    try:
        from src.voice_playback import list_audio_devices  # type: ignore
        return list_audio_devices()
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/run/start")
async def api_run_start(request: Request):
    body = await request.json()
    cfg = _load_config()

    contacts = body.get("contacts_file") or cfg.get("contacts_file", "data/contacts.xlsx")
    profile = body.get("profile_name") or cfg.get("profile_name", "sales_profile")
    limit = int(body.get("limit", cfg.get("limit", 10)))
    dry_run = bool(body.get("dry_run", False))
    loopback = body.get("loopback_device") or cfg.get("loopback_device", "CABLE Input")
    capture = body.get("capture_device") or cfg.get("capture_device", "default")
    callback = body.get("callback_number") or cfg.get("callback_number", "")

    args = [
        "--contacts", contacts,
        "--profile", profile,
        "--limit", str(limit),
        "--loopback-device", loopback,
        "--capture-device", capture,
    ]
    if callback:
        args += ["--callback-number", callback]
    if dry_run:
        args.append("--dry-run")

    result = run_manager.start(args)
    return result


@app.post("/api/run/stop")
async def api_run_stop():
    result = run_manager.stop()
    return result


@app.get("/api/run/status")
async def api_run_status():
    return run_manager.get_state()


@app.get("/api/run/lines")
async def api_run_lines(since: int = 0):
    lines = run_manager.lines_since(since)
    state = run_manager.get_state()
    return {"lines": lines, "state": state, "next_index": since + len(lines)}


@app.get("/api/run/stream")
async def api_run_stream(request: Request, since: int = 0):
    """Server-sent events for live run output."""

    async def event_gen():
        index = since
        while True:
            if await request.is_disconnected():
                break
            new_lines = run_manager.lines_since(index)
            state = run_manager.get_state()
            if new_lines:
                for line in new_lines:
                    payload = json.dumps({"line": line, "idx": index})
                    yield f"data: {payload}\n\n"
                    index += 1
            else:
                # Status heartbeat every ~1s
                payload = json.dumps({"heartbeat": True, "state": state})
                yield f"data: {payload}\n\n"
            # If run finished and no new lines, send a final done event
            if not state["is_running"] and state["status"] not in ("idle", "running"):
                yield f"data: {json.dumps({'done': True, 'state': state})}\n\n"
                break
            # Wait for new data or timeout
            run_manager._new_data_event.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.get_event_loop().run_in_executor(
                        None, run_manager._new_data_event.wait
                    )),
                    timeout=1.0,
                )
            except (asyncio.TimeoutError, Exception):
                pass

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/logs")
async def api_logs(limit: int = 200):
    return _read_call_logs(limit=limit)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import uvicorn  # type: ignore
    uvicorn.run(
        "src.web_app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
