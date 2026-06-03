"""
SQLite-backed connected-call and carrier CRM data layer.

The operator console still keeps the legacy call_logs.csv and leads.csv files for
compatibility, but this module is the permanent relationship store for:
connected calls, carrier profiles, transcripts, recordings, AI summaries, notes,
follow-ups, and searchable CRM history.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from src.call_session import CallSession, CallState
from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent.CRM")

BASE_DIR = runtime_base()
CALL_LOG_FILE = BASE_DIR / "logs" / "call_logs.csv"
LEADS_FILE = BASE_DIR / "logs" / "leads.csv"
NOTES_FILE = BASE_DIR / "logs" / "carrier_notes.json"
TRANSCRIPTS_DIR = BASE_DIR / "logs" / "transcripts"
RECORDINGS_DIR = BASE_DIR / "logs" / "recordings"
CRM_DB_FILE = BASE_DIR / "logs" / "carrier_crm.sqlite3"

CONNECTED_CALLS_DIR = BASE_DIR / "connected_calls"
VOICEMAIL_CALLS_DIR = BASE_DIR / "voicemail_calls"
FAILED_CALLS_DIR = BASE_DIR / "failed_calls"

FOLLOW_UP_STATUSES = [
    "Hot Lead",
    "Interested",
    "Negotiating",
    "Waiting For Documents",
    "Follow Up Today",
    "Not Interested",
    "DNC",
    "Onboarded",
    "Active Carrier",
]

CONNECTED_CALL_EXPORT_FIELDS = [
    "id",
    "timestamp",
    "company_name",
    "carrier_name",
    "phone",
    "mc_number",
    "dot_number",
    "email",
    "truck_type",
    "truck_length",
    "preferred_lanes",
    "agreed_percentage",
    "interested_status",
    "callback_time",
    "follow_up_status",
    "duration",
    "transcript_path",
    "recording_path",
    "ai_summary",
]

CARRIER_EXPORT_FIELDS = [
    "id",
    "company_name",
    "carrier_name",
    "phone",
    "mc_number",
    "dot_number",
    "email",
    "truck_type",
    "truck_length",
    "dimensions",
    "accessories",
    "preferred_lanes",
    "local_or_otr",
    "dispatcher_status",
    "factoring_company",
    "agreed_percentage",
    "interested_status",
    "callback_time",
    "follow_up_status",
    "onboarding_status",
    "assigned_dispatcher",
    "close_probability",
    "urgency",
    "updated_at",
]


# ---------------------------------------------------------------------------
# Normalization and storage helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _norm_phone(raw: object) -> str:
    raw_s = _clean(raw)
    digits = re.sub(r"\D", "", raw_s)
    if len(digits) < 7:
        return raw_s
    if raw_s.startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def _norm_mc(raw: object) -> str:
    value = _clean(raw).upper()
    if not value:
        return ""
    digits = re.sub(r"\D", "", value)
    return digits or value


def _norm_email(raw: object) -> str:
    return _clean(raw).lower()


def _safe_slug(raw: object, fallback: str = "call") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", _clean(raw)).strip("._")
    return slug[:90] or fallback


def _duration(session: CallSession) -> float:
    return float(session.connected_duration_seconds() or session.total_duration_seconds() or 0.0)


def _format_duration(seconds: object) -> str:
    try:
        value = float(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "-"
    if value < 60:
        return f"{value:.0f}s"
    minutes, sec = divmod(int(value), 60)
    return f"{minutes}m {sec:02d}s"


def ensure_storage_dirs() -> None:
    for path in (
        CRM_DB_FILE.parent,
        TRANSCRIPTS_DIR,
        RECORDINGS_DIR,
        CONNECTED_CALLS_DIR,
        VOICEMAIL_CALLS_DIR,
        FAILED_CALLS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _storage_dir_for(call_type: str) -> Path:
    if call_type == "connected":
        return CONNECTED_CALLS_DIR
    if call_type == "voicemail":
        return VOICEMAIL_CALLS_DIR
    return FAILED_CALLS_DIR


def _read_text(path: Optional[Path]) -> str:
    if not path:
        return ""
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        logger.debug("Text read skipped for %s: %s", path, exc)
    return ""


def _copy_artifact(source: Optional[Path], dest_dir: Path, dest_name: str) -> str:
    if not source:
        return ""
    try:
        src = Path(source)
        if not src.exists():
            return ""
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / dest_name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        return str(dest)
    except Exception as exc:
        logger.warning("Artifact copy failed (%s): %s", source, exc)
        return str(source)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _call_id(session: CallSession, contact: dict, lead: dict) -> str:
    stamp = (
        session.connected_at
        or session.started_at
        or session.ended_at
        or datetime.now()
    ).strftime("%Y%m%d_%H%M%S")
    phone = _safe_slug(session.phone or contact.get("phone") or lead.get("phone_number"), "unknown")
    seed = "|".join([
        session.phone,
        session.contact_name,
        str(session.started_at or ""),
        str(session.connected_at or ""),
        str(session.transcript_path or ""),
        str(session.recording_path or ""),
    ])
    short = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{stamp}_{phone}_{short}"


def _has_real_conversation(transcript_text: str) -> bool:
    """Heuristic to decide whether we should treat a call as a real conversation.

    Important: this function is intentionally permissive.
    The operator UI (recordings/transcript pages) should not hide calls just
    because STT produced partial or unlabeled output.
    """
    if not _clean(transcript_text):
        return False

    # If we have any labeled turn with any meaningful spoken content, accept it.
    for line in transcript_text.splitlines():
        if re.search(r"\b(Prospect|Carrier|Driver|Customer)\s*:", line, re.I):
            spoken = line.split(":", 1)[-1]
            if len(re.findall(r"[A-Za-z0-9]+", spoken)) >= 1:
                return True

    # If we don't see labeled turns, be conservative and treat as *not* a real
    # conversation. (This keeps silent/failed calls excluded from Connected Calls.)
    return False





def _classify_call(session: CallSession, transcript_text: str) -> tuple[str, str]:
    outcome = _clean(session.outcome).lower()
    notes = " ".join(session.notes).lower()
    if session.state == CallState.VOICEMAIL or session.voicemail_detected_at or "voicemail" in outcome:
        return "voicemail", "voicemail"
    if session.state == CallState.FAILED or not session.connected_at:
        return "failed", "not_connected"
    if "voicemail" in notes:
        return "voicemail", "voicemail_after_answer"
    if not _has_real_conversation(transcript_text):
        return "failed", "silent_connected"
    return "connected", "real_conversation"


def _lead_value(lead: dict, *keys: str) -> str:
    for key in keys:
        value = _clean(lead.get(key, ""))
        if value:
            return value
    return ""


def _session_payload(session: CallSession) -> dict:
    return {
        "phone": session.phone,
        "contact_name": session.contact_name,
        "state": session.state.value,
        "outcome": session.outcome,
        "started_at": session.started_at.isoformat() if session.started_at else "",
        "connected_at": session.connected_at.isoformat() if session.connected_at else "",
        "voicemail_detected_at": session.voicemail_detected_at.isoformat() if session.voicemail_detected_at else "",
        "ended_at": session.ended_at.isoformat() if session.ended_at else "",
        "duration": _duration(session),
        "notes": list(session.notes),
        "transcript_path": str(session.transcript_path or ""),
        "recording_path": str(session.recording_path or ""),
    }


# ---------------------------------------------------------------------------
# SQLite schema and indexing
# ---------------------------------------------------------------------------

def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    ensure_storage_dirs()
    conn = sqlite3.connect(str(db_path or CRM_DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS carriers (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            company_name TEXT DEFAULT '',
            carrier_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            phone_norm TEXT DEFAULT '',
            mc_number TEXT DEFAULT '',
            mc_norm TEXT DEFAULT '',
            dot_number TEXT DEFAULT '',
            email TEXT DEFAULT '',
            email_norm TEXT DEFAULT '',
            truck_type TEXT DEFAULT '',
            truck_length TEXT DEFAULT '',
            dimensions TEXT DEFAULT '',
            accessories TEXT DEFAULT '',
            preferred_lanes TEXT DEFAULT '',
            local_or_otr TEXT DEFAULT '',
            dispatcher_status TEXT DEFAULT '',
            factoring_company TEXT DEFAULT '',
            pricing_discussion TEXT DEFAULT '',
            agreed_percentage TEXT DEFAULT '',
            objections TEXT DEFAULT '',
            pain_points TEXT DEFAULT '',
            interested_status TEXT DEFAULT '',
            interest_level TEXT DEFAULT '',
            callback_time TEXT DEFAULT '',
            follow_up_status TEXT DEFAULT '',
            follow_up_notes TEXT DEFAULT '',
            onboarding_status TEXT DEFAULT '',
            assigned_dispatcher TEXT DEFAULT '',
            remarks TEXT DEFAULT '',
            close_probability TEXT DEFAULT '',
            urgency TEXT DEFAULT '',
            best_follow_up_strategy TEXT DEFAULT '',
            last_summary TEXT DEFAULT '',
            last_sentiment TEXT DEFAULT '',
            searchable_text TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS connected_calls (
            id TEXT PRIMARY KEY,
            carrier_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            company_name TEXT DEFAULT '',
            carrier_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            mc_number TEXT DEFAULT '',
            dot_number TEXT DEFAULT '',
            email TEXT DEFAULT '',
            truck_type TEXT DEFAULT '',
            truck_length TEXT DEFAULT '',
            dimensions TEXT DEFAULT '',
            accessories TEXT DEFAULT '',
            preferred_lanes TEXT DEFAULT '',
            local_or_otr TEXT DEFAULT '',
            agreed_percentage TEXT DEFAULT '',
            interested_status TEXT DEFAULT '',
            callback_time TEXT DEFAULT '',
            remarks TEXT DEFAULT '',
            duration REAL DEFAULT 0,
            transcript_path TEXT DEFAULT '',
            transcript_filename TEXT DEFAULT '',
            recording_path TEXT DEFAULT '',
            recording_filename TEXT DEFAULT '',
            ai_summary TEXT DEFAULT '',
            sentiment TEXT DEFAULT '',
            close_probability TEXT DEFAULT '',
            urgency TEXT DEFAULT '',
            pain_points TEXT DEFAULT '',
            objections TEXT DEFAULT '',
            follow_up_status TEXT DEFAULT '',
            storage_dir TEXT DEFAULT '',
            searchable_text TEXT DEFAULT '',
            FOREIGN KEY(carrier_id) REFERENCES carriers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS call_artifacts (
            id TEXT PRIMARY KEY,
            carrier_id TEXT,
            call_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            phone TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            duration REAL DEFAULT 0,
            transcript_path TEXT DEFAULT '',
            recording_path TEXT DEFAULT '',
            ai_summary TEXT DEFAULT '',
            storage_dir TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            metadata_json TEXT DEFAULT '',
            FOREIGN KEY(carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id TEXT PRIMARY KEY,
            carrier_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            text TEXT NOT NULL,
            author TEXT DEFAULT '',
            FOREIGN KEY(carrier_id) REFERENCES carriers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS follow_ups (
            id TEXT PRIMARY KEY,
            carrier_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            status TEXT NOT NULL,
            callback_time TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            assigned_dispatcher TEXT DEFAULT '',
            FOREIGN KEY(carrier_id) REFERENCES carriers(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_carriers_phone ON carriers(phone_norm);
        CREATE INDEX IF NOT EXISTS idx_carriers_mc ON carriers(mc_norm);
        CREATE INDEX IF NOT EXISTS idx_carriers_email ON carriers(email_norm);
        CREATE INDEX IF NOT EXISTS idx_connected_calls_carrier ON connected_calls(carrier_id);
        CREATE INDEX IF NOT EXISTS idx_connected_calls_timestamp ON connected_calls(timestamp);
        CREATE INDEX IF NOT EXISTS idx_artifacts_carrier ON call_artifacts(carrier_id);
        CREATE INDEX IF NOT EXISTS idx_notes_carrier ON notes(carrier_id);
        CREATE INDEX IF NOT EXISTS idx_followups_carrier ON follow_ups(carrier_id);
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS crm_fts "
            "USING fts5(entity_type, entity_id, carrier_id, content)"
        )
    except sqlite3.OperationalError as exc:
        logger.debug("SQLite FTS5 unavailable; falling back to LIKE search: %s", exc)


def _fts_available(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='crm_fts'"
    ).fetchone()
    return bool(row)


def _index_document(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
    carrier_id: str,
    content: str,
) -> None:
    if not _fts_available(conn):
        return
    conn.execute(
        "DELETE FROM crm_fts WHERE entity_type=? AND entity_id=?",
        (entity_type, entity_id),
    )
    if _clean(content):
        conn.execute(
            "INSERT INTO crm_fts(entity_type, entity_id, carrier_id, content) VALUES (?, ?, ?, ?)",
            (entity_type, entity_id, carrier_id, content),
        )


def _search_expr(query: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9@.+-]+", query)
    if not tokens:
        return query
    return " ".join(f"{token}*" for token in tokens)


def _row_to_dict(row: sqlite3.Row | dict | None) -> dict:
    if not row:
        return {}
    return dict(row)


# ---------------------------------------------------------------------------
# Legacy CSV readers
# ---------------------------------------------------------------------------

def _read_call_logs() -> list[dict]:
    try:
        from src.call_log import read_call_logs  # type: ignore

        return list(reversed(read_call_logs(limit=None, path=CALL_LOG_FILE)))
    except Exception as exc:
        logger.warning("call_logs read error: %s", exc)
        return []


def _read_leads() -> list[dict]:
    if not LEADS_FILE.exists():
        return []
    try:
        from src.leads import read_leads  # type: ignore

        return list(reversed(read_leads(LEADS_FILE)))
    except Exception as exc:
        logger.warning("leads read error: %s", exc)
        return []


def _leads_by_phone(leads: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for lead in leads:
        phone = _norm_phone(lead.get("phone_number", ""))
        if not phone:
            continue
        merged = index.setdefault(phone, {})
        for key, value in lead.items():
            if value:
                merged[key] = value
    return index


def _legacy_connected_calls() -> list[dict]:
    calls = _read_call_logs()
    leads = _leads_by_phone(_read_leads())
    result: list[dict] = []
    for call in calls:
        if not call.get("connected_at"):
            continue
        phone = _norm_phone(call.get("phone", ""))
        lead = leads.get(phone, {})
        tf = _lead_value(lead, "transcript_file")
        result.append({
            "id": f"legacy-{len(result)}-{phone}",
            "timestamp": call.get("timestamp", ""),
            "phone": phone,
            "carrier_name": call.get("name") or lead.get("contact_name", ""),
            "name": call.get("name") or lead.get("contact_name", ""),
            "status": call.get("status", ""),
            "outcome": call.get("outcome", ""),
            "connected_at": call.get("connected_at", ""),
            "ended_at": call.get("ended_at", ""),
            "connected_duration_s": call.get("connected_duration_s", ""),
            "connected_duration": _format_duration(call.get("connected_duration_s")),
            "duration": call.get("connected_duration_s", ""),
            "notes": call.get("notes", ""),
            "company_name": lead.get("company_name", ""),
            "mc_number": lead.get("mc_number", ""),
            "dot_number": lead.get("dot_number", ""),
            "email": lead.get("email", ""),
            "truck_type": lead.get("truck_type", ""),
            "truck_length": lead.get("truck_length") or lead.get("dimensions", ""),
            "accessories": lead.get("accessories", ""),
            "preferred_lanes": lead.get("preferred_lanes", ""),
            "local_or_otr": lead.get("local_or_otr", ""),
            "agreed_percentage": lead.get("agreed_percentage", ""),
            "interested_status": lead.get("interested", ""),
            "interested": lead.get("interested", ""),
            "callback_time": lead.get("callback_time", ""),
            "close_probability": lead.get("close_probability", ""),
            "urgency": lead.get("urgency", ""),
            "objections": lead.get("objections", ""),
            "pain_points": lead.get("pain_points", ""),
            "best_follow_up_strategy": lead.get("best_follow_up_strategy", ""),
            "ai_summary": lead.get("post_call_summary", ""),
            "post_call_summary": lead.get("post_call_summary", ""),
            "sentiment": lead.get("post_call_sentiment", ""),
            "transcript_path": tf,
            "transcript_file": tf,
            "transcript_filename": Path(tf).name if tf else "",
            "recording_path": "",
            "recording_filename": "",
            "follow_up_status": lead.get("follow_up_status", ""),
        })
    result.reverse()
    return result


# ---------------------------------------------------------------------------
# AI transcription and extraction helpers
# ---------------------------------------------------------------------------

def transcribe_recording(
    recording_path: Path,
    groq_api_key: str,
    model: str = "whisper-large-v3-turbo",
    prompt: str = "INDUS TRANSPORTS freight dispatch carrier conversation",
) -> str:
    """Transcribe a saved WAV recording. Returns empty string on any failure."""
    if not groq_api_key or not recording_path.exists():
        return ""
    try:
        from src.groq_pool import pool_for_request

        pool = pool_for_request(groq_api_key)
        with recording_path.open("rb") as fh:
            wav = fh.read()
        result = pool.execute(
            lambda client: client.audio.transcriptions.create(
                file=(recording_path.name, wav, "audio/wav"),
                model=model,
                language="en",
                prompt=prompt,
                response_format="text",
            )
        )
        return _clean(getattr(result, "text", result))
    except Exception as exc:
        logger.warning("Recording transcription failed for %s: %s", recording_path, exc)
        return ""


def _ensure_transcript_from_recording(
    session: CallSession,
    call_id: str,
    groq_api_key: str,
    stt_model: str,
) -> Optional[Path]:
    transcript_path = Path(session.transcript_path) if session.transcript_path else None
    transcript_text = _read_text(transcript_path)
    if _has_real_conversation(transcript_text):
        return transcript_path
    recording_path = Path(session.recording_path) if session.recording_path else None
    if not recording_path or not recording_path.exists() or not groq_api_key:
        return transcript_path

    text = transcribe_recording(recording_path, groq_api_key=groq_api_key, model=stt_model)
    if not text:
        return transcript_path

    if transcript_path is None:
        transcript_path = TRANSCRIPTS_DIR / f"{call_id}.txt"
        session.transcript_path = transcript_path
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with transcript_path.open("a", encoding="utf-8") as fh:
        if transcript_text and not transcript_text.endswith("\n"):
            fh.write("\n")
        fh.write(f"[{datetime.now().strftime('%H:%M:%S')}] Prospect: {text}\n")
    return transcript_path


def _extract_lead_if_needed(
    session: CallSession,
    contact: dict,
    lead: dict,
    groq_api_key: str,
    model: str,
) -> dict:
    if any(_clean(v) for v in lead.values()):
        return dict(lead)
    if not session.transcript_path or not groq_api_key:
        return dict(lead)
    try:
        from src.leads import extract_lead_from_transcript  # type: ignore

        extracted = extract_lead_from_transcript(
            transcript_path=Path(session.transcript_path),
            contact=contact,
            groq_api_key=groq_api_key,
            model=model,
        )
        return dict(extracted)
    except Exception as exc:
        logger.warning("Lead extraction during CRM finalize failed: %s", exc)
        return dict(lead)


# ---------------------------------------------------------------------------
# Carrier merge/upsert
# ---------------------------------------------------------------------------

def _carrier_payload(lead: dict, contact: dict, session: Optional[CallSession] = None) -> dict:
    phone = _norm_phone(_lead_value(lead, "phone_number", "phone") or contact.get("phone") or (session.phone if session else ""))
    mc = _lead_value(lead, "mc_number", "mc")
    email = _lead_value(lead, "email")
    interested = _lead_value(lead, "interested", "interested_status")
    follow_status = _lead_value(lead, "follow_up_status")
    if not follow_status:
        if interested == "DNC":
            follow_status = "DNC"
        elif interested == "No":
            follow_status = "Not Interested"
        elif _lead_value(lead, "callback_time"):
            follow_status = "Follow Up Today"
        elif interested in ("Yes", "Maybe"):
            follow_status = "Interested"

    return {
        "company_name": _lead_value(lead, "company_name"),
        "carrier_name": _lead_value(lead, "contact_name", "carrier_name") or contact.get("name") or (session.contact_name if session else ""),
        "phone": phone,
        "phone_norm": phone,
        "mc_number": mc,
        "mc_norm": _norm_mc(mc),
        "dot_number": _lead_value(lead, "dot_number"),
        "email": email,
        "email_norm": _norm_email(email),
        "truck_type": _lead_value(lead, "truck_type"),
        "truck_length": _lead_value(lead, "truck_length", "dimensions"),
        "dimensions": _lead_value(lead, "dimensions", "truck_length"),
        "accessories": _lead_value(lead, "accessories"),
        "preferred_lanes": _lead_value(lead, "preferred_lanes", "lanes"),
        "local_or_otr": _lead_value(lead, "local_or_otr", "otr_local_preference"),
        "dispatcher_status": _lead_value(lead, "dispatcher_status"),
        "factoring_company": _lead_value(lead, "factoring_company"),
        "pricing_discussion": _lead_value(lead, "pricing_discussion"),
        "agreed_percentage": _lead_value(lead, "agreed_percentage"),
        "objections": _lead_value(lead, "objections"),
        "pain_points": _lead_value(lead, "pain_points"),
        "interested_status": interested,
        "interest_level": _lead_value(lead, "interest_level"),
        "callback_time": _lead_value(lead, "callback_time"),
        "follow_up_status": follow_status,
        "follow_up_notes": _lead_value(lead, "follow_up_notes"),
        "onboarding_status": _lead_value(lead, "onboarding_status"),
        "assigned_dispatcher": _lead_value(lead, "assigned_dispatcher"),
        "remarks": _lead_value(lead, "remarks"),
        "close_probability": _lead_value(lead, "close_probability"),
        "urgency": _lead_value(lead, "urgency"),
        "best_follow_up_strategy": _lead_value(lead, "best_follow_up_strategy"),
        "last_summary": _lead_value(lead, "post_call_summary", "ai_summary"),
        "last_sentiment": _lead_value(lead, "post_call_sentiment", "sentiment"),
    }


def _identity_matches(conn: sqlite3.Connection, payload: dict) -> list[str]:
    clauses: list[str] = []
    params: list[str] = []
    for column in ("phone_norm", "mc_norm", "email_norm"):
        value = _clean(payload.get(column, ""))
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if not clauses:
        return []
    rows = conn.execute(
        f"SELECT id FROM carriers WHERE {' OR '.join(clauses)} ORDER BY created_at ASC",
        params,
    ).fetchall()
    return [row["id"] for row in rows]


def _carrier_search_text(payload: dict, extra: Iterable[str] = ()) -> str:
    fields = [
        payload.get("company_name", ""),
        payload.get("carrier_name", ""),
        payload.get("phone", ""),
        payload.get("mc_number", ""),
        payload.get("dot_number", ""),
        payload.get("email", ""),
        payload.get("truck_type", ""),
        payload.get("truck_length", ""),
        payload.get("dimensions", ""),
        payload.get("accessories", ""),
        payload.get("preferred_lanes", ""),
        payload.get("local_or_otr", ""),
        payload.get("dispatcher_status", ""),
        payload.get("factoring_company", ""),
        payload.get("pricing_discussion", ""),
        payload.get("agreed_percentage", ""),
        payload.get("objections", ""),
        payload.get("pain_points", ""),
        payload.get("interested_status", ""),
        payload.get("callback_time", ""),
        payload.get("follow_up_status", ""),
        payload.get("remarks", ""),
        payload.get("last_summary", ""),
        *extra,
    ]
    return " ".join(_clean(v) for v in fields if _clean(v))


def _refresh_carrier_index(conn: sqlite3.Connection, carrier_id: str) -> None:
    row = conn.execute("SELECT * FROM carriers WHERE id=?", (carrier_id,)).fetchone()
    if not row:
        return
    payload = dict(row)
    note_text = " ".join(
        r["text"] for r in conn.execute("SELECT text FROM notes WHERE carrier_id=?", (carrier_id,))
    )
    call_text = " ".join(
        r["searchable_text"] for r in conn.execute("SELECT searchable_text FROM connected_calls WHERE carrier_id=?", (carrier_id,))
    )
    content = _carrier_search_text(payload, [note_text, call_text])
    conn.execute(
        "UPDATE carriers SET searchable_text=?, updated_at=? WHERE id=?",
        (content, _now(), carrier_id),
    )
    _index_document(conn, "carrier", carrier_id, carrier_id, content)


def _merge_duplicate_carriers(conn: sqlite3.Connection, winner_id: str, loser_ids: list[str]) -> None:
    for loser_id in loser_ids:
        if loser_id == winner_id:
            continue
        winner = conn.execute("SELECT * FROM carriers WHERE id=?", (winner_id,)).fetchone()
        loser = conn.execute("SELECT * FROM carriers WHERE id=?", (loser_id,)).fetchone()
        if not winner or not loser:
            continue
        updates: dict[str, str] = {}
        for key in CARRIER_EXPORT_FIELDS + [
            "phone_norm", "mc_norm", "email_norm", "pricing_discussion", "objections",
            "pain_points", "interest_level", "follow_up_notes", "best_follow_up_strategy",
            "last_summary", "last_sentiment", "remarks",
        ]:
            if key in ("id", "updated_at"):
                continue
            if not _clean(winner[key]) and _clean(loser[key]):
                updates[key] = loser[key]
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            conn.execute(
                f"UPDATE carriers SET {assignments}, updated_at=? WHERE id=?",
                [*updates.values(), _now(), winner_id],
            )
        for table in ("connected_calls", "call_artifacts", "notes", "follow_ups"):
            conn.execute(f"UPDATE {table} SET carrier_id=? WHERE carrier_id=?", (winner_id, loser_id))
        if _fts_available(conn):
            conn.execute("UPDATE crm_fts SET carrier_id=? WHERE carrier_id=?", (winner_id, loser_id))
        conn.execute("DELETE FROM carriers WHERE id=?", (loser_id,))


def upsert_carrier_profile(
    lead: dict,
    contact: Optional[dict] = None,
    session: Optional[CallSession] = None,
) -> str:
    payload = _carrier_payload(lead, contact or {}, session)
    with _connect() as conn:
        matches = _identity_matches(conn, payload)
        carrier_id = matches[0] if matches else hashlib.sha1(
            "|".join([
                payload.get("phone_norm", ""),
                payload.get("mc_norm", ""),
                payload.get("email_norm", ""),
                str(uuid.uuid4()) if not any((payload.get("phone_norm"), payload.get("mc_norm"), payload.get("email_norm"))) else "",
            ]).encode("utf-8")
        ).hexdigest()[:16]

        if matches[1:]:
            _merge_duplicate_carriers(conn, carrier_id, matches[1:])

        existing = conn.execute("SELECT id FROM carriers WHERE id=?", (carrier_id,)).fetchone()
        payload["searchable_text"] = _carrier_search_text(payload)
        now = _now()
        if existing:
            updates = {k: v for k, v in payload.items() if _clean(v)}
            if updates:
                assignments = ", ".join(f"{key}=?" for key in updates)
                conn.execute(
                    f"UPDATE carriers SET {assignments}, updated_at=? WHERE id=?",
                    [*updates.values(), now, carrier_id],
                )
        else:
            columns = ["id", "created_at", "updated_at", *payload.keys()]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO carriers({', '.join(columns)}) VALUES ({placeholders})",
                [carrier_id, now, now, *payload.values()],
            )
        _refresh_carrier_index(conn, carrier_id)
        return carrier_id


# ---------------------------------------------------------------------------
# Finalization: sessions -> artifacts + CRM
# ---------------------------------------------------------------------------

def finalize_call_session(
    session: CallSession,
    contact: Optional[dict] = None,
    lead: Optional[dict] = None,
    groq_api_key: str = "",
    model: str = "llama-3.3-70b-versatile",
    stt_model: str = "whisper-large-v3-turbo",
) -> dict:
    """
    Archive a finished call and, for real connected conversations, upsert the CRM.

    Connected-call records are created only when there is answered-call evidence and
    at least one non-empty prospect/carrier transcript turn. Silent connected calls
    are archived under failed_calls with reason=silent_connected.
    """
    ensure_storage_dirs()
    contact = contact or {}
    lead = dict(lead or {})
    call_id = _call_id(session, contact, lead)

    _ensure_transcript_from_recording(session, call_id, groq_api_key, stt_model)
    transcript_path = Path(session.transcript_path) if session.transcript_path else None
    recording_path = Path(session.recording_path) if session.recording_path else None
    transcript_text = _read_text(transcript_path)
    call_type, reason = _classify_call(session, transcript_text)

    if call_type == "connected":
        lead = _extract_lead_if_needed(session, contact, lead, groq_api_key, model)

    lead["phone_number"] = _lead_value(lead, "phone_number") or session.phone or contact.get("phone", "")
    lead["contact_name"] = _lead_value(lead, "contact_name") or contact.get("name", "") or session.contact_name
    lead["timestamp"] = _lead_value(lead, "timestamp") or _now()
    if transcript_path:
        lead["transcript_file"] = str(transcript_path)
    if not lead.get("call_outcome"):
        lead["call_outcome"] = session.outcome or session.state.value

    storage_root = _storage_dir_for(call_type)
    call_dir = storage_root / call_id
    copied_transcript = _copy_artifact(transcript_path, call_dir, f"{call_id}_transcript.txt")
    copied_recording = _copy_artifact(recording_path, call_dir, "recording.wav")
    if copied_transcript:
        lead["transcript_file"] = copied_transcript

    carrier_id = upsert_carrier_profile(lead, contact, session)

    if call_type in ("connected", "voicemail"):
        if call_type == "voicemail":
            lead.setdefault("call_outcome", "Voicemail")
            lead.setdefault("interested", lead.get("interested") or "")
            if not _lead_value(lead, "post_call_summary", "ai_summary") and transcript_text.strip():
                lead["post_call_summary"] = transcript_text.strip()[:500]
            if not _lead_value(lead, "post_call_summary", "ai_summary"):
                lead["post_call_summary"] = "Voicemail — no live conversation"
            lead["company_name"] = _lead_value(lead, "company_name") or contact.get("company", "") or contact.get("name", "")
            lead["contact_name"] = _lead_value(lead, "contact_name") or contact.get("name", "") or session.contact_name
        try:
            from src.leads import upsert_lead  # type: ignore

            upsert_lead(lead, LEADS_FILE)
        except Exception as exc:
            logger.warning("leads.csv upsert during CRM finalize failed: %s", exc)

    ai_summary = _lead_value(lead, "post_call_summary", "ai_summary")
    metadata = {
        "id": call_id,
        "call_type": call_type,
        "reason": reason,
        "carrier_id": carrier_id,
        "session": _session_payload(session),
        "lead": lead,
        "transcript_path": copied_transcript,
        "recording_path": copied_recording,
        "ai_summary": ai_summary,
    }
    _write_json(call_dir / "metadata.json", metadata)
    if ai_summary:
        _write_json(call_dir / "summary.json", {
            "call_id": call_id,
            "carrier_id": carrier_id,
            "summary": ai_summary,
            "sentiment": _lead_value(lead, "post_call_sentiment", "sentiment"),
            "close_probability": _lead_value(lead, "close_probability"),
            "urgency": _lead_value(lead, "urgency"),
            "pain_points": _lead_value(lead, "pain_points"),
            "best_follow_up_strategy": _lead_value(lead, "best_follow_up_strategy"),
        })

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO call_artifacts(
                id, carrier_id, call_type, timestamp, phone, contact_name, outcome,
                duration, transcript_path, recording_path, ai_summary, storage_dir,
                reason, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                carrier_id,
                call_type,
                lead["timestamp"],
                _norm_phone(lead.get("phone_number")),
                lead.get("contact_name", ""),
                lead.get("call_outcome", ""),
                _duration(session),
                copied_transcript,
                copied_recording,
                ai_summary,
                str(call_dir),
                reason,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

        if call_type == "connected":
            call_search = " ".join([
                _carrier_search_text(_carrier_payload(lead, contact, session)),
                transcript_text,
                ai_summary,
                _lead_value(lead, "objections"),
                _lead_value(lead, "pain_points"),
                _lead_value(lead, "best_follow_up_strategy"),
            ])
            conn.execute(
                """
                INSERT OR REPLACE INTO connected_calls(
                    id, carrier_id, timestamp, company_name, carrier_name, phone,
                    mc_number, dot_number, email, truck_type, truck_length,
                    dimensions, accessories, preferred_lanes, local_or_otr,
                    agreed_percentage, interested_status, callback_time, remarks,
                    duration, transcript_path, transcript_filename, recording_path,
                    recording_filename, ai_summary, sentiment, close_probability,
                    urgency, pain_points, objections, follow_up_status, storage_dir,
                    searchable_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    call_id,
                    carrier_id,
                    lead["timestamp"],
                    _lead_value(lead, "company_name"),
                    lead.get("contact_name", ""),
                    _norm_phone(lead.get("phone_number")),
                    _lead_value(lead, "mc_number"),
                    _lead_value(lead, "dot_number"),
                    _lead_value(lead, "email"),
                    _lead_value(lead, "truck_type"),
                    _lead_value(lead, "truck_length", "dimensions"),
                    _lead_value(lead, "dimensions", "truck_length"),
                    _lead_value(lead, "accessories"),
                    _lead_value(lead, "preferred_lanes", "lanes"),
                    _lead_value(lead, "local_or_otr", "otr_local_preference"),
                    _lead_value(lead, "agreed_percentage"),
                    _lead_value(lead, "interested", "interested_status"),
                    _lead_value(lead, "callback_time"),
                    _lead_value(lead, "remarks"),
                    _duration(session),
                    copied_transcript,
                    Path(copied_transcript).name if copied_transcript else "",
                    copied_recording,
                    Path(copied_recording).name if copied_recording else "",
                    ai_summary,
                    _lead_value(lead, "post_call_sentiment", "sentiment"),
                    _lead_value(lead, "close_probability"),
                    _lead_value(lead, "urgency"),
                    _lead_value(lead, "pain_points"),
                    _lead_value(lead, "objections"),
                    _lead_value(lead, "follow_up_status"),
                    str(call_dir),
                    call_search,
                ),
            )
            _index_document(conn, "connected_call", call_id, carrier_id, call_search)
        _refresh_carrier_index(conn, carrier_id)

    return {
        "stored": call_type == "connected",
        "call_type": call_type,
        "reason": reason,
        "call_id": call_id,
        "carrier_id": carrier_id,
        "storage_dir": str(call_dir),
        "transcript_path": copied_transcript,
        "recording_path": copied_recording,
    }


