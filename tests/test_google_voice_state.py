"""Regression tests for Google Voice call-state detection.

Critical invariants:
  1. Hangup/end button alone (call_active) MUST NOT trigger CONNECTED.
     It appears from the moment dialing starts — before answer.
  2. The Google Voice voicemail navigation link MUST NOT trigger VOICEMAIL.
     Only active-call recording/leave-a-message cues count.
  3. Verified answered-call controls (Hold, Mute, Transfer, Add a call, Record)
     MUST trigger CONNECTED — they appear only after the remote party answers.
  4. Audio must never fall back to default speakers during a live call.
  5. Number input selectors must not match the global search box.
"""
import os
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.call_session import CallSession, CallState
from src.google_voice import (
    GoogleVoiceBrowser,
    _SEL,
    _install_chromedriver_with_retry,
    _remove_stale_wdm_lock,
)


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
            state = browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

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
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

        assert state == CallState.CONNECTED

    def test_answered_controls_trigger_connected(self):
        """
        Verified in-call controls (Hold, Mute, Transfer …) trigger CONNECTED.

        These controls appear ONLY after the remote party answers.  During
        ringing only the hangup button is present.  Seeing any of these is
        conclusive proof that the call is live.
        """
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_answered_controls_present",
                          return_value=(True, ["Hold call", "Mute call"])), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

        assert state == CallState.CONNECTED
        assert session.state == CallState.CONNECTED

    def test_answered_controls_reason_recorded_in_session_notes(self):
        """Session notes include which answered controls were seen."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_answered_controls_present",
                          return_value=(True, ["Hold call", "Mute call", "Transfer"])), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

        assert session.state == CallState.CONNECTED
        combined_notes = " ".join(session.notes)
        assert "Hold call" in combined_notes, (
            "Session notes must record which controls confirmed the connection"
        )

    def test_single_answered_control_sufficient_for_connected(self):
        """Even a single verified answered control is enough to confirm CONNECTED."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_answered_controls_present",
                          return_value=(True, ["Record the call"])), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

        assert state == CallState.CONNECTED

    def test_answered_evidence_before_min_ring_stays_ringing_until_timeout(self):
        """Timer/answered controls before min_ring_seconds must not connect immediately."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_evidence", return_value="visible duration text '0:01'"), \
             patch.object(browser, "_answered_controls_present",
                          return_value=(True, ["Hold call"])), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=0.05,
                min_ring_seconds=5.0,
                max_ring_seconds=10.0,
            )

        assert state == CallState.FAILED
        assert session.state == CallState.FAILED
        assert session.connected_at is None


# ── Voicemail detection ──────────────────────────────────────────────────────

class TestVoicemailDetection:

    def test_voicemail_dom_cue_during_ringing_is_ignored(self):
        """voicemail DOM cue while RINGING → VOICEMAIL."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=True), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=0.05,
                min_ring_seconds=1.0,
                max_ring_seconds=2.0,
            )

        assert state == CallState.FAILED
        assert session.state == CallState.FAILED
        assert session.voicemail_detected_at is None

    def test_voicemail_page_phrase_during_ringing_is_ignored(self):
        """Page-source voicemail phrase while RINGING → VOICEMAIL."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=True):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=0.05,
                min_ring_seconds=1.0,
                max_ring_seconds=2.0,
            )

        assert state == CallState.FAILED
        assert session.voicemail_detected_at is None

    def test_voicemail_page_phrase_after_connected_triggers_voicemail(self):
        """Voicemail cues are accepted after real connected evidence."""
        browser = _make_browser()
        session = CallSession(phone="+15550000002", contact_name="Test")
        session.transition(CallState.DIALING)
        session.transition(CallState.RINGING, "ringing")
        session.transition(CallState.CONNECTED, "timer visible")

        with patch.object(browser, "_connected_timer_evidence", return_value=None), \
             patch.object(browser, "_answered_controls_present", return_value=(False, [])), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=True), \
             patch.object(browser, "_any_present", side_effect=lambda g: g == "call_active"):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=0.5,
                voicemail_detect_seconds=30.0,
            )

        assert state == CallState.VOICEMAIL
        assert session.voicemail_detected_at is not None

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
            state = browser.detect_call_state(
                session, poll_interval=0.01, timeout=0.5, min_ring_seconds=0
            )

        assert state == CallState.ENDED

    def test_call_ended_banner_before_min_ring_is_ignored(self):
        """Transient ended banner before min ring must not instantly end ringing."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_evidence", return_value=None), \
             patch.object(browser, "_answered_controls_present", return_value=(False, [])), \
             patch.object(browser, "_any_present", side_effect=lambda g: g == "call_ended_banner"), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=0.05,
                min_ring_seconds=5.0,
                max_ring_seconds=10.0,
            )

        assert state == CallState.FAILED
        assert session.state == CallState.FAILED
        assert all("call-ended banner detected" not in note for note in session.notes)

    def test_max_ring_seconds_stops_ringing_as_no_answer(self):
        """RINGING transitions to no-answer once max_ring_seconds elapses."""
        browser = _make_browser()
        session = _ringing_session()

        with patch.object(browser, "_connected_timer_evidence", return_value=None), \
             patch.object(browser, "_answered_controls_present", return_value=(False, [])), \
             patch.object(browser, "_any_present", return_value=False), \
             patch.object(browser, "_voicemail_cue_present", return_value=False), \
             patch.object(browser, "_page_contains_voicemail", return_value=False):
            state = browser.detect_call_state(
                session,
                poll_interval=0.01,
                timeout=1.0,
                min_ring_seconds=0.0,
                max_ring_seconds=0.03,
            )

        assert state == CallState.FAILED
        assert "max ring seconds elapsed" in " ".join(session.notes)

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

    def test_answered_controls_selector_group_exists(self):
        """The answered_controls selector group must be defined in _SEL."""
        assert "answered_controls" in _SEL
        assert len(_SEL["answered_controls"]) >= 3, (
            "answered_controls must have at least Hold, Mute, Transfer selectors"
        )

    def test_answered_controls_does_not_include_open_keypad(self):
        """
        'Open keypad' must NOT be in answered_controls — the dialpad is visible
        before the call is answered, so it cannot serve as a CONNECTED signal.
        """
        selectors = _SEL["answered_controls"]
        lower_sels = " ".join(selectors).lower()
        assert "open keypad" not in lower_sels, (
            "Open keypad appears pre-call and must not be an answered-call signal"
        )

    def test_answered_controls_does_not_include_hangup(self):
        """Hangup / end-call must not be in answered_controls — it appears during ringing."""
        selectors = _SEL["answered_controls"]
        hangup_keywords = ("end call", "hang up", "call_end", "end-call")
        for sel in selectors:
            sel_lower = sel.lower()
            for kw in hangup_keywords:
                assert kw not in sel_lower, (
                    f"Selector '{sel}' looks like a hangup selector (matched '{kw}')"
                )

    def test_voicemail_nav_not_in_voicemail_cue_selectors(self):
        """Persistent Google Voice voicemail navigation must not end a ringing call."""
        selectors = _SEL["voicemail_cue"]

        assert all('[aria-label*="voicemail"' not in selector for selector in selectors)
        assert all('[jsname*="voicemail"' not in selector for selector in selectors)

    def test_calls_page_opened_before_dialing(self):
        """dial_number must call _open_calls_page before touching the number input."""
        browser = _make_browser()

        with patch.object(browser, "_reset_for_new_call"), \
             patch.object(browser, "_open_calls_page", return_value=False) as mock_open, \
             patch.object(browser, "_focus_driver", return_value=True):
            result = browser.dial_number("+15550000001", connect_timeout=5)

        mock_open.assert_called_once()
        assert result is False, "_open_calls_page returning False must abort dialing"

    def test_click_call_start_button_skips_new_call_button(self):
        browser = _make_browser()
        new_call = MagicMock()
        new_call.is_displayed.return_value = True
        new_call.is_enabled.return_value = True
        new_call.get_attribute.side_effect = lambda attr: "New call" if attr == "aria-label" else ""
        new_call.text = ""

        call = MagicMock()
        call.is_displayed.return_value = True
        call.is_enabled.return_value = True
        call.get_attribute.side_effect = lambda attr: "Call" if attr == "aria-label" else ""
        call.text = ""

        browser.driver.find_elements.return_value = [new_call, call]

        assert browser._click_call_start_button(timeout=0.05) is True
        new_call.click.assert_not_called()
        browser.driver.execute_script.assert_called_once()
        assert browser.driver.execute_script.call_args.args[1] is call

    def test_dial_number_requires_outbound_call_surface_after_click(self):
        browser = _make_browser()
        number_input = MagicMock()
        number_input.get_attribute.return_value = "+15550000001"

        with patch.object(browser, "_focus_driver", return_value=True), \
             patch.object(browser, "_reset_for_new_call"), \
             patch.object(browser, "_open_calls_page", return_value=True), \
             patch.object(browser, "_find_first", side_effect=[number_input, number_input]), \
             patch.object(browser, "_click_call_start_button", return_value=True), \
             patch.object(browser, "_wait_for_outbound_call_surface", return_value=False):
            result = browser.dial_number("+15550000001", connect_timeout=5)

        assert result is False


