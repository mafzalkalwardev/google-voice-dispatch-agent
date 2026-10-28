"""Tests for src/web_app.py FastAPI routes."""
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """Build a TestClient with isolated tmp dirs for logs/data."""
    tmp = tmp_path_factory.mktemp("webapp")
    logs_dir = tmp / "logs"
    logs_dir.mkdir()
    data_dir = tmp / "data"
    data_dir.mkdir()

    # Patch BASE_DIR so the app doesn't write to the real project
    with patch("src.web_app.BASE_DIR", tmp), \
         patch("src.web_app.CALL_LOG_FILE", logs_dir / "call_logs.csv"), \
         patch("src.web_app.CONFIG_FILE", tmp / "dialer_config.json"), \
         patch("src.web_app.DATA_DIR", data_dir):
        from fastapi.testclient import TestClient
        from src.web_app import app
        yield TestClient(app)


# ── Page routes return 200 ────────────────────────────────────────────────────

def test_dashboard_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "INDUS TRANSPORTS" in r.text
    assert "/static/indus-logo.jpg" in r.text


def test_preflight_page_loads(client):
    r = client.get("/preflight")
    assert r.status_code == 200
    assert "Preflight" in r.text


def test_settings_page_loads(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Settings" in r.text


def test_contacts_page_loads(client):
    r = client.get("/contacts")
    assert r.status_code == 200
    assert "Contacts" in r.text


def test_audio_page_loads(client):
    with patch("src.voice_playback.list_audio_devices", return_value=[]):
        r = client.get("/audio")
    assert r.status_code == 200
    assert "Audio" in r.text


def test_run_page_loads(client):
    r = client.get("/run")
    assert r.status_code == 200
    assert "Live Run" in r.text
    assert "Single Test Call" in r.text


def test_logs_page_loads(client):
    r = client.get("/logs")
    assert r.status_code == 200
    assert "Call Logs" in r.text


def test_connected_calls_page_loads(client):
    with patch("src.crm.get_connected_calls", return_value=[]), \
         patch("src.crm.connected_calls_stats", return_value={
             "total": 0, "interested": 0, "maybe": 0, "no": 0, "dnc": 0, "avg_duration": "-",
         }):
        r = client.get("/connected-calls")
    assert r.status_code == 200
    assert "Connected Calls" in r.text


def test_carrier_crm_page_loads(client):
    with patch("src.crm.get_carrier_profiles", return_value=[]), \
         patch("src.crm.carrier_stats", return_value={
             "total": 0, "interested": 0, "callbacks": 0, "dnc": 0,
         }):
        r = client.get("/carrier-crm")
    assert r.status_code == 200
    assert "Carrier CRM" in r.text


def test_carrier_profile_page_loads(client):
    profile = {
        "id": "carrier1",
        "company_name": "Road Star Logistics",
        "carrier_name": "Sam Carrier",
        "phone": "+15551234567",
        "timeline": [],
        "calls": [],
        "connected_count": 0,
    }
    with patch("src.crm.get_carrier_profile", return_value=profile):
        r = client.get("/carrier-crm/carrier1")
    assert r.status_code == 200
    assert "Road Star Logistics" in r.text


# ── Branding present ──────────────────────────────────────────────────────────

def test_branding_in_all_pages(client):
    pages = ["/", "/preflight", "/settings", "/contacts", "/run", "/logs"]
    for url in pages:
        with patch("src.voice_playback.list_audio_devices", return_value=[]):
            r = client.get(url)
        assert "INDUS TRANSPORTS" in r.text, f"Branding missing on {url}"
        assert "Muhammad Afzal" in r.text, f"Developer credit missing on {url}"
        assert "+923079670503" in r.text, f"WhatsApp missing on {url}"


# ── API: preflight ────────────────────────────────────────────────────────────

def test_api_preflight_returns_list(client):
    from src.preflight import CheckResult
    results = [
        CheckResult("ENV File", "ok", ".env present"),
        CheckResult("Groq API", "ok", "Connected"),
    ]
    with patch("src.preflight.run_all", return_value=results):
        r = client.post("/api/preflight")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "name" in data[0]
    assert "status" in data[0]


# ── API: settings ─────────────────────────────────────────────────────────────

def test_api_settings_save_and_load(client, tmp_path):
    payload = {
        "agent_name": "TestTony",
        "groq_model": "llama-3.3-70b-versatile",
        "call_timeout": "45",
        "vad_threshold": "0.02",
    }
    r = client.post("/api/settings", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "agent_name" in data["saved"]


def test_api_settings_ignores_unknown_keys(client):
    payload = {"agent_name": "Tony", "evil_key": "inject"}
    r = client.post("/api/settings", json=payload)
    data = r.json()
    assert "evil_key" not in data.get("saved", [])


# ── API: contacts upload ──────────────────────────────────────────────────────

def test_contacts_upload_csv(client):
    csv_content = b"Name,Phone\nAlice Carrier,+12125550100\nBob Trucking,+12125550101\n"
    with patch("src.contacts.load_contacts") as mock_load:
        mock_load.return_value = [
            {"name": "Alice Carrier", "phone": "+12125550100"},
            {"name": "Bob Trucking",  "phone": "+12125550101"},
        ]
        r = client.post(
            "/api/contacts/upload",
            files={"file": ("contacts.csv", BytesIO(csv_content), "text/csv")},
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["count"] == 2


def test_contacts_upload_bad_format(client):
    r = client.post(
        "/api/contacts/upload",
        files={"file": ("evil.exe", BytesIO(b"data"), "application/octet-stream")},
    )
    assert r.status_code == 400


# ── API: audio devices ────────────────────────────────────────────────────────

def test_api_audio_devices(client):
    fake_devices = [
        {"index": 0, "name": "Speakers", "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 44100},
        {"index": 1, "name": "CABLE Input", "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 44100},
    ]
    with patch("src.voice_playback.list_audio_devices", return_value=fake_devices):
        r = client.get("/api/audio/devices")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2


# ── API: run status ───────────────────────────────────────────────────────────

def test_api_run_status_idle(client):
    r = client.get("/api/run/status")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "is_running" in data


def test_api_run_stop_when_idle(client):
    r = client.post("/api/run/stop")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True


def test_api_run_start_no_duplicate(client):
    """Starting a second run while one is running returns an error."""
    from src.web_app import run_manager
    # Simulate a running process
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # still running
    mock_proc.pid = 9999
    run_manager._process = mock_proc
    run_manager._status = "running"

    r = client.post("/api/run/start", json={"limit": 1, "dry_run": True})
    data = r.json()
    assert data["ok"] is False

    # Cleanup
    run_manager._process = None
    run_manager._status = "idle"


# ── API: log lines ────────────────────────────────────────────────────────────

def test_api_single_call_writes_one_contact_and_starts(client):
    from src.web_app import DATA_DIR, run_manager

    with patch.object(run_manager, "start", return_value={"ok": True, "pid": 1234}) as mock_start:
        r = client.post("/api/run/single-call", json={
            "phone": "212-555-0100",
            "name": "Acme Carrier",
            "dry_run": True,
            "profile_name": "sales_profile",
            "callback_number": "+15551234567",
            "loopback_device": "CABLE Input",
            "capture_device": "default",
        })

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["phone"] == "+12125550100"

    args = mock_start.call_args.args[0]
    assert args[args.index("--limit") + 1] == "1"
    assert "--dry-run" in args

    contacts_path = Path(args[args.index("--contacts") + 1])
    assert contacts_path == DATA_DIR / "single_test_call.csv"
    text = contacts_path.read_text(encoding="utf-8")
    assert "Acme Carrier" in text
    assert "+12125550100" in text


def test_api_single_call_rejects_invalid_phone(client):
    r = client.post("/api/run/single-call", json={"phone": "abc", "dry_run": True})
    assert r.status_code == 400


def test_api_run_lines_empty(client):
    r = client.get("/api/run/lines?since=0")
    assert r.status_code == 200
    data = r.json()
    assert "lines" in data
    assert isinstance(data["lines"], list)


# ── API: call logs ────────────────────────────────────────────────────────────

def test_api_logs_empty(client):
    r = client.get("/api/logs")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_api_logs_with_data(client, tmp_path):
    log_file = tmp_path / "call_logs.csv"
    log_file.write_text(
        "timestamp,phone,name,status,outcome,started_at,connected_at,"
        "voicemail_detected_at,ended_at,connected_duration_s,total_duration_s,notes\n"
        "2026-05-23 12:00:00,+12125550100,Alice,ENDED,ENDED,,,,,12.5,30.0,\n"
    )
    with patch("src.web_app.CALL_LOG_FILE", log_file):
        r = client.get("/api/logs")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Alice"


def test_connected_calls_api_routes(client):
    call = {"id": "call1", "company_name": "Road Star Logistics"}
    with patch("src.crm.get_connected_calls", return_value=[call]):
        r = client.get("/api/connected-calls")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "call1"

    with patch("src.crm.search_connected_calls", return_value=[call]):
        r = client.get("/api/connected-calls/search?q=Road")
    assert r.status_code == 200
    assert r.json()[0]["company_name"] == "Road Star Logistics"

    with patch("src.crm.get_connected_call", return_value=call):
        r = client.get("/api/connected-calls/call1")
    assert r.status_code == 200
    assert r.json()["id"] == "call1"

    with patch("src.crm.get_connected_calls", return_value=[call]), \
         patch("src.crm.export_connected_calls_csv", return_value="id,company_name\ncall1,Road Star Logistics\n"):
        r = client.get("/api/connected-calls/export")
    assert r.status_code == 200
    assert "Road Star Logistics" in r.text


def test_carrier_crm_api_routes(client):
    profile = {"id": "carrier1", "company_name": "Road Star Logistics"}
    with patch("src.crm.get_carrier_profiles", return_value=[profile]):
        r = client.get("/api/carrier-crm")
    assert r.status_code == 200
    assert r.json()[0]["id"] == "carrier1"

    with patch("src.crm.search_carrier_crm", return_value=[profile]):
        r = client.get("/api/carrier-crm/search?q=Road")
    assert r.status_code == 200
    assert r.json()[0]["company_name"] == "Road Star Logistics"

    with patch("src.crm.get_carrier_profile", return_value=profile):
        r = client.get("/api/carrier-crm/carrier1")
    assert r.status_code == 200
    assert r.json()["id"] == "carrier1"

    with patch("src.crm.edit_carrier", return_value=profile):
        r = client.patch("/api/carrier-crm/carrier1", json={"follow_up_status": "Hot Lead"})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    with patch("src.crm.add_carrier_note", return_value={"id": "note1", "text": "Call back"}):
        r = client.post("/api/carrier-crm/carrier1/notes", json={"text": "Call back"})
    assert r.status_code == 200
    assert r.json()["note"]["id"] == "note1"

    with patch("src.crm.schedule_follow_up", return_value={"id": "fu1", "status": "Hot Lead"}):
        r = client.post("/api/carrier-crm/carrier1/follow-up", json={"status": "Hot Lead"})
    assert r.status_code == 200
    assert r.json()["follow_up"]["status"] == "Hot Lead"

    with patch("src.crm.assign_dispatcher", return_value=profile):
        r = client.post("/api/carrier-crm/carrier1/assign-dispatcher", json={"dispatcher": "Tony"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
