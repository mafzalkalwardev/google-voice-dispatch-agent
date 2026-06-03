"""Per-number call memory: learn from outcomes and avoid repeating mistakes."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.contacts import normalize_phone
from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent.Intelligence")

BASE_DIR = runtime_base()
DB_FILE = BASE_DIR / "logs" / "call_intelligence.sqlite3"

_MAX_LESSONS = 12
_MAX_MISTAKES = 10
_MAX_OPENINGS = 8
_VM_BLOCK_WITHOUT_CONNECT = 4
_NO_ANSWER_BLOCK_WITHOUT_CONNECT = 5


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _phone_key(phone: str) -> str:
    return normalize_phone(phone) or str(phone or "").strip()


def _json_list(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except json.JSONDecodeError:
        pass
    return []


def _json_dump(items: list[str], *, limit: int = 10) -> str:
    cleaned = [str(x).strip() for x in items if str(x).strip()]
    return json.dumps(cleaned[-limit:], ensure_ascii=False)


def _append_unique(existing: list[str], value: str, *, limit: int) -> list[str]:
    value = (value or "").strip()
    if not value:
        return existing
    out = [x for x in existing if x.lower() != value.lower()]
    out.append(value)
    return out[-limit:]


def _connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS contact_memory (
                phone_norm TEXT PRIMARY KEY,
                phone_display TEXT DEFAULT '',
                contact_name TEXT DEFAULT '',
                call_count INTEGER DEFAULT 0,
                connect_count INTEGER DEFAULT 0,
                voicemail_count INTEGER DEFAULT 0,
                no_answer_count INTEGER DEFAULT 0,
                last_outcome TEXT DEFAULT '',
                last_call_at TEXT DEFAULT '',
                last_connected_at TEXT DEFAULT '',
                interested TEXT DEFAULT '',
                dnc INTEGER DEFAULT 0,
                blocked INTEGER DEFAULT 0,
                block_reason TEXT DEFAULT '',
                truck_type TEXT DEFAULT '',
                carrier_style TEXT DEFAULT '',
                objections TEXT DEFAULT '[]',
                lessons TEXT DEFAULT '[]',
                mistakes TEXT DEFAULT '[]',
                good_openings TEXT DEFAULT '[]',
                bad_openings TEXT DEFAULT '[]',
                last_opening TEXT DEFAULT '',
                last_summary TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_contact_memory_blocked
                ON contact_memory(blocked, dnc);
            """
        )


def get_memory(phone: str) -> Optional[dict[str, Any]]:
    key = _phone_key(phone)
    if not key:
        return None
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM contact_memory WHERE phone_norm = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["objections"] = _json_list(d.get("objections") or "")
    d["lessons"] = _json_list(d.get("lessons") or "")
    d["mistakes"] = _json_list(d.get("mistakes") or "")
    d["good_openings"] = _json_list(d.get("good_openings") or "")
    d["bad_openings"] = _json_list(d.get("bad_openings") or "")
    d["dnc"] = bool(d.get("dnc"))
    d["blocked"] = bool(d.get("blocked"))
    return d


def should_skip_call(phone: str) -> tuple[bool, str]:
    mem = get_memory(phone)
    if not mem:
        return False, ""
    if mem.get("dnc"):
        return True, "Do-not-call (carrier requested removal)"
    if mem.get("blocked"):
        reason = mem.get("block_reason") or "Blocked by call intelligence"
        return True, reason
    if "wrong_number" in [m.lower() for m in mem.get("mistakes", [])]:
        return True, "Wrong number — do not redial"
    connects = int(mem.get("connect_count") or 0)
    if connects == 0:
        if int(mem.get("voicemail_count") or 0) >= _VM_BLOCK_WITHOUT_CONNECT:
            return True, f"{_VM_BLOCK_WITHOUT_CONNECT}+ voicemails without live conversation"
        if int(mem.get("no_answer_count") or 0) >= _NO_ANSWER_BLOCK_WITHOUT_CONNECT:
            return True, f"{_NO_ANSWER_BLOCK_WITHOUT_CONNECT}+ no-answer attempts"
    if (mem.get("interested") or "").lower() in ("no", "dnc") and int(mem.get("call_count") or 0) >= 2:
        if connects == 0:
            return True, "Repeated not-interested / never connected"
    return False, ""


def filter_dialable_contacts(contacts: list[dict]) -> tuple[list[dict], int, list[dict]]:
    """Return dialable contacts, skip count, and skipped rows with reasons."""
    dialable: list[dict] = []
    skipped_rows: list[dict] = []
    for c in contacts:
        phone = c.get("phone", "")
        skip, reason = should_skip_call(phone)
        if skip:
            skipped_rows.append({**c, "skip_reason": reason})
            continue
        dialable.append(c)
    return dialable, len(skipped_rows), skipped_rows


