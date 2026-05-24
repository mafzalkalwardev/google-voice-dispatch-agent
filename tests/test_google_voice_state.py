"""Regression tests for Google Voice call-state detection.

Critical invariant: the hangup button (call_active) appears while Google Voice
is still ringing an outbound call.  It MUST NOT be used as proof that the call
was answered.  Only the call-duration timer (_connected_timer_present) proves
a live connection.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.call_session import CallSession, CallState
from src.google_voice import GoogleVoiceBrowser, _SEL


def _make_browser() -> GoogleVoiceBrowser:
    browser = GoogleVoiceBrowser.__new__(GoogleVoiceBrowser)
    browser.driver = MagicMock()
    return browser


def _ringing_session(phone: str = "+15550000001") -> CallSession:
    s = CallSession(phone=phone, contact_name="Test Carrier")
    s.transition(CallState.DIALING)
    return s


# ── Core CONNECTED regression ────────────────────────────────────────────────

class TestConnectedDetection:

    def test_call_timer_triggers_connected(self):
        """Call-duration timer appearing transitions RINGING → CONNECTED."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=True), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.CONNECTED
        assert session.state == CallState.CONNECTED

    def test_hangup_button_alone_does_not_trigger_connected(self):
        """
        REGRESSION: hangup button visible (call_active) while RINGING must NOT
        transition to CONNECTED.

        The Google Voice UI shows the hangup button from the moment an outbound
        call starts dialing — long before the remote party answers.  Using it as
        a CONNECTED signal causes the agent to speak while the phone is still
        ringing.  Only _connected_timer_present (the MM:SS counter) proves the
        call was answered.
        """
        browser = _make_browser()
        session = _ringing_session()

        # call_active present, but no call timer and no voicemail
        def _any_present_hangup_only(group: str) -> bool:
            return group == "call_active"

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_any_present", side_effect=_any_present_hangup_only), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.15)

        assert state != CallState.CONNECTED, (
            "Hangup button alone MUST NOT trigger CONNECTED — "
            "it appears while the call is still ringing"
        )
        assert session.state != CallState.CONNECTED, (
            "Session must not reach CONNECTED on hangup-button-only signal"
        )

    def test_both_hangup_and_timer_triggers_connected(self):
        """When both hangup button AND call timer are visible, CONNECTED is correct."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=True), \
             patch.object(browser, "_any_present", return_value=True), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.CONNECTED


# ── Voicemail detection ──────────────────────────────────────────────────────

class TestVoicemailDetection:

    def test_voicemail_dom_cue_from_ringing(self):
        """voicemail DOM cue while RINGING → VOICEMAIL."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=True), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.VOICEMAIL
        assert session.state == CallState.VOICEMAIL

    def test_voicemail_page_phrase_from_ringing(self):
        """Page-source voicemail phrase while RINGING → VOICEMAIL."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=True):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.VOICEMAIL

    def test_generic_voicemail_navigation_is_not_a_dom_cue(self):
        """Persistent Google Voice voicemail navigation must not end a ringing call."""
        selectors = _SEL["voicemail_cue"]

        assert all("[aria-label*=\"voicemail\"" not in selector for selector in selectors)
        assert all("[jsname*=\"voicemail\"" not in selector for selector in selectors)


# ── End / timeout detection ──────────────────────────────────────────────────

class TestEndDetection:

    def test_call_ended_banner(self):
        """call-ended banner while RINGING → ENDED."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_any_present", side_effect=lambda g: g == "call_ended_banner"), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.ENDED

    def test_hangup_disappears_after_connected_triggers_ended(self):
        """Once CONNECTED, hangup button disappearing → ENDED."""
        browser = _make_browser()
        session = CallSession(phone="+15550000002", contact_name="Test")
        session.transition(CallState.DIALING)
        session.transition(CallState.RINGING, "ringing")
        session.transition(CallState.CONNECTED, "timer visible")

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.5)

        assert state == CallState.ENDED

    def test_no_signal_within_timeout_fails(self):
        """No DOM signals within timeout → FAILED."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(session, poll_interval=0.01, timeout=0.1)

        assert state == CallState.FAILED
        assert session.state == CallState.FAILED


class TestSelectorHelpers:

    def test_find_first_skips_hidden_element_for_same_selector(self):
        browser = _make_browser()
        hidden = MagicMock()
        hidden.is_displayed.return_value = False
        visible = MagicMock()
        visible.is_displayed.return_value = True
        browser.driver.find_elements.return_value = [hidden, visible]

        result = browser._find_first("number_input", timeout=0.05)

        assert result is visible

    def test_number_input_selectors_do_not_match_global_search(self):
        selectors = _SEL["number_input"]

        assert all("search" not in selector.lower() for selector in selectors)
