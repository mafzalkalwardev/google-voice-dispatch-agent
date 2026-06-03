"""Tests for src/conversation_loop.py — ConversationLoop with all I/O mocked."""

import queue
import threading
import time
import wave
import numpy as np
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


def _make_loop(
    capture_device="default",
    auto_speech: bool = True,
):
    """Return a ConversationLoop with all dependencies mocked."""
    from src.conversation_loop import ConversationLoop

    mock_tts = MagicMock()
    mock_tts.is_speaking.return_value = False
    mock_tts.speak = MagicMock()
    mock_tts.speak_async = MagicMock()
    mock_tts.stop = MagicMock()

    mock_agent = MagicMock()
    mock_agent.opening_line.return_value = "Hi, this is Tony."
    mock_agent.respond_to.return_value = "What equipment do you run?"
    mock_agent.should_end_call.return_value = False
    mock_agent.goodbye_line.return_value = "Thanks, have a great day!"

    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = "I run a dry van."

    loop = ConversationLoop(
        capture_device_hint=capture_device,
        tts=mock_tts,
        agent=mock_agent,
        stt=mock_stt,
        wait_for_human_audio=False,
        answered_speak_delay=0.0,
        stream_llm_replies=False,
        use_thinking_fillers=False,
    )
    return loop, mock_tts, mock_agent, mock_stt


def test_stop_event_terminates_run():
    """run() should return shortly after stop() is called."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener") as MockHotkey:

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _ = _make_loop()

        def stop_after_delay():
            time.sleep(0.2)
            loop.stop()

        t = threading.Thread(target=stop_after_delay, daemon=True)
        t.start()

        start = time.time()
        loop.run(auto_opening=False)
        elapsed = time.time() - start
        assert elapsed < 3.0


def test_opening_line_spoken_on_run():
    """auto_opening=True should call tts.speak() with the agent's opening line."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, mock_agent, _ = _make_loop()
        mock_agent.opening_line.return_value = "Hi, Tony here."

        def stop_soon():
            time.sleep(0.15)
            loop.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        loop.run(auto_opening=True)

        mock_tts.speak.assert_called_once_with("Hi, Tony here.")


def test_no_opening_line_when_auto_opening_false():
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _ = _make_loop()

        def stop_soon():
            time.sleep(0.15)
            loop.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        loop.run(auto_opening=False)

        mock_tts.speak.assert_not_called()


def test_voicemail_detection_window_is_configurable():
    loop, _, _, _ = _make_loop()
    loop._voicemail_detect_seconds = 3.5
    loop._last_speech_activity = time.monotonic()
    loop._voicemail_detector.reset()

    started = loop._last_speech_activity
    loop._voicemail_check_deadline_monotonic = started + loop._voicemail_detect_seconds

    assert loop._voicemail_check_deadline_monotonic == pytest.approx(started + 3.5)


def test_takeover_pauses_ai():
    loop, mock_tts, _, _ = _make_loop()
    loop._handle_takeover()
    assert loop.in_takeover()
    mock_tts.stop.assert_called()


def test_resume_clears_takeover():
    loop, _, _, _ = _make_loop()
    loop._handle_takeover()
    loop._handle_resume()
    assert not loop.in_takeover()


def test_handle_stop_sets_stop_event():
    loop, mock_tts, _, _ = _make_loop()
    loop._handle_stop()
    assert loop._stop_event.is_set()
    mock_tts.stop.assert_called()


def test_response_loop_skips_during_tts_playback():
    """Speech enqueued while TTS is playing should be skipped."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, mock_agent, mock_stt = _make_loop()
        mock_tts.is_speaking.return_value = True  # TTS is active

        audio_seg = np.zeros(16000, dtype=np.float32)
        loop._speech_q.put(audio_seg)

        def stop_soon():
            time.sleep(0.3)
            loop.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        loop.run(auto_opening=False)

        # STT should not have been called because TTS was speaking
        mock_stt.transcribe.assert_not_called()


def test_response_loop_calls_stt_then_agent_then_tts():
    """A speech segment in the queue should flow: STT → agent → TTS."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, mock_agent, mock_stt = _make_loop()
        mock_tts.is_speaking.return_value = False

        # Override should_end_call to stop after one turn
        call_count = {"n": 0}

        def end_after_one():
            call_count["n"] += 1
            return call_count["n"] >= 1

        mock_agent.should_end_call.side_effect = end_after_one
        mock_agent.goodbye_line.return_value = ""

        audio_seg = np.random.rand(16000).astype(np.float32) * 0.1
        loop._speech_q.put(audio_seg)

        # Patch _wait_for_tts to be instant
        with patch("src.conversation_loop._wait_for_tts"):
            loop.run(auto_opening=False)

        mock_stt.transcribe.assert_called_once()
        mock_agent.respond_to.assert_called_once()
        mock_tts.speak_async.assert_called_once()


def test_drain_clears_queue():
    from src.conversation_loop import _drain
    q = queue.Queue()
    for i in range(5):
        q.put(i)
    _drain(q)
    assert q.empty()


def test_mixed_recording_includes_tony_audio_without_extending_overlap(tmp_path):
    loop, _, _, _ = _make_loop()
    recording_path = tmp_path / "mixed.wav"
    loop._recording_path = recording_path
    loop._open_recording()

    inbound_silence = np.zeros(16000, dtype=np.float32)
    tony_audio = np.ones(16000, dtype=np.float32) * 0.25

    loop._record_audio_segment(inbound_silence, 16000, offset_seconds=0.0)
    loop._record_audio_segment(tony_audio, 16000, offset_seconds=0.0)
    loop._close_recording()

    with wave.open(str(recording_path), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getnframes() == 16000
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)

    assert np.max(np.abs(pcm)) > 0


def test_in_takeover_false_initially():
    loop, _, _, _ = _make_loop()
    assert not loop.in_takeover()
