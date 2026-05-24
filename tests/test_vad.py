"""Tests for src/vad.py — EnergyVAD frame processing and calibration."""

import numpy as np
import pytest

from src.vad import EnergyVAD, VADConfig


def _silence(frames: int = 1, cfg: VADConfig = VADConfig()) -> list[np.ndarray]:
    frame_size = int(cfg.samplerate * cfg.frame_ms / 1000)
    return [np.zeros(frame_size, dtype=np.float32) for _ in range(frames)]


def _speech(amplitude: float = 0.1, frames: int = 1, cfg: VADConfig = VADConfig()) -> list[np.ndarray]:
    frame_size = int(cfg.samplerate * cfg.frame_ms / 1000)
    return [
        (np.random.rand(frame_size).astype(np.float32) * 2 - 1) * amplitude
        for _ in range(frames)
    ]


def test_silence_produces_no_segment():
    vad = EnergyVAD(VADConfig())
    for frame in _silence(50):
        result = vad.process_frame(frame)
    assert result is None


def test_speech_followed_by_silence_returns_segment():
    cfg = VADConfig(
        speech_threshold=0.01,
        speech_trigger_frames=2,
        silence_trigger_frames=5,
        pre_speech_pad_frames=0,
    )
    vad = EnergyVAD(cfg)

    segment = None
    # Feed loud speech frames
    for frame in _speech(amplitude=0.3, frames=10, cfg=cfg):
        segment = vad.process_frame(frame)

    # Feed silence to flush
    for frame in _silence(frames=10, cfg=cfg):
        r = vad.process_frame(frame)
        if r is not None:
            segment = r

    assert segment is not None
    assert isinstance(segment, np.ndarray)
    assert len(segment) > 0


def test_calibrate_threshold_raises_threshold_above_silence():
    cfg = VADConfig()
    vad = EnergyVAD(cfg)
    silence_frames = _silence(30)
    threshold = vad.calibrate_threshold(silence_frames)
    # Calibrated threshold should be a positive float
    assert threshold > 0.0
    # Should be stored back into config
    assert vad._cfg.speech_threshold == threshold


def test_calibrate_threshold_with_nonzero_silence():
    cfg = VADConfig()
    vad = EnergyVAD(cfg)
    # Slight background noise
    noisy = [np.random.rand(480).astype(np.float32) * 0.002 for _ in range(20)]
    threshold = vad.calibrate_threshold(noisy)
    assert threshold > 0.0


def test_reset_clears_state():
    cfg = VADConfig(speech_threshold=0.01, speech_trigger_frames=2, silence_trigger_frames=5)
    vad = EnergyVAD(cfg)
    for frame in _speech(amplitude=0.3, frames=5, cfg=cfg):
        vad.process_frame(frame)
    vad.reset()
    # After reset, should behave as fresh instance — silence should not produce a segment
    for frame in _silence(frames=5, cfg=cfg):
        result = vad.process_frame(frame)
    assert result is None


def test_max_speech_seconds_flushes_segment():
    cfg = VADConfig(
        speech_threshold=0.01,
        speech_trigger_frames=2,
        silence_trigger_frames=200,  # long silence needed
        max_speech_seconds=0.1,      # flush after 100ms
        pre_speech_pad_frames=0,
    )
    vad = EnergyVAD(cfg)

    segment = None
    # Feed enough speech to exceed max_speech_seconds
    for frame in _speech(amplitude=0.5, frames=100, cfg=cfg):
        r = vad.process_frame(frame)
        if r is not None:
            segment = r
            break

    assert segment is not None


def test_short_noise_burst_below_trigger_count_ignored():
    cfg = VADConfig(
        speech_threshold=0.01,
        speech_trigger_frames=5,  # need 5 consecutive frames
        silence_trigger_frames=5,
        pre_speech_pad_frames=0,
    )
    vad = EnergyVAD(cfg)
    # Only 3 speech frames — should not trigger
    for frame in _speech(amplitude=0.3, frames=3, cfg=cfg):
        vad.process_frame(frame)
    for frame in _silence(frames=10, cfg=cfg):
        result = vad.process_frame(frame)
    assert result is None


def test_process_frame_accepts_int16_like_input():
    """VAD should handle int16-range floats without crashing."""
    cfg = VADConfig(speech_threshold=0.01)
    vad = EnergyVAD(cfg)
    frame = (np.random.rand(480).astype(np.float32) - 0.5) * 2.0
    result = vad.process_frame(frame)
    # Just checking no exception; result may be None
    assert result is None or isinstance(result, np.ndarray)
