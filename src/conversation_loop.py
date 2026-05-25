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

Usage:
    loop = ConversationLoop(...)
    loop.run(session=session, auto_opening=True)  # blocks until stopped
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from src.audio_capture     import AudioCapture
from src.call_session      import CallSession, CallState
from src.conversation_agent import ConversationAgent
from src.hotkey_listener   import HotkeyListener
from src.realtime_tts      import RealtimeTTS
from src.stt               import GroqWhisperSTT
from src.vad               import EnergyVAD, VADConfig

logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000


class ConversationLoop:
    """
    Orchestrates capture → VAD → STT → LLM → TTS in a thread-safe loop.

    All dependencies are injected so they can be mocked in tests.
    """

    def __init__(
        self,
        capture_device_hint: str,
        tts: RealtimeTTS,
        agent: ConversationAgent,
        stt: GroqWhisperSTT,
        vad_config: Optional[VADConfig] = None,
        calibrate_frames: int = 40,    # frames of silence for VAD calibration
        stt_prompt: str = "Indus Transports freight dispatch",
        transcript_path: Optional[Path] = None,
    ):
        self.capture_device_hint = capture_device_hint
        self.tts   = tts
        self.agent = agent
        self.stt   = stt
        self._vad  = EnergyVAD(vad_config or VADConfig())
        self._calibrate_frames = calibrate_frames
        self._stt_prompt = stt_prompt
        self._transcript_path = transcript_path

        self._stop_event     = threading.Event()
        self._takeover_event = threading.Event()
        self._speech_q: queue.Queue[bytes] = queue.Queue(maxsize=8)

        self._capture:  Optional[AudioCapture]   = None
        self._hotkeys:  Optional[HotkeyListener] = None
        self._session:  Optional[CallSession]    = None
        self._transcript_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        session:        Optional[CallSession] = None,
        opening_line:   Optional[str] = None,
        auto_opening:   bool = True,
    ) -> None:
        """
        Start the realtime conversation and block until it ends.
        Call this after the Selenium GoogleVoiceBrowser confirms CONNECTED state.
        """
        self._session = session
        self._stop_event.clear()
        self._takeover_event.clear()

        # Hotkeys
        self._hotkeys = HotkeyListener(
            on_takeover=self._handle_takeover,
            on_resume=self._handle_resume,
            on_stop=self._handle_stop,
        )
        self._hotkeys.start()

        # Opening line (plays before capture starts so we don't echo ourselves)
        if auto_opening:
            logger.info("Opening line generation started")
            line = opening_line or self.agent.opening_line()
            if line:
                logger.info("Opening line generated: %s", line)
                logger.info("Tony (opening): %s", line)
                self._write_transcript("Tony", line)
                self.tts.speak(line)
            else:
                logger.warning("Opening line generated empty; no opening TTS will play")

        # Capture
        self._capture = AudioCapture(
            device_name_hint=self.capture_device_hint,
            samplerate=_SAMPLERATE,
        )

        # Worker threads
        t_capture  = threading.Thread(target=self._capture_loop,  daemon=True, name="Capture")
        t_response = threading.Thread(target=self._response_loop, daemon=True, name="Response")
        t_capture.start()
        t_response.start()

        logger.info(
            "Conversation live — Ctrl+Shift+T takeover | Ctrl+Shift+R resume | Ctrl+Shift+S stop"
        )
        self._stop_event.wait()

        # Cleanup
        if self._capture:
            self._capture.stop()
        if self._hotkeys:
            self._hotkeys.stop()
        self.tts.stop()

        t_capture.join(timeout=3.0)
        t_response.join(timeout=3.0)
        logger.info("ConversationLoop finished")

    def stop(self) -> None:
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
    # Capture thread: AudioCapture → VAD → speech_q
    # ------------------------------------------------------------------ #

    def _capture_loop(self) -> None:
        self._capture.start()
        calibration: list = []

        while not self._stop_event.is_set():
            capture_error = self._capture.last_error
            if isinstance(capture_error, BaseException):
                logger.error("STT capture failed: %s", capture_error)
                self.stop()
                return
            frame = self._capture.read(timeout=0.05)
            if frame is None:
                continue

            # Silence calibration: collect the first N frames before processing
            if len(calibration) < self._calibrate_frames:
                calibration.append(frame)
                if len(calibration) == self._calibrate_frames:
                    thr = self._vad.calibrate_threshold(calibration)
                    logger.info("VAD threshold calibrated: %.4f", thr)
                continue

            if self._takeover_event.is_set():
                continue   # human is speaking

            if self.tts.is_speaking():
                continue   # Tony is speaking — ignore echo

            segment = self._vad.process_frame(frame)
            if segment is not None:
                dur = len(segment) / _SAMPLERATE
                logger.debug("VAD: %.2fs speech segment enqueued", dur)
                if not self._speech_q.full():
                    self._speech_q.put(segment)

    # ------------------------------------------------------------------ #
    # Response thread: speech_q → STT → LLM → TTS
    # ------------------------------------------------------------------ #

    def _response_loop(self) -> None:
        import numpy as np

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
                logger.error("STT failed: %s", exc)
                continue
            if not transcript:
                logger.info("STT empty: no transcript for %.2fs segment", len(audio_segment) / _SAMPLERATE)
                continue
            logger.info("Prospect: %s", transcript)
            self._write_transcript("Prospect", transcript)

            # LLM
            response = self.agent.respond_to(transcript)
            if not response:
                continue
            logger.info("Tony: %s", response)
            self._write_transcript("Tony", response)

            # TTS
            self.tts.speak_async(response)

            # Check graceful end
            if self.agent.should_end_call():
                logger.info("Agent signalling end of call")
                # Wait for last response, then say goodbye
                _wait_for_tts(self.tts, timeout=15.0)
                goodbye = self.agent.goodbye_line()
                if goodbye:
                    logger.info("Tony (goodbye): %s", goodbye)
                    self._write_transcript("Tony", goodbye)
                    self.tts.speak(goodbye)
                # Update session state
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
