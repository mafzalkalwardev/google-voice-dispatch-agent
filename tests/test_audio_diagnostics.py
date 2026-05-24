"""Tests for src/audio_diagnostics.py — routing checks with mocked device lists."""

import pytest
from unittest.mock import patch


_FAKE_DEVICES = [
    {"index": 0, "name": "Speakers (Realtek)", "max_input_channels": 0, "max_output_channels": 2},
    {"index": 1, "name": "CABLE Input (VB-Audio Virtual Cable)", "max_input_channels": 0, "max_output_channels": 2},
    {"index": 2, "name": "CABLE Output (VB-Audio Virtual Cable)", "max_input_channels": 2, "max_output_channels": 0},
    {"index": 3, "name": "Microphone (Realtek)", "max_input_channels": 2, "max_output_channels": 0},
]


@pytest.fixture(autouse=True)
def mock_device_list():
    # run_diagnostics imports list_audio_devices locally, so patch at the source module
    with patch("src.voice_playback.list_audio_devices", return_value=_FAKE_DEVICES):
        yield


@pytest.fixture(autouse=True)
def mock_soundcard():
    with patch("src.audio_diagnostics._check_soundcard", return_value=True):
        yield


def test_ok_when_all_devices_present():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="CABLE Output", output_hint="CABLE Input")
    assert result["ok"] is True
    assert result["issues"] == []


def test_detects_missing_cable_input():
    devices = [d for d in _FAKE_DEVICES if "CABLE Input" not in d["name"]]
    with patch("src.voice_playback.list_audio_devices", return_value=devices):
        from src.audio_diagnostics import run_diagnostics
        result = run_diagnostics(capture_hint="CABLE Output", output_hint="CABLE Input")
    assert any("CABLE Input" in issue for issue in result["issues"])
    assert result["ok"] is False


def test_detects_missing_cable_output():
    devices = [d for d in _FAKE_DEVICES if "CABLE Output" not in d["name"]]
    with patch("src.voice_playback.list_audio_devices", return_value=devices):
        from src.audio_diagnostics import run_diagnostics
        result = run_diagnostics(capture_hint="CABLE Output", output_hint="CABLE Input")
    assert any("CABLE Output" in issue for issue in result["issues"])


def test_detects_missing_playback_device():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="CABLE Output", output_hint="NONEXISTENT Device")
    assert any("NONEXISTENT Device" in issue for issue in result["issues"])
    assert result["ok"] is False


def test_loopback_mode_detected_for_default_hint():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="default", output_hint="CABLE Input")
    assert result["using_loopback"] is True


def test_loopback_mode_not_used_for_named_hint():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="CABLE Output", output_hint="CABLE Input")
    assert result["using_loopback"] is False


def test_capture_match_resolved_for_named_hint():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="CABLE Output", output_hint="CABLE Input")
    assert result["capture_match"] is not None
    assert "CABLE Output" in result["capture_match"]["name"]


def test_named_capture_not_found_reports_issue():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics(capture_hint="Nonexistent Capture", output_hint="CABLE Input")
    assert any("Nonexistent Capture" in issue for issue in result["issues"])
    assert result["ok"] is False


def test_soundcard_missing_reports_issue():
    with patch("src.audio_diagnostics._check_soundcard", return_value=False):
        from src.audio_diagnostics import run_diagnostics
        result = run_diagnostics(capture_hint="default", output_hint="CABLE Input")
    assert any("soundcard" in issue.lower() for issue in result["issues"])


def test_result_contains_all_expected_keys():
    from src.audio_diagnostics import run_diagnostics
    result = run_diagnostics()
    for key in ("cable_inputs", "cable_outputs", "output_devices", "input_devices",
                "output_match", "capture_match", "using_loopback", "soundcard_ok",
                "issues", "suggestions", "ok"):
        assert key in result
