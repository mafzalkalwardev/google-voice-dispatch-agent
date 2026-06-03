"""Tests for batch resume progress tracking."""

from pathlib import Path

from src.batch_progress import (
    contacts_fingerprint,
    filter_contacts,
    get_completed_phones,
    mark_completed,
    reset_progress,
)


def test_batch_progress_resume_skips_completed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "src.batch_progress.PROGRESS_FILE",
        tmp_path / "batch_progress.json",
    )
    contacts_file = tmp_path / "contacts.csv"
    contacts_file.write_text("name,phone\nA,+15551111111\nB,+15552222222\n", encoding="utf-8")
    fp = contacts_fingerprint(contacts_file)
    reset_progress(fp)

    mark_completed(fp, "+15551111111", 1)
    contacts = [
        {"name": "A", "phone": "+15551111111"},
        {"name": "B", "phone": "+15552222222"},
    ]
    remaining = filter_contacts(contacts, fp, resume=True)
    assert len(remaining) == 1
    assert remaining[0]["phone"] == "+15552222222"
    assert "+15551111111" in get_completed_phones(fp)

    reset_progress(fp)
    assert filter_contacts(contacts, fp, resume=True) == contacts