def build_prompt_addon(phone: str) -> str:
    mem = get_memory(phone)
    if not mem:
        return ""
    lines: list[str] = []
    if int(mem.get("call_count") or 0) > 0:
        lines.append(f"- Prior calls to this number: {mem['call_count']} (connected {mem.get('connect_count', 0)} times)")
    if mem.get("last_outcome"):
        lines.append(f"- Last outcome: {mem['last_outcome']}")
    if mem.get("interested"):
        lines.append(f"- Last interest level: {mem['interested']}")
    if mem.get("truck_type"):
        lines.append(f"- Known equipment: {mem['truck_type']}")
    if mem.get("carrier_style") and mem["carrier_style"] != "unknown":
        lines.append(f"- Carrier style: {mem['carrier_style']}")
    if mem.get("objections"):
        lines.append(f"- Past objections: {', '.join(mem['objections'][:5])}")
    if mem.get("lessons"):
        lines.append("- Lessons learned:")
        lines.extend(f"  • {x}" for x in mem["lessons"][-5:])
    if mem.get("mistakes"):
        lines.append("- Do NOT repeat these mistakes:")
        lines.extend(f"  • {x}" for x in mem["mistakes"][-5:])
    if mem.get("good_openings"):
        lines.append(f"- Openings that worked before: {mem['good_openings'][-1]}")
    if mem.get("bad_openings"):
        lines.append(f"- Avoid openings like: {mem['bad_openings'][-1]}")
    if mem.get("last_summary"):
        lines.append(f"- Last call summary: {mem['last_summary'][:280]}")
    if not lines:
        return ""
    return "\n".join(lines)


def get_opening_hints(phone: str) -> tuple[list[str], list[str]]:
    mem = get_memory(phone)
    if not mem:
        return [], []
    return list(mem.get("good_openings") or []), list(mem.get("bad_openings") or [])


def _detect_mistakes(
    *,
    outcome: str,
    connected: bool,
    interested: str,
    transcript_hint: str,
    session_notes: str,
) -> list[str]:
    mistakes: list[str] = []
    lower = f"{transcript_hint} {session_notes}".lower()
    if interested == "DNC" or any(
        p in lower for p in ("remove me", "stop calling", "do not call", "don't call again")
    ):
        mistakes.append("Carrier requested DNC — never call again")
    if any(p in lower for p in ("wrong number", "wrong person", "don't know who", "never heard of")):
        mistakes.append("wrong_number")
    if outcome in ("FAILED", "failed") and not connected:
        if "voicemail" in lower or "leave a message" in lower:
            mistakes.append("Reached voicemail only — adjust timing or opener")
        else:
            mistakes.append("No live conversation — short or bad window")
    if "too pushy" in lower or "stop talking" in lower:
        mistakes.append("Tone was too pushy — stay shorter and softer")
    return mistakes


def _derive_lessons(lead: dict, mem: dict) -> list[str]:
    lessons = list(mem.get("lessons") or [])
    for key in (
        "best_follow_up_strategy",
        "post_call_summary",
        "pain_points",
        "pricing_discussion",
    ):
        val = str(lead.get(key, "")).strip()
        if val:
            lessons = _append_unique(lessons, val[:200], limit=_MAX_LESSONS)
    interested = str(lead.get("interested", "")).strip()
    if interested == "Yes":
        lessons = _append_unique(
            lessons,
            "Carrier showed interest — lead with lanes and onboarding, not a long pitch.",
            limit=_MAX_LESSONS,
        )
    elif interested == "No":
        lessons = _append_unique(
            lessons,
            "Not interested — if calling again, open with respect and one short question only.",
            limit=_MAX_LESSONS,
        )
    return lessons


