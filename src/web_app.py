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
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from urllib.parse import quote as _url_quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from src.paths import ensure_runtime_dirs, resource_path, runtime_base

BASE_DIR = ensure_runtime_dirs()
TEMPLATES_DIR = resource_path("src", "templates")
STATIC_DIR = resource_path("src", "static")
CALL_LOG_FILE = BASE_DIR / "logs" / "call_logs.csv"
LEADS_FILE = BASE_DIR / "logs" / "leads.csv"
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

WEB_APP_BUILD = "2026-06-02-call-sync"

# Custom Jinja2 filters
templates.env.filters["basename"] = lambda p: Path(str(p or "")).name
templates.env.filters["url_encode"] = lambda v: _url_quote(str(v or ""), safe="")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_CONFIG_KEYS = [
    "contacts_file", "profile_name", "callback_number", "agent_name",
    "company_name", "company_website", "company_context",
    "groq_model", "loopback_device", "call_timeout", "call_max_duration",
    "capture_device", "tts_voice", "stt_model", "vad_threshold",
    "answered_speak_delay_seconds", "wait_for_human_audio",
    "human_audio_timeout_seconds", "answer_confirm_polls",
    "min_ring_seconds", "max_ring_seconds", "voicemail_detect_seconds",
    "vad_silence_frames", "vad_speech_frames", "stt_retry_count",
    "tts_warmup", "call_cooldown_seconds",
    "silence_does_not_end_call", "use_stt_context", "max_silence_seconds",
    "llm_model_realtime", "llm_model_batch", "listen_after_tts_delay_ms",
    "use_thinking_fillers", "filler_probability", "stream_llm_replies",
    "voicemail_max_wait_seconds", "voicemail_play_on_greeting",
    "voicemail_play_after_seconds", "voicemail_message_max_seconds",
    "voicemail_greeting_frames_required", "screening_purpose_text",
    "chrome_restart_every_n_calls", "max_calls_per_run",
    "groq_max_retries_per_minute", "avoid_gv_page_reload",
    "use_call_intelligence",
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
            "answered_speak_delay_seconds": cfg.answered_speak_delay_seconds,
            "wait_for_human_audio": cfg.wait_for_human_audio,
            "human_audio_timeout_seconds": cfg.human_audio_timeout_seconds,
            "answer_confirm_polls": cfg.answer_confirm_polls,
            "min_ring_seconds": cfg.min_ring_seconds,
            "max_ring_seconds": cfg.max_ring_seconds,
            "voicemail_detect_seconds": cfg.voicemail_detect_seconds,
            "call_cooldown_seconds": cfg.call_cooldown_seconds,
            "vad_silence_frames": cfg.vad_silence_frames,
            "vad_speech_frames": cfg.vad_speech_frames,
            "stt_retry_count": cfg.stt_retry_count,
            "tts_warmup": cfg.tts_warmup,
            "silence_does_not_end_call": cfg.silence_does_not_end_call,
            "use_stt_context": cfg.use_stt_context,
            "max_silence_seconds": cfg.max_silence_seconds,
            "llm_model_realtime": cfg.llm_model_realtime,
            "llm_model_batch": cfg.llm_model_batch,
            "listen_after_tts_delay_ms": cfg.listen_after_tts_delay_ms,
            "use_thinking_fillers": cfg.use_thinking_fillers,
            "filler_probability": cfg.filler_probability,
            "stream_llm_replies": cfg.stream_llm_replies,
            "voicemail_max_wait_seconds": cfg.voicemail_max_wait_seconds,
            "voicemail_play_on_greeting": cfg.voicemail_play_on_greeting,
            "chrome_restart_every_n_calls": cfg.chrome_restart_every_n_calls,
            "avoid_gv_page_reload": cfg.avoid_gv_page_reload,
            "use_call_intelligence": cfg.use_call_intelligence,
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
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--agent-cli"] + extra_args
        else:
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
    from src.call_log import read_call_logs  # type: ignore

    return read_call_logs(limit=limit, path=CALL_LOG_FILE)


def _call_log_stats() -> dict:
    from src.call_log import call_log_stats  # type: ignore

    return call_log_stats(path=CALL_LOG_FILE)


def _resolve_contacts_path(contacts_file: Optional[str] = None) -> Path | None:
    path_str = contacts_file or _load_config().get("contacts_file", "")
    if not path_str:
        return None
    p = Path(path_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    return p


def _get_contacts_preview(contacts_file: Optional[str] = None) -> dict:
    p = _resolve_contacts_path(contacts_file)
    if p is None:
        return {"rows": [], "total": 0, "error": "No contacts file configured"}
    if not p.exists():
        return {"rows": [], "total": 0, "error": f"File not found: {p.name}"}
    try:
        from src.contacts_index import get_total_count, query_contacts
        from src.call_intelligence import enrich_contact_row, intelligence_stats

        total = get_total_count(p)
        page_data = query_contacts(p, page=1, per_page=15, ensure_index=False)
        stats = intelligence_stats()
        return {
            "rows": [enrich_contact_row(c) for c in page_data["rows"]],
            "total": total,
            "error": None,
            "intelligence": stats,
        }
    except Exception as exc:
        return {"rows": [], "total": 0, "error": str(exc)}


def _normalize_phone_number(raw_phone: object) -> str:
    raw = str(raw_phone or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 7 or len(digits) > 15:
        return ""
    if raw.startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def _write_single_call_contacts(phone: str, name: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / "single_test_call.csv"
    with dest.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Name", "Phone"])
        writer.writeheader()
        writer.writerow({"Name": name, "Phone": phone})
    return dest


def _build_run_args(
    *,
    contacts: str,
    profile: str,
    limit: int,
    loopback: str,
    capture: str,
    callback: str,
    dry_run: bool,
) -> List[str]:
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
    return args


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
        "log_stats": _call_log_stats(),
    })


@app.get("/preflight", response_class=HTMLResponse)
async def preflight_page(request: Request):
    return templates.TemplateResponse(request, "preflight.html", {})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = _load_config()
    return templates.TemplateResponse(request, "settings.html", {"cfg": cfg})


def _contacts_list_payload(
    contacts_file: Optional[str],
    *,
    page: int = 1,
    per_page: int = 50,
    q: str = "",
    hide_blocked: bool = False,
) -> dict:
    """Load one page of contacts from the SQLite index."""
    p = _resolve_contacts_path(contacts_file)
    if p is None or not p.exists():
        return {
            "ok": False,
            "error": "Contacts file not configured",
            "rows": [],
            "total": 0,
            "page": 1,
            "pages": 1,
            "start": 0,
            "end": 0,
            "per_page": per_page,
        }
    from src.contacts_index import query_contacts
    from src.call_intelligence import enrich_contact_row, intelligence_stats

    page_data = query_contacts(p, page=page, per_page=per_page, query=q)
    rows = [enrich_contact_row(c) for c in page_data["rows"]]
    if hide_blocked:
        rows = [r for r in rows if r.get("intel_status") not in ("blocked", "dnc")]
    page_data["rows"] = rows
    page_data["ok"] = True
    page_data["intelligence"] = intelligence_stats()
    return page_data


@app.get("/contacts", response_class=HTMLResponse)
async def contacts_page(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    q: str = "",
    hide_blocked: bool = False,
):
    import asyncio

    cfg = _load_config()
    contacts_file = cfg.get("contacts_file")
    preview = _get_contacts_preview(contacts_file)
    list_error = None
    list_data: dict = {"rows": [], "total": 0, "page": 1, "pages": 1, "start": 0, "end": 0}

    if preview.get("total", 0) > 0 and not preview.get("error"):
        try:
            list_data = await asyncio.to_thread(
                lambda: _contacts_list_payload(
                    contacts_file,
                    page=page,
                    per_page=per_page,
                    q=q,
                    hide_blocked=hide_blocked,
                )
            )
            if not list_data.get("ok"):
                list_error = list_data.get("error")
        except Exception as exc:
            logger.exception("contacts page load failed")
            list_error = str(exc)

    use_intel = cfg.get("use_call_intelligence", True)
    if isinstance(use_intel, str):
        use_intel = use_intel.lower() not in ("false", "0", "no")

    return templates.TemplateResponse(request, "contacts.html", {
        "contacts_file": contacts_file or "",
        "preview": preview,
        "use_call_intelligence": use_intel,
        "list_data": list_data,
        "list_error": list_error,
        "query": q,
        "per_page": per_page,
        "hide_blocked": hide_blocked,
        "web_build": WEB_APP_BUILD,
    })


async def _api_contacts_list_handler(page: int, per_page: int, q: str) -> dict:
    import asyncio

    cfg = _load_config()
    try:
        return await asyncio.to_thread(
            lambda: _contacts_list_payload(
                cfg.get("contacts_file"),
                page=page,
                per_page=per_page,
                q=q,
            )
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc), "rows": [], "total": 0}
    except Exception as exc:
        logger.exception("contacts/list failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/contacts/list")
@app.get("/api/contacts")  # alias — some older bookmarks/tools hit this path
async def api_contacts_list(
    page: int = 1,
    per_page: int = 50,
    q: str = "",
):
    return await _api_contacts_list_handler(page, per_page, q)


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


@app.get("/api/batch-progress")
async def api_batch_progress():
    from pathlib import Path
    from src.batch_progress import contacts_fingerprint, get_completed_phones

    cfg = _load_config()
    contacts_path = Path(cfg.get("contacts_file", "data/contacts.xlsx"))
    fp = contacts_fingerprint(contacts_path)
    completed = sorted(get_completed_phones(fp))
    return {
        "contacts_file": str(contacts_path),
        "fingerprint": fp,
        "completed_count": len(completed),
        "completed_phones": completed,
    }


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    logs = _read_call_logs(limit=200)
    return templates.TemplateResponse(request, "logs.html", {"logs": logs})


@app.get("/leads", response_class=HTMLResponse)
async def leads_page(request: Request):
    from src.leads import read_leads  # type: ignore
    leads = read_leads(LEADS_FILE)
    return templates.TemplateResponse(request, "leads.html", {"leads": leads})


@app.get("/recordings", response_class=HTMLResponse)
async def recordings_page(request: Request):
    """Render recent connected and voicemail calls (with recordings when available)."""
    from src.crm import get_recent_call_artifacts  # type: ignore

    try:
        calls = get_recent_call_artifacts(limit=100) or []
    except Exception:
        calls = []

    return templates.TemplateResponse(request, "recordings.html", {"calls": calls})


@app.get("/recordings/{call_id}", response_class=HTMLResponse)
async def recording_view_page(request: Request, call_id: str):
    """Dedicated recording viewer (audio + transcript + AI summary)."""
    from src.crm import get_call_for_ui  # type: ignore

    call = get_call_for_ui(call_id)
    return templates.TemplateResponse(request, "recording_view.html", {"call": call})





@app.get("/connected-calls", response_class=HTMLResponse)
async def connected_calls_page(request: Request):
    from src.crm import get_connected_calls, connected_calls_stats  # type: ignore
    calls = get_connected_calls()
    stats = connected_calls_stats(calls)
    # Collect unique truck types for filter dropdown
    truck_types = sorted({c["truck_type"] for c in calls if c["truck_type"]})
    return templates.TemplateResponse(request, "connected_calls.html", {
        "calls": calls,
        "stats": stats,
        "truck_types": truck_types,
    })


@app.get("/carrier-crm", response_class=HTMLResponse)
async def carrier_crm_page(request: Request):
    from src.crm import get_carrier_profiles, carrier_stats  # type: ignore
    profiles = get_carrier_profiles()
    stats = carrier_stats(profiles)
    truck_types = sorted({p["truck_type"] for p in profiles if p["truck_type"]})
    return templates.TemplateResponse(request, "carrier_crm.html", {
        "profiles": profiles,
        "stats": stats,
        "truck_types": truck_types,
    })


@app.get("/carrier-crm/{phone}", response_class=HTMLResponse)
async def carrier_profile_page(request: Request, phone: str):
    from src.crm import get_carrier_profile  # type: ignore
    profile = get_carrier_profile(phone)
    if not profile:
        return templates.TemplateResponse(request, "carrier_crm.html", {
            "profiles": [],
            "stats": {"total": 0, "interested": 0, "callbacks": 0, "dnc": 0},
            "truck_types": [],
            "error": f"No carrier found for {phone}",
        })
    return templates.TemplateResponse(request, "carrier_profile.html", {
        "profile": profile,
    })


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
        capture_device=cfg.get("capture_device"),
    )
    return [{"name": r.name, "status": r.status, "message": r.message} for r in results]


