from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from src.paths import runtime_base

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _USE_WDM = True
except ImportError:
    _USE_WDM = False

from src.call_session import CallSession, CallState

BASE_DIR = runtime_base()
GV_URL = "https://voice.google.com"

logger = logging.getLogger("GoogleVoiceAgent")

# ---------------------------------------------------------------------------
# Selector banks — each group is tried in order; first visible match wins
# ---------------------------------------------------------------------------
_SEL = {
    "login_indicator": [
        '[aria-label="Google Account"]',
        'img[alt="profile photo"]',
        '[data-email]',
        'a[aria-label*="account" i]',
    ],
    "dialpad_open": [
        'button[aria-label*="keypad" i]',
        'button[aria-label*="dialpad" i]',
        "gv-icon-button[icon-name='phone']",
        'button[aria-label*="dial" i]',
        'button[aria-label*="new call" i]',
        "gv-new-conversation-fab",
        '[data-action="new-call"]',
        'button[aria-label*="make" i]',
    ],
    "calls_tab": [
        'a[aria-label="Calls"]',
        'a[role="tab"][aria-label*="Calls" i]',
    ],
    "number_input": [
        'input[aria-label*="number" i]',
        'input[placeholder*="number" i]',
        'input[placeholder*="name or number" i]',
        "input[type='tel']",
    ],
    "call_button": [
        'button[aria-label*="call" i]:not([aria-label*="end" i]):not([aria-label*="video" i])',
        "gv-icon-button[icon-name='call']",
        '[data-action="call"]',
        "button.call-button",
    ],
    "hangup_button": [
        'button[aria-label*="end" i]',
        'button[aria-label*="hang" i]',
        "gv-icon-button[icon-name='call_end']",
        '[data-action="end-call"]',
        "button.end-call",
    ],
    "call_active": [
        'button[aria-label*="end" i]',
        'button[aria-label*="hang" i]',
        "gv-icon-button[icon-name='call_end']",
        '[data-action="end-call"]',
    ],
    "call_timer": [
        '[jsname="pRLmDf"]',
        '[aria-label*="call duration" i]',
        ".call-duration",
        "[data-e2eid='call-timer']",
    ],
    "voicemail_cue": [
        ".voicemail-indicator",
        "[data-e2eid='voicemail-record']",
        '[aria-label*="leave a message" i]',
        '[aria-label*="record after" i]',
        '[title*="leave a message" i]',
        '[title*="record after" i]',
    ],
    "call_ended_banner": [
        '[aria-label*="Call ended" i]',
        "[data-e2eid='call-ended']",
        ".call-ended",
    ],
}

_VOICEMAIL_PAGE_PHRASES = [
    "leave a message",
    "record after the tone",
    "mailbox is full",
    "not available right now",
    "please leave",
    "after the beep",
    "leave a voicemail",
]

_DURATION_RE = re.compile(r"\b(?:\d{1,2}:)?\d{1,2}:\d{2}\b")
_DURATION_WORD_RE = re.compile(r"\b\d+\s*(?:second|seconds|minute|minutes)\b", re.I)


def _js_click(driver: webdriver.Chrome, element) -> None:
    try:
        driver.execute_script("arguments[0].click();", element)
    except Exception:
        element.click()


