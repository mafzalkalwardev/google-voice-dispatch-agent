"""Tests for realtime answer-detection and capture-first architecture in ConversationLoop."""

import threading
import time
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

# 30 ms of silence at 16 kHz — enough for VAD calibration frames
_SILENT_FRAME = np.zeros(480, dtype=np.float32)


def _make_loop(
    wait_for_human_audio: bool = True,
    answered_speak_delay: float = 0.0,
    human_audio_timeout: float = 5.0,
    capture_read_value=None,
):
    """
    Build a ConversationLoop with all I/O mocked.

    capture_read_value: value returned by mock AudioCapture.read().
      - None → capture always starves (calibration never completes on its own)
      - _SILENT_FRAME → calibration completes quickly, no answer confirmed
    """
    from src.conversation_loop import ConversationLoop

    mock_tts = MagicMock()
    mock_tts.is_speaking.return_value = False
    mock_tts.speak = MagicMock()
    mock_tts.speak_async = MagicMock()
    mock_tts.stop = MagicMock()

    mock_agent = MagicMock()
    mock_agent.opening_line.return_value = "Hi, Tony here."
    mock_agent.respond_to.return_value = "What equipment do you run?"
    mock_agent.should_end_call.return_value = False
    mock_agent.goodbye_line.return_value = "Thanks!"

    mock_stt = MagicMock()
    mock_stt.transcribe.return_value = ""

    loop = ConversationLoop(
        capture_device_hint="default",
        tts=mock_tts,
        agent=mock_agent,
        stt=mock_stt,
        wait_for_human_audio=wait_for_human_audio,
        answered_speak_delay=answered_speak_delay,
        human_audio_timeout=human_audio_timeout,
    )
    return loop, mock_tts, mock_agent, mock_stt, capture_read_value


# ---------------------------------------------------------------------------
# 1. AudioCapture is instantiated before TTS speaks
# ---------------------------------------------------------------------------

def test_audiocapture_instantiated_before_tts_opening():
    """AudioCapture must be created in run() before tts.speak() is ever called."""
    call_order: list[str] = []

    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        def capture_factory(**kwargs):
            call_order.append("capture_init")
            inst = MagicMock()
            inst.read.return_value = None
            inst.last_error = None
            return inst

        MockCapture.side_effect = capture_factory

        loop, mock_tts, _, _, _ = _make_loop(
            wait_for_human_audio=False,
            answered_speak_delay=0.0,
        )

        def track_speak(text):
            call_order.append("tts_speak")

        mock_tts.speak.side_effect = track_speak

        def stop_after_speak():
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if "tts_speak" in call_order:
                    loop.stop()
                    return
                time.sleep(0.02)
            loop.stop()

        threading.Thread(target=stop_after_speak, daemon=True).start()
        loop.run(auto_opening=True)

        assert "capture_init" in call_order, "AudioCapture was never instantiated"
        assert "tts_speak" in call_order, "TTS never spoke"
        cap_idx = call_order.index("capture_init")
        tts_idx = call_order.index("tts_speak")
        assert cap_idx < tts_idx, (
            f"AudioCapture init ({cap_idx}) must precede tts.speak ({tts_idx})"
        )


# ---------------------------------------------------------------------------
# 2. With wait_for_human_audio=True: waits for _answer_confirmed before speaking
# ---------------------------------------------------------------------------

def test_wait_for_human_audio_waits_for_answer_confirmed():
    """Tony must not speak the opening line until _answer_confirmed is set."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = _SILENT_FRAME  # calibration completes, no audio
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _, _ = _make_loop(
            wait_for_human_audio=True,
            human_audio_timeout=10.0,
        )

        confirmed_at: list[float] = []
        speak_at: list[float] = []

        def tracked_speak(text):
            speak_at.append(time.time())

        mock_tts.speak.side_effect = tracked_speak

        def confirm_then_stop():
            # Let run() clear events and start capture thread
            time.sleep(0.15)
            confirmed_at.append(time.time())
            loop._answer_confirmed.set()
            # Wait for speak to happen
            deadline = time.time() + 3.0
            while time.time() < deadline and not speak_at:
                time.sleep(0.02)
            time.sleep(0.05)
            loop.stop()

        threading.Thread(target=confirm_then_stop, daemon=True).start()
        loop.run(auto_opening=True)

        assert speak_at, "Tony should have spoken an opening line"
        assert confirmed_at, "Test did not record confirm time"
        assert speak_at[0] >= confirmed_at[0], (
            f"Tony spoke at {speak_at[0]:.4f} but answer was only confirmed at "
            f"{confirmed_at[0]:.4f} — spoke too early"
        )


# ---------------------------------------------------------------------------
# 3. After human_audio_timeout: Tony speaks anyway (no confirm received)
# ---------------------------------------------------------------------------

def test_human_audio_timeout_speaks_anyway():
    """After human_audio_timeout seconds with no inbound audio, Tony speaks anyway."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = _SILENT_FRAME
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _, _ = _make_loop(
            wait_for_human_audio=True,
            human_audio_timeout=0.15,  # very short — times out almost immediately
        )

        def stop_after_speak():
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if mock_tts.speak.called:
                    loop.stop()
                    return
                time.sleep(0.02)
            loop.stop()

        threading.Thread(target=stop_after_speak, daemon=True).start()
        loop.run(auto_opening=True)

        mock_tts.speak.assert_called_once_with("Hi, Tony here.")


