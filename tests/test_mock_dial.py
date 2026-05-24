"""Tests for GoogleVoiceBrowser with a mocked Selenium driver."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.call_session import CallSession, CallState
from src.google_voice import GoogleVoiceBrowser


def _make_browser() -> GoogleVoiceBrowser:
    b = GoogleVoiceBrowser(profile_name="test_profile", headless=True)
    b.driver = MagicMock()
    b.driver.current_url = "https://voice.google.com/u/0/calls"
    b.driver.current_window_handle = "default"
    return b


# ---- is_logged_in ----

def test_is_logged_in_true_when_element_found():
    b = _make_browser()
    mock_el = MagicMock()
    mock_el.is_displayed.return_value = True
    b.driver.find_element.return_value = mock_el
    assert b.is_logged_in() is True


def test_is_logged_in_false_when_wrong_url():
    b = _make_browser()
    b.driver.current_url = "https://accounts.google.com/signin"
    assert b.is_logged_in() is False


def test_is_logged_in_false_when_url_temporarily_none():
    b = _make_browser()
    b.driver.current_url = None
    assert b.is_logged_in() is False


def test_is_logged_in_false_when_driver_none():
    b = GoogleVoiceBrowser(profile_name="test")
    b.driver = None
    assert b.is_logged_in() is False


# ---- _any_present ----

def test_any_present_true():
    b = _make_browser()
    mock_el = MagicMock()
    mock_el.is_displayed.return_value = True
    b.driver.find_elements.return_value = [mock_el]
    assert b._any_present("hangup_button") is True


def test_any_present_false_no_elements():
    b = _make_browser()
    b.driver.find_elements.return_value = []
    assert b._any_present("hangup_button") is False


# ---- hangup_call ----

def test_hangup_call_success():
    b = _make_browser()
    mock_btn = MagicMock()
    mock_btn.is_displayed.return_value = True

    with patch("src.google_voice.WebDriverWait") as MockWait:
        MockWait.return_value.until.return_value = mock_btn
        result = b.hangup_call()

    assert result is True


def test_hangup_call_falls_back_to_escape():
    b = _make_browser()
    from selenium.common.exceptions import TimeoutException

    with patch("src.google_voice.WebDriverWait") as MockWait:
        MockWait.return_value.until.side_effect = TimeoutException()
        mock_body = MagicMock()
        b.driver.find_element.return_value = mock_body
        result = b.hangup_call()

    assert result is True
    mock_body.send_keys.assert_called_once()


# ---- detect_call_state — quick timeout path ----

def test_detect_call_state_times_out_to_failed():
    b = _make_browser()
    b.driver.find_elements.return_value = []
    b.driver.find_element.side_effect = Exception("not found")
    b.driver.page_source = "<html></html>"

    session = CallSession(phone="+15551234567", contact_name="Test")
    session.transition(CallState.DIALING)

    result = b.detect_call_state(session, poll_interval=0.05, timeout=0.15)
    assert result == CallState.FAILED
    assert session.is_terminal()


def test_detect_call_state_connected_when_timer_found():
    b = _make_browser()

    call_count = {"n": 0}

    def side_effect(by, sel):
        call_count["n"] += 1
        # Return timer element on first call that matches a timer selector
        if "pRLmDf" in sel or "call-timer" in sel or "call duration" in sel.lower():
            mock_el = MagicMock()
            mock_el.is_displayed.return_value = True
            mock_el.text = "0:03"
            mock_el.get_attribute.return_value = ""
            return [mock_el]
        return []

    b.driver.find_elements.side_effect = side_effect
    b.driver.page_source = "<html></html>"

    session = CallSession(phone="+15551234567", contact_name="Test")
    session.transition(CallState.DIALING)

    result = b.detect_call_state(session, poll_interval=0.05, timeout=2.0)
    assert result == CallState.CONNECTED


def test_detect_call_state_does_not_connect_on_hangup_button_only():
    b = _make_browser()

    def side_effect(by, sel):
        if "hang" in sel.lower() or "end-call" in sel.lower() or "call_end" in sel:
            mock_el = MagicMock()
            mock_el.is_displayed.return_value = True
            return [mock_el]
        return []

    b.driver.find_elements.side_effect = side_effect
    b.driver.page_source = "<html></html>"

    session = CallSession(phone="+15551234567", contact_name="Test")
    session.transition(CallState.DIALING)

    result = b.detect_call_state(session, poll_interval=0.05, timeout=0.2)

    assert result == CallState.FAILED
    assert session.state == CallState.FAILED


def test_detect_call_state_voicemail_via_page_source():
    b = _make_browser()
    b.driver.find_elements.return_value = []
    b.driver.page_source = "<html>please leave a message after the beep</html>"

    session = CallSession(phone="+15551234567", contact_name="Test")
    session.transition(CallState.DIALING)

    result = b.detect_call_state(session, poll_interval=0.05, timeout=2.0)
    assert result == CallState.VOICEMAIL
    assert session.state == CallState.VOICEMAIL


# ---- Legacy helpers ----

def test_is_call_active_delegates_to_any_present():
    b = _make_browser()
    b.driver.find_elements.return_value = []
    assert b._is_call_active() is False


def test_detect_voicemail_returns_true_on_phrase():
    b = _make_browser()
    b.driver.page_source = "<html>leave a voicemail</html>"
    assert b.detect_voicemail() is True


def test_detect_voicemail_returns_false_on_empty():
    b = _make_browser()
    b.driver.page_source = "<html>Welcome to Google Voice</html>"
    assert b.detect_voicemail() is False


# ---- close ----

def test_close_quits_driver():
    b = _make_browser()
    saved_driver = b.driver  # capture before close() nulls it
    b.close()
    saved_driver.quit.assert_called_once()
    assert b.driver is None
