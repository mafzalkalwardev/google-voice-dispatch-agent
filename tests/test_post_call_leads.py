import logging
from pathlib import Path
from unittest.mock import patch

from src.call_session import CallSession, CallState


def test_extract_and_upsert_lead_after_realtime_call(tmp_path: Path) -> None:
    from src.main import _extract_and_upsert_lead

    transcript = tmp_path / "transcript.txt"
    transcript.write_text("[12:00] Tony: Hi\n[12:01] Prospect: I run dry van", encoding="utf-8")
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)
    session.transition(CallState.ENDED)
    session.outcome = "ENDED"
    session.transcript_path = transcript

    extracted = {
        "contact_name": "",
        "phone_number": "",
        "truck_type": "Dry Van",
        "interested": "Yes",
        "call_outcome": "",
    }
    with patch("src.leads.extract_lead_from_transcript", return_value=extracted) as mock_extract, \
         patch("src.leads.upsert_lead") as mock_upsert, \
         patch("src.crm.finalize_call_session", return_value={
             "call_type": "connected",
             "stored": True,
             "call_id": "call1",
             "carrier_id": "carrier1",
         }) as mock_finalize:
        _extract_and_upsert_lead(
            session=session,
            contact={"name": "Test Carrier", "phone": "+15551234567"},
            groq_api_key="gsk_test",
            model="llama-3.3-70b-versatile",
            logger=logging.getLogger("test"),
            index=1,
        )

    mock_extract.assert_called_once()
    lead = mock_upsert.call_args.args[0]
    assert lead["phone_number"] == "+15551234567"
    assert lead["contact_name"] == "Test Carrier"
    assert lead["transcript_file"] == str(transcript)
    assert lead["call_outcome"] == "ENDED"
    mock_finalize.assert_called_once()
    assert mock_finalize.call_args.kwargs["lead"]["truck_type"] == "Dry Van"
