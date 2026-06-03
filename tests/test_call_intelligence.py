"""Tests for per-number call intelligence memory."""

import pytest

from src.call_intelligence import (
    build_prompt_addon,
    filter_dialable_contacts,
    init_db,
    record_call,
    should_skip_call,
)


@pytest.fixture(autouse=True)
def _temp_intel_db(tmp_path, monkeypatch):
    db = tmp_path / "intel.sqlite3"
    monkeypatch.setattr("src.call_intelligence.DB_FILE", db)
    init_db()
    yield


def test_dnc_blocks_redial():
    record_call(
        phone="+15551234001",
        contact_name="Sam",
        outcome="ENDED",
        connected=True,
        lead={"interested": "DNC"},
    )
    skip, reason = should_skip_call("+15551234001")
    assert skip is True
    assert "not-call" in reason.lower() or "dnc" in reason.lower()


def test_wrong_number_blocks():
    record_call(
        phone="+15551234002",
        contact_name="X",
        outcome="FAILED",
        connected=False,
        transcript_excerpt="sorry wrong number",
    )
    skip, _ = should_skip_call("+15551234002")
    assert skip is True


def test_filter_dialable_skips_blocked():
    contacts = [
        {"phone": "+15551110001", "name": "A"},
        {"phone": "+15551110002", "name": "B"},
    ]
    record_call(phone="+15551110002", lead={"interested": "DNC"})
    dialable, skipped, _ = filter_dialable_contacts(contacts)
    assert len(dialable) == 1
    assert skipped == 1
    assert dialable[0]["phone"] == "+15551110001"


def test_prompt_addon_includes_lessons():
    record_call(
        phone="+15551234003",
        contact_name="Joe",
        outcome="ENDED",
        connected=True,
        lead={
            "interested": "Yes",
            "post_call_summary": "Warm dry van lead in Texas lanes.",
            "best_follow_up_strategy": "Call Thursday with Midwest reload plan.",
        },
    )
    addon = build_prompt_addon("+15551234003")
    assert "PRIOR" not in addon  # addon is body only
    assert "Texas" in addon or "Thursday" in addon or "calls" in addon.lower()
