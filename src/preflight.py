"""Preflight checks — run before dialing to verify all dependencies are ready."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from src.paths import runtime_base

BASE_DIR = runtime_base()


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str


def check_env() -> CheckResult:
    from src.groq_pool import load_groq_api_keys

    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return CheckResult("ENV File", "warn", ".env not found — using system env vars only")
    keys = load_groq_api_keys()
    if not keys:
        return CheckResult(
            "ENV File",
            "fail",
            ".env found but no valid Groq API keys (GROQ_API_KEY / GROQ_API_KEY_2 / GROQ_API_KEYS)",
        )
    if len(keys) == 1:
        return CheckResult("ENV File", "ok", ".env present and GROQ_API_KEY is set")
    return CheckResult("ENV File", "ok", f".env present — {len(keys)} Groq API keys configured")


def check_groq_api(api_key: Optional[str] = None) -> CheckResult:
    from src.groq_pool import GroqKeyPool, get_groq_pool, load_groq_api_keys

    keys = list(load_groq_api_keys())
    if api_key:
        k = api_key.strip()
        if k.startswith("your_"):
            if not keys:
                return CheckResult("Groq API", "fail", "GROQ_API_KEY not set — cannot connect")
        elif k not in keys:
            keys.insert(0, k)
    if not keys:
        return CheckResult("Groq API", "fail", "GROQ_API_KEY not set — cannot connect")
    try:
        pool = GroqKeyPool(keys)
    except ValueError:
        return CheckResult("Groq API", "fail", "No valid Groq API keys configured")
    ok, total, lines = pool.test_all_keys()
    summary = "; ".join(lines[:3])
    if len(lines) > 3:
        summary += f"; +{len(lines) - 3} more"
    if ok == 0:
        return CheckResult("Groq API", "fail", f"All {total} key(s) failed — {summary}")
    if ok < total:
        return CheckResult(
            "Groq API",
            "warn",
            f"{ok}/{total} key(s) OK (failover available) — {summary}",
        )
    if total == 1:
        return CheckResult("Groq API", "ok", f"Connected — {lines[0].split(':', 2)[-1].strip()}")
    return CheckResult(
        "Groq API",
        "ok",
        f"All {total} API keys OK — multi-account failover enabled",
    )


def check_contacts(contacts_file: Optional[Path] = None) -> CheckResult:
    if contacts_file is None:
        contacts_file = Path(
            os.getenv("CONTACTS_FILE", str(BASE_DIR / "data" / "contacts.xlsx"))
        )
    contacts_file = Path(contacts_file)
    if not contacts_file.exists():
        try:
            display = contacts_file.relative_to(BASE_DIR)
        except ValueError:
            display = contacts_file.name
        return CheckResult("Contacts File", "fail", f"Not found: {display}")
    try:
        from src.contacts import load_contacts  # type: ignore
        rows = load_contacts(contacts_file)
        if not rows:
            return CheckResult("Contacts File", "warn", f"Loaded but empty: {contacts_file.name}")
        return CheckResult("Contacts File", "ok", f"{len(rows)} contacts in {contacts_file.name}")
    except Exception as exc:
        return CheckResult("Contacts File", "fail", f"Parse error: {exc}")


def check_chrome_profile(profile_name: Optional[str] = None) -> CheckResult:
    name = profile_name or os.getenv("PROFILE_NAME", "sales_profile")
    profile_path = BASE_DIR / "chrome_profiles" / name
    if not profile_path.exists():
        return CheckResult(
            "Chrome Profile", "warn",
            f"chrome_profiles/{name} not found — will be created on first launch"
        )
    return CheckResult("Chrome Profile", "ok", f"chrome_profiles/{name} found")


def check_audio_loopback(device_hint: Optional[str] = None) -> CheckResult:
    hint = device_hint or os.getenv("LOOPBACK_DEVICE", "CABLE Input")
    try:
        from src.voice_playback import find_loopback_devices, list_audio_devices, probe_output_device  # type: ignore
        devices = list_audio_devices()
        if not devices:
            return CheckResult(
                "Audio Loopback", "fail",
                "No audio devices found — is sounddevice installed?"
            )
        matches = find_loopback_devices(hint)
        if not matches:
            return CheckResult(
                "Audio Loopback", "fail",
                f"'{hint}' not found. Install VB-CABLE from vb-audio.com/Cable/"
            )
        failures = []
        for idx in matches:
            ok, detail = probe_output_device(idx)
            if ok:
                return CheckResult("Audio Loopback", "ok", f"Loopback device [{idx}] is playable for '{hint}'")
            failures.append(f"[{idx}] {detail}")
        return CheckResult(
            "Audio Loopback", "fail",
            f"Matched '{hint}', but no output device could be opened: {'; '.join(failures)}"
        )
    except Exception as exc:
        return CheckResult("Audio Loopback", "fail", f"Audio check error: {exc}")


def check_capture_device(device_hint: Optional[str] = None) -> CheckResult:
    """
    Verify Python's prospect-audio capture path for realtime STT.
    CAPTURE_DEVICE is not the Chrome/Google Voice microphone selector.

    Audio flow: TTS → CABLE Input (output) → VB-CABLE → CABLE Output (input) → Chrome mic.
    Chrome must use CABLE Output as its microphone for voice.google.com; if it uses the
    laptop mic instead, Tony's voice never reaches the call.
    """
    hint = device_hint or os.getenv("CAPTURE_DEVICE", "default")
    if not hint or hint.lower() in ("default", "wasapi", "default (wasapi)"):
        try:
            import soundcard as sc  # type: ignore
            speaker = sc.default_speaker()
            speaker_name = getattr(speaker, "name", "unknown speaker")
            status = "warn" if "cable input" in str(speaker_name).lower() else "ok"
            message = (
                f"CAPTURE_DEVICE='{hint}' uses WASAPI loopback of Windows default speaker: "
                f"{speaker_name}. Chrome's Google Voice mic still must be CABLE Output."
            )
            if status == "warn":
                message += (
                    " Default speaker is the TTS cable, so STT may hear Tony/silence. "
                    "Use real speakers for single-cable capture or a second cable for prospect audio."
                )
            return CheckResult("Capture Device", status, message)
        except ImportError:
            return CheckResult(
                "Capture Device", "fail",
                "CAPTURE_DEVICE='default' needs soundcard for WASAPI loopback. "
                "Run: pip install soundcard",
            )
        except Exception as exc:
            return CheckResult("Capture Device", "warn", f"WASAPI capture check error: {exc}")
        return CheckResult(
            "Capture Device", "warn",
            f"CAPTURE_DEVICE is '{hint}' — Chrome will fall back to the Windows default mic. "
            "Set CAPTURE_DEVICE to 'CABLE Output' so Tony's TTS reaches the call, "
            "then set Chrome mic to 'CABLE Output' for voice.google.com.",
        )
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        hint_lower = hint.lower()
        matches = [
            d for d in devices
            if hint_lower in d["name"].lower() and d["max_input_channels"] > 0
        ]
        if not matches:
            return CheckResult(
                "Capture Device", "fail",
                f"'{hint}' not found as a recording device. "
                "Install VB-CABLE (https://vb-audio.com/Cable/), then set Chrome mic "
                "to 'CABLE Output' for voice.google.com.",
            )
        dev = matches[0]
        return CheckResult(
            "Capture Device", "ok",
            f"'{dev['name']}' found as recording device — "
            "confirm Chrome mic is set to this device for voice.google.com.",
        )
    except Exception as exc:
        return CheckResult("Capture Device", "warn", f"Capture device check error: {exc}")


def check_callback_number() -> CheckResult:
    number = os.getenv("CALLBACK_NUMBER", os.getenv("GOOGLE_VOICE_NUMBER", ""))
    # also check dialer_config.json
    if not number:
        config_file = BASE_DIR / "dialer_config.json"
        if config_file.exists():
            import json
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                number = data.get("callback_number", "")
            except Exception:
                pass
    if not number:
        return CheckResult("Callback Number", "fail", "CALLBACK_NUMBER not configured")
    masked = number[:3] + "***" + number[-2:] if len(number) > 5 else "***"
    return CheckResult("Callback Number", "ok", f"Callback number configured: {masked}")


def run_all(
    groq_api_key: Optional[str] = None,
    contacts_file: Optional[Path] = None,
    profile_name: Optional[str] = None,
    loopback_device: Optional[str] = None,
    capture_device: Optional[str] = None,
) -> List[CheckResult]:
    return [
        check_env(),
        check_groq_api(groq_api_key),
        check_contacts(contacts_file),
        check_chrome_profile(profile_name),
        check_audio_loopback(loopback_device),
        check_capture_device(capture_device),
        check_callback_number(),
    ]
