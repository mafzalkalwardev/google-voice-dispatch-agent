from pathlib import Path
from unittest.mock import MagicMock, patch

from src.call_session import CallSession, CallState
from src.main import _run_realtime_loop


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
    assert "hung up after realtime voicemail playback" in session.notes