# ---------------------------------------------------------------------------
# Connected calls
# ---------------------------------------------------------------------------

def _public_call(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    data["name"] = data.get("carrier_name", "")
    data["interested"] = data.get("interested_status", "")
    data["connected_duration_s"] = data.get("duration", "")
    data["connected_duration"] = _format_duration(data.get("duration"))
    data["transcript_file"] = data.get("transcript_path", "")
    data["post_call_summary"] = data.get("ai_summary", "")
    data["post_call_sentiment"] = data.get("sentiment", "")
    return data


def _public_artifact(row: sqlite3.Row | dict) -> dict:
    data = dict(row)
    carrier: dict = {}
    carrier_id = data.get("carrier_id", "")
    if carrier_id:
        try:
            with _connect() as conn:
                crow = conn.execute(
                    "SELECT company_name, carrier_name, email, truck_type, truck_length, "
                    "preferred_lanes, interested_status FROM carriers WHERE id=?",
                    (carrier_id,),
                ).fetchone()
            if crow:
                carrier = dict(crow)
        except Exception:
            carrier = {}
    name = (
        data.get("contact_name")
        or carrier.get("carrier_name")
        or carrier.get("company_name")
        or ""
    )
    return {
        "id": data.get("id", ""),
        "timestamp": data.get("timestamp", ""),
        "phone": data.get("phone", ""),
        "company_name": carrier.get("company_name", "") or name,
        "carrier_name": carrier.get("carrier_name", "") or name,
        "name": name,
        "email": carrier.get("email", ""),
        "truck_type": carrier.get("truck_type", ""),
        "truck_length": carrier.get("truck_length", ""),
        "preferred_lanes": carrier.get("preferred_lanes", ""),
        "interested_status": carrier.get("interested_status", ""),
        "interested": carrier.get("interested_status", ""),
        "follow_up_status": data.get("call_type", "").title() if data.get("call_type") else "Open",
        "call_type": data.get("call_type", ""),
        "duration": data.get("duration", 0),
        "connected_duration_s": data.get("duration", 0),
        "connected_duration": _format_duration(data.get("duration")),
        "recording_path": data.get("recording_path", ""),
        "transcript_path": data.get("transcript_path", ""),
        "transcript_file": data.get("transcript_path", ""),
        "ai_summary": data.get("ai_summary", ""),
        "post_call_summary": data.get("ai_summary", ""),
        "outcome": data.get("outcome", ""),
        "reason": data.get("reason", ""),
    }


def get_recent_call_artifacts(
    limit: int = 100,
    call_types: tuple[str, ...] = ("connected", "voicemail"),
) -> list[dict]:
    """Recent calls for Recordings / activity views (includes voicemail with audio)."""
    if not call_types:
        return []
    placeholders = ", ".join("?" for _ in call_types)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM call_artifacts
            WHERE call_type IN ({placeholders})
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (*call_types, limit),
        ).fetchall()
    return [_public_artifact(row) for row in rows]