class GoogleVoiceBrowser:
    def __init__(self, profile_name: str = "sales_profile", headless: bool = False):
        self.profile_dir = BASE_DIR / "chrome_profiles" / profile_name
        self.headless = headless
        self.driver: Optional[webdriver.Chrome] = None

    # ------------------------------------------------------------------
    # Launch / teardown
    # ------------------------------------------------------------------

    def launch(self) -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        opts = Options()
        opts.add_argument(f"--user-data-dir={self.profile_dir}")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        # Microphone: grant without popup; suppress notifications
        opts.add_experimental_option("prefs", {
            "profile.default_content_setting_values.media_stream_mic": 1,
            "profile.default_content_setting_values.notifications": 2,
        })

        # Low-RAM flags
        opts.add_argument("--no-first-run")
        opts.add_argument("--no-default-browser-check")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--blink-settings=imagesEnabled=false")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-background-timer-throttling")

        if self.headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--use-fake-ui-for-media-stream")
            opts.add_argument("--use-fake-device-for-media-stream")

        if _USE_WDM:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=opts)
        else:
            self.driver = webdriver.Chrome(options=opts)

        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.driver.get(GV_URL)
        time.sleep(3)

    def close(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            self.driver = None

    def _focus_driver(self) -> bool:
        if not self.driver:
            return False
        try:
            self.driver.switch_to.window(self.driver.current_window_handle)
            self.driver.execute_script("window.focus();")
            return True
        except WebDriverException as exc:
            logger.error("Google Voice browser session is unavailable: %s", exc)
            self.driver = None
            return False

    # ------------------------------------------------------------------
    # Login detection
    # ------------------------------------------------------------------

    def is_logged_in(self) -> bool:
        if not self.driver:
            return False
        try:
            url = self.driver.current_url or ""
            if "voice.google.com" not in url:
                return False
            return self._find_first("login_indicator", timeout=4) is not None
        except WebDriverException:
            return False

    def wait_for_manual_login(self, timeout: int = 300) -> bool:
        logger.info("Waiting up to %ds for manual Google login...", timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_logged_in():
                return True
            time.sleep(2)
        return False

    # ------------------------------------------------------------------
    # Internal selector helpers
    # ------------------------------------------------------------------

    def _find_first(self, group: str, timeout: float = 5.0):
        """Try each selector in the group; return the first visible element."""
        selectors = _SEL.get(group, [])
        deadline = time.time() + timeout
        while time.time() < deadline:
            for sel in selectors:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        if el.is_displayed():
                            return el
                except (NoSuchElementException, WebDriverException):
                    pass
            time.sleep(0.3)
        return None

    def _click_first(self, group: str, timeout: float = 5.0) -> bool:
        """Click the first visible/enabled element from a selector group."""
        selectors = _SEL.get(group, [])
        deadline = time.time() + timeout
        while time.time() < deadline:
            for sel in selectors:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                except WebDriverException:
                    continue
                for el in els:
                    try:
                        if el.is_displayed() and el.is_enabled():
                            _js_click(self.driver, el)
                            return True
                    except WebDriverException:
                        continue
            time.sleep(0.3)
        return False

    def _set_input_value(self, element, value: str) -> None:
        """Set input text using native events when normal send_keys is blocked."""
        self.driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            const proto = el.tagName === 'TEXTAREA'
              ? window.HTMLTextAreaElement.prototype
              : window.HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            el.focus();
            if (setter) {
              setter.call(el, value);
            } else {
              el.value = value;
            }
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            """,
            element,
            value,
        )

    def _open_calls_page(self) -> bool:
        """Navigate to the Calls view where the keypad number input exists."""
        try:
            if "/calls" in (self.driver.current_url or ""):
                return True
        except WebDriverException:
            return False

        if self._click_first("calls_tab", timeout=5):
            time.sleep(2.0)
            return True

        try:
            self.driver.get(f"{GV_URL}/u/0/calls")
            time.sleep(3.0)
            return "/calls" in (self.driver.current_url or "")
        except WebDriverException as exc:
            logger.warning("Could not open Google Voice Calls page: %s", exc)
            return False

    def _any_present(self, group: str) -> bool:
        for sel in _SEL.get(group, []):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if any(e.is_displayed() for e in els):
                    return True
            except WebDriverException:
                pass
        return False

    def _connected_timer_present(self) -> bool:
        """
        Return True only for timer-like connected-call evidence.
        The hangup button appears while Google Voice is still ringing, so it is
        not enough to treat the call as answered.
        """
        for sel in _SEL.get("call_timer", []):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    raw_parts = (
                        getattr(el, "text", ""),
                        el.get_attribute("aria-label"),
                        el.get_attribute("title"),
                    )
                    text = " ".join(part for part in raw_parts if isinstance(part, str))
                    if _DURATION_RE.search(text) or (
                        "duration" in text.lower() and _DURATION_WORD_RE.search(text)
                    ):
                        return True
                except WebDriverException:
                    continue
        return False

    def _voicemail_cue_present(self) -> bool:
        """
        Return True only for voicemail evidence from the active call surface.

        Google Voice has persistent navigation/sidebar elements labelled
        "voicemail"; those are not proof that the outbound call reached
        voicemail. Keep this focused on leave-message/recording cues.
        """
        for sel in _SEL.get("voicemail_cue", []):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    if "voicemail-indicator" in sel or "voicemail-record" in sel:
                        return True
                    raw_parts = (
                        getattr(el, "text", ""),
                        el.get_attribute("aria-label"),
                        el.get_attribute("title"),
                        el.get_attribute("data-e2eid"),
                    )
                    text = " ".join(part for part in raw_parts if isinstance(part, str)).lower()
                    if (
                        "leave a message" in text
                        or "record after" in text
                        or "after the beep" in text
                        or "voicemail-record" in text
                    ):
                        return True
                except WebDriverException:
                    continue
        return False

    # ------------------------------------------------------------------
    # Dialing
    # ------------------------------------------------------------------

    def dial_number(self, phone: str, connect_timeout: int = 30) -> bool:
        if not self.driver:
            raise RuntimeError("Browser is not launched")

        if not self._focus_driver():
            return False
        time.sleep(0.5)

        if not self._open_calls_page():
            logger.warning("Could not open Google Voice Calls page for %s", phone)
            return False

        # Google Voice can keep the keypad open between sessions. If the
        # number input is already visible, start there.
        opened = self._find_first("number_input", timeout=2) is not None
        for sel in _SEL["dialpad_open"]:
            if opened:
                break
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                btn = next((e for e in els if e.is_displayed() and e.is_enabled()), None)
                if btn is None:
                    continue
                _js_click(self.driver, btn)
                time.sleep(1.2)
                opened = True
                break
            except WebDriverException:
                continue

        if not opened:
            logger.warning("Could not find dialpad button for %s", phone)
            return False

        # Type the number
        number_input = self._find_first("number_input", timeout=8)
        if number_input is None:
            logger.warning("Could not find number input for %s", phone)
            return False

        try:
            _js_click(self.driver, number_input)
            number_input.send_keys(Keys.CONTROL + "a")
            number_input.send_keys(Keys.DELETE)
            time.sleep(0.2)
            number_input.send_keys(phone)
        except WebDriverException as exc:
            logger.warning("Direct number entry failed; retrying with DOM input events: %s", exc)
            try:
                self._set_input_value(number_input, phone)
            except WebDriverException as js_exc:
                logger.warning("Could not type number for %s: %s", phone, js_exc)
                return False
        time.sleep(0.8)

        # Click call button
        called = self._click_first("call_button", timeout=8)
        if called:
            time.sleep(2)

        if not called:
            try:
                number_input.send_keys(Keys.RETURN)
                time.sleep(2)
                called = True
            except WebDriverException:
                return False

        return called

    # ------------------------------------------------------------------
    # Call state detection — drives the CallSession state machine
    # ------------------------------------------------------------------

    def detect_call_state(
        self,
        session: CallSession,
        poll_interval: float = 0.75,
        timeout: float = 90.0,
    ) -> CallState:
        """
        Poll the DOM until a definitive state is reached or timeout expires.
        Drives session.transition() at each state change.
        Returns the final CallState.
        """
        if not self.driver:
            if not session.is_terminal():
                session.transition(CallState.FAILED, "browser not running")
            return CallState.FAILED

        if session.state == CallState.DIALING:
            session.transition(CallState.RINGING, "dial confirmed, polling for state")

        deadline = time.time() + timeout

        while time.time() < deadline:
            # --- CONNECTED: call timer appeared ---
            if self._connected_timer_present():
                if session.state == CallState.RINGING:
                    session.transition(CallState.CONNECTED, "call timer visible")
                return CallState.CONNECTED

            # --- VOICEMAIL: DOM cue ---
            if self._voicemail_cue_present():
                if session.state in (CallState.RINGING, CallState.CONNECTED):
                    session.transition(CallState.VOICEMAIL, "voicemail DOM cue")
                return CallState.VOICEMAIL

            # --- VOICEMAIL: page source phrases ---
            if self._page_contains_voicemail():
                if session.state in (CallState.RINGING, CallState.CONNECTED):
                    session.transition(CallState.VOICEMAIL, "voicemail page-source heuristic")
                return CallState.VOICEMAIL

            # --- ENDED: explicit banner ---
            if self._any_present("call_ended_banner"):
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "call-ended banner detected")
                return CallState.ENDED

            # --- ENDED: hangup button disappeared while connected ---
            if session.state == CallState.CONNECTED and not self._any_present("call_active"):
                session.transition(CallState.ENDED, "hangup button vanished")
                return CallState.ENDED

            time.sleep(poll_interval)

        logger.warning("detect_call_state timed out after %.0fs for %s", timeout, session.phone)
        if not session.is_terminal():
            session.transition(CallState.FAILED, "state detection timeout")
        return CallState.FAILED

    def _page_contains_voicemail(self) -> bool:
        try:
            src = self.driver.page_source.lower()
            return any(phrase in src for phrase in _VOICEMAIL_PAGE_PHRASES)
        except WebDriverException:
            return False

    # ------------------------------------------------------------------
    # Voicemail beep wait
    # ------------------------------------------------------------------

    def wait_for_voicemail_beep(self, timeout: float = 35.0) -> bool:
        """
        Wait for voicemail to begin recording. Returns True when detected.
        Call this after transitioning to VOICEMAIL before playing audio.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._voicemail_cue_present() or self._page_contains_voicemail():
                time.sleep(1.5)  # wait for actual beep tone
                return True
            time.sleep(0.5)
        return False

    # ------------------------------------------------------------------
    # Hangup
    # ------------------------------------------------------------------

    def hangup_call(self) -> bool:
        if not self.driver:
            return False
        if not self._focus_driver():
            return False

        wait = WebDriverWait(self.driver, 8)
        for sel in _SEL["hangup_button"]:
            try:
                btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
                _js_click(self.driver, btn)
                time.sleep(1)
                return True
            except (TimeoutException, WebDriverException):
                continue

        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            return True
        except WebDriverException:
            return False

    # ------------------------------------------------------------------
    # Legacy helpers (kept for backward compatibility)
    # ------------------------------------------------------------------

    def _is_call_active(self) -> bool:
        return self._any_present("call_active")

    def is_call_active(self) -> bool:
        """Return True while Google Voice still shows an active call control."""
        return self._is_call_active()

    def wait_for_call_connect(self, timeout: int = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_call_active():
                return True
            time.sleep(1)
        return False

    def wait_for_call_end(self, timeout: int = 300) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._is_call_active():
                return True
            time.sleep(2)
        return False

    def detect_voicemail(self) -> bool:
        return self._page_contains_voicemail()
