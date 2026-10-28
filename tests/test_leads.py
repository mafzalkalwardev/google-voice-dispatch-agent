"""Unit tests for src.leads — read/write/upsert logic."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.leads import LEADS_HEADERS, read_leads, upsert_lead


@pytest.fixture()
def leads_file(tmp_path: Path) -> Path:
    return tmp_path / "leads.csv"


def test_read_leads_nonexistent_creates_file(leads_file: Path) -> None:
    result = read_leads(path=leads_file)
    assert result == []
    assert leads_file.exists()


def test_upsert_single_lead(leads_file: Path) -> None:
    upsert_lead({"contact_name": "Alice", "phone_number": "+17775551234"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert len(rows) == 1
    assert rows[0]["contact_name"] == "Alice"
    assert rows[0]["phone_number"] == "+17775551234"


def test_upsert_merges_existing_by_phone(leads_file: Path) -> None:
    upsert_lead({"contact_name": "Bob", "phone_number": "+17775559999", "interested": "Maybe"}, path=leads_file)
    upsert_lead({"phone_number": "+17775559999", "interested": "Yes", "callback_time": "Thursday 2pm"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert len(rows) == 1
    assert rows[0]["interested"] == "Yes"
    assert rows[0]["callback_time"] == "Thursday 2pm"
    assert rows[0]["contact_name"] == "Bob"  # preserved from first insert


def test_upsert_adds_new_row_for_different_phone(leads_file: Path) -> None:
    upsert_lead({"contact_name": "A", "phone_number": "+17775550001"}, path=leads_file)
    upsert_lead({"contact_name": "B", "phone_number": "+17775550002"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert len(rows) == 2


def test_read_leads_newest_first(leads_file: Path) -> None:
    upsert_lead({"contact_name": "First", "phone_number": "+17775550001"}, path=leads_file)
    upsert_lead({"contact_name": "Second", "phone_number": "+17775550002"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert rows[0]["contact_name"] == "Second"
    assert rows[1]["contact_name"] == "First"


def test_all_headers_present_in_row(leads_file: Path) -> None:
    upsert_lead({}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert len(rows) == 1
    for h in LEADS_HEADERS:
        assert h in rows[0], f"Missing header: {h}"


def test_upsert_without_phone_always_inserts(leads_file: Path) -> None:
    upsert_lead({"contact_name": "No Phone"}, path=leads_file)
    upsert_lead({"contact_name": "Also No Phone"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert len(rows) == 2


def test_timestamp_auto_filled(leads_file: Path) -> None:
    upsert_lead({"contact_name": "Timestamped"}, path=leads_file)
    rows = read_leads(path=leads_file)
    assert rows[0]["timestamp"] != ""


def test_extract_lead_no_transcript(tmp_path: Path) -> None:
    from src.leads import extract_lead_from_transcript

    result = extract_lead_from_transcript(
        transcript_path=tmp_path / "nonexistent.txt",
        contact={"name": "Test Contact", "phone": "+1234567890"},
        groq_api_key="fake_key_no_call",
    )
    assert isinstance(result, dict)
    for h in LEADS_HEADERS:
        assert h in result


def test_extract_lead_empty_transcript(tmp_path: Path) -> None:
    from src.leads import extract_lead_from_transcript

    t = tmp_path / "empty.txt"
    t.write_text("", encoding="utf-8")
    result = extract_lead_from_transcript(
        transcript_path=t,
        contact={"name": "Test", "phone": "+1"},
        groq_api_key="fake_key",
    )
    assert isinstance(result, dict)
    assert all(h in result for h in LEADS_HEADERS)


def test_extract_lead_no_api_key(tmp_path: Path) -> None:
    from src.leads import extract_lead_from_transcript

    t = tmp_path / "t.txt"
    t.write_text("[12:00:00] Tony: Hi there\n[12:00:05] Prospect: Hello", encoding="utf-8")
    result = extract_lead_from_transcript(
        transcript_path=t,
        contact={"name": "X", "phone": "+1"},
        groq_api_key="",
    )
    assert all(result[h] == "" for h in LEADS_HEADERS)


def test_extract_lead_includes_post_call_summary_fields(tmp_path: Path) -> None:
    from src.leads import extract_lead_from_transcript

    transcript = tmp_path / "call.txt"
    transcript.write_text(
        "\n".join([
            "[12:00:00] Tony: What truck are you running?",
            "[12:00:05] Prospect: ABC Trucking, MC 123456, 53ft dry van, Midwest to Texas.",
            "[12:00:20] Prospect: We use OTR Capital and I hoped for 5%. Call me Thursday.",
        ]),
        encoding="utf-8",
    )
    payload = {
        "company_name": "ABC Trucking",
        "contact_name": "Sam",
        "mc_number": "MC-123456",
        "email": "sam@example.com",
        "truck_type": "Dry Van",
        "truck_length": "53ft",
        "dimensions": "53ft",
        "accessories": "load bars",
        "preferred_lanes": "Midwest to Texas",
        "local_or_otr": "OTR",
        "dispatcher_status": "Self-dispatched",
        "factoring_company": "OTR Capital",
        "pricing_discussion": "Carrier asked for 5%",
        "agreed_percentage": "5%",
        "objections": "price",
        "interest_level": "warm",
        "interested": "Yes",
        "callback_time": "Thursday",
        "follow_up_status": "callback booked",
        "follow_up_notes": "Call Thursday",
        "post_call_sentiment": "positive",
        "close_probability": "70%",
        "urgency": "medium",
        "pain_points": "rates have been rough",
        "best_follow_up_strategy": "Lead with Midwest to Texas lane planning",
        "post_call_summary": "Warm dry van lead with pricing discussion.",
        "remarks": "Good fit",
        "call_outcome": "Interested - callback Thursday",
    }
    resp = MagicMock()
    resp.choices[0].message.content = json.dumps(payload)
    with patch("groq.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.return_value = resp
        result = extract_lead_from_transcript(
            transcript_path=transcript,
            contact={"name": "Sam", "phone": "+15551234567"},
            groq_api_key="gsk_test",
        )
    assert result["truck_type"] == "Dry Van"
    assert result["factoring_company"] == "OTR Capital"
    assert result["pricing_discussion"] == "Carrier asked for 5%"
    assert result["post_call_sentiment"] == "positive"
    assert result["close_probability"] == "70%"
    assert result["best_follow_up_strategy"].startswith("Lead with")


def test_extract_lead_includes_spectrum_business_fields(tmp_path: Path) -> None:
    from src.leads import extract_lead_from_transcript

    transcript = tmp_path / "spectrum_call.txt"
    transcript.write_text(
        "\n".join([
            "[12:00:00] Jason: Are you the person who handles internet and phone services?",
            "[12:00:05] Prospect: Yes, we use AT&T and have outages. We need two phone lines.",
            "[12:00:20] Prospect: Tuesday at 10 AM works for a technician visit.",
        ]),
        encoding="utf-8",
    )
    payload = {
        "company_name": "Main Street Cafe",
        "contact_name": "Pat",
        "decision_maker": "Yes",
        "current_provider": "AT&T",
        "internet_needs": "Reliable internet for POS and guest WiFi",
        "outages_or_issues": "Outages",
        "phone_needs": "Two business phone lines",
        "phone_lines": "2",
        "appointment_day": "Tuesday",
        "appointment_time": "10 AM",
        "appointment_window": "Tuesday 10 AM",
        "services_discussed": "Internet, Voice, WiFi",
        "interest_level": "hot",
        "interested": "Yes",
        "call_outcome": "Appointment requested - Tuesday 10 AM",
    }
    resp = MagicMock()
    resp.choices[0].message.content = json.dumps(payload)
    with patch("groq.Groq") as MockGroq:
        MockGroq.return_value.chat.completions.create.return_value = resp
        result = extract_lead_from_transcript(
            transcript_path=transcript,
            contact={"name": "Pat", "phone": "+15551234567"},
            groq_api_key="gsk_test",
        )

    assert result["current_provider"] == "AT&T"
    assert result["decision_maker"] == "Yes"
    assert result["appointment_window"] == "Tuesday 10 AM"
    assert result["services_discussed"] == "Internet, Voice, WiFi"
