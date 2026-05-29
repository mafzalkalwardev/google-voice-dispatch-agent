"""Helpers for validating realtime Google Voice audio routing."""

from __future__ import annotations

import re

_DEFAULT_CAPTURE_HINTS = frozenset(
    {"", "default", "loopback", "speaker", "speakers", "wasapi", "default (wasapi)"}
)


def is_default_capture_hint(capture_hint: str | None) -> bool:
    """True when capture should use WASAPI loopback of the default speaker."""
    return (capture_hint or "").strip().lower() in _DEFAULT_CAPTURE_HINTS


def paired_capture_hint(loopback_hint: str | None) -> str:
    """Return the input-side cable name paired with an output-side cable hint."""
    hint = (loopback_hint or "").strip()
    if not hint:
        return ""
    return re.sub("input", "Output", hint, flags=re.IGNORECASE)


def captures_tts_loopback(capture_hint: str | None, loopback_hint: str | None) -> bool:
    """
    True when CAPTURE_DEVICE points at the same virtual cable used for Tony's TTS.

    For the common VB-CABLE route, Tony is played to "CABLE Input" and Chrome's
    microphone reads "CABLE Output". Python must not also capture "CABLE Output",
    because that hears Tony's injected audio instead of the prospect.
    """
    capture = (capture_hint or "").strip()
    if is_default_capture_hint(capture):
        return False

    paired = paired_capture_hint(loopback_hint)
    if not paired or paired.lower() == (loopback_hint or "").strip().lower():
        return False

    capture_key = _route_key(capture)
    paired_key = _route_key(paired)
    if not capture_key or not paired_key:
        return False

    return capture_key == paired_key or capture_key in paired_key or paired_key in capture_key


def safe_capture_hint(capture_hint: str | None, loopback_hint: str | None) -> str:
    """Return a capture hint that can hear the prospect in single-cable setups."""
    capture = (capture_hint or "").strip() or "default"
    if captures_tts_loopback(capture, loopback_hint):
        return "default"
    return capture


def _route_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
