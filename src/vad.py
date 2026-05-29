"""
Energy-based Voice Activity Detection.

Uses RMS energy thresholding to identify speech segments. No extra dependencies.

Typical usage:
    vad = EnergyVAD()
    for frame in stream:                # numpy float32, e.g. 480 samples at 16kHz
        segment = vad.process_frame(frame)
        if segment is not None:
            stt.transcribe(segment)     # complete utterance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class VADConfig:
    samplerate: int = 16000
    frame_ms: int = 30             # must match AudioCapture.frame_ms

    # Energy threshold (0.0–1.0 RMS). Calibrate with calibrate_threshold().
    speech_threshold: float = 0.015

    # Consecutive speech frames required before declaring speech started.
    # 2 frames × 30 ms = 60 ms onset — fast response without false triggers.
    speech_trigger_frames: int = 2

    # Consecutive silence frames required before declaring speech ended.
    # 12 frames × 30 ms = 360 ms trailing silence — much tighter than 900 ms.
    # This lets Tony reply ~540 ms faster after the carrier finishes speaking.
    silence_trigger_frames: int = 12

    # Hard cap on segment length.
    max_speech_seconds: float = 25.0

    # Pre-roll: frames prepended from the ring buffer before speech started.
    pre_speech_pad_frames: int = 5

    # Minimum threshold floor — never calibrate below this (avoids false triggers
    # on silent-room noise floor).
    min_threshold: float = 0.004

    # Hysteresis factor: once in speech, require threshold * this multiplier
    # to fall out of speech (reduces mid-word choppping on quiet speakers).
    hysteresis_factor: float = 0.70


class EnergyVAD:
    """Stateful single-channel voice activity detector.

    Call process_frame() for every audio frame.
    Returns a complete speech segment (float32 numpy array) when speech ends.
    Returns None for all other frames.
    """

    @property
    def is_in_speech(self) -> bool:
        """Whether the VAD currently considers itself inside an utterance."""
        return bool(getattr(self, "_in_speech", False))

    def __init__(self, config: Optional[VADConfig] = None):

        self.config = config or VADConfig()
        self._cfg = self.config
        self._reset_state()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def process_frame(self, frame: np.ndarray) -> Optional[np.ndarray]:
        """
        Feed one audio frame. Returns a complete utterance or None.
        Uses hysteresis so quiet speakers don't get chopped mid-word.
        """
        rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))

        # Hysteresis: once inside speech, use a lower effective threshold
        # so quiet phone audio doesn't get fragmented.
        if self._in_speech:
            effective_threshold = (
                self.config.speech_threshold * self.config.hysteresis_factor
            )
        else:
            effective_threshold = self.config.speech_threshold

        is_speech = rms >= effective_threshold

        if not self._in_speech:
            self._ring.append(frame)
            if len(self._ring) > self.config.pre_speech_pad_frames:
                self._ring.pop(0)

            self._speech_trigger = (self._speech_trigger + 1) if is_speech else max(0, self._speech_trigger - 1)

            if self._speech_trigger >= self.config.speech_trigger_frames:
                self._in_speech = True
                self._silence_count = 0
                self._speech_frames = 0
                self._speech_buf = list(self._ring)
                self._ring = []
        else:
            self._speech_buf.append(frame)
            self._speech_frames += 1

            if not is_speech:
                self._silence_count += 1
            else:
                # Reset silence counter on any speech — handles brief pauses
                self._silence_count = 0

            max_frames = int(
                self.config.max_speech_seconds
                * self.config.samplerate
                / max(1, int(self.config.samplerate * self.config.frame_ms / 1000))
            )
            timed_out    = self._speech_frames >= max_frames
            long_silence = self._silence_count >= self.config.silence_trigger_frames

            if timed_out or long_silence:
                audio = np.concatenate(self._speech_buf)
                self._reset_state()
                return audio

        return None

    def calibrate_threshold(self, silence_frames: List[np.ndarray]) -> float:
        """
        Auto-calibrate from ambient noise frames.
        Sets threshold to 3.5× the mean RMS of provided silence frames,
        but never below config.min_threshold to prevent false triggers.
        Also uses the 90th percentile instead of mean to better handle
        frames with brief noise bursts in the calibration window.
        Returns the new threshold.
        """
        if not silence_frames:
            return self.config.speech_threshold
        rmss = np.array([float(np.sqrt(np.mean(f.astype(np.float64) ** 2))) for f in silence_frames])
        # Use 90th percentile to be robust against noise bursts during calibration
        ambient = float(np.percentile(rmss, 90))
        new_threshold = max(ambient * 3.5, self.config.min_threshold)
        # Don't lower threshold drastically in one calibration step
        new_threshold = max(new_threshold, self.config.speech_threshold * 0.3)
        self.config.speech_threshold = new_threshold
        return new_threshold

    def reset(self) -> None:
        self._reset_state()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _reset_state(self) -> None:
        self._ring:         List[np.ndarray] = []
        self._speech_buf:   List[np.ndarray] = []
        self._in_speech:    bool = False
        self._speech_trigger: int = 0
        self._silence_count:  int = 0
        self._speech_frames:  int = 0