def get_connected_calls() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM connected_calls ORDER BY timestamp DESC"
        ).fetchall()
    if not rows:
        return _legacy_connected_calls()
    return [_public_call(row) for row in rows]


def get_call_artifact(call_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM call_artifacts WHERE id=?", (call_id,)).fetchone()
    return _public_artifact(row) if row else None


def get_call_for_ui(call_id: str) -> Optional[dict]:
    """Resolve a call row for recording viewer (connected table or artifact)."""
    return get_connected_call(call_id) or get_call_artifact(call_id)


def get_connected_call(call_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM connected_calls WHERE id=?", (call_id,)).fetchone()
    return _public_call(row) if row else None


def search_connected_calls(query: str) -> list[dict]:
    q = _clean(query)
    if not q:
        return get_connected_calls()
    like = f"%{q.lower()}%"
    with _connect() as conn:
        rows: list[sqlite3.Row] = []
        if _fts_available(conn):
            try:
                ids = [
                    row["entity_id"]
                    for row in conn.execute(
                        "SELECT entity_id FROM crm_fts WHERE entity_type='connected_call' AND crm_fts MATCH ?",
                        (_search_expr(q),),
                    )
                ]
                if ids:
                    placeholders = ", ".join("?" for _ in ids)
                    rows = conn.execute(
                        f"SELECT * FROM connected_calls WHERE id IN ({placeholders}) ORDER BY timestamp DESC",
                        ids,
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            rows = conn.execute(
                """
                SELECT * FROM connected_calls
                WHERE lower(searchable_text) LIKE ?
                   OR lower(company_name) LIKE ?
                   OR lower(carrier_name) LIKE ?
                   OR lower(phone) LIKE ?
                   OR lower(mc_number) LIKE ?
                   OR lower(email) LIKE ?
                ORDER BY timestamp DESC
                """,
                (like, like, like, like, like, like),
            ).fetchall()
    return [_public_call(row) for row in rows]


def export_connected_calls_csv(rows: Optional[list[dict]] = None) -> str:
    rows = rows if rows is not None else get_connected_calls()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CONNECTED_CALL_EXPORT_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in CONNECTED_CALL_EXPORT_FIELDS})
    return buf.getvalue()


def connected_calls_stats(calls: list[dict]) -> dict:
    total = len(calls)
    interested = sum(1 for c in calls if c.get("interested_status") in ("Yes", "Hot Lead", "Interested"))
    maybe = sum(1 for c in calls if c.get("interested_status") == "Maybe")
    no = sum(1 for c in calls if c.get("interested_status") == "No")
    dnc = sum(1 for c in calls if c.get("interested_status") == "DNC")
    durations: list[float] = []
    for call in calls:
        try:
            durations.append(float(call.get("duration") or call.get("connected_duration_s") or 0))
        except (TypeError, ValueError):
            pass
    avg_dur = _format_duration(sum(durations) / len(durations)) if durations else "-"
    return {
        "total": total,
        "interested": interested,
        "maybe": maybe,
        "no": no,
        "dnc": dnc,
        "avg_duration": avg_dur,
    }


# ---------------------------------------------------------------------------
# Carrier CRM
# ---------------------------------------------------------------------------

def _carrier_counts(conn: sqlite3.Connection, carrier_id: str) -> dict:
    calls = conn.execute(
        "SELECT COUNT(*) AS n FROM call_artifacts WHERE carrier_id=?", (carrier_id,)
    ).fetchone()["n"]
    connected = conn.execute(
        "SELECT COUNT(*) AS n FROM connected_calls WHERE carrier_id=?", (carrier_id,)
    ).fetchone()["n"]
    notes = conn.execute(
        "SELECT COUNT(*) AS n FROM notes WHERE carrier_id=?", (carrier_id,)
    ).fetchone()["n"]
    return {"call_count": calls, "connected_count": connected, "note_count": notes}


def _public_carrier(row: sqlite3.Row | dict, counts: Optional[dict] = None) -> dict:
    data = dict(row)
    data["name"] = data.get("carrier_name", "")
    data["interested"] = data.get("interested_status", "")
    data["post_call_summary"] = data.get("last_summary", "")
    data["post_call_sentiment"] = data.get("last_sentiment", "")
    if counts:
        data.update(counts)
    return data


def _legacy_carrier_profiles() -> list[dict]:
    leads = _read_leads()
    calls = _read_call_logs()
    leads_idx = _leads_by_phone(leads)
    calls_by_phone: dict[str, list[dict]] = {}
    for call in calls:
        phone = _norm_phone(call.get("phone", ""))
        if phone:
            calls_by_phone.setdefault(phone, []).append(call)
    profiles: list[dict] = []
    for phone in set(leads_idx) | set(calls_by_phone):
        lead = leads_idx.get(phone, {})
        pcalls = sorted(calls_by_phone.get(phone, []), key=lambda c: c.get("timestamp", ""))
        connected = [c for c in pcalls if c.get("connected_at")]
        last_call = pcalls[-1] if pcalls else {}
        profiles.append({
            "id": phone,
            "phone": phone,
            "carrier_name": lead.get("contact_name") or last_call.get("name", ""),
            "name": lead.get("contact_name") or last_call.get("name", ""),
            "company_name": lead.get("company_name", ""),
            "mc_number": lead.get("mc_number", ""),
            "dot_number": lead.get("dot_number", ""),
            "email": lead.get("email", ""),
            "truck_type": lead.get("truck_type", ""),
            "interested_status": lead.get("interested", ""),
            "interested": lead.get("interested", ""),
            "last_contact": last_call.get("timestamp") or lead.get("timestamp", ""),
            "call_count": len(pcalls),
            "connected_count": len(connected),
            "callback_time": lead.get("callback_time", ""),
            "close_probability": lead.get("close_probability", ""),
            "preferred_lanes": lead.get("preferred_lanes", ""),
            "local_or_otr": lead.get("local_or_otr", ""),
            "agreed_percentage": lead.get("agreed_percentage", ""),
            "urgency": lead.get("urgency", ""),
            "follow_up_status": lead.get("follow_up_status", ""),
            "post_call_summary": lead.get("post_call_summary", ""),
        })
    profiles.sort(key=lambda p: p.get("last_contact", ""), reverse=True)
    return profiles


def get_carrier_profiles() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM carriers ORDER BY updated_at DESC").fetchall()
        profiles = [_public_carrier(row, _carrier_counts(conn, row["id"])) for row in rows]
    if not profiles:
        return _legacy_carrier_profiles()
    return profiles


def _find_carrier_id(conn: sqlite3.Connection, identifier: str) -> Optional[str]:
    raw = _clean(identifier)
    if not raw:
        return None
    candidates = {
        "id": raw,
        "phone_norm": _norm_phone(raw),
        "mc_norm": _norm_mc(raw),
        "email_norm": _norm_email(raw),
    }
    for column, value in candidates.items():
        if not value:
            continue
        row = conn.execute(f"SELECT id FROM carriers WHERE {column}=?", (value,)).fetchone()
        if row:
            return row["id"]
    return None


def get_carrier_profile(identifier: str) -> Optional[dict]:
    with _connect() as conn:
        carrier_id = _find_carrier_id(conn, identifier)
        if not carrier_id:
            return None
        row = conn.execute("SELECT * FROM carriers WHERE id=?", (carrier_id,)).fetchone()
        if not row:
            return None
        calls = [
            _public_call(r)
            for r in conn.execute(
                "SELECT * FROM connected_calls WHERE carrier_id=? ORDER BY timestamp DESC",
                (carrier_id,),
            ).fetchall()
        ]
        artifacts = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM call_artifacts WHERE carrier_id=? ORDER BY timestamp DESC",
                (carrier_id,),
            ).fetchall()
        ]
        notes = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM notes WHERE carrier_id=? ORDER BY timestamp DESC",
                (carrier_id,),
            ).fetchall()
        ]
        follow_ups = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM follow_ups WHERE carrier_id=? ORDER BY timestamp DESC",
                (carrier_id,),
            ).fetchall()
        ]
    profile = _public_carrier(row)
    recordings = [a for a in artifacts if a.get("recording_path")]
    transcripts = [a for a in artifacts if a.get("transcript_path")]
    summaries = [a for a in artifacts if a.get("ai_summary")]
    timeline: list[dict] = []
    for call in calls:
        timeline.append({"type": "connected_call", **call})
    for artifact in artifacts:
        if artifact.get("call_type") != "connected":
            timeline.append({"type": artifact.get("call_type", "call"), **artifact})
    for note in notes:
        timeline.append({"type": "note", **note})
    for follow in follow_ups:
        timeline.append({"type": "follow_up", **follow})
    timeline.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    profile.update({
        "calls": calls,
        "call_history": artifacts,
        "recordings": recordings,
        "transcripts": transcripts,
        "ai_summaries": summaries,
        "notes": notes,
        "follow_ups": follow_ups,
        "follow_up_history": follow_ups,
        "timeline": timeline,
        "call_count": len(artifacts),
        "connected_count": len(calls),
        "last_contact": timeline[0].get("timestamp", "") if timeline else profile.get("updated_at", ""),
    })
    return profile