# ---------------------------------------------------------------------------
# 4. stop() while waiting for human audio — returns without speaking
# ---------------------------------------------------------------------------

def test_stop_unblocks_waiting_for_human_audio():
    """stop() called during the human-audio wait must unblock run() promptly."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = _SILENT_FRAME
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _, _ = _make_loop(
            wait_for_human_audio=True,
            human_audio_timeout=30.0,   # would take 30 s without stop()
        )

        def stop_early():
            time.sleep(0.2)
            loop.stop()

        threading.Thread(target=stop_early, daemon=True).start()

        start = time.time()
        loop.run(auto_opening=True)
        elapsed = time.time() - start

        assert elapsed < 3.0, f"run() blocked {elapsed:.1f}s — stop() did not unblock"
        mock_tts.speak.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Answered delay (wait_for_human_audio=False, answered_speak_delay > 0)
# ---------------------------------------------------------------------------

def test_answered_speak_delay_delays_opening():
    """With wait_for_human_audio=False and a delay, Tony waits before speaking."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, _, _, _ = _make_loop(
            wait_for_human_audio=False,
            answered_speak_delay=0.2,   # 200 ms delay
        )

        speak_at: list[float] = []
        run_started_at: list[float] = []

        original_speak = mock_tts.speak
        def tracked_speak(text):
            speak_at.append(time.time())
        mock_tts.speak.side_effect = tracked_speak

        def stop_after_speak():
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if speak_at:
                    loop.stop()
                    return
                time.sleep(0.02)
            loop.stop()

        t = threading.Thread(target=stop_after_speak, daemon=True)
        t.start()

        run_started_at.append(time.time())
        loop.run(auto_opening=True)

        assert speak_at, "Tony never spoke"
        elapsed = speak_at[0] - run_started_at[0]
        assert elapsed >= 0.15, (
            f"Tony spoke after only {elapsed:.3f}s — expected >= 0.2s delay"
        )


# ---------------------------------------------------------------------------
# 6. Response loop ignores speech while TTS is speaking
# ---------------------------------------------------------------------------

def test_response_loop_skips_stt_while_tts_speaking():
    """Speech segments arriving while TTS is playing must not reach STT."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"):

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        loop, mock_tts, mock_agent, mock_stt, _ = _make_loop(
            wait_for_human_audio=False,
            answered_speak_delay=0.0,
        )
        mock_tts.is_speaking.return_value = True   # TTS is playing throughout

        audio_seg = np.random.rand(16000).astype(np.float32) * 0.1
        loop._speech_q.put(audio_seg)

        def stop_soon():
            time.sleep(0.3)
            loop.stop()

        threading.Thread(target=stop_soon, daemon=True).start()
        loop.run(auto_opening=False)

        mock_stt.transcribe.assert_not_called()


# ---------------------------------------------------------------------------
# 7. VAD reset is called after opening TTS
# ---------------------------------------------------------------------------

def test_vad_reset_called_after_opening_tts():
    """After Tony speaks the opening line, VAD must be reset to clear stale buffers."""
    with patch("src.conversation_loop.AudioCapture") as MockCapture, \
         patch("src.conversation_loop.HotkeyListener"), \
         patch("src.conversation_loop.EnergyVAD") as MockVAD:

        mock_capture_inst = MagicMock()
        mock_capture_inst.read.return_value = None
        mock_capture_inst.last_error = None
        MockCapture.return_value = mock_capture_inst

        mock_vad_inst = MagicMock()
        mock_vad_inst.config.speech_threshold = 0.015
        mock_vad_inst.reset = MagicMock()
        mock_vad_inst.calibrate_threshold.return_value = 0.015
        MockVAD.return_value = mock_vad_inst

        from src.conversation_loop import ConversationLoop

        mock_tts = MagicMock()
        mock_tts.is_speaking.return_value = False
        mock_tts.speak = MagicMock()
        mock_tts.speak_async = MagicMock()
        mock_tts.stop = MagicMock()

        mock_agent = MagicMock()
        mock_agent.opening_line.return_value = "Hi, Tony here."
        mock_agent.should_end_call.return_value = False

        mock_stt = MagicMock()
        mock_stt.transcribe.return_value = ""

        loop = ConversationLoop(
            capture_device_hint="default",
            tts=mock_tts,
            agent=mock_agent,
            stt=mock_stt,
            wait_for_human_audio=False,
            answered_speak_delay=0.0,
        )

        def stop_after_speak():
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if mock_tts.speak.called:
                    loop.stop()
                    return
                time.sleep(0.02)
            loop.stop()

        threading.Thread(target=stop_after_speak, daemon=True).start()
        loop.run(auto_opening=True)

        mock_tts.speak.assert_called_with("Hi, Tony here.")
        mock_vad_inst.reset.assert_called()