def record_call(
    *,
    phone: str,
    contact_name: str = "",
    session_state: str = "",
    outcome: str = "",
    connected: bool = False,
    voicemail: bool = False,
    opening_line: str = "",
    lead: Optional[dict] = None,
    transcript_excerpt: str = "",
    session_notes: str = "",
) -> dict[str, Any]:
    """Update memory after a call completes."""
    init_db()
    key = _phone_key(phone)
    if not key:
        return {}

    lead = lead or {}
    interested = str(lead.get("interested", "")).strip()
    now = _now()
    outcome = outcome or session_state or ""
    lower_outcome = outcome.lower()

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM contact_memory WHERE phone_norm = ?",
            (key,),
        ).fetchone()
        mem = _row_to_dict(row) if row else {
            "phone_norm": key,
            "objections": [],
            "lessons": [],
            "mistakes": [],
            "good_openings": [],
            "bad_openings": [],
            "call_count": 0,
            "connect_count": 0,
            "voicemail_count": 0,
            "no_answer_count": 0,
            "dnc": False,
            "blocked": False,
        }

        mem["call_count"] = int(mem.get("call_count") or 0) + 1
        if connected:
            mem["connect_count"] = int(mem.get("connect_count") or 0) + 1
            mem["last_connected_at"] = now
        if voicemail or "voicemail" in lower_outcome:
            mem["voicemail_count"] = int(mem.get("voicemail_count") or 0) + 1
        elif not connected and outcome.upper() in ("FAILED", "NO_ANSWER", "ENDED"):
            mem["no_answer_count"] = int(mem.get("no_answer_count") or 0) + 1

        mem["last_outcome"] = outcome
        mem["last_call_at"] = now
        mem["contact_name"] = contact_name or mem.get("contact_name") or ""
        mem["phone_display"] = phone

        if lead.get("truck_type"):
            mem["truck_type"] = str(lead["truck_type"]).strip()
        if lead.get("objections"):
            for part in re.split(r"[,;]+", str(lead["objections"])):
                part = part.strip()
                if part:
                    mem["objections"] = _append_unique(mem.get("objections", []), part, limit=8)

        summary = str(lead.get("post_call_summary", "")).strip()
        if summary:
            mem["last_summary"] = summary[:500]

        if opening_line:
            mem["last_opening"] = opening_line[:200]
            if connected and interested in ("Yes", "Maybe"):
                mem["good_openings"] = _append_unique(
                    mem.get("good_openings", []), opening_line, limit=_MAX_OPENINGS
                )
            elif not connected or interested in ("No", "DNC"):
                mem["bad_openings"] = _append_unique(
                    mem.get("bad_openings", []), opening_line, limit=_MAX_OPENINGS
                )

        mem["lessons"] = _derive_lessons(lead, mem)
        new_mistakes = _detect_mistakes(
            outcome=outcome,
            connected=connected,
            interested=interested,
            transcript_hint=transcript_excerpt,
            session_notes=session_notes,
        )
        for m in new_mistakes:
            mem["mistakes"] = _append_unique(mem.get("mistakes", []), m, limit=_MAX_MISTAKES)

        if interested:
            mem["interested"] = interested
        if interested == "DNC":
            mem["dnc"] = True
            mem["blocked"] = True
            mem["block_reason"] = "DNC requested on call"

        if "wrong_number" in [x.lower() for x in mem.get("mistakes", [])]:
            mem["blocked"] = True
            mem["block_reason"] = "Wrong number"

        if (
            int(mem.get("connect_count") or 0) == 0
            and int(mem.get("voicemail_count") or 0) >= _VM_BLOCK_WITHOUT_CONNECT
        ):
            mem["blocked"] = True
            mem["block_reason"] = (
                f"Auto-block: {mem['voicemail_count']} voicemails with no live answer"
            )

        if (
            int(mem.get("connect_count") or 0) == 0
            and int(mem.get("no_answer_count") or 0) >= _NO_ANSWER_BLOCK_WITHOUT_CONNECT
        ):
            mem["blocked"] = True
            mem["block_reason"] = (
                f"Auto-block: {mem['no_answer_count']} no-answer attempts"
            )

        conn.execute(
            """
            INSERT INTO contact_memory (
                phone_norm, phone_display, contact_name, call_count, connect_count,
                voicemail_count, no_answer_count, last_outcome, last_call_at,
                last_connected_at, interested, dnc, blocked, block_reason,
                truck_type, carrier_style, objections, lessons, mistakes,
                good_openings, bad_openings, last_opening, last_summary, updated_at
            ) VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(phone_norm) DO UPDATE SET
                phone_display=excluded.phone_display,
                contact_name=excluded.contact_name,
                call_count=excluded.call_count,
                connect_count=excluded.connect_count,
                voicemail_count=excluded.voicemail_count,
                no_answer_count=excluded.no_answer_count,
                last_outcome=excluded.last_outcome,
                last_call_at=excluded.last_call_at,
                last_connected_at=excluded.last_connected_at,
                interested=excluded.interested,
                dnc=excluded.dnc,
                blocked=excluded.blocked,
                block_reason=excluded.block_reason,
                truck_type=excluded.truck_type,
                carrier_style=excluded.carrier_style,
                objections=excluded.objections,
                lessons=excluded.lessons,
                mistakes=excluded.mistakes,
                good_openings=excluded.good_openings,
                bad_openings=excluded.bad_openings,
                last_opening=excluded.last_opening,
                last_summary=excluded.last_summary,
                updated_at=excluded.updated_at
            """,
            (
                key,
                mem.get("phone_display", phone),
                mem.get("contact_name", ""),
                mem["call_count"],
                mem.get("connect_count", 0),
                mem.get("voicemail_count", 0),
                mem.get("no_answer_count", 0),
                mem.get("last_outcome", ""),
                mem.get("last_call_at", ""),
                mem.get("last_connected_at", ""),
                mem.get("interested", ""),
                1 if mem.get("dnc") else 0,
                1 if mem.get("blocked") else 0,
                mem.get("block_reason", ""),
                mem.get("truck_type", ""),
                mem.get("carrier_style", ""),
                _json_dump(mem.get("objections", []), limit=8),
                _json_dump(mem.get("lessons", []), limit=_MAX_LESSONS),
                _json_dump(mem.get("mistakes", []), limit=_MAX_MISTAKES),
                _json_dump(mem.get("good_openings", []), limit=_MAX_OPENINGS),
                _json_dump(mem.get("bad_openings", []), limit=_MAX_OPENINGS),
                mem.get("last_opening", ""),
                mem.get("last_summary", ""),
                now,
            ),
        )
        conn.commit()

    logger.info(
        "Intelligence updated %s: calls=%s blocked=%s dnc=%s",
        key,
        mem.get("call_count"),
        mem.get("blocked"),
        mem.get("dnc"),
    )
    return mem