def search_carrier_crm(query: str) -> list[dict]:
    q = _clean(query)
    if not q:
        return get_carrier_profiles()
    like = f"%{q.lower()}%"
    with _connect() as conn:
        carrier_ids: list[str] = []
        if _fts_available(conn):
            try:
                carrier_ids = list({
                    row["carrier_id"]
                    for row in conn.execute(
                        "SELECT carrier_id FROM crm_fts WHERE crm_fts MATCH ?",
                        (_search_expr(q),),
                    )
                    if row["carrier_id"]
                })
            except sqlite3.OperationalError:
                carrier_ids = []
        if not carrier_ids:
            rows = conn.execute(
                """
                SELECT DISTINCT c.id
                FROM carriers c
                LEFT JOIN connected_calls cc ON cc.carrier_id = c.id
                LEFT JOIN notes n ON n.carrier_id = c.id
                WHERE lower(c.searchable_text) LIKE ?
                   OR lower(c.company_name) LIKE ?
                   OR lower(c.carrier_name) LIKE ?
                   OR lower(c.phone) LIKE ?
                   OR lower(c.mc_number) LIKE ?
                   OR lower(c.email) LIKE ?
                   OR lower(cc.searchable_text) LIKE ?
                   OR lower(n.text) LIKE ?
                ORDER BY c.updated_at DESC
                """,
                (like, like, like, like, like, like, like, like),
            ).fetchall()
            carrier_ids = [row["id"] for row in rows]
        if not carrier_ids:
            return []
        placeholders = ", ".join("?" for _ in carrier_ids)
        rows = conn.execute(
            f"SELECT * FROM carriers WHERE id IN ({placeholders}) ORDER BY updated_at DESC",
            carrier_ids,
        ).fetchall()
        return [_public_carrier(row, _carrier_counts(conn, row["id"])) for row in rows]


