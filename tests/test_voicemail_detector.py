"""Tests for voicemail audio classifier."""

import numpy as np

from src.voicemail_detector import BeepDetector, VoicemailAudioClassifier, VoicemailDetector


def test_beep_detector_on_tone():
    sr = 16000
    duration = 0.5
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    tone = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    det = BeepDetector()
    frame_len = int(sr * 0.03)
    found = False
    for i in range(0, len(tone) - frame_len, frame_len):
        if det.process_frame(tone[i : i + frame_len], samplerate=sr):
            found = True
            break
    assert found


def test_classifier_reset():
    clf = VoicemailAudioClassifier()
    clf.reset()
    assert clf.beep_ready is False


def test_silence_label():
    clf = VoicemailAudioClassifier()
    frame = np.zeros(480, dtype=np.float32)
    label = clf.process_frame(frame, samplerate=16000)
    assert label in ("silence", "uncertain", "live_call")


def test_multisignal_detector_requires_answer_window_and_two_factors():
    detector = VoicemailDetector()

    too_early = detector.classify(
        answer_elapsed_seconds=6.9,
        continuous_greeting_duration_seconds=5.0,
        beep_detected=True,
    )
    single_signal = detector.classify(
        answer_elapsed_seconds=8.0,
        beep_detected=True,
    )
    confirmed = detector.classify(
        answer_elapsed_seconds=8.0,
        continuous_greeting_duration_seconds=5.0,
        beep_detected=True,
    )

    assert too_early.candidate is False
    assert single_signal.candidate is False
    assert confirmed.candidate is True
    assert confirmed.confidence >= 0.85


def test_multisignal_detector_human_evidence_blocks_voicemail():
    result = VoicemailDetector().classify(
        transcript="Please leave a message after the beep",
        answer_elapsed_seconds=10.0,
        continuous_greeting_duration_seconds=6.0,
        beep_detected=True,
        human_detected=True,
    )

    assert result.candidate is False
    assert result.confidence <= 0.35
