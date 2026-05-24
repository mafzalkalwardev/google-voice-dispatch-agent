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