def edit_carrier(identifier: str, updates: dict) -> Optional[dict]:
    allowed = set(CARRIER_EXPORT_FIELDS) | {
        "carrier_name", "interest_level", "pricing_discussion", "objections",
        "pain_points", "follow_up_notes", "best_follow_up_strategy",
        "last_summary", "last_sentiment", "remarks",
    }
    with _connect() as conn:
        carrier_id = _find_carrier_id(conn, identifier)
        if not carrier_id:
            return None
        clean_updates = {k: _clean(v) for k, v in updates.items() if k in allowed}
        if "phone" in clean_updates:
            clean_updates["phone_norm"] = _norm_phone(clean_updates["phone"])
        if "mc_number" in clean_updates:
            clean_updates["mc_norm"] = _norm_mc(clean_updates["mc_number"])
        if "email" in clean_updates:
            clean_updates["email_norm"] = _norm_email(clean_updates["email"])
        if clean_updates:
            assignments = ", ".join(f"{key}=?" for key in clean_updates)
            conn.execute(
                f"UPDATE carriers SET {assignments}, updated_at=? WHERE id=?",
                [*clean_updates.values(), _now(), carrier_id],
            )
        _refresh_carrier_index(conn, carrier_id)
    return get_carrier_profile(carrier_id)


def add_carrier_note(identifier: str, text: str, author: str = "") -> dict:
    text = _clean(text)
    if not text:
        raise ValueError("Note text is required")
    with _connect() as conn:
        carrier_id = _find_carrier_id(conn, identifier)
        if not carrier_id:
            raise KeyError(f"Carrier not found: {identifier}")
        note = {
            "id": uuid.uuid4().hex,
            "carrier_id": carrier_id,
            "timestamp": _now(),
            "text": text,
            "author": _clean(author),
        }
        conn.execute(
            "INSERT INTO notes(id, carrier_id, timestamp, text, author) VALUES (?, ?, ?, ?, ?)",
            (note["id"], carrier_id, note["timestamp"], note["text"], note["author"]),
        )
        _index_document(conn, "note", note["id"], carrier_id, text)
        _refresh_carrier_index(conn, carrier_id)
        _append_legacy_note(identifier, note)
        return note


