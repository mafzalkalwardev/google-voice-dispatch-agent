"""Tests for SQLite contacts index."""

from pathlib import Path

import pytest

from src.contacts_index import build_index, is_index_current, query_contacts


@pytest.fixture
def sample_csv(tmp_path):
    p = tmp_path / "contacts.csv"
    p.write_text(
        "Name,Phone\n"
        "Alice Corp,+12125550100\n"
        "Bob Trucking,+12125550101\n"
        "Carol LLC,+12125550102\n",
        encoding="utf-8",
    )
    return p


def test_build_and_query(sample_csv, monkeypatch):
    db = sample_csv.parent / "idx.sqlite3"
    monkeypatch.setattr("src.contacts_index.INDEX_DB", db)
    count = build_index(sample_csv, force=True)
    assert count == 3
    assert is_index_current(sample_csv)
    page = query_contacts(sample_csv, page=1, per_page=2, ensure_index=False)
    assert page["total"] == 3
    assert len(page["rows"]) == 2
    assert page["pages"] == 2
