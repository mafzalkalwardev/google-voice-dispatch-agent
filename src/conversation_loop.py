"""
Full-duplex realtime conversation loop orchestrator.

The loop is intentionally explicit about state transitions:
capture starts first, Tony speaks, Tony's echo tail is suppressed briefly, then
carrier speech is segmented, transcribed, answered, and the cycle repeats.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import unicodedata
import wave
from pathlib import Path
from typing import Callable, Optional
import difflib



import numpy as np

from src.audio_capture import AudioCapture
from src.call_session import CallSession, CallState
from src.conversation_agent import ConversationAgent
from src.hotkey_listener import HotkeyListener
from src.paths import runtime_base
from src.realtime_diagnostics import DIAGNOSTICS_PATH, write_live_diagnostics
from src.realtime_tts import RealtimeTTS
from src.stt import GroqWhisperSTT
from src.vad import EnergyVAD, VADConfig
from src.voicemail_detector import VoicemailAudioClassifier


logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000
_BASE_DIR = runtime_base()
_SEGMENT_DIR = _BASE_DIR / "logs" / "realtime_segments"

STATE_WAITING_FOR_ANSWER = "WAITING_FOR_ANSWER"
STATE_SPEAKING_OPENING = "SPEAKING_OPENING"
STATE_LISTENING = "LISTENING"
STATE_CAPTURING_SPEECH = "CAPTURING_SPEECH"
STATE_TRANSCRIBING = "TRANSCRIBING"
STATE_THINKING = "THINKING"
STATE_SPEAKING_REPLY = "SPEAKING_REPLY"
STATE_SILENCE_TIMEOUT = "SILENCE_TIMEOUT"
STATE_CALL_ENDED = "CALL_ENDED"


class ConversationLoop:
    """Orchestrates capture -> VAD -> STT -> LLM -> TTS until stopped."""

    def __init__(
        self,
        capture_device_hint: str,
        tts: RealtimeTTS,
        agent: ConversationAgent,
        stt: GroqWhisperSTT,

        vad_config: Optional[VADConfig] = None,
        calibrate_frames: int = 40,
        stt_prompt: str = "Indus Transports freight dispatch",
        transcript_path: Optional[Path] = None,
        recording_path: Optional[Path] = None,
        answered_speak_delay: float = 0.0,
        wait_for_human_audio: bool = False,
        human_audio_timeout: float = 8.0,
        listen_after_tts_delay_ms: int = 80,
        min_speech_seconds: float = 0.25,
        max_silence_seconds: float = 8.0,
        silence_does_not_end_call: bool = True,
        capture_rms_log_interval_seconds: float = 1.0,
        realtime_debug: bool = True,
        diagnostics_path: Optional[Path] = None,
        capture_factory: Optional[Callable[..., AudioCapture]] = None,
        voicemail_detect_seconds: float = 15.0,
    ):
        self.capture_device_hint = capture_device_hint
        self.tts = tts
        self.agent = agent
        self.stt = stt
        cfg = vad_config or VADConfig()
        cfg.min_speech_seconds = min_speech_seconds
        self._vad = EnergyVAD(cfg)
        self._calibrate_frames = max(0, int(calibrate_frames))
        self._stt_prompt = stt_prompt
        self._transcript_path = transcript_path
        self._recording_path = recording_path

        self._answered_speak_delay = answered_speak_delay
        self._wait_for_human_audio = wait_for_human_audio
        self._human_audio_timeout = human_audio_timeout
        self._listen_after_tts_delay_s = max(0.0, listen_after_tts_delay_ms / 1000.0)
        self._min_speech_seconds = max(0.0, min_speech_seconds)
        self._max_silence_seconds = max(0.0, max_silence_seconds)
        self._silence_does_not_end_call = silence_does_not_end_call
        self._rms_log_interval = max(0.1, capture_rms_log_interval_seconds)
        self._realtime_debug = realtime_debug
        self._diagnostics_path = diagnostics_path or DIAGNOSTICS_PATH
        self._capture_factory = capture_factory or AudioCapture

        self._stop_event = threading.Event()
        self._takeover_event = threading.Event()
        self._speech_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)

        self._calibration_event = threading.Event()
        self._answer_confirmed = threading.Event()
        self._response_active = threading.Event()

        self._capture: Optional[AudioCapture] = None
        self._hotkeys: Optional[HotkeyListener] = None
        self._session: Optional[CallSession] = None
        self._transcript_lock = threading.Lock()
        self._recording_lock = threading.Lock()
        self._recording_segments: list[tuple[int, np.ndarray]] = []
        self._recording_start_monotonic = 0.0
        self._recording_open = False

        set_audio_observer = getattr(self.tts, "set_audio_observer", None)
        if callable(set_audio_observer):
            try:
                set_audio_observer(self._record_tony_audio)
            except Exception as exc:
                logger.debug("[RECORDING] TTS audio observer setup skipped: %s", exc)

        self._state_lock = threading.Lock()
        self._diag_lock = threading.Lock()
        self._state = STATE_WAITING_FOR_ANSWER
        self._loop_iteration = 0
        self._capture_suppress_until = 0.0
        self._last_speech_activity = time.monotonic()
        self._next_silence_log = time.monotonic() + self._max_silence_seconds
        self._next_rms_log = time.monotonic()
        self._speech_started_wall = ""
        self._speech_started_monotonic = 0.0
        self._last_capture_rms = 0.0
        self._last_segment_path = ""
        self._last_stt_text = ""
        self._last_empty_stt_reason = ""
        self._last_tts_text = ""
        self._last_tts_duration = 0.0
        self._last_voicemail_classifier_label = ""
        self._bad_transcript_count = 0

        # Agent echo semantic suppression settings
        self._toney_echo_similarity_threshold = 0.86
        self._toney_echo_min_transcript_chars = 12


        # Voicemail detection (first N seconds after call connect)
        self._voicemail_detector = VoicemailAudioClassifier(samplerate=_SAMPLERATE)

        self._voicemail_detect_seconds = max(0.0, float(voicemail_detect_seconds))
        self._voicemail_check_deadline_monotonic = 0.0
        self._voicemail_detected = False


    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def run(
        self,
        session: Optional[CallSession] = None,
        opening_line: Optional[str] = None,
        auto_opening: bool = True,
    ) -> None:
        self._session = session
        self._stop_event.clear()
        self._takeover_event.clear()
        self._calibration_event.clear()
        self._answer_confirmed.clear()
        self._response_active.clear()

        self._loop_iteration = 0
        self._last_speech_activity = time.monotonic()

        # Reset voicemail detection window for this call.
        self._voicemail_detector.reset()
        self._voicemail_check_deadline_monotonic = (
            self._last_speech_activity + self._voicemail_detect_seconds
        )
        self._voicemail_detected = False
        self._last_voicemail_classifier_label = ""

        self._next_silence_log = self._last_speech_activity + self._max_silence_seconds
        self._set_state(STATE_WAITING_FOR_ANSWER, "call connected; starting capture before TTS")

        if self._wait_for_human_audio or self._answered_speak_delay > 0:
            logger.info(
                "[LOOP] Opening wait configured "
                "(wait_for_human_audio=%s answered_speak_delay=%.2fs timeout=%.2fs)",
                self._wait_for_human_audio,
                self._answered_speak_delay,
                self._human_audio_timeout,
            )

        t_capture: Optional[threading.Thread] = None
        t_response: Optional[threading.Thread] = None

        try:
            self._hotkeys = HotkeyListener(
                on_takeover=self._handle_takeover,
                on_resume=self._handle_resume,
                on_stop=self._handle_stop,
            )
            self._hotkeys.start()

            self._capture = self._capture_factory(
                device_name_hint=self.capture_device_hint,
                samplerate=_SAMPLERATE,
            )
            self._open_recording()
            logger.info("[CAPTURE] Starting AudioCapture before opening TTS")
            self._capture.start()
            self._wait_for_capture_ready()

            t_capture = threading.Thread(target=self._capture_loop, daemon=True, name="Capture")
            t_response = threading.Thread(target=self._response_loop, daemon=True, name="Response")
            t_capture.start()
            t_response.start()

            if auto_opening and not self._stop_event.is_set():
                # Critical fix: do NOT speak until the carrier answers.
                # We wait for inbound human audio evidence (VAD) or an explicit
                # answered_speak_delay timeout configured by the caller.
                self._wait_before_opening()

            if auto_opening and not self._stop_event.is_set():
                line = opening_line or self.agent.opening_line()
                if line:
                    speaker = self._agent_display_name()
                    logger.info("[CALL] %s opening (after answer): %s", speaker, line)
                    self._write_transcript(speaker, line)
                    self._play_tts_blocking(line, STATE_SPEAKING_OPENING, "opening")
                else:
                    logger.warning("[CALL] Opening line empty; entering listening mode")
                    self._set_state(STATE_LISTENING, "opening line empty")

            self._response_active.set()
            if self._state not in (STATE_LISTENING, STATE_SILENCE_TIMEOUT):
                self._set_state(STATE_LISTENING, "conversation loop active")
            logger.info(
                "[LOOP] Conversation active; hotkeys: Ctrl+Shift+T takeover, "
                "Ctrl+Shift+R resume, Ctrl+Shift+S stop"
            )

            self._stop_event.wait()
        finally:
            self._set_state(STATE_CALL_ENDED, "conversation loop stopping")
            if self._capture:
                self._capture.stop()
            if self._hotkeys:
                self._hotkeys.stop()
            self.tts.stop()
            self._close_recording()

            if t_capture:
                t_capture.join(timeout=3.0)
            if t_response:
                self._response_active.set()
                t_response.join(timeout=3.0)
            logger.info("[LOOP] ConversationLoop finished")

    def stop(self) -> None:
        self._response_active.set()
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def in_takeover(self) -> bool:
        return self._takeover_event.is_set()

    def diagnostics_snapshot(self) -> dict:
        return self._diagnostics_payload()

    def _wait_before_opening(self) -> None:
        if self._wait_for_human_audio:
            calibrated = self._wait_stop_or_event(self._calibration_event, timeout=10.0)
            if self._stop_event.is_set():
                return
            if calibrated:
                logger.info("[CALL] VAD calibrated; listening for inbound human audio")
            else:
                logger.warning("[CALL] VAD calibration timed out; proceeding with opening wait")

            logger.info(
                "[CALL] Waiting for inbound human audio (timeout=%.1fs)",
                self._human_audio_timeout,
            )
            human_heard = self._wait_stop_or_event(
                self._answer_confirmed,
                timeout=self._human_audio_timeout,
            )
            if self._stop_event.is_set():
                return
            if human_heard:
                logger.info("[CALL] Human audio confirmed; speaking opening line")
            else:
                logger.info(
                    "[CALL] No inbound audio in %.1fs; speaking opening line",
                    self._human_audio_timeout,
                )
        elif self._answered_speak_delay > 0:
            logger.info(
                "[CALL] Waiting %.1fs before opening line",
                self._answered_speak_delay,
            )
            self._stop_event.wait(timeout=self._answered_speak_delay)

    def _wait_stop_or_event(self, event: threading.Event, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return event.is_set()
            if event.wait(timeout=min(0.05, remaining)):
                return True
        return event.is_set()

    def _agent_display_name(self) -> str:
        return str(getattr(self.agent, "agent_name", "") or "Agent").strip() or "Agent"

    # ------------------------------------------------------------------ #
    # State and diagnostics
    # ------------------------------------------------------------------ #

    def _set_state(self, state: str, reason: str = "") -> None:
        with self._state_lock:
            self._state = state
        suffix = f" reason={reason}" if reason else ""
        logger.info("[LOOP] iteration=%d state=%s%s", self._loop_iteration, state, suffix)
        self._write_diagnostics()

    def _diagnostics_payload(self) -> dict:
        capture_diag = self._capture_diagnostics()
        return {
            "state": self._state,
            "loop_iteration": self._loop_iteration,
            "capture_device": self.capture_device_hint,
            "capture_device_index": capture_diag.get("selected_device_index"),
            "capture_device_name": capture_diag.get("selected_device_name", ""),
            "capture_mode": capture_diag.get("capture_mode", ""),
            "capture_rms": self._last_capture_rms,
            "vad_threshold": self._vad.config.speech_threshold,
            "vad_detected": self._vad.is_in_speech,
            "speech_started_at": self._speech_started_wall,
            "speech_ended_at": "",
            "speech_duration_seconds": 0.0,
            "captured_speech_path": self._last_segment_path,
            "last_stt_text": self._last_stt_text,
            "last_empty_stt_reason": self._last_empty_stt_reason,
            "last_tts_text": self._last_tts_text,
            "last_tts_duration_seconds": self._last_tts_duration,
            "voicemail_classifier_result": self._last_voicemail_classifier_label,
            "silence_seconds": max(0.0, time.monotonic() - self._last_speech_activity),
        }

    def _write_diagnostics(self, **extra) -> None:
        payload = self._diagnostics_payload()
        payload.update(extra)
        try:
            with self._diag_lock:
                write_live_diagnostics(payload, self._diagnostics_path)
        except Exception as exc:
            logger.debug("Realtime diagnostics write skipped: %s", exc)

    def _capture_diagnostics(self) -> dict:
        if not self._capture:
            return {}
        diag_fn = getattr(self._capture, "diagnostics", None)
        if callable(diag_fn):
            try:
                data = diag_fn()
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "selected_device_index": getattr(self._capture, "selected_device_index", None),
            "selected_device_name": getattr(self._capture, "selected_device_name", ""),
            "capture_mode": getattr(self._capture, "capture_mode", ""),
        }

    def _wait_for_capture_ready(self) -> None:
        if not self._capture:
            return
        wait_ready = getattr(self._capture, "wait_ready", None)
        ready = True
        if callable(wait_ready):
            try:
                ready = bool(wait_ready(timeout=5.0))
            except TypeError:
                ready = bool(wait_ready(5.0))
            except Exception:
                ready = False
        diag = self._capture_diagnostics()
        logger.info(
            "[CAPTURE] Device diagnostics: ready=%s mode=%s index=%s name='%s' threshold=%.4f",
            ready,
            diag.get("capture_mode", ""),
            diag.get("selected_device_index"),
            diag.get("selected_device_name", ""),
            self._vad.config.speech_threshold,
        )
        if not ready:
            logger.warning("[CAPTURE] Capture device did not report ready within 5s")
        self._write_diagnostics()

    # ------------------------------------------------------------------ #
    # Transcript and recording helpers
    # ------------------------------------------------------------------ #

    def _normalize_text_for_similarity(self, text: str) -> str:
        # Lowercase, strip punctuation-ish chars, collapse whitespace.
        normalized = unicodedata.normalize("NFKD", text or "")
        asciiish = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        t = "".join(ch.lower() if ch.isalnum() or ch.isspace() else " " for ch in asciiish)
        return " ".join(t.split())

    def _is_tony_echo_transcript(self, stt_text: str) -> bool:
        """Return True if stt_text likely matches Tony's last TTS output."""
        if not stt_text:
            return False
        toney = self._last_tts_text or ""
        if not toney:
            return False

        stt_norm = self._normalize_text_for_similarity(stt_text)
        tts_norm = self._normalize_text_for_similarity(toney)

        if len(stt_norm) < self._toney_echo_min_transcript_chars:
            return False

        # Fast path: substring match (handles partial transcription)
        if stt_norm in tts_norm or tts_norm in stt_norm:
            return True

        ratio = difflib.SequenceMatcher(None, stt_norm, tts_norm).ratio()
        logger.debug(
            "[ECHO] similarity ratio=%.3f stt='%s' tts='%s'",
            ratio,
            stt_norm,
            tts_norm,
        )
        return ratio >= self._toney_echo_similarity_threshold

    def _is_voicemail_transcript(self, stt_text: str) -> bool:
        text = self._normalize_text_for_similarity(stt_text)
        if not text:
            return False
        phrases = (
            "forwarded to voicemail",
            "forwarded to voice mail",
            "leave a message",
            "record your message",
            "after the tone",
            "at the tone",
            "mailbox",
            "buzon de voz",
            "buzon voz",
            "llamada se reenvio",
            "llamada fue reenviada",
            "mensaje despues del tono",
        )
        if any(phrase in text for phrase in phrases):
            return True
        return ("buzon" in text and "voz" in text) or ("buz" in text and "voz" in text) or ("llamada" in text and "voz" in text)

    def _is_unusable_transcript(self, stt_text: str) -> bool:
        """Filter STT hallucinations/fragments before the sales agent responds."""
        text = self._normalize_text_for_similarity(stt_text)
        if not text:
            return True

        allowed_short = {
            "yes", "yeah", "yep", "no", "nope", "hello", "hi", "hey",
            "owner", "manager", "speaking", "busy",
        }
        if text in allowed_short:
            return False

        bad_exact = {
            "and", "is", "you", "the", "a", "to", "for", "okay", "ok",
            "busin", "business", "to 4 pm", "8 am to 4 pm", "4 pm",
        }
        if text in bad_exact:
            return True

        bad_phrases = (
            "i love you",
            "ski i love you",
            "thank you for watching",
            "please subscribe",
            "music",
        )
        if any(phrase in text for phrase in bad_phrases):
            return True

        words = text.split()
        if len(words) == 1 and len(text) < 7:
            return True
        if len(words) <= 3 and any(fragment in text for fragment in ("8 am", "4 pm", "to 4", "monday through friday")):
            return True
        return False

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
            logger.info("[TRANSCRIPT] wrote speaker=%s path=%s", speaker, self._transcript_path)
        except OSError as exc:
            logger.warning("[TRANSCRIPT] write error: %s", exc)

    def _open_recording(self) -> None:
        if not self._recording_path:
            return
        try:
            self._recording_path.parent.mkdir(parents=True, exist_ok=True)
            with self._recording_lock:
                self._recording_segments = []
                self._recording_start_monotonic = time.monotonic()
                self._recording_open = True
            logger.info("[RECORDING] mixed call audio path=%s", self._recording_path)
        except OSError as exc:
            self._recording_open = False
            logger.warning("[RECORDING] setup error: %s", exc)

    def _write_recording_frame(self, frame: np.ndarray) -> None:
        self._record_audio_segment(frame, _SAMPLERATE, offset_seconds=None)

    def _record_tony_audio(self, audio: np.ndarray, samplerate: int) -> None:
        """Add Tony's synthesized TTS to the mixed call recording timeline."""
        self._record_audio_segment(audio, samplerate, offset_seconds=None)

    def _record_audio_segment(
        self,
        audio: np.ndarray,
        samplerate: int,
        *,
        offset_seconds: Optional[float],
    ) -> None:
        try:
            mono = np.asarray(audio, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            if int(samplerate) != _SAMPLERATE:
                mono = _resample_mono(mono, int(samplerate), _SAMPLERATE)
            else:
                mono = mono.astype(np.float32, copy=True)
            with self._recording_lock:
                if not self._recording_open or not self._recording_start_monotonic:
                    return
                if offset_seconds is None:
                    offset_seconds = time.monotonic() - self._recording_start_monotonic
                start_sample = max(0, int(round(offset_seconds * _SAMPLERATE)))
                self._recording_segments.append((start_sample, mono))
        except Exception as exc:
            logger.debug("[RECORDING] frame skipped: %s", exc)

    def _close_recording(self) -> None:
        with self._recording_lock:
            segments = list(self._recording_segments)
            self._recording_segments = []
            recording_path = self._recording_path
            self._recording_open = False
        if not recording_path:
            return
        try:
            mixed = _mix_recording_segments(segments)
            pcm = (np.clip(mixed, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(recording_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(_SAMPLERATE)
                wf.writeframes(pcm.tobytes())
            logger.info("[RECORDING] finalized mixed path=%s", recording_path)
        except Exception as exc:
            logger.warning("[RECORDING] close error: %s", exc)

    def _write_speech_segment(self, audio: np.ndarray) -> str:
        if not self._realtime_debug:
            return ""
        try:
            _SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
            name = f"speech_{time.strftime('%Y%m%d_%H%M%S')}_{self._loop_iteration + 1:03d}.wav"
            path = _SEGMENT_DIR / name
            pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
            with wave.open(str(path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(_SAMPLERATE)
                wf.writeframes(pcm.tobytes())
            return str(path)
        except Exception as exc:
            logger.debug("[VAD] speech segment file write skipped: %s", exc)
            return ""

    # ------------------------------------------------------------------ #
    # Hotkey handlers
    # ------------------------------------------------------------------ #

    def _handle_takeover(self) -> None:
        logger.info("[TAKEOVER] AI paused; human takeover active")
        self._takeover_event.set()
        self.tts.stop()

    def _handle_resume(self) -> None:
        logger.info("[RESUME] AI resumed")
        self._takeover_event.clear()
        self._vad.reset()
        _drain(self._speech_q)
        self._set_state(STATE_LISTENING, "manual resume")

    def _handle_stop(self) -> None:
        logger.info("[STOP] Ending conversation")
        self.tts.stop()
        self.stop()

    # ------------------------------------------------------------------ #
    # Capture thread
    # ------------------------------------------------------------------ #

    def _capture_loop(self) -> None:
        logger.info("[CAPTURE] Capture processing thread started")
        calibration: list[np.ndarray] = []
        if self._calibrate_frames == 0:
            self._calibration_event.set()

        while not self._stop_event.is_set():
            capture_error = getattr(self._capture, "last_error", None)
            if isinstance(capture_error, BaseException):
                logger.error("[CAPTURE] AudioCapture failed: %s", capture_error)
                self.stop()
                return

            frame = self._capture.read(timeout=0.05) if self._capture else None
            now = time.monotonic()
            if frame is None:
                self._maybe_log_silence(now)
                continue

            frame = np.asarray(frame, dtype=np.float32)
            self._write_recording_frame(frame)
            rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))
            self._last_capture_rms = rms
            threshold = self._vad.config.speech_threshold

            self._log_rms_if_due(now, rms, threshold)

            if self.tts.is_speaking():
                if rms >= max(threshold * 1.2, threshold + 0.003):
                    logger.info(
                        "[CAPTURE] Barge-in audio while TTS speaking rms=%.4f threshold=%.4f; stopping agent",
                        rms,
                        threshold,
                    )
                    self.tts.stop()
                    self._answer_confirmed.set()
                    self._capture_suppress_until = time.monotonic() + 0.02
                self._vad.reset()
                self._write_diagnostics(vad_detected=False)
                continue

            if now < self._capture_suppress_until:
                self._vad.reset()
                self._write_diagnostics(vad_detected=False)
                continue

            self._maybe_calibrate(calibration, frame, rms)

            if not self._answer_confirmed.is_set() and rms >= threshold:
                self._answer_confirmed.set()
                logger.info("[CAPTURE] Inbound audio evidence rms=%.4f threshold=%.4f", rms, threshold)

            if self._takeover_event.is_set():
                continue

            before = self._vad.is_in_speech
            # Voicemail detection for the first 15 seconds after call connect.
            if not self._voicemail_detected and time.monotonic() <= self._voicemail_check_deadline_monotonic:
                try:
                    label = self._voicemail_detector.process_frame(frame, samplerate=_SAMPLERATE)
                    if label != self._last_voicemail_classifier_label:
                        self._last_voicemail_classifier_label = label
                        logger.info(
                            "[VM] audio_classifier_result=%s window_remaining=%.1fs rms=%.4f",
                            label,
                            max(0.0, self._voicemail_check_deadline_monotonic - time.monotonic()),
                            rms,
                        )
                    if label == "beep_detected":
                        self._voicemail_detected = True
                        logger.info("[VM] Voicemail beep detected early; stopping conversation loop")
                        if self._session is not None and not self._session.is_terminal():
                            try:
                                self._session.outcome = "voicemail"
                                self._session.transition(CallState.VOICEMAIL, "voicemail beep detected early")
                            except ValueError:
                                # Transition already occurred or illegal; ignore.
                                pass
                        self.stop()
                        return
                    if label == "voicemail_greeting":
                        logger.debug("[VM] Possible voicemail greeting observed; waiting for beep confirmation")
                except Exception as exc:
                    logger.debug("[VM] detection frame error: %s", exc)

            segment = self._vad.process_frame(frame)

            after = self._vad.is_in_speech
            self._write_diagnostics(vad_detected=after or rms >= threshold)

            if not before and after:
                self._speech_started_monotonic = now
                self._speech_started_wall = _wall_ts()
                self._set_state(
                    STATE_CAPTURING_SPEECH,
                    f"speech_start={self._speech_started_wall} rms={rms:.4f}",
                )
                logger.info(
                    "[VAD] speech start timestamp=%s rms=%.4f threshold=%.4f",
                    self._speech_started_wall,
                    rms,
                    threshold,
                )

            if segment is not None:
                self._handle_speech_segment(segment, now)

            self._maybe_log_silence(now)

    def _maybe_calibrate(
        self,
        calibration: list[np.ndarray],
        frame: np.ndarray,
        rms: float,
    ) -> None:
        if self._calibration_event.is_set() or self._calibrate_frames <= 0:
            return
        quiet_limit = max(0.002, self._vad.config.speech_threshold * 0.65)
        if rms > quiet_limit or self._vad.is_in_speech:
            return
        calibration.append(frame)
        if len(calibration) >= self._calibrate_frames:
            old = self._vad.config.speech_threshold
            new = self._vad.calibrate_threshold(calibration)
            self._calibration_event.set()
            logger.info(
                "[CAPTURE] VAD calibrated old_threshold=%.4f new_threshold=%.4f quiet_frames=%d",
                old,
                new,
                len(calibration),
            )
            self._write_diagnostics(vad_threshold=new)

    def _handle_speech_segment(self, segment: np.ndarray, ended_monotonic: float) -> None:
        duration = len(segment) / float(_SAMPLERATE)
        ended_wall = _wall_ts()
        path = self._write_speech_segment(segment)
        self._last_segment_path = path
        self._last_speech_activity = ended_monotonic
        self._next_silence_log = ended_monotonic + self._max_silence_seconds
        logger.info(
            "[VAD] speech end timestamp=%s duration=%.2fs captured_speech_path=%s",
            ended_wall,
            duration,
            path or "<not saved>",
        )
        self._write_diagnostics(
            speech_ended_at=ended_wall,
            speech_duration_seconds=duration,
            captured_speech_path=path,
        )

        if duration < self._min_speech_seconds:
            logger.info(
                "[VAD] segment dropped: duration %.2fs below MIN_SPEECH_SECONDS %.2fs",
                duration,
                self._min_speech_seconds,
            )
            self._set_state(STATE_LISTENING, "short segment dropped")
            return

        if self._speech_q.full():
            logger.warning("[VAD] speech queue full; dropping %.2fs segment", duration)
            self._set_state(STATE_LISTENING, "speech queue full")
            return

        self._speech_q.put(segment)
        if self._response_active.is_set():
            logger.info("[VAD] queued %.2fs speech segment for STT", duration)
        else:
            logger.info(
                "[VAD] queued %.2fs speech segment for STT after opening completes",
                duration,
            )
            self._set_state(STATE_LISTENING, "speech queued until response loop activates")

    def _log_rms_if_due(self, now: float, rms: float, threshold: float) -> None:
        if now < self._next_rms_log:
            return
        self._next_rms_log = now + self._rms_log_interval
        if self._state in (STATE_LISTENING, STATE_CAPTURING_SPEECH, STATE_SILENCE_TIMEOUT):
            diag = self._capture_diagnostics()
            logger.info(
                "[AUDIO] state=%s rms=%.4f vad_threshold=%.4f vad_detected=%s "
                "capture_device='%s' index=%s iteration=%d",
                self._state,
                rms,
                threshold,
                self._vad.is_in_speech or rms >= threshold,
                diag.get("selected_device_name") or self.capture_device_hint,
                diag.get("selected_device_index"),
                self._loop_iteration,
            )
            self._write_diagnostics()

    def _maybe_log_silence(self, now: float) -> None:
        if (
            self._max_silence_seconds <= 0
            or not self._response_active.is_set()
            or self._takeover_event.is_set()
            or self.tts.is_speaking()
        ):
            return
        if self._state not in (STATE_LISTENING, STATE_SILENCE_TIMEOUT):
            return
        silence_for = now - self._last_speech_activity
        if silence_for < self._max_silence_seconds or now < self._next_silence_log:
            return
        self._next_silence_log = now + self._max_silence_seconds
        self._set_state(STATE_SILENCE_TIMEOUT, f"no carrier speech for {silence_for:.1f}s")
        logger.info(
            "[LOOP] silence timeout %.1fs reached; silence_does_not_end_call=%s",
            silence_for,
            self._silence_does_not_end_call,
        )
        if not self._silence_does_not_end_call:
            self.stop()

    # ------------------------------------------------------------------ #
    # Response thread
    # ------------------------------------------------------------------ #

    def _response_loop(self) -> None:
        logger.info("[RESPONSE] Response thread started; waiting for LISTENING")
        while not self._stop_event.is_set():
            if self._response_active.wait(timeout=0.25):
                break
        if self._stop_event.is_set():
            return
        logger.info("[RESPONSE] Response thread active")

        while not self._stop_event.is_set():
            try:
                audio_segment = self._speech_q.get(timeout=0.25)
            except queue.Empty:
                continue

            if self._takeover_event.is_set():
                logger.info("[RESPONSE] segment ignored during human takeover")
                continue

            # --- Barge-in detection ---
            # If carrier spoke while Tony was still speaking, stop TTS immediately
            # so Tony doesn't finish his reply over the carrier's interruption.
            if self.tts.is_speaking():
                logger.info("[RESPONSE] Barge-in detected: prospect spoke during TTS; stopping agent")
                self.tts.stop()
                # Brief wait for audio pipeline to settle
                time.sleep(0.05)
            if self._stop_event.is_set():
                break

            # Re-check TTS state right before STT; unit tests expect that if
            # TTS is speaking, we do not even call STT.
            if self.tts.is_speaking():
                logger.info("[RESPONSE] segment skipped because TTS is speaking")
                continue

            self._loop_iteration += 1
            duration = len(audio_segment) / float(_SAMPLERATE)
            self._set_state(STATE_TRANSCRIBING, f"audio_duration={duration:.2f}s")


            # Build a per-call enriched STT prompt including known carrier context
            dynamic_prompt = (
                getattr(self.agent, "build_stt_prompt", lambda: self._stt_prompt)()
                or self._stt_prompt
            )

            try:
                transcript = self.stt.transcribe(

                    audio_segment,
                    samplerate=_SAMPLERATE,
                    prompt=dynamic_prompt,
                )

            except Exception as exc:
                self._last_empty_stt_reason = f"exception: {exc}"
                self._write_diagnostics(last_empty_stt_reason=self._last_empty_stt_reason)
                logger.error("[STT] Failed: %s", exc)
                self._set_state(STATE_LISTENING, "STT exception; waiting for retry")
                continue

            if not transcript:
                reason = getattr(self.stt, "last_empty_reason", "") or "STT returned empty text"

                self._last_empty_stt_reason = reason
                self._write_diagnostics(last_empty_stt_reason=reason)
                logger.info("[STT] Empty reason=%s", reason)
                self._set_state(STATE_LISTENING, "empty STT; waiting for next speech")
                continue

            # Tony self-talk suppression: if STT transcript looks like our last TTS output,
            # treat it as echo and ignore it.
            if self._is_tony_echo_transcript(transcript):
                self._last_empty_stt_reason = "suppressed_tony_echo_similarity"
                self._write_diagnostics(last_empty_stt_reason=self._last_empty_stt_reason)
                logger.info("[STT] Suppressed Tony echo transcript=%s", transcript)
                self._set_state(STATE_LISTENING, "suppressed tony echo")
                continue

            if self._is_unusable_transcript(transcript):
                self._bad_transcript_count += 1
                self._last_empty_stt_reason = "suppressed_low_quality_transcript"
                self._write_diagnostics(last_empty_stt_reason=self._last_empty_stt_reason)
                logger.info(
                    "[STT] Suppressed low-quality transcript=%s count=%d",
                    transcript,
                    self._bad_transcript_count,
                )
                if self._bad_transcript_count == 1:
                    clarification = "Sorry, I didn't catch that clearly. Could you repeat that?"
                    speaker = self._agent_display_name()
                    logger.info("[RESPONSE] %s clarification: %s", speaker, clarification)
                    self._write_transcript(speaker, clarification)
                    self._play_tts_async_like(clarification, STATE_SPEAKING_REPLY, "clarification")
                else:
                    self._set_state(STATE_LISTENING, "suppressed low-quality STT")
                continue

            self._last_stt_text = transcript
            self._last_empty_stt_reason = ""
            self._bad_transcript_count = 0
            logger.info("[STT] response_text=%s", transcript)

            self._write_diagnostics(last_stt_text=transcript, last_empty_stt_reason="")
            self._write_transcript("Prospect", transcript)

            if self._is_voicemail_transcript(transcript):
                logger.info("[VM] voicemail prompt detected from STT transcript; switching to voicemail handling")
                if self._session is not None and not self._session.is_terminal():
                    try:
                        self._session.outcome = "voicemail"
                        self._session.transition(CallState.VOICEMAIL, "voicemail prompt detected by STT")
                    except ValueError:
                        pass
                self.stop()
                break

            self._set_state(STATE_THINKING, "generating LLM reply")
            response = self.agent.respond_to(transcript)
            if not response:
                logger.info("[RESPONSE] Empty LLM response; returning to listening")
                self._set_state(STATE_LISTENING, "empty LLM response")
                continue

            if self._defer_reply_if_prospect_is_talking():
                self._set_state(STATE_LISTENING, "deferred stale reply for newer prospect speech")
                continue

            speaker = self._agent_display_name()
            logger.info("[RESPONSE] %s: %s", speaker, response)
            self._write_transcript(speaker, response)
            # Reply path: prefer speak_async so unit tests can assert it.
            # Store last TTS for self-talk suppression.
            self._last_tts_text = response
            self._play_tts_async_like(response, STATE_SPEAKING_REPLY, "reply")


            if self.agent.should_end_call():
                logger.info("[RESPONSE] Agent requested graceful call end")
                goodbye = self.agent.goodbye_line()
                if goodbye:
                    speaker = self._agent_display_name()
                    logger.info("[RESPONSE] %s goodbye: %s", speaker, goodbye)
                    self._write_transcript(speaker, goodbye)
                    self._play_tts_blocking(goodbye, STATE_SPEAKING_REPLY, "goodbye")
                if self._session and not self._session.is_terminal():
                    try:
                        self._session.transition(CallState.ENDED, "agent ended call gracefully")
                    except ValueError:
                        pass
                self.stop()

    def _defer_reply_if_prospect_is_talking(self) -> bool:
        """Avoid speaking over a caller who continued talking while LLM was thinking."""
        if not self._speech_q.empty():
            logger.info("[RESPONSE] Newer prospect speech is queued; skipping stale reply")
            return True
        if not self._vad.is_in_speech:
            return False

        deadline = time.monotonic() + 1.5
        logger.info("[RESPONSE] Prospect still talking while reply is ready; waiting before TTS")
        while time.monotonic() < deadline and self._vad.is_in_speech and not self._stop_event.is_set():
            time.sleep(0.03)
        if not self._speech_q.empty():
            logger.info("[RESPONSE] Prospect speech arrived after wait; skipping stale reply")
            return True
        return self._vad.is_in_speech

    def _play_tts_async_like(self, text: str, state: str, label: str) -> None:
        """Play TTS while preferring speak_async (used by unit tests)."""
        if not text.strip() or self._stop_event.is_set():
            return
        self._set_state(state, f"tts_{label}_start")
        self._last_tts_text = text
        self._write_diagnostics(last_tts_text=text)
        start = time.monotonic()
        try:
            speak_async = getattr(self.tts, "speak_async", None)
            if callable(speak_async):
                thread = speak_async(text)
                if hasattr(thread, "join"):
                    thread.join()
            else:
                self.tts.speak(text)
        finally:
            duration = time.monotonic() - start
            self._last_tts_duration = duration
            logger.info("[TTS] %s finished duration=%.2fs text=%s", label, duration, text)
            self._write_diagnostics(
                last_tts_duration_seconds=duration,
                last_tts_text=text,
            )
            self._after_tts(label)

    def _play_tts_blocking(self, text: str, state: str, label: str) -> None:
        if not text.strip() or self._stop_event.is_set():

            return
        self._set_state(state, f"tts_{label}_start")
        self._last_tts_text = text
        self._write_diagnostics(last_tts_text=text)
        start = time.monotonic()
        try:
            # For opening/regular replies we keep the existing sync behavior
            # expected by tests (tts.speak). Some tests also assert
            # speak_async was used in the reply path.
            self.tts.speak(text)

        finally:
            duration = time.monotonic() - start

            self._last_tts_duration = duration
            logger.info("[TTS] %s finished duration=%.2fs text=%s", label, duration, text)
            self._write_diagnostics(
                last_tts_duration_seconds=duration,
                last_tts_text=text,
            )
            self._after_tts(label)

    def _after_tts(self, label: str) -> None:
        if self._listen_after_tts_delay_s > 0:
            self._capture_suppress_until = time.monotonic() + self._listen_after_tts_delay_s
            logger.info(
                "[TTS] suppressing agent echo for %.0fms after %s",
                self._listen_after_tts_delay_s * 1000.0,
                label,
            )
            time.sleep(self._listen_after_tts_delay_s)
        if self._capture:
            clear_fn = getattr(self._capture, "clear", None)
            if callable(clear_fn):
                try:
                    dropped = clear_fn()
                    logger.info("[TTS] post-%s capture buffer clear dropped_frames=%s", label, dropped)
                except Exception as exc:
                    logger.debug("[TTS] capture buffer clear skipped: %s", exc)
        self._vad.reset()
        self._last_speech_activity = time.monotonic()
        self._next_silence_log = self._last_speech_activity + self._max_silence_seconds
        if not self._stop_event.is_set():
            self._set_state(STATE_LISTENING, f"tts_{label}_complete")


# ------------------------------------------------------------------ #
# Utilities
# ------------------------------------------------------------------ #


def _resample_mono(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    mono = np.asarray(audio, dtype=np.float32)
    if mono.ndim > 1:
        mono = mono.mean(axis=1)
    if source_rate <= 0 or target_rate <= 0 or source_rate == target_rate or len(mono) == 0:
        return mono.astype(np.float32, copy=True)
    duration = len(mono) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    old_x = np.linspace(0.0, duration, num=len(mono), endpoint=False)
    new_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(new_x, old_x, mono).astype(np.float32)


def _mix_recording_segments(segments: list[tuple[int, np.ndarray]]) -> np.ndarray:
    if not segments:
        return np.zeros(0, dtype=np.float32)

    total_samples = max(start + len(audio) for start, audio in segments)
    mixed = np.zeros(total_samples, dtype=np.float32)
    for start, audio in segments:
        if len(audio) == 0:
            continue
        end = min(total_samples, start + len(audio))
        mixed[start:end] += audio[: end - start]

    peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
    if peak > 1.0:
        mixed = mixed / peak
    return mixed.astype(np.float32, copy=False)


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


def _wall_ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
