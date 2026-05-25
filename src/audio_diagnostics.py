"""
Windows audio routing diagnostics for VB-CABLE + Google Voice + realtime agent.

Run directly:
    python -m src.audio_diagnostics
    python -m src.audio_diagnostics "CABLE B Output" "CABLE Input"
                                     ^ capture hint     ^ playback hint

Returns a structured dict with: cable_inputs, cable_outputs, input_devices,
output_devices, capture_match, soundcard_ok, issues, suggestions, ok.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("GoogleVoiceAgent")


def run_diagnostics(
    capture_hint: str = "default",
    output_hint:  str = "CABLE Input",
) -> dict:
    """Check audio devices and routing.  Does not produce output."""
    from src.voice_playback import list_audio_devices

    devices = list_audio_devices()

    cable_inputs  = [d for d in devices if "cable input"  in d["name"].lower()]
    cable_outputs = [d for d in devices if "cable output" in d["name"].lower()]
    output_devices = [d for d in devices if d["max_output_channels"] > 0]
    input_devices  = [d for d in devices if d["max_input_channels"]  > 0]

    # Resolve the target playback device
    output_match: Optional[dict] = None
    for d in output_devices:
        if output_hint.lower() in d["name"].lower():
            output_match = d
            break

    # Resolve the capture device (only for named hints; loopback is via soundcard)
    capture_match: Optional[dict] = None
    _loopback_aliases = {"default", "loopback", "speaker", "speakers"}
    using_loopback = capture_hint.lower() in _loopback_aliases
    if not using_loopback:
        for d in input_devices:
            if capture_hint.lower() in d["name"].lower():
                capture_match = d
                break

    issues: list[str] = []
    suggestions: list[str] = []

    # VB-CABLE presence
    if not cable_inputs:
        issues.append("CABLE Input device not found — Tony's TTS cannot reach Google Voice")
        suggestions.append("Install VB-CABLE from https://vb-audio.com/Cable/ and reboot")
    if not cable_outputs:
        issues.append("CABLE Output device not found — Chrome microphone chain is broken")

    # Output device
    if output_match is None:
        issues.append(f"Playback device '{output_hint}' not found")
        suggestions.append(
            f"Available output devices: {[d['name'] for d in output_devices[:6]]}"
        )

    # Capture device
    if using_loopback:
        soundcard_ok = _check_soundcard()
        if not soundcard_ok:
            issues.append("soundcard not installed — WASAPI loopback capture unavailable")
            suggestions.append("Run: pip install soundcard")
    else:
        soundcard_ok = _check_soundcard()
        if capture_match is None:
            issues.append(f"Capture device '{capture_hint}' not found as an input device")
            suggestions.append(
                f"Available input devices: {[d['name'] for d in input_devices[:6]]}"
            )

    return {
        "cable_inputs":   cable_inputs,
        "cable_outputs":  cable_outputs,
        "output_devices": output_devices,
        "input_devices":  input_devices,
        "output_match":   output_match,
        "capture_match":  capture_match,
        "using_loopback": using_loopback,
        "soundcard_ok":   soundcard_ok,
        "issues":         issues,
        "suggestions":    suggestions,
        "ok":             len(issues) == 0,
    }


def play_test_tts(
    output_hint: str = "CABLE Input",
    text: str = (
        "Indus Transports audio test. If Google Voice microphone uses CABLE Output, "
        "the caller can hear this."
    ),
    output_dir: Optional[Path] = None,
) -> dict:
    """Generate a short TTS WAV and play it to the configured loopback output."""
    from src.paths import runtime_base
    from src.tts import save_text_to_speech
    from src.voice_playback import (
        describe_audio_device,
        find_playable_loopback_device,
        play_wav_to_device,
    )

    diagnostics_dir = Path(output_dir or (runtime_base() / "audio" / "diagnostics"))
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    wav_path = diagnostics_dir / f"tts_loopback_test_{time.strftime('%Y%m%d_%H%M%S')}.wav"

    logger.info("Audio test: generating TTS file for LOOPBACK_DEVICE='%s'", output_hint)
    save_text_to_speech(text, wav_path)
    device_index = find_playable_loopback_device(output_hint)
    if device_index is None:
        raise RuntimeError(f"No playable LOOPBACK_DEVICE found for '{output_hint}'")

    selected_device = describe_audio_device(device_index)
    logger.info("Audio test: playing TTS file to selected output device %s", selected_device)
    duration = play_wav_to_device(wav_path, device_index, block=True)
    return {
        "ok": True,
        "wav_path": str(wav_path),
        "duration_s": duration,
        "selected_output_device": selected_device,
    }


def record_capture_sample(
    capture_hint: str = "default",
    duration_s: float = 5.0,
    stt_api_key: str = "",
    stt_model: str = "whisper-large-v3-turbo",
    output_dir: Optional[Path] = None,
) -> dict:
    """Record a short sample from CAPTURE_DEVICE and optionally transcribe it."""
    import numpy as np
    import soundfile as sf

    from src.audio_capture import AudioCapture
    from src.paths import runtime_base
    from src.stt import GroqWhisperSTT

    samplerate = 16000
    diagnostics_dir = Path(output_dir or (runtime_base() / "audio" / "diagnostics"))
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    wav_path = diagnostics_dir / f"capture_test_{time.strftime('%Y%m%d_%H%M%S')}.wav"

    logger.info(
        "Audio test: recording %.1fs from CAPTURE_DEVICE='%s'",
        duration_s,
        capture_hint,
    )
    cap = AudioCapture(device_name_hint=capture_hint, samplerate=samplerate)
    frames: list[np.ndarray] = []
    cap.start()
    try:
        deadline = time.monotonic() + max(0.1, duration_s)
        while time.monotonic() < deadline:
            if cap.last_error is not None:
                raise RuntimeError(f"Capture failed: {cap.last_error}")
            frame = cap.read(timeout=0.2)
            if frame is not None:
                frames.append(frame)
    finally:
        cap.stop()

    if frames:
        audio = np.concatenate(frames).astype(np.float32)
    else:
        audio = np.zeros(0, dtype=np.float32)

    if audio.size == 0:
        logger.warning("Audio test: capture returned no frames")
        sf.write(str(wav_path), audio, samplerate)
        return {
            "ok": False,
            "wav_path": str(wav_path),
            "duration_s": 0.0,
            "rms": 0.0,
            "peak": 0.0,
            "stt_text": "",
            "stt_error": "No audio frames captured",
            "capture_device": capture_hint,
        }

    rms = float(np.sqrt(np.mean(np.square(audio))))
    peak = float(np.max(np.abs(audio)))
    recorded_duration = len(audio) / float(samplerate)
    sf.write(str(wav_path), audio, samplerate)
    logger.info(
        "Audio test: capture file generated: %s (duration=%.2fs rms=%.5f peak=%.5f)",
        wav_path,
        recorded_duration,
        rms,
        peak,
    )

    stt_text = ""
    stt_error = ""
    if stt_api_key:
        logger.info("Audio test: STT started for capture sample")
        try:
            stt = GroqWhisperSTT(api_key=stt_api_key, model=stt_model)
            stt_text = stt.transcribe(audio, samplerate=samplerate)
            if not stt_text:
                logger.info("Audio test: STT empty for capture sample")
        except Exception as exc:
            stt_error = str(exc)
            logger.error("Audio test: STT failed for capture sample: %s", exc)
    else:
        stt_error = "GROQ_API_KEY is not set; recording saved but STT was skipped"
        logger.warning("Audio test: %s", stt_error)

    return {
        "ok": True,
        "wav_path": str(wav_path),
        "duration_s": recorded_duration,
        "rms": rms,
        "peak": peak,
        "stt_text": stt_text,
        "stt_error": stt_error,
        "capture_device": capture_hint,
    }


def print_report(
    capture_hint: str = "default",
    output_hint:  str = "CABLE Input",
) -> None:
    """Print a full diagnostic report to stdout."""
    from src.voice_playback import print_devices

    report = run_diagnostics(capture_hint, output_hint)

    _banner("Google Voice Agent — Audio Diagnostics")

    _section("All Audio Devices")
    print_devices()
    print()

    _section("VB-CABLE Status")
    if report["cable_inputs"]:
        for d in report["cable_inputs"]:
            print(f"  [OUT ] [{d['index']:>2}] {d['name']} — Tony speaks into this")
    else:
        print("  ✗  CABLE Input not found")

    if report["cable_outputs"]:
        for d in report["cable_outputs"]:
            print(f"  [IN  ] [{d['index']:>2}] {d['name']} — Chrome mic should point here")
    else:
        print("  ✗  CABLE Output not found")
    print()

    _section("Recommended Routing")
    print(f"  Tony → output_hint    : '{output_hint}'")
    if report["output_match"]:
        m = report["output_match"]
        print(f"           resolved to   : [{m['index']:>2}] {m['name']}")
    else:
        print("           ✗  NOT FOUND")

    print(f"  Prospect → capture_hint: '{capture_hint}'")
    if report["using_loopback"]:
        sc = "OK" if report["soundcard_ok"] else "MISSING — pip install soundcard"
        print(f"           mode          : WASAPI loopback  [{sc}]")
    elif report["capture_match"]:
        m = report["capture_match"]
        print(f"           resolved to   : [{m['index']:>2}] {m['name']}")
    else:
        print("           ✗  NOT FOUND")
    print()

    if report["issues"]:
        _section("Issues")
        for i, issue in enumerate(report["issues"], 1):
            print(f"  [{i}] {issue}")
        print()

    if report["suggestions"]:
        _section("How to Fix")
        for s in report["suggestions"]:
            print(f"  → {s}")
        print()

    _section("Dual-Cable Setup (recommended for clean capture)")
    print("  1. Download VB-CABLE A+B pack: https://vb-audio.com/Cable/#DownloadCable")
    print("  2. Set Chrome speaker  = 'VB-Cable-B Input'")
    print("  3. Set LOOPBACK_DEVICE = 'CABLE Input'  (Tony's voice → Chrome mic)")
    print("  4. Set CAPTURE_DEVICE  = 'CABLE-B Output' (prospect voice → Python)")
    print()

    status = "READY" if report["ok"] else "NEEDS ATTENTION"
    _banner(f"Status: {status}")


def _banner(title: str) -> None:
    bar = "=" * (len(title) + 6)
    print(f"\n{bar}\n   {title}\n{bar}\n")


def _section(title: str) -> None:
    print(f"─── {title} " + "─" * max(0, 60 - len(title)))


def _check_soundcard() -> bool:
    try:
        import soundcard  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    cap = sys.argv[1] if len(sys.argv) > 1 else "default"
    out = sys.argv[2] if len(sys.argv) > 2 else "CABLE Input"
    print_report(cap, out)
