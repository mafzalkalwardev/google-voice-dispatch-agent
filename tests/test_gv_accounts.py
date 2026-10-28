import json

import pytest

import src.gv_accounts as accounts


def test_account_password_is_encrypted_and_redacted(tmp_path, monkeypatch):
    account_file = tmp_path / "gv_accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", account_file)
    saved = accounts.save_account({
        "name": "Line One", "email": "line@example.com",
        "password": "not-plain-text", "profile_name": "line_one",
        "loopback_device": "Cable A Input", "capture_device": "Cable A Output",
        "enabled": True,
    })
    assert saved["has_password"] is True
    assert "password" not in saved
    assert "not-plain-text" not in account_file.read_text(encoding="utf-8")
    assert accounts.load_accounts(include_password=True)[0]["password"] == "not-plain-text"


def test_duplicate_profile_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", tmp_path / "gv_accounts.json")
    accounts.save_account({"email": "one@example.com", "profile_name": "same"})
    with pytest.raises(ValueError, match="distinct Chrome profile"):
        accounts.save_account({"email": "two@example.com", "profile_name": "SAME"})


def test_delete_preserves_chrome_profile_data(tmp_path, monkeypatch):
    account_file = tmp_path / "gv_accounts.json"
    monkeypatch.setattr(accounts, "ACCOUNTS_FILE", account_file)
    saved = accounts.save_account({"email": "one@example.com", "profile_name": "one"})
    assert accounts.delete_account(saved["id"]) is True
    assert json.loads(account_file.read_text(encoding="utf-8")) == []
