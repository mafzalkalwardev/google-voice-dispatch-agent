"""Tests for voicemail audio classifier."""

import numpy as np

from src.voicemail_detector import BeepDetector, VoicemailAudioClassifier


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
