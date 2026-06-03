"""Backfill leads.csv from voicemail rows in carrier CRM (one-time / after upgrade)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.crm import CRM_DB_FILE, LEADS_FILE  # noqa: E402
from src.leads import upsert_lead  # noqa: E402


def main() -> int:
    if not CRM_DB_FILE.exists():
        print("No CRM database found.")
        return 1
    conn = sqlite3.connect(CRM_DB_FILE)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM call_artifacts WHERE call_type='voicemail' ORDER BY timestamp"
    ).fetchall()
    count = 0
    for row in rows:
        meta = {}
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            pass
        lead = dict(meta.get("lead") or {})
        session = meta.get("session") or {}
        lead.setdefault("timestamp", row["timestamp"])
        lead.setdefault("phone_number", row["phone"])
        lead.setdefault("contact_name", row["contact_name"] or session.get("contact_name", ""))
        lead.setdefault("call_outcome", "Voicemail")
        lead.setdefault("post_call_summary", row["ai_summary"] or "Voicemail — no live conversation")
        if not lead.get("company_name"):
            lead["company_name"] = lead.get("contact_name", "")
        upsert_lead(lead, LEADS_FILE)
        count += 1
    print(f"Upserted {count} voicemail lead(s) into {LEADS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