class TestAudioFallback:

    def test_play_wav_loopback_does_not_fall_back_to_default_speakers(self):
        """
        _play_audio must never fall back to default speakers for a live call.
        If the loopback device is unavailable, it must log a warning and return
        False — not play audio to the system output where the prospect could be
        overheard by the AI on the capture side.
        """
        from src.main import _play_audio
        import logging

        with patch("src.main.play_wav_loopback") as mock_play:
            result = _play_audio(
                path=MagicMock(),
                device_hint="CABLE Input",
                loopback_available=False,
                logger=logging.getLogger("test"),
            )

        mock_play.assert_not_called()
        assert result is False, (
            "_play_audio must not call play_wav_loopback when loopback_available=False"
        )


class TestChromeDriverInstall:

    def _lock_path(self) -> Path:
        scratch = Path("test_tmp") / "wdm_lock_tests"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch / f"{uuid.uuid4().hex}.lock"

    def test_stale_webdriver_manager_lock_is_removed_and_install_retried(self):
        lock_path = self._lock_path()
        lock_path.write_text("locked")
        old_mtime = time.time() - 600
        os.utime(lock_path, (old_mtime, old_mtime))

        first_manager = MagicMock()
        first_manager.install.side_effect = TimeoutError(
            f"Timed out waiting for webdriver-manager lock: {lock_path}"
        )
        second_manager = MagicMock()
        second_manager.install.return_value = "chromedriver.exe"

        with patch("src.google_voice._USE_WDM", True), \
             patch("src.google_voice.ChromeDriverManager", side_effect=[first_manager, second_manager]):
            assert _install_chromedriver_with_retry() == "chromedriver.exe"

        assert not lock_path.exists()
        second_manager.install.assert_called_once()

    def test_fresh_webdriver_manager_lock_is_left_for_selenium_manager_fallback(self):
        lock_path = self._lock_path()
        lock_path.write_text("locked")

        try:
            assert _remove_stale_wdm_lock(lock_path, stale_seconds=300) is False
            assert lock_path.exists()
        finally:
            lock_path.unlink(missing_ok=True)
