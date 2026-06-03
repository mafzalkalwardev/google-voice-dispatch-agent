"""Calibrate voicemail audio classifier against sample WAV files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.voicemail_detector import VoicemailAudioClassifier

SAMPLES_DIR = ROOT / "data" / "voicemail_samples"
CONFIG_FILE = ROOT / "dialer_config.json"


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    data, rate = sf.read(path, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, rate


def classify_file(path: Path) -> str:
    data, rate = _load_wav(path)
    clf = VoicemailAudioClassifier(samplerate=rate)
    frame_len = int(rate * 0.03)
    labels: dict[str, int] = {}
    for start in range(0, len(data) - frame_len, frame_len):
        frame = data[start : start + frame_len]
        label = clf.process_frame(frame, samplerate=rate)
        labels[label] = labels.get(label, 0) + 1
    if not labels:
        return "uncertain"
    return max(labels, key=labels.get)


def main() -> int:
    files = sorted(SAMPLES_DIR.glob("*.wav"))
    if not files:
        print(f"No WAV files in {SAMPLES_DIR}. Add samples and re-run.")
        return 1

    print(f"Classifying {len(files)} sample(s)...")
    vm_hits = 0
    for path in files:
        label = classify_file(path)
        print(f"  {path.name}: {label}")
        if label in ("voicemail_greeting", "beep_detected"):
            vm_hits += 1

    ratio = vm_hits / max(len(files), 1)
    suggestion = {
        "voicemail_play_on_greeting": True,
        "voicemail_greeting_frames_required": 4 if ratio > 0.5 else 6,
        "voicemail_max_wait_seconds": 6 if ratio > 0.5 else 8,
        "voicemail_play_after_seconds": 3 if ratio > 0.5 else 4,
    }
    print("\nSuggested dialer_config.json updates:")
    print(json.dumps(suggestion, indent=2))

    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg.update(suggestion)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"\nMerged into {CONFIG_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