def record_call_from_session(
    session: Any,
    contact: dict,
    lead: Optional[dict] = None,
    opening_line: str = "",
) -> dict[str, Any]:
    """Bridge from CallSession + lead dict into record_call()."""
    connected = bool(getattr(session, "connected_at", None))
    state_val = getattr(getattr(session, "state", None), "value", "") or str(
        getattr(session, "state", "")
    )
    voicemail = bool(getattr(session, "voicemail_detected_at", None)) or state_val == "VOICEMAIL"
    excerpt = ""
    path = getattr(session, "transcript_path", None)
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            excerpt = text[-1500:]
        except OSError:
            pass
    return record_call(
        phone=getattr(session, "phone", "") or contact.get("phone", ""),
        contact_name=getattr(session, "contact_name", "") or contact.get("name", ""),
        session_state=state_val,
        outcome=getattr(session, "outcome", "") or state_val,
        connected=connected,
        voicemail=voicemail,
        opening_line=opening_line,
        lead=lead,
        transcript_excerpt=excerpt,
        session_notes=getattr(session, "notes", "") or "",
    )


def intelligence_stats() -> dict[str, int]:
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM contact_memory").fetchone()[0]
        blocked = conn.execute(
            "SELECT COUNT(*) FROM contact_memory WHERE blocked = 1 OR dnc = 1"
        ).fetchone()[0]
        with_history = conn.execute(
            "SELECT COUNT(*) FROM contact_memory WHERE call_count > 0"
        ).fetchone()[0]
        connected_once = conn.execute(
            "SELECT COUNT(*) FROM contact_memory WHERE connect_count > 0"
        ).fetchone()[0]
    return {
        "numbers_with_memory": int(total),
        "blocked_or_dnc": int(blocked),
        "called_at_least_once": int(with_history),
        "ever_connected": int(connected_once),
    }


def enrich_contact_row(contact: dict) -> dict:
    """Attach intelligence summary for UI."""
    mem = get_memory(contact.get("phone", ""))
    if not mem:
        return {**contact, "intel_status": "new", "intel_label": "New", "skip_reason": ""}
    skip, reason = should_skip_call(contact.get("phone", ""))
    if mem.get("dnc"):
        label, status = "DNC", "dnc"
    elif mem.get("blocked") or skip:
        label, status = "Blocked", "blocked"
    elif int(mem.get("connect_count") or 0) > 0:
        label, status = f"Connected ×{mem['connect_count']}", "ok"
    elif int(mem.get("call_count") or 0) > 0:
        label, status = f"Called ×{mem['call_count']}", "warn"
    else:
        label, status = "New", "new"
    return {
        **contact,
        "intel_status": status,
        "intel_label": label,
        "skip_reason": reason,
        "call_count": int(mem.get("call_count") or 0),
        "last_outcome": mem.get("last_outcome", ""),
        "interested": mem.get("interested", ""),
    }
