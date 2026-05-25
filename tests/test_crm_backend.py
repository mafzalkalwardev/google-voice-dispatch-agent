from __future__ import annotations

import csv
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from src.call_session import CallSession, CallState


@pytest.fixture()
def crm_env(tmp_path: Path, monkeypatch):
    import src.crm as crm

    monkeypatch.setattr(crm, "BASE_DIR", tmp_path)
    monkeypatch.setattr(crm, "CALL_LOG_FILE", tmp_path / "logs" / "call_logs.csv")
    monkeypatch.setattr(crm, "LEADS_FILE", tmp_path / "logs" / "leads.csv")
    monkeypatch.setattr(crm, "NOTES_FILE", tmp_path / "logs" / "carrier_notes.json")
    monkeypatch.setattr(crm, "TRANSCRIPTS_DIR", tmp_path / "logs" / "transcripts")
    monkeypatch.setattr(crm, "RECORDINGS_DIR", tmp_path / "logs" / "recordings")
    monkeypatch.setattr(crm, "CRM_DB_FILE", tmp_path / "logs" / "carrier_crm.sqlite3")
    monkeypatch.setattr(crm, "CONNECTED_CALLS_DIR", tmp_path / "connected_calls")
    monkeypatch.setattr(crm, "VOICEMAIL_CALLS_DIR", tmp_path / "voicemail_calls")
    monkeypatch.setattr(crm, "FAILED_CALLS_DIR", tmp_path / "failed_calls")
    return crm


def _connected_session(phone: str = "+15551234567", name: str = "Sam Carrier") -> CallSession:
    session = CallSession(phone=phone, contact_name=name)
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED, "answered-call timer detected")
    session.transition(CallState.ENDED)
    session.outcome = "ENDED"
    return session


def _write_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\0\0" * 1600)
    return path


def _lead(**overrides: str) -> dict:
    data = {
        "company_name": "Road Star Logistics",
        "contact_name": "Sam Carrier",
        "phone_number": "+15551234567",
        "mc_number": "MC-123456",
        "dot_number": "DOT-987654",
        "email": "sam@roadstar.test",
        "truck_type": "Dry Van",
        "truck_length": "53ft",
        "dimensions": "53ft dry van",
        "accessories": "load bars",
        "preferred_lanes": "Midwest to Texas",
        "local_or_otr": "OTR",
        "factoring_company": "OTR Capital",
        "agreed_percentage": "6%",
        "interested": "Yes",
        "callback_time": "Thursday 2pm",
        "follow_up_status": "Interested",
        "post_call_sentiment": "positive",
        "close_probability": "75%",
        "urgency": "medium",
        "pain_points": "deadhead and weak Florida outbound",
        "best_follow_up_strategy": "Lead with Midwest reload planning",
        "post_call_summary": "Warm dry van carrier interested in dispatch help.",
    }
    data.update(overrides)
    return data


def test_connected_call_is_archived_linked_and_exported(crm_env, tmp_path: Path) -> None:
    crm = crm_env
    transcript = tmp_path / "call.txt"
    transcript.write_text(
        "[12:00:00] Tony: What truck are you running?\n"
        "[12:00:04] Prospect: I run a 53 foot dry van from Chicago to Dallas.\n",
        encoding="utf-8",
    )
    recording = _write_wav(tmp_path / "call.wav")
    session = _connected_session()
    session.transcript_path = transcript
    session.recording_path = recording

    result = crm.finalize_call_session(
        session=session,
        contact={"name": "Sam Carrier", "phone": "+15551234567"},
        lead=_lead(),
    )

    assert result["stored"] is True
    assert result["call_type"] == "connected"
    assert "connected_calls" in result["storage_dir"]
    assert Path(result["transcript_path"]).exists()
    assert Path(result["recording_path"]).exists()

    calls = crm.get_connected_calls()
    assert len(calls) == 1
    assert calls[0]["company_name"] == "Road Star Logistics"
    assert calls[0]["recording_path"].endswith("recording.wav")

    profile = crm.get_carrier_profile("+15551234567")
    assert profile is not None
    assert profile["mc_number"] == "MC-123456"
    assert len(profile["calls"]) == 1
    assert len(profile["recordings"]) == 1
    assert len(profile["transcripts"]) == 1
    assert profile["ai_summaries"][0]["ai_summary"].startswith("Warm dry van")

    csv_text = crm.export_connected_calls_csv()
    assert "Road Star Logistics" in csv_text
    assert "connected_calls.csv" not in csv_text

    with crm.LEADS_FILE.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["company_name"] == "Road Star Logistics"
    assert rows[0]["dot_number"] == "DOT-987654"


