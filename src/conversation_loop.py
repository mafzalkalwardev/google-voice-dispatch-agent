"""
Full-duplex realtime conversation loop orchestrator.

Audio flow (single VB-CABLE — default):
  Prospect speaks
      → Chrome plays audio to system speakers
      → WASAPI loopback capture (soundcard)
      → EnergyVAD segments speech
      → Groq Whisper STT transcribes
      → ConversationAgent generates reply
      → RealtimeTTS synthesises
      → sounddevice plays to CABLE Input
      → Chrome mic (CABLE Output) sends to prospect

Audio flow (dual VB-CABLE — set capture_device="CABLE B Output"):
  Prospect speaks
      → Chrome plays audio to CABLE B Input
      → sounddevice InputStream on CABLE B Output
      → same pipeline above ...

Hotkeys (require 'keyboard' package):
  Ctrl+Shift+T  — human takeover (AI pauses)
  Ctrl+Shift+R  — resume AI
  Ctrl+Shift+S  — stop and hang up

Answer detection flow:
  1. AudioCapture starts immediately on CONNECTED
  2. VAD calibrates in background (40 frames × 30 ms = 1.2 s)
  3. Loop listens for inbound human audio (up to human_audio_timeout seconds)
  4. On audio detected OR timeout → Tony speaks opening line
  5. Speech queue drained → response loop activated
  6. Normal conversation continues

Usage:
    loop = ConversationLoop(...)
    loop.run(session=session, auto_opening=True)  # blocks until stopped
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from src.audio_capture      import AudioCapture
from src.call_session       import CallSession, CallState
from src.conversation_agent import ConversationAgent
from src.hotkey_listener    import HotkeyListener
from src.realtime_tts       import RealtimeTTS
from src.stt                import GroqWhisperSTT
from src.vad                import EnergyVAD, VADConfig

logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000


class ConversationLoop:
    """
    Orchestrates capture → VAD → STT → LLM → TTS in a thread-safe loop.

    All dependencies are injected so they can be mocked in tests.

    Answer detection:
      AudioCapture starts BEFORE the opening line so inbound speech can be
      heard immediately.  When wait_for_human_audio=True the loop listens up
      to human_audio_timeout seconds for the carrier to speak before Tony
      says anything.  This prevents Tony from talking over a still-ringing
      phone or firing into silence on a fresh pick-up.
    """

    def __init__(
        self,
        capture_device_hint: str,
        tts: RealtimeTTS,
        agent: ConversationAgent,
        stt: GroqWhisperSTT,
        vad_config: Optional[VADConfig] = None,
        calibrate_frames: int = 40,     # frames of silence for VAD calibration
        stt_prompt: str = "Indus Transports freight dispatch",
        transcript_path: Optional[Path] = None,
        recording_path: Optional[Path] = None,
        # Answer detection / timing
        answered_speak_delay: float = 4.0,  # fallback delay (wait_for_human_audio=False)
        wait_for_human_audio: bool = True,  # listen for inbound audio before speaking
        human_audio_timeout: float = 8.0,   # max seconds to wait for inbound audio
    ):
        self.capture_device_hint  = capture_device_hint
        self.tts   = tts
        self.agent = agent
        self.stt   = stt
        self._vad  = EnergyVAD(vad_config or VADConfig())
        self._calibrate_frames = calibrate_frames
        self._stt_prompt = stt_prompt
        self._transcript_path = transcript_path
        self._recording_path  = recording_path

        self._answered_speak_delay = answered_speak_delay
        self._wait_for_human_audio = wait_for_human_audio
        self._human_audio_timeout  = human_audio_timeout

        # Threading primitives
        self._stop_event     = threading.Event()
        self._takeover_event = threading.Event()
        self._speech_q: queue.Queue[bytes] = queue.Queue(maxsize=8)

        # Answer-detection events (cleared/set each call in run())
        self._calibration_event = threading.Event()  # VAD calibrated
        self._answer_confirmed  = threading.Event()  # inbound audio detected
        self._response_active   = threading.Event()  # response loop unlocked

        self._capture:  Optional[AudioCapture]   = None
        self._hotkeys:  Optional[HotkeyListener] = None
        self._session:  Optional[CallSession]    = None
        self._transcript_lock = threading.Lock()
        self._recording_lock  = threading.Lock()
        self._recording_wave: Optional[wave.Wave_write] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        session:       Optional[CallSession] = None,
        opening_line:  Optional[str] = None,
        auto_opening:  bool = True,
    ) -> None:
        """
        Start the realtime conversation and block until it ends.

        New call ordering:
          1. AudioCapture starts immediately (before TTS)
          2. VAD calibrates in background
          3. Listen for inbound human audio (if wait_for_human_audio=True)
          4. Tony speaks opening line
          5. Response loop activates
        """
        self._session = session
        self._stop_event.clear()
        self._takeover_event.clear()
        self._calibration_event.clear()
        self._answer_confirmed.clear()
        self._response_active.clear()

        # --- 1. Hotkeys ---
        self._hotkeys = HotkeyListener(
            on_takeover=self._handle_takeover,
            on_resume=self._handle_resume,
            on_stop=self._handle_stop,
        )
        self._hotkeys.start()

        # --- 2. AudioCapture starts IMMEDIATELY — before any LLM/TTS ---
        logger.info("[CALL] Initializing AudioCapture")
        self._capture = AudioCapture(
            device_name_hint=self.capture_device_hint,
            samplerate=_SAMPLERATE,
        )
        self._open_recording()

        t_capture  = threading.Thread(target=self._capture_loop,  daemon=True, name="Capture")
        t_response = threading.Thread(target=self._response_loop, daemon=True, name="Response")
        t_capture.start()
        t_response.start()
        logger.info("[CALL] AudioCapture started — capture and response threads running")

        # --- 3. Opening line with answer detection ---
        if auto_opening:
            need_audio_wait = self._wait_for_human_audio or self._answered_speak_delay > 0

            if self._wait_for_human_audio:
                # Wait for VAD calibration so detection thresholds are accurate
                calibrated = self._wait_stop_or_event(self._calibration_event, timeout=10.0)
                if calibrated:
                    logger.info("[CALL] VAD calibrated — listening for inbound human audio")
                else:
                    logger.warning("[CALL] VAD calibration timed out/stopped — proceeding anyway")

                if not self._stop_event.is_set():
                    logger.info(
                        "[CALL] Waiting for inbound human audio (timeout=%.1fs)...",
                        self._human_audio_timeout,
                    )
                    human_heard = self._wait_stop_or_event(
                        self._answer_confirmed, timeout=self._human_audio_timeout
                    )
                    if human_heard:
                        logger.info("[CALL] Human audio confirmed — preparing opening line")
                    else:
                        logger.info(
                            "[CALL] No inbound audio in %.1fs — speaking anyway",
                            self._human_audio_timeout,
                        )

            elif self._answered_speak_delay > 0:
                logger.info(
                    "[CALL] Waiting %.1fs before opening line",
                    self._answered_speak_delay,
                )
                self._stop_event.wait(timeout=self._answered_speak_delay)

            # Play opening line (only if call still active)
            if not self._stop_event.is_set():
                line = opening_line or self.agent.opening_line()
                if line:
                    logger.info("[CALL] Tony (opening): %s", line)
                    self._write_transcript("Tony", line)
                    self.tts.speak(line)
                    self._vad.reset()   # clear any partial buffer accumulated during TTS
                    logger.info("[CALL] Opening complete — switching to listening mode")
                else:
                    logger.warning("[CALL] Opening line empty — listening for carrier to speak first")

                # Drain speech segments captured during opening (carrier + echo)
                _drain(self._speech_q)

        # --- 4. Activate response loop ---
        self._response_active.set()
        logger.info(
            "[CALL] Conversation loop active — Ctrl+Shift+T takeover | "
            "Ctrl+Shift+R resume | Ctrl+Shift+S stop"
        )

        self._stop_event.wait()

        # --- 5. Cleanup ---
        if self._capture:
            self._capture.stop()
        if self._hotkeys:
            self._hotkeys.stop()
        self.tts.stop()
        self._close_recording()

        t_capture.join(timeout=3.0)
        t_response.join(timeout=3.0)
        logger.info("[CALL] ConversationLoop finished")

    def stop(self) -> None:
        self._response_active.set()  # unblock waiting response thread
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def in_takeover(self) -> bool:
        return self._takeover_event.is_set()

    # ------------------------------------------------------------------ #
    # Transcript helpers
    # ------------------------------------------------------------------ #

    def _write_transcript(self, speaker: str, text: str) -> None:
        if not self._transcript_path or not text:
            return
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {speaker}: {text}\n"
        try:
            with self._transcript_lock:
                self._transcript_path.parent.mkdir(parents=True, exist_ok=True)
                with self._transcript_path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError as exc:
            logger.warning("Transcript write error: %s", exc)

    def _open_recording(self) -> None:
        if not self._recording_path:
            return
        try:
            self._recording_path.parent.mkdir(parents=True, exist_ok=True)
            wf = wave.open(str(self._recording_path), "wb")
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLERATE)
            self._recording_wave = wf
            logger.info("[CALL] Recording incoming call audio to: %s", self._recording_path)
        except OSError as exc:
            self._recording_wave = None
            logger.warning("Recording setup error: %s", exc)

    def _write_recording_frame(self, frame: np.ndarray) -> None:
        if self._recording_wave is None:
            return
        try:
            mono = np.asarray(frame, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16)
            with self._recording_lock:
                if self._recording_wave is not None:
                    self._recording_wave.writeframes(pcm.tobytes())
        except Exception as exc:
            logger.debug("Recording frame skipped: %s", exc)

    def _close_recording(self) -> None:
        with self._recording_lock:
            wf = self._recording_wave
            self._recording_wave = None
        if wf is not None:
            try:
                wf.close()
                logger.info("[CALL] Recording finalized: %s", self._recording_path)
            except Exception as exc:
                logger.warning("Recording close error: %s", exc)

    # ------------------------------------------------------------------ #
    # Hotkey handlers  (called from daemon threads)
    # ------------------------------------------------------------------ #

    def _handle_takeover(self) -> None:
        logger.info("[TAKEOVER] AI paused — human speaking. Ctrl+Shift+R to resume.")
        self._takeover_event.set()
        self.tts.stop()

    def _handle_resume(self) -> None:
        logger.info("[RESUME] AI re-enabled.")
        self._takeover_event.clear()
        self._vad.reset()
        _drain(self._speech_q)

    def _handle_stop(self) -> None:
        logger.info("[STOP] Ending conversation.")
        self.tts.stop()
        self.stop()

    # ------------------------------------------------------------------ #
    # Wait helper: returns True if event fired, False if stop/timeout
    # ------------------------------------------------------------------ #

    def _wait_stop_or_event(self, event: threading.Event, timeout: float) -> bool:
        """Poll event with 100 ms slices, exit early if stop_event fires."""
        deadline = time.time() + timeout
        while not self._stop_event.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                return False
            if event.wait(timeout=min(0.1, remaining)):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Capture thread: AudioCapture → VAD calibration → answer detection
    #                 → VAD segments → speech_q
    # ------------------------------------------------------------------ #

    def _capture_loop(self) -> None:
        self._capture.start()
        calibration: list = []
        logger.info("[CAPTURE] Capture thread started")

        _rms_log_interval = 5.0   # log peak RMS every 5 s in active phase
        _rms_log_next = time.time() + _rms_log_interval
        _rms_peak: float = 0.0

        while not self._stop_event.is_set():
            capture_error = self._capture.last_error
            if isinstance(capture_error, BaseException):
                logger.error("[CAPTURE] AudioCapture failed: %s", capture_error)
                self.stop()
                return

            frame = self._capture.read(timeout=0.05)
            if frame is None:
                continue

            self._write_recording_frame(frame)

            # === Phase 1: VAD calibration (first N frames of silence) ===
            if not self._calibration_event.is_set():
                calibration.append(frame)
                if len(calibration) >= self._calibrate_frames:
                    thr = self._vad.calibrate_threshold(calibration)
                    logger.info("[CAPTURE] VAD calibrated: threshold=%.4f", thr)
                    self._calibration_event.set()
                continue  # don't process for VAD until calibrated

            # === Phase 2: Answer detection (fires once on first inbound audio) ===
            if not self._answer_confirmed.is_set():
                rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
                if rms >= self._vad.config.speech_threshold:
                    logger.info(
                        "[CAPTURE] Inbound audio detected (rms=%.4f) — human answer confirmed",
                        rms,
                    )
                    self._answer_confirmed.set()

            # === Phase 3: Active conversation — VAD → speech_q ===
            if self._takeover_event.is_set():
                continue  # human is speaking

            if self.tts.is_speaking():
                self._vad.reset()  # prevent stale pre-speech buffer across TTS playback
                continue  # ignore loopback echo

            # Track peak incoming RMS and log periodically — helps diagnose capture routing
            now = time.time()
            rms3 = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            _rms_peak = max(_rms_peak, rms3)
            if now >= _rms_log_next and self._response_active.is_set():
                logger.info(
                    "[CAPTURE] Active — peak RMS last 5s: %.4f (VAD threshold: %.4f)",
                    _rms_peak,
                    self._vad.config.speech_threshold,
                )
                _rms_peak = 0.0
                _rms_log_next = now + _rms_log_interval

            segment = self._vad.process_frame(frame)
            if segment is not None:
                if self._response_active.is_set():
                    dur = len(segment) / _SAMPLERATE
                    logger.info("[VAD] %.2fs speech segment → STT queue", dur)
                    if not self._speech_q.full():
                        self._speech_q.put(segment)
                else:
                    dur = len(segment) / _SAMPLERATE
                    logger.debug("[VAD] %.2fs segment discarded (pre-conversation)", dur)

    # ------------------------------------------------------------------ #
    # Response thread: speech_q → STT → LLM → TTS
    # ------------------------------------------------------------------ #

    def _response_loop(self) -> None:
        # Wait for the opening line to complete before processing any speech.
        # Uses a polling loop so stop() is respected immediately.
        logger.info("[RESPONSE] Response thread started — waiting for conversation to go live")
        while not self._stop_event.is_set():
            if self._response_active.wait(timeout=0.5):
                break
        if self._stop_event.is_set():
            return
        logger.info("[RESPONSE] Conversation active — processing speech")

        while not self._stop_event.is_set():
            try:
                audio_segment = self._speech_q.get(timeout=0.5)
            except queue.Empty:
                continue

            if self._takeover_event.is_set() or self.tts.is_speaking():
                continue

            # STT
            try:
                transcript = self.stt.transcribe(
                    audio_segment,
                    samplerate=_SAMPLERATE,
                    prompt=self._stt_prompt,
                )
            except Exception as exc:
                logger.error("[STT] Failed: %s", exc)
                continue
            if not transcript:
                logger.info("[STT] Empty: no transcript for segment")
                continue
            logger.info("[RESPONSE] Prospect: %s", transcript)
            self._write_transcript("Prospect", transcript)

            # LLM
            response = self.agent.respond_to(transcript)
            if not response:
                continue
            logger.info("[RESPONSE] Tony: %s", response)
            self._write_transcript("Tony", response)

            # TTS
            self.tts.speak_async(response)

            # Graceful end
            if self.agent.should_end_call():
                logger.info("[RESPONSE] Agent signalling end of call")
                _wait_for_tts(self.tts, timeout=15.0)
                goodbye = self.agent.goodbye_line()
                if goodbye:
                    logger.info("[RESPONSE] Tony (goodbye): %s", goodbye)
                    self._write_transcript("Tony", goodbye)
                    self.tts.speak(goodbye)
                if self._session and not self._session.is_terminal():
                    try:
                        self._session.transition(CallState.ENDED, "agent ended call gracefully")
                    except ValueError:
                        pass
                self.stop()


# ------------------------------------------------------------------ #
# Utilities
# ------------------------------------------------------------------ #

def _drain(q: queue.Queue) -> None:
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def _wait_for_tts(tts: RealtimeTTS, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while tts.is_speaking() and time.time() < deadline:
        time.sleep(0.1)
