"""
Lead management for INDUS TRANSPORTS LLC.

Reads/writes logs/leads.csv and extracts structured data from call
transcripts using Groq JSON extraction.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.paths import runtime_base

logger = logging.getLogger("GoogleVoiceAgent.Leads")

LEADS_FILE = runtime_base() / "logs" / "leads.csv"

LEADS_HEADERS = [
    "timestamp",
    "company_name",
    "contact_name",
    "phone_number",
    "mc_number",
    "email",
    "truck_type",
    "truck_length",
    "dimensions",
    "accessories",
    "preferred_lanes",
    "local_or_otr",
    "dispatcher_status",
    "factoring_company",
    "pricing_discussion",
    "agreed_percentage",
    "objections",
    "interest_level",
    "interested",
    "callback_time",
    "follow_up_status",
    "follow_up_notes",
    "post_call_sentiment",
    "close_probability",
    "urgency",
    "pain_points",
    "best_follow_up_strategy",
    "post_call_summary",
    "remarks",
    "call_outcome",
    "transcript_file",
]

_INTERESTED_VALUES = {"Yes", "Maybe", "No", "DNC"}

_EXTRACT_SYSTEM = (
    "You extract lead information and a post-call summary from a freight dispatch call transcript.\n"
    'Return ONLY a valid JSON object with these exact keys (use "" for unknown):\n'
    "  company_name, contact_name, mc_number, email, truck_type, truck_length, dimensions,\n"
    "  accessories, preferred_lanes, local_or_otr, dispatcher_status, factoring_company,\n"
    "  pricing_discussion, agreed_percentage, objections, interest_level, interested,\n"
    "  callback_time, follow_up_status, follow_up_notes, post_call_sentiment,\n"
    "  close_probability, urgency, pain_points, best_follow_up_strategy,\n"
    "  post_call_summary, remarks, call_outcome\n\n"
    "Rules:\n"
    '- interested: exactly one of "Yes", "Maybe", "No", "DNC" or ""\n'
    '- interest_level: use "hot", "warm", "cold", "not_interested", "dnc", or ""\n'
    '- close_probability: use a percentage string like "70%" or ""\n'
    '- urgency: use "high", "medium", "low", or ""\n'
    '- call_outcome: short phrase e.g. "Interested - callback Thursday", "Voicemail", "Not interested"\n'
    '- agreed_percentage: commission % if mentioned (e.g. "8%"), else ""\n'
    "- Include factoring company, objections, pain points, and follow-up strategy when stated or clearly inferable.\n"
    "- Do NOT invent data. Use \"\" for unknowns.\n"
    "- Return raw JSON only. No markdown, no code fences, no extra text."
)


def _ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(LEADS_HEADERS)


def read_leads(path: Optional[Path] = None) -> list[dict]:
    """Return all leads newest-first. Creates the file if missing."""
    p = path or LEADS_FILE
    _ensure_file(p)
    rows: list[dict] = []
    try:
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({h: row.get(h, "") for h in LEADS_HEADERS})
    except Exception as exc:
        logger.warning("leads read error: %s", exc)
    return list(reversed(rows))


def _parse_json_object(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start:end + 1])
        raise


def upsert_lead(lead: dict, path: Optional[Path] = None) -> None:
    """Insert a new lead, or merge fields into an existing row matched by phone_number."""
    p = path or LEADS_FILE
    _ensure_file(p)

    phone = str(lead.get("phone_number") or "").strip()
    rows: list[dict] = []
    updated = False

    try:
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if phone and row.get("phone_number", "").strip() == phone:
                    merged = {h: row.get(h, "") for h in LEADS_HEADERS}
                    for k, v in lead.items():
                        if k in LEADS_HEADERS and str(v).strip():
                            merged[k] = str(v).strip()
                    rows.append(merged)
                    updated = True
                else:
                    rows.append({h: row.get(h, "") for h in LEADS_HEADERS})
    except Exception:
        pass

    if not updated:
        new_row = {h: str(lead.get(h, "")).strip() for h in LEADS_HEADERS}
        if not new_row["timestamp"]:
            new_row["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows.append(new_row)

    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LEADS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def extract_lead_from_transcript(
    transcript_path: Path,
    contact: dict,
    groq_api_key: str,
    model: str = "llama-3.3-70b-versatile",
) -> dict:
    """
    Read a transcript file and use Groq to extract structured lead data.
    Returns a partial lead dict. Caller fills timestamp, phone_number, transcript_file.
    Returns empty strings on any failure — never raises.
    """
    blank: dict = {h: "" for h in LEADS_HEADERS}
    if not groq_api_key:
        return blank
    if not transcript_path or not transcript_path.exists():
        logger.debug("No transcript at %s — skipping extraction", transcript_path)
        return blank

    try:
        text = transcript_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        logger.warning("Transcript read error: %s", exc)
        return blank

    if len(text) < 30:
        return blank

    try:
        from groq import Groq  # type: ignore

        client = Groq(api_key=groq_api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": f"TRANSCRIPT:\n{text[:6000]}"},
            ],
            max_tokens=512,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content.strip()
        data = _parse_json_object(raw)
    except Exception as exc:
        logger.warning("Groq lead extraction failed: %s", exc)
        return blank

    result: dict = {h: str(data.get(h, "")).strip() for h in LEADS_HEADERS}
    if result["interested"] not in _INTERESTED_VALUES:
        result["interested"] = ""
    if not result["interested"] and result["interest_level"]:
        level = result["interest_level"].lower()
        if level in ("hot", "warm"):
            result["interested"] = "Yes"
        elif level in ("cold", "not_interested"):
            result["interested"] = "No"
        elif level == "dnc":
            result["interested"] = "DNC"
    if not result["interest_level"] and result["interested"]:
        result["interest_level"] = {
            "Yes": "warm",
            "Maybe": "warm",
            "No": "not_interested",
            "DNC": "dnc",
        }.get(result["interested"], "")
    if not result["dimensions"] and result["truck_length"]:
        result["dimensions"] = result["truck_length"]
    if not result["truck_length"] and result["dimensions"]:
        result["truck_length"] = result["dimensions"]
    # Seed from contact dict when extraction left key fields blank
    if not result["contact_name"]:
        result["contact_name"] = contact.get("name", "")
    if not result["phone_number"]:
        result["phone_number"] = contact.get("phone", "")
    return result