def test_silent_voicemail_and_failed_calls_are_excluded_from_connected_calls(crm_env, tmp_path: Path) -> None:
    crm = crm_env

    silent_transcript = tmp_path / "silent.txt"
    silent_transcript.write_text("[12:00:00] Tony: Hi, this is Tony.\n", encoding="utf-8")
    silent = _connected_session(phone="+15550000001", name="Silent Carrier")
    silent.transcript_path = silent_transcript
    silent_result = crm.finalize_call_session(silent, {"name": "Silent Carrier", "phone": silent.phone}, _lead(phone_number=silent.phone))
    assert silent_result["stored"] is False
    assert silent_result["reason"] == "silent_connected"
    assert "failed_calls" in silent_result["storage_dir"]

    voicemail = CallSession(phone="+15550000002", contact_name="VM Carrier")
    voicemail.transition(CallState.DIALING)
    voicemail.transition(CallState.VOICEMAIL)
    voicemail.transition(CallState.ENDED)
    voicemail.outcome = "VOICEMAIL"
    vm_result = crm.finalize_call_session(voicemail, {"name": "VM Carrier", "phone": voicemail.phone}, {})
    assert vm_result["call_type"] == "voicemail"
    assert "voicemail_calls" in vm_result["storage_dir"]

    failed = CallSession(phone="+15550000003", contact_name="Failed Carrier")
    failed.transition(CallState.DIALING)
    failed.transition(CallState.FAILED)
    failed.outcome = "DIAL_FAILED"
    failed_result = crm.finalize_call_session(failed, {"name": "Failed Carrier", "phone": failed.phone}, {})
    assert failed_result["call_type"] == "failed"

    assert crm.get_connected_calls() == []


def test_duplicate_carriers_merge_by_phone_mc_or_email(crm_env, tmp_path: Path) -> None:
    crm = crm_env
    for idx, phone in enumerate(("+15551110000", "+15552220000")):
        transcript = tmp_path / f"call{idx}.txt"
        transcript.write_text(
            "[12:00:00] Tony: Tell me about your lanes.\n"
            "[12:00:04] Prospect: Midwest to Texas has been working for my reefer.\n",
            encoding="utf-8",
        )
        session = _connected_session(phone=phone, name=f"Carrier {idx}")
        session.transcript_path = transcript
        crm.finalize_call_session(
            session,
            {"name": f"Carrier {idx}", "phone": phone},
            _lead(phone_number=phone, email=f"carrier{idx}@test.com", mc_number="MC-777777"),
        )

    profiles = crm.get_carrier_profiles()
    assert len(profiles) == 1
    profile = crm.get_carrier_profile("MC-777777")
    assert profile is not None
    assert len(profile["calls"]) == 2


def test_search_indexes_transcripts_notes_and_identity_fields(crm_env, tmp_path: Path) -> None:
    crm = crm_env
    transcript = tmp_path / "indexed.txt"
    transcript.write_text(
        "[12:00:00] Tony: What is painful right now?\n"
        "[12:00:05] Prospect: Detention and deadhead are killing us in Florida.\n",
        encoding="utf-8",
    )
    session = _connected_session()
    session.transcript_path = transcript
    result = crm.finalize_call_session(session, {"name": "Sam Carrier", "phone": session.phone}, _lead())
    crm.add_carrier_note(result["carrier_id"], "Send factoring checklist and W9 packet.")

    assert crm.search_connected_calls("Florida")[0]["id"] == result["call_id"]
    assert crm.search_carrier_crm("factoring checklist")[0]["id"] == result["carrier_id"]
    assert crm.search_carrier_crm("sam@roadstar.test")[0]["id"] == result["carrier_id"]
    assert crm.search_carrier_crm("MC-123456")[0]["id"] == result["carrier_id"]


def test_actions_followups_dispatcher_and_profile_export(crm_env, tmp_path: Path) -> None:
    crm = crm_env
    transcript = tmp_path / "profile.txt"
    transcript.write_text(
        "[12:00:00] Tony: Are you interested?\n"
        "[12:00:05] Prospect: Yes, call me tomorrow about onboarding docs.\n",
        encoding="utf-8",
    )
    session = _connected_session()
    session.transcript_path = transcript
    result = crm.finalize_call_session(session, {"name": "Sam Carrier", "phone": session.phone}, _lead())

    follow = crm.schedule_follow_up(
        result["carrier_id"],
        status="Waiting For Documents",
        callback_time="Tomorrow 10am",
        notes="Send packet before callback",
        assigned_dispatcher="Tony",
    )
    assert follow["status"] == "Waiting For Documents"
    crm.assign_dispatcher(result["carrier_id"], "Aisha")
    profile = crm.export_profile(result["carrier_id"])
    assert profile is not None
    assert profile["assigned_dispatcher"] == "Aisha"
    assert profile["follow_ups"][0]["callback_time"] == "Tomorrow 10am"
    assert "Road Star Logistics" in crm.export_carriers_csv()


def test_finalize_can_transcribe_recording_before_extraction(crm_env, tmp_path: Path) -> None:
    crm = crm_env
    session = _connected_session(phone="+15553334444", name="Reefer Carrier")
    session.recording_path = _write_wav(tmp_path / "incoming.wav")

    extracted = _lead(
        company_name="Cold Chain LLC",
        contact_name="Reefer Carrier",
        phone_number="+15553334444",
        truck_type="Reefer",
        preferred_lanes="Texas outbound produce",
    )
    with patch("src.crm.transcribe_recording", return_value="I run a reefer out of Texas for produce season."), \
         patch("src.leads.extract_lead_from_transcript", return_value=extracted) as mock_extract:
        result = crm.finalize_call_session(
            session,
            {"name": "Reefer Carrier", "phone": "+15553334444"},
            lead={},
            groq_api_key="gsk_test",
        )

    assert result["stored"] is True
    mock_extract.assert_called_once()
    call = crm.get_connected_call(result["call_id"])
    assert call is not None
    assert call["truck_type"] == "Reefer"
    assert Path(result["transcript_path"]).read_text(encoding="utf-8").count("Prospect:") == 1
