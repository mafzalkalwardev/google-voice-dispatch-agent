from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.call_session import CallSession, CallState
from src.conversation_loop import ConversationLoop
from src.main import _run_realtime_loop
from src.voicemail_detector import VoicemailDetector


class _FakeLoop:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self._stopped = False

    def run(self, session=None, opening_line=None, auto_opening=True):
        session.transition(CallState.VOICEMAIL, "test voicemail beep")
        self._stopped = True

    def is_stopped(self):
        return self._stopped

    def stop(self):
        self._stopped = True


def test_realtime_voicemail_detection_plays_voicemail_audio():
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    browser = MagicMock()
    browser.is_call_active.return_value = True
    voicemail_path = Path("audio/voicemails/test_voicemail.wav")

    with patch("src.realtime_tts.validate_tts_output_device"), \
         patch("src.voice_playback.describe_audio_device", return_value="Cable Input"), \
         patch("src.realtime_tts.RealtimeTTS"), \
         patch("src.conversation_agent.ConversationAgent"), \
         patch("src.stt.GroqWhisperSTT"), \
         patch("src.conversation_loop.ConversationLoop", _FakeLoop), \
         patch("src.main._play_audio", return_value=True) as play_audio:
        _run_realtime_loop(
            session=session,
            contact_name="Test Carrier",
            groq_api_key="test_key",
            groq_model="llama-3.3-70b-versatile",
            agent_name="Tony",
            company_name="Indus Transports LLC",
            company_context="dispatch services",
            company_website="",
            callback_number="+15550000000",
            loopback_device_index=1,
            capture_device="default",
            tts_voice="en-US-GuyNeural",
            stt_model="whisper-large-v3-turbo",
            vad_threshold=0.015,
            opening_line="Hi",
            voicemail_path=voicemail_path,
            browser=browser,
            loopback_device="CABLE Input",
            loopback_available=True,
            call_max_duration=30,
            logger=MagicMock(),
            voicemail_detect_seconds=7.0,
        )

    assert play_audio.call_args.args[:3] == (voicemail_path, "CABLE Input", True)
    browser.hangup_call.assert_called_once()
    assert session.state == CallState.ENDED
    assert any("voicemail" in n.lower() for n in session.notes)


def test_realtime_loop_requires_three_stable_multisignal_confirmations():
    session = CallSession(phone="+15551234567", contact_name="Test Carrier")
    session.transition(CallState.DIALING)
    session.transition(CallState.CONNECTED)

    loop = ConversationLoop.__new__(ConversationLoop)
    loop._voicemail_detected = False
    loop._vm_human_detected = False
    loop._vm_answer_started_monotonic = 100.0
    loop._vm_greeting_hits = 0
    loop._vm_greeting_frames_required = 6
    loop._voicemail_play_on_greeting = True
    loop._vm_latest_transcript = ""
    loop._vm_candidate_confirmations = 0
    loop._voicemail_detector = SimpleNamespace(
        continuous_greeting_duration_seconds=5.0,
        beep_ready=True,
        machine_pattern_detected=False,
    )
    loop._voicemail_evidence_detector = VoicemailDetector()
    loop._session = session
    loop.stop = MagicMock()

    assert loop._evaluate_voicemail_evidence(108.0) is False
    assert loop._evaluate_voicemail_evidence(108.1) is False
    assert loop._evaluate_voicemail_evidence(108.2) is True

    assert session.state == CallState.VOICEMAIL
    assert session.outcome == "voicemail"
    loop.stop.assert_called_once()
