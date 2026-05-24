"""Tests for src/preflight.py."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── check_env ────────────────────────────────────────────────────────────────

def test_check_env_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from src.preflight import check_env
    r = check_env()
    assert r.status == "warn"
    assert ".env" in r.message


def test_check_env_missing_key(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=your_key_here\n")
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "your_key_here")
    from src.preflight import check_env
    r = check_env()
    assert r.status == "fail"


def test_check_env_ok(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_real_key_abc123\n")
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_real_key_abc123")
    from src.preflight import check_env
    r = check_env()
    assert r.status == "ok"


# ── check_groq_api ───────────────────────────────────────────────────────────

def test_check_groq_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    from src.preflight import check_groq_api
    r = check_groq_api(api_key="")
    assert r.status == "fail"
    assert "not set" in r.message.lower() or "GROQ_API_KEY" in r.message


def test_check_groq_placeholder():
    from src.preflight import check_groq_api
    r = check_groq_api(api_key="your_groq_api_key_here")
    assert r.status == "fail"


def test_check_groq_connection_error():
    from src.preflight import check_groq_api
    with patch("src.preflight.check_groq_api.__wrapped__" if hasattr(check_groq_api, "__wrapped__") else "builtins.open"):
        # Simulate Groq client raising
        with patch("groq.Groq") as MockGroq:
            MockGroq.return_value.models.list.side_effect = Exception("Connection refused")
            r = check_groq_api(api_key="gsk_test_key_that_is_real")
            assert r.status == "fail"
            assert "Connection" in r.message or "failed" in r.message.lower()


def test_check_groq_ok():
    from src.preflight import check_groq_api
    mock_models = MagicMock()
    mock_models.data = [MagicMock(), MagicMock(), MagicMock()]
    with patch("groq.Groq") as MockGroq:
        MockGroq.return_value.models.list.return_value = mock_models
        r = check_groq_api(api_key="gsk_valid_key")
        assert r.status == "ok"
        assert "3" in r.message


# ── check_contacts ───────────────────────────────────────────────────────────

def test_check_contacts_missing(tmp_path):
    from src.preflight import check_contacts
    r = check_contacts(tmp_path / "nonexistent.xlsx")
    assert r.status == "fail"
    assert "Not found" in r.message


def test_check_contacts_ok(tmp_path):
    csv_file = tmp_path / "contacts.csv"
    csv_file.write_text("Name,Phone\nAlice,+12125550100\nBob,+12125550101\n")
    from src.preflight import check_contacts
    r = check_contacts(csv_file)
    assert r.status == "ok"
    assert "2" in r.message


def test_check_contacts_empty(tmp_path):
    csv_file = tmp_path / "contacts.csv"
    csv_file.write_text("Name,Phone\n")
    from src.preflight import check_contacts
    r = check_contacts(csv_file)
    assert r.status in ("warn", "fail")


# ── check_chrome_profile ─────────────────────────────────────────────────────

def test_check_chrome_profile_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    from src.preflight import check_chrome_profile
    r = check_chrome_profile("no_such_profile")
    assert r.status == "warn"
    assert "not found" in r.message.lower()


def test_check_chrome_profile_ok(tmp_path, monkeypatch):
    (tmp_path / "chrome_profiles" / "myprofile").mkdir(parents=True)
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    from src.preflight import check_chrome_profile
    r = check_chrome_profile("myprofile")
    assert r.status == "ok"


# ── check_audio_loopback ─────────────────────────────────────────────────────

def test_check_audio_no_devices():
    from src.preflight import check_audio_loopback
    with patch("src.voice_playback.list_audio_devices", return_value=[]):
        r = check_audio_loopback("CABLE Input")
        assert r.status == "fail"


def test_check_audio_no_loopback():
    from src.preflight import check_audio_loopback
    devices = [{"index": 0, "name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100}]
    with patch("src.voice_playback.list_audio_devices", return_value=devices), \
         patch("src.voice_playback.find_loopback_device", return_value=None):
        r = check_audio_loopback("CABLE Input")
        assert r.status == "fail"
        assert "not found" in r.message.lower()


def test_check_audio_ok():
    from src.preflight import check_audio_loopback
    devices = [{"index": 3, "name": "CABLE Input (VB-Audio)", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100}]
    with patch("src.voice_playback.list_audio_devices", return_value=devices), \
         patch("src.voice_playback.probe_output_device", return_value=(True, "ok")):
        r = check_audio_loopback("CABLE Input")
        assert r.status == "ok"
        assert "[3]" in r.message


def test_check_audio_fails_when_matched_device_unavailable():
    from src.preflight import check_audio_loopback
    devices = [{"index": 6, "name": "CABLE Input (VB-Audio)", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 44100}]
    with patch("src.voice_playback.list_audio_devices", return_value=devices), \
         patch("src.voice_playback.probe_output_device", return_value=(False, "Device unavailable")):
        r = check_audio_loopback("CABLE Input")
        assert r.status == "fail"
        assert "unavailable" in r.message.lower() or "could be opened" in r.message.lower()


# ── check_callback_number ────────────────────────────────────────────────────

def test_check_callback_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("src.preflight.BASE_DIR", tmp_path)
    monkeypatch.delenv("CALLBACK_NUMBER", raising=False)
    monkeypatch.delenv("GOOGLE_VOICE_NUMBER", raising=False)
    from src.preflight import check_callback_number
    r = check_callback_number()
    assert r.status == "fail"


def test_check_callback_ok(monkeypatch):
    monkeypatch.setenv("CALLBACK_NUMBER", "+15555550199")
    from src.preflight import check_callback_number
    r = check_callback_number()
    assert r.status == "ok"
    assert "***" in r.message


# ── run_all ──────────────────────────────────────────────────────────────────

def test_run_all_returns_six_results(monkeypatch):
    monkeypatch.setenv("CALLBACK_NUMBER", "+15551234567")
    with patch("src.preflight.check_env") as ce, \
         patch("src.preflight.check_groq_api") as cg, \
         patch("src.preflight.check_contacts") as cc, \
         patch("src.preflight.check_chrome_profile") as cp, \
         patch("src.preflight.check_audio_loopback") as ca, \
         patch("src.preflight.check_callback_number") as cn:
        from src.preflight import CheckResult
        for mock in (ce, cg, cc, cp, ca, cn):
            mock.return_value = CheckResult("X", "ok", "ok")
        from src.preflight import run_all
        results = run_all()
        assert len(results) == 6