@app.post("/api/settings")
async def api_save_settings(request: Request):
    body = await request.json()
    allowed = {k: v for k, v in body.items() if k in _CONFIG_KEYS}
    # Coerce numeric fields
    for f in (
        "call_timeout", "call_max_duration", "answer_confirm_polls",
        "vad_silence_frames", "vad_speech_frames", "stt_retry_count",
    ):
        if f in allowed:
            try:
                allowed[f] = int(allowed[f])
            except (ValueError, TypeError):
                pass
    for f in (
        "vad_threshold", "answered_speak_delay_seconds",
        "human_audio_timeout_seconds", "min_ring_seconds",
        "max_ring_seconds", "voicemail_detect_seconds",
        "call_cooldown_seconds",
    ):
        if f in allowed:
            try:
                allowed[f] = float(allowed[f])
            except (ValueError, TypeError):
                pass
    for f in ("wait_for_human_audio", "tts_warmup"):
        if f in allowed:
            allowed[f] = str(allowed[f]).lower() in ("true", "1", "yes", "on")
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
        from src.contacts_index import build_index

        count = build_index(dest, force=True)
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


@app.post("/api/audio/test-tts")
async def api_audio_test_tts(request: Request):
    body = await request.json()
    cfg = _load_config()
    loopback = body.get("loopback_device") or cfg.get("loopback_device", "CABLE Input")
    try:
        from src.audio_diagnostics import play_test_tts  # type: ignore
        return play_test_tts(output_hint=loopback)
    except Exception as exc:
        logger.exception("Audio TTS test failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.post("/api/audio/test-capture")
async def api_audio_test_capture(request: Request):
    body = await request.json()
    cfg = _load_config()
    capture = body.get("capture_device") or cfg.get("capture_device", "default")
    try:
        from src.audio_diagnostics import record_capture_sample  # type: ignore
        from src.config import Config  # type: ignore

        runtime_cfg = Config.load()
        return record_capture_sample(
            capture_hint=capture,
            duration_s=5.0,
            stt_api_key=runtime_cfg.groq_api_key,
            stt_model=str(cfg.get("stt_model") or runtime_cfg.stt_model),
        )
    except Exception as exc:
        logger.exception("Audio capture test failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


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

    args = _build_run_args(
        contacts=contacts,
        profile=profile,
        limit=limit,
        loopback=loopback,
        capture=capture,
        callback=callback,
        dry_run=dry_run,
    )

    result = run_manager.start(args)
    return result


@app.post("/api/run/single-call")
async def api_run_single_call(request: Request):
    body = await request.json()
    cfg = _load_config()

    phone = _normalize_phone_number(body.get("phone"))
    if not phone:
        raise HTTPException(status_code=400, detail="Enter a valid phone number")

    name = str(body.get("name") or "Single Test Contact").strip()
    if not name:
        name = "Single Test Contact"
    name = name[:120]

    contacts_file = _write_single_call_contacts(phone, name)
    profile = body.get("profile_name") or cfg.get("profile_name", "sales_profile")
    dry_run = bool(body.get("dry_run", False))
    loopback = body.get("loopback_device") or cfg.get("loopback_device", "CABLE Input")
    capture = body.get("capture_device") or cfg.get("capture_device", "default")
    callback = body.get("callback_number") or cfg.get("callback_number", "")

    args = _build_run_args(
        contacts=str(contacts_file),
        profile=profile,
        limit=1,
        loopback=loopback,
        capture=capture,
        callback=callback,
        dry_run=dry_run,
    )
    result = run_manager.start(args)
    if result.get("ok"):
        result["phone"] = phone
        result["contacts_file"] = str(contacts_file)
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


@app.get("/api/dashboard/stats")
async def api_dashboard_stats():
    return {
        "logs": _call_log_stats(),
        "recent": _read_call_logs(limit=10),
        "run": run_manager.get_state(),
    }


# ---------------------------------------------------------------------------
# Leads API
# ---------------------------------------------------------------------------

@app.get("/api/leads")
async def api_get_leads():
    from src.leads import read_leads  # type: ignore
    return read_leads(LEADS_FILE)


@app.post("/api/leads")
async def api_upsert_lead(request: Request):
    from src.leads import upsert_lead, LEADS_HEADERS  # type: ignore
    body = await request.json()
    lead = {h: str(body.get(h, "")).strip() for h in LEADS_HEADERS}
    if not lead["timestamp"]:
        lead["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upsert_lead(lead, LEADS_FILE)
    return {"ok": True}


@app.get("/api/leads/export")
async def api_export_leads():
    from src.leads import read_leads, LEADS_HEADERS  # type: ignore
    rows = list(reversed(read_leads(LEADS_FILE)))  # chronological for export

    def _generate():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=LEADS_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        yield buf.getvalue()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


# ---------------------------------------------------------------------------
# CRM API
# ---------------------------------------------------------------------------

@app.get("/api/connected-calls")
async def api_connected_calls():
    from src.crm import get_connected_calls  # type: ignore
    return get_connected_calls()


@app.get("/api/connected-calls/search")
async def api_connected_calls_search(q: str = ""):
    from src.crm import search_connected_calls  # type: ignore
    return search_connected_calls(q)


@app.get("/api/connected-calls/export")
async def api_connected_calls_export(q: str = ""):
    from src.crm import export_connected_calls_csv, search_connected_calls, get_connected_calls  # type: ignore
    rows = search_connected_calls(q) if q else get_connected_calls()
    csv_text = export_connected_calls_csv(rows)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=connected_calls.csv"},
    )


@app.get("/api/connected-calls/{call_id}")
async def api_connected_call_detail(call_id: str):
    from src.crm import get_connected_call  # type: ignore
    call = get_connected_call(call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Connected call not found")
    return call


@app.get("/api/carriers")
async def api_carriers():
    from src.crm import get_carrier_profiles  # type: ignore
    return get_carrier_profiles()


@app.get("/api/carrier-crm")
async def api_carrier_crm():
    from src.crm import get_carrier_profiles  # type: ignore
    return get_carrier_profiles()


@app.get("/api/carrier-crm/search")
async def api_carrier_crm_search(q: str = ""):
    from src.crm import search_carrier_crm  # type: ignore
    return search_carrier_crm(q)


@app.get("/api/carrier-crm/export")
async def api_carrier_crm_export(q: str = ""):
    from src.crm import export_carriers_csv, search_carrier_crm, get_carrier_profiles  # type: ignore
    rows = search_carrier_crm(q) if q else get_carrier_profiles()
    csv_text = export_carriers_csv(rows)
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=carrier_crm.csv"},
    )


@app.get("/api/carrier-crm/{carrier_id}/export")
async def api_carrier_profile_export(carrier_id: str):
    from src.crm import export_profile  # type: ignore
    profile = export_profile(carrier_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Carrier not found")
    payload = json.dumps(profile, indent=2, ensure_ascii=False)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=carrier_{carrier_id}.json"},
    )


@app.get("/api/carrier-crm/{carrier_id}")
async def api_carrier_crm_profile(carrier_id: str):
    from src.crm import get_carrier_profile  # type: ignore
    profile = get_carrier_profile(carrier_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return profile


@app.patch("/api/carrier-crm/{carrier_id}")
async def api_edit_carrier_crm_profile(carrier_id: str, request: Request):
    from src.crm import edit_carrier  # type: ignore
    body = await request.json()
    profile = edit_carrier(carrier_id, body)
    if not profile:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return {"ok": True, "profile": profile}


@app.post("/api/carrier-crm/{carrier_id}/notes")
async def api_add_carrier_crm_note(carrier_id: str, request: Request):
    from src.crm import add_carrier_note  # type: ignore
    body = await request.json()
    text = str(body.get("text", "")).strip()
    author = str(body.get("author", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text is required")
    try:
        note = add_carrier_note(carrier_id, text, author=author)
    except KeyError:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return {"ok": True, "note": note}


@app.post("/api/carrier-crm/{carrier_id}/follow-up")
async def api_schedule_carrier_follow_up(carrier_id: str, request: Request):
    from src.crm import schedule_follow_up  # type: ignore
    body = await request.json()
    try:
        follow = schedule_follow_up(
            carrier_id,
            status=str(body.get("status", "Follow Up Today")).strip(),
            callback_time=str(body.get("callback_time", "")).strip(),
            notes=str(body.get("notes", "")).strip(),
            assigned_dispatcher=str(body.get("assigned_dispatcher", "")).strip(),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Carrier not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "follow_up": follow}


@app.post("/api/carrier-crm/{carrier_id}/assign-dispatcher")
async def api_assign_carrier_dispatcher(carrier_id: str, request: Request):
    from src.crm import assign_dispatcher  # type: ignore
    body = await request.json()
    dispatcher = str(body.get("dispatcher", "")).strip()
    if not dispatcher:
        raise HTTPException(status_code=400, detail="Dispatcher is required")
    profile = assign_dispatcher(carrier_id, dispatcher)
    if not profile:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return {"ok": True, "profile": profile}


@app.get("/api/carrier/{phone}")
async def api_carrier_profile(phone: str):
    from src.crm import get_carrier_profile  # type: ignore
    profile = get_carrier_profile(phone)
    if not profile:
        raise HTTPException(status_code=404, detail="Carrier not found")
    return profile


@app.post("/api/carrier/{phone}/note")
async def api_add_carrier_note(phone: str, request: Request):
    body = await request.json()
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text is required")
    from src.crm import add_carrier_note  # type: ignore
    note = add_carrier_note(phone, text)
    return {"ok": True, "note": note}


@app.get("/api/recordings/{call_id}")
async def api_download_recording(call_id: str):
    from src.crm import recording_path_for_call  # type: ignore
    path = recording_path_for_call(call_id)
    if not path:
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=path.name,
    )


@app.get("/api/transcript/{filename}")
async def api_get_transcript(filename: str):
    from src.crm import get_transcript_text, TRANSCRIPTS_DIR  # type: ignore
    # Security: only serve .txt files, no path separators allowed
    safe = Path(filename).name
    if not safe.endswith(".txt") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    text = get_transcript_text(safe)
    if not text:
        # Return empty rather than 404 so the modal can show a message
        return {"text": "", "filename": safe}
    return {"text": text, "filename": safe}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _server_port(default: int = 8000) -> int:
    raw_port = os.environ.get("INDUS_CONSOLE_PORT")
    for idx, arg in enumerate(sys.argv[1:]):
        if arg == "--port" and idx + 2 <= len(sys.argv[1:]):
            raw_port = sys.argv[idx + 2]
        elif arg.startswith("--port="):
            raw_port = arg.split("=", 1)[1]

    if not raw_port:
        return default
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid console port: {raw_port}") from exc
    if port < 1 or port > 65535:
        raise SystemExit(f"Console port out of range: {port}")
    return port


@app.on_event("startup")
async def _web_startup() -> None:
    logger.info("INDUS web console build=%s (contacts API: /api/contacts/list)", WEB_APP_BUILD)


def main() -> None:
    import uvicorn  # type: ignore

    runtime_base()
    port = _server_port()
    try:
        from src.scripts.kill_port import kill_port  # type: ignore

        kill_port(port)
    except Exception:
        pass
    uvicorn.run(
        "src.web_app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
