"""Tests for the CallSession state machine."""
import pytest
from src.call_session import CallSession, CallState


def _session() -> CallSession:
    return CallSession(phone="+15551234567", contact_name="Test User")


# ---- Initial state ----

def test_initial_state():
    s = _session()
    assert s.state == CallState.IDLE
    assert s.started_at is None
    assert s.connected_at is None
    assert s.ended_at is None
    assert s.notes == []


# ---- Legal transitions ----

def test_idle_to_dialing():
    s = _session()
    s.transition(CallState.DIALING)
    assert s.state == CallState.DIALING
    assert s.started_at is not None


def test_dialing_to_ringing():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.RINGING)
    assert s.state == CallState.RINGING


def test_ringing_to_connected():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.RINGING)
    s.transition(CallState.CONNECTED)
    assert s.state == CallState.CONNECTED
    assert s.connected_at is not None


def test_ringing_to_voicemail():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.RINGING)
    s.transition(CallState.VOICEMAIL)
    assert s.state == CallState.VOICEMAIL
    assert s.voicemail_detected_at is not None


def test_connected_to_ended():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.CONNECTED)
    s.transition(CallState.ENDED)
    assert s.state == CallState.ENDED
    assert s.ended_at is not None


def test_idle_to_failed():
    s = _session()
    s.transition(CallState.FAILED, "immediate failure")
    assert s.state == CallState.FAILED
    assert s.notes == ["immediate failure"]


# ---- Illegal transitions raise ----

def test_idle_to_voicemail_raises():
    s = _session()
    with pytest.raises(ValueError, match="IDLE"):
        s.transition(CallState.VOICEMAIL)


def test_idle_to_connected_raises():
    s = _session()
    with pytest.raises(ValueError, match="IDLE"):
        s.transition(CallState.CONNECTED)


def test_ended_to_anything_raises():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.ENDED)
    with pytest.raises(ValueError):
        s.transition(CallState.CONNECTED)


def test_failed_is_terminal():
    s = _session()
    s.transition(CallState.FAILED)
    with pytest.raises(ValueError):
        s.transition(CallState.ENDED)


# ---- Duration calculations ----

def test_connected_duration_none_when_not_ended():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.CONNECTED)
    assert s.connected_duration_seconds() is None


def test_connected_duration_positive_when_ended():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.CONNECTED)
    s.transition(CallState.ENDED)
    dur = s.connected_duration_seconds()
    assert dur is not None
    assert dur >= 0.0


def test_total_duration_none_when_not_started():
    s = _session()
    assert s.total_duration_seconds() is None


def test_total_duration_positive():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.ENDED)
    assert s.total_duration_seconds() >= 0.0


# ---- is_terminal ----

def test_is_terminal_ended():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.ENDED)
    assert s.is_terminal()


def test_is_terminal_failed():
    s = _session()
    s.transition(CallState.FAILED)
    assert s.is_terminal()


def test_is_not_terminal_ringing():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.RINGING)
    assert not s.is_terminal()


# ---- to_log_dict ----

def test_to_log_dict_keys():
    s = _session()
    s.transition(CallState.DIALING)
    s.transition(CallState.CONNECTED, "connected ok")
    s.transition(CallState.ENDED)
    s.outcome = "ENDED"
    d = s.to_log_dict()
    for key in ("phone", "contact_name", "state", "outcome", "started_at",
                 "connected_at", "ended_at", "connected_duration_s", "total_duration_s", "notes"):
        assert key in d, f"Missing key: {key}"
    assert d["state"] == "ENDED"
    assert d["notes"] == "connected ok"


# ---- Notes accumulate ----

def test_notes_accumulate():
    s = _session()
    s.transition(CallState.DIALING, "starting dial")
    s.transition(CallState.RINGING, "ringing now")
    assert s.notes == ["starting dial", "ringing now"]
