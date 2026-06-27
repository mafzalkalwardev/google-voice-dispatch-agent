"""
Continuous audio capture for prospect speech detection.

Two capture modes:
  'default'  — WASAPI loopback on the system default speaker via soundcard.
               Captures everything Chrome plays, including the prospect's voice.
               Requires: pip install soundcard

  <name>     — sounddevice InputStream on the first input device whose name
               contains <name>.  Use with a second VB-CABLE: set Chrome's
               speaker to "CABLE B Input" and set capture_device="CABLE B Output".

Usage:
    cap = AudioCapture("default")   # or AudioCapture("CABLE B Output")
    cap.start()
    while True:
        frame = cap.read(timeout=0.1)   # numpy float32 mono, length = frame_size
        if frame is not None:
            process(frame)
    cap.stop()
"""

from __future__ import annotations

import logging
import queue
import threading
import warnings
from typing import Optional

import numpy as np

logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000   # Whisper-native; also VAD-compatible
_FRAME_MS   = 30      # 30 ms per frame

_LOOPBACK_ALIASES = frozenset({"default", "loopback", "speaker", "speakers"})


class AudioCapture:
    def __init__(
        self,
        device_name_hint: str = "default",
        samplerate: int = _SAMPLERATE,
        frame_ms: int = _FRAME_MS,
    ):
        self.device_name_hint = device_name_hint
        self.samplerate = samplerate
        self.frame_ms = frame_ms
        self.frame_size = int(samplerate * frame_ms / 1000)

        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[Exception] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, name="AudioCapture", daemon=True
        )
        self._thread.start()
        logger.info(
            "AudioCapture started (device='%s', rate=%d Hz, frame=%d ms)",
            self.device_name_hint, self.samplerate, self.frame_ms,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.debug("AudioCapture stopped")

    def read(self, timeout: float = 0.1) -> Optional[np.ndarray]:
        """Return next audio frame or None if no data within timeout."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def last_error(self) -> Optional[Exception]:
        return self._error

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _put(self, frame: np.ndarray) -> None:
        """Non-blocking enqueue; drops frame if queue is full."""
        if not self._q.full():
            self._q.put(frame)

    def _run(self) -> None:
        try:
            if self.device_name_hint.lower() in _LOOPBACK_ALIASES:
                self._run_soundcard_loopback()
            else:
                self._run_sounddevice()
        except Exception as exc:
            self._error = exc
            logger.error("AudioCapture thread error: %s", exc)

    def _run_soundcard_loopback(self) -> None:
        """WASAPI loopback on the system default speaker."""
        try:
            import soundcard as sc
        except ImportError:
            raise RuntimeError(
                "soundcard not installed. Run: pip install soundcard\n"
                "Or set capture_device to a named input device instead of 'default'."
            )

        speaker = sc.default_speaker()
        try:
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
        except Exception as exc:
            raise RuntimeError(
                f"Could not open WASAPI loopback capture for default speaker "
                f"'{speaker.name}': {exc}"
            ) from exc

        logger.info(
            "Selected capture device for CAPTURE_DEVICE='%s': WASAPI loopback of default speaker '%s'",
            self.device_name_hint,
            speaker.name,
        )
        if "cable input" in str(speaker.name).lower():
            logger.warning(
                "CAPTURE_DEVICE=default is loopbacking '%s'. This records whatever is "
                "played to the TTS cable, so STT may hear Tony or silence instead of the prospect. "
                "For single-cable routing, keep Windows/Chrome speaker on real speakers; "
                "for cleaner routing use a second cable and set CAPTURE_DEVICE to that cable output.",
                speaker.name,
            )

        with mic.recorder(samplerate=self.samplerate) as recorder:
            while not self._stop.is_set():
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message="data discontinuity in recording",
                    )
                    data = recorder.record(numframes=self.frame_size)
                if data.ndim > 1:
                    data = data.mean(axis=1)         # stereo → mono
                self._put(data.astype(np.float32))

    def _run_sounddevice(self) -> None:
        """Capture from a named input device via sounddevice InputStream."""
        try:
            import sounddevice as sd
        except ImportError:
            raise RuntimeError("sounddevice not installed. Run: pip install sounddevice")

        from src.voice_playback import describe_audio_device, list_audio_devices

        device_idx: Optional[int] = None
        for d in list_audio_devices():
            if (
                self.device_name_hint.lower() in d["name"].lower()
                and d["max_input_channels"] > 0
            ):
                device_idx = d["index"]
                logger.info(
                    "Selected capture device for CAPTURE_DEVICE='%s': %s",
                    self.device_name_hint,
                    describe_audio_device(device_idx),
                )
                break

        if device_idx is None:
            raise RuntimeError(
                f"Capture device '{self.device_name_hint}' not found as an input device. "
                "Run: python -m src.audio_diagnostics"
            )

        def _cb(indata: np.ndarray, frames: int, time_info, status) -> None:
            if status:
                logger.debug("AudioCapture status: %s", status)
            self._put(indata[:, 0].copy().astype(np.float32))

        with sd.InputStream(
            device=device_idx,
            samplerate=self.samplerate,
            channels=1,
            dtype="float32",
            blocksize=self.frame_size,
            callback=_cb,
        ):
            self._stop.wait()