def _append_legacy_note(identifier: str, note: dict) -> None:
    """Keep carrier_notes.json readable by older builds."""
    try:
        NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if NOTES_FILE.exists():
            data = json.loads(NOTES_FILE.read_text(encoding="utf-8"))
        key = _norm_phone(identifier)
        data.setdefault(key, []).append({
            "timestamp": note["timestamp"],
            "text": note["text"],
            "author": note.get("author", ""),
        })
        NOTES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("Legacy note append skipped: %s", exc)


def schedule_follow_up(
    identifier: str,
    status: str,
    callback_time: str = "",
    notes: str = "",
    assigned_dispatcher: str = "",
) -> dict:
    status = _clean(status) or "Follow Up Today"
    if status not in FOLLOW_UP_STATUSES:
        raise ValueError(f"Invalid follow-up status: {status}")
    with _connect() as conn:
        carrier_id = _find_carrier_id(conn, identifier)
        if not carrier_id:
            raise KeyError(f"Carrier not found: {identifier}")
        follow = {
            "id": uuid.uuid4().hex,
            "carrier_id": carrier_id,
            "timestamp": _now(),
            "status": status,
            "callback_time": _clean(callback_time),
            "notes": _clean(notes),
            "assigned_dispatcher": _clean(assigned_dispatcher),
        }
        conn.execute(
            """
            INSERT INTO follow_ups(id, carrier_id, timestamp, status, callback_time, notes, assigned_dispatcher)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                follow["id"], carrier_id, follow["timestamp"], follow["status"],
                follow["callback_time"], follow["notes"], follow["assigned_dispatcher"],
            ),
        )
        conn.execute(
            """
            UPDATE carriers
            SET follow_up_status=?, callback_time=COALESCE(NULLIF(?, ''), callback_time),
                assigned_dispatcher=COALESCE(NULLIF(?, ''), assigned_dispatcher), updated_at=?
            WHERE id=?
            """,
            (status, follow["callback_time"], follow["assigned_dispatcher"], _now(), carrier_id),
        )
        _index_document(
            conn,
            "follow_up",
            follow["id"],
            carrier_id,
            " ".join([status, follow["callback_time"], follow["notes"], follow["assigned_dispatcher"]]),
        )
        _refresh_carrier_index(conn, carrier_id)
        return follow


def assign_dispatcher(identifier: str, dispatcher: str) -> Optional[dict]:
    return edit_carrier(identifier, {"assigned_dispatcher": dispatcher})


def export_profile(identifier: str) -> Optional[dict]:
    return get_carrier_profile(identifier)


def export_carriers_csv(rows: Optional[list[dict]] = None) -> str:
    rows = rows if rows is not None else get_carrier_profiles()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CARRIER_EXPORT_FIELDS)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in CARRIER_EXPORT_FIELDS})
    return buf.getvalue()


def recording_path_for_call(call_id: str) -> Optional[Path]:
    cid = _clean(call_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT recording_path FROM connected_calls WHERE id=?",
            (cid,),
        ).fetchone()
        if not row or not row["recording_path"]:
            row = conn.execute(
                "SELECT recording_path FROM call_artifacts WHERE id=?",
                (cid,),
            ).fetchone()
    if not row or not row["recording_path"]:
        return None
    path = Path(row["recording_path"])
    try:
        if path.exists():
            return path
    except Exception:
        return None
    return None


def get_transcript_text(filename: str) -> str:
    if not filename:
        return ""
    safe = Path(filename).name
    candidates = [
        TRANSCRIPTS_DIR / safe,
        CONNECTED_CALLS_DIR / safe,
    ]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT transcript_path FROM connected_calls WHERE transcript_filename=? OR id=?",
            (safe, _clean(filename)),
        ).fetchall()
        candidates.extend(Path(r["transcript_path"]) for r in rows if r["transcript_path"])
    for path in candidates:
        try:
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Transcript read error (%s): %s", filename, exc)
    return ""


def carrier_stats(profiles: list[dict]) -> dict:
    total = len(profiles)
    interested = sum(1 for p in profiles if p.get("interested_status") in ("Yes", "Interested", "Hot Lead"))
    callbacks = sum(1 for p in profiles if p.get("callback_time"))
    dnc = sum(1 for p in profiles if p.get("interested_status") == "DNC" or p.get("follow_up_status") == "DNC")
    return {
        "total": total,
        "interested": interested,
        "callbacks": callbacks,
        "dnc": dnc,
    }
