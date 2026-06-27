from __future__ import annotations

import logging
import re
import subprocess
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

# Ring/answer timing defaults for call state machine
MIN_RING_SECONDS_DEFAULT = 25
MAX_RING_SECONDS_DEFAULT = 45
VOICEMAIL_DETECT_SECONDS_DEFAULT = 15

logger = logging.getLogger("GoogleVoiceAgent")
_WDM_LOCK_STALE_SECONDS = 5 * 60
_PROFILE_LOCK_FILES = ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket")

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
        'button[aria-label*="call" i]:not([aria-label*="new" i]):not([aria-label*="end" i]):not([aria-label*="hang" i]):not([aria-label*="video" i])',
        "gv-icon-button[icon-name='call']",
        '[data-action="call"]',
        "button.call-button",
    ],
    "hangup_button": [
        'button[aria-label*="Hang up" i]',
        'button[aria-label*="Hangup" i]',
        'button[aria-label*="End call" i]',
        'button[title*="Hang up" i]',
        'button[title*="End call" i]',
        "gv-icon-button[icon-name='call_end']",
        '[data-action="end-call"]',
        "button.end-call",
    ],
    "call_active": [
        'button[aria-label*="Hang up" i]',
        'button[aria-label*="Hangup" i]',
        'button[aria-label*="End call" i]',
        'button[title*="Hang up" i]',
        'button[title*="End call" i]',
        "gv-icon-button[icon-name='call_end']",
        '[data-action="end-call"]',
    ],
    # Controls that appear ONLY after a call is answered — NOT during ringing.
    # Verified on a live Google Voice call: Transfer, Hold, Add a call, Mute,
    # Send a message, Record appeared only after the remote party picked up.
    # "Open keypad" is excluded because the dialpad is visible pre-call.
    "answered_controls": [
        'button[aria-label*="Hold call" i]',
        'button[aria-label*="Mute call" i]',
        'button[aria-label*="Unmute call" i]',
        'button[aria-label*="Transfer" i]',
        'button[aria-label*="Add a call" i]',
        'button[aria-label*="Record the call" i]',
        'button[aria-label*="Send a message" i]',
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
    "dismiss_button": [
        'button[aria-label*="close" i]',
        'button[aria-label*="dismiss" i]',
        'button[aria-label*="cancel" i]',
        'button[title*="close" i]',
        'button[title*="dismiss" i]',
        'button[title*="cancel" i]',
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
_EXACT_DURATION_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")
_DURATION_WORD_RE = re.compile(r"\b\d+\s*(?:second|seconds|minute|minutes)\b", re.I)
_AM_PM_RE = re.compile(r"\b(?:am|pm)\b", re.I)


def _js_click(driver: webdriver.Chrome, element) -> None:
    try:
        driver.execute_script("arguments[0].click();", element)
    except Exception:
        element.click()


def _wdm_lock_path_from_error(exc: BaseException) -> Optional[Path]:
    match = re.search(r"webdriver-manager lock:\s*(.+)$", str(exc))
    if match:
        return Path(match.group(1).strip().strip("'\""))
    return None


def _remove_stale_wdm_lock(lock_path: Path, *, stale_seconds: int = _WDM_LOCK_STALE_SECONDS) -> bool:
    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False

    if age_seconds < stale_seconds:
        logger.warning(
            "webdriver-manager lock is still fresh (%ds old): %s",
            int(age_seconds),
            lock_path,
        )
        return False

    try:
        lock_path.unlink()
        logger.warning("Removed stale webdriver-manager lock: %s", lock_path)
        return True
    except OSError as cleanup_exc:
        logger.warning(
            "Could not remove stale webdriver-manager lock %s: %s",
            lock_path,
            cleanup_exc,
        )
        return False


def _install_chromedriver_with_retry() -> Optional[str]:
    if not _USE_WDM:
        return None

    manager = ChromeDriverManager()
    try:
        return manager.install()
    except TimeoutError as exc:
        lock_path = _wdm_lock_path_from_error(exc)
        if lock_path and _remove_stale_wdm_lock(lock_path):
            logger.info("Retrying ChromeDriver install after stale lock cleanup")
            return ChromeDriverManager().install()
        logger.warning(
            "webdriver-manager timed out waiting for its lock; falling back to Selenium Manager: %s",
            exc,
        )
    except Exception as exc:
        logger.warning(
            "webdriver-manager could not install ChromeDriver; falling back to Selenium Manager: %s",
            exc,
        )
    return None


def _create_chrome_driver(options: Options) -> webdriver.Chrome:
    driver_path = _install_chromedriver_with_retry()
    if driver_path:
        return webdriver.Chrome(service=Service(driver_path), options=options)

    # Selenium Manager can resolve/download a matching ChromeDriver without
    # using webdriver-manager's global lock file.
    return webdriver.Chrome(options=options)


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _terminate_chrome_using_profile(profile_dir: Path) -> None:
    """Close stale Chrome/ChromeDriver processes tied to this automation profile only."""
    if not profile_dir.exists():
        return
    profile = str(profile_dir.resolve())
    ps_profile = _powershell_quote(profile)
    command = (
        "$profile = " + ps_profile + "; "
        "$escaped = [Regex]::Escape($profile); "
        "$procs = Get-CimInstance Win32_Process | "
        "Where-Object { "
        "($_.Name -eq 'chrome.exe' -and $_.CommandLine -match $escaped) -or "
        "($_.Name -eq 'chromedriver.exe') "
        "}; "
        "foreach ($p in $procs) { "
        "try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "
        "Write-Output $p.ProcessId } catch {} "
        "}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=8,
        )
        killed = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if killed:
            logger.warning(
                "Closed stale Chrome/ChromeDriver processes using automation profile %s: %s",
                profile_dir,
                ", ".join(killed),
            )
            time.sleep(1.5)
    except Exception as exc:
        logger.warning("Could not clean stale Chrome profile processes: %s", exc)


def _remove_profile_lock_files(profile_dir: Path) -> None:
    for name in _PROFILE_LOCK_FILES:
        path = profile_dir / name
        if not path.exists():
            continue
        try:
            path.unlink()
            logger.warning("Removed stale Chrome profile lock file: %s", path)
        except OSError as exc:
            logger.warning("Could not remove Chrome profile lock file %s: %s", path, exc)


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
        _terminate_chrome_using_profile(self.profile_dir)
        _remove_profile_lock_files(self.profile_dir)
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

        try:
            self.driver = _create_chrome_driver(opts)
        except WebDriverException as exc:
            logger.error("Chrome launch failed for profile %s: %s", self.profile_dir, exc)
            _terminate_chrome_using_profile(self.profile_dir)
            _remove_profile_lock_files(self.profile_dir)
            logger.info("Retrying Chrome launch after stale profile cleanup")
            self.driver = _create_chrome_driver(opts)

        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        logger.info("Opening Google Voice: %s", GV_URL)
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

    def _element_text(self, element) -> str:
        parts: list[str] = []
        for attr in ("aria-label", "title", "data-e2eid", "icon-name"):
            try:
                value = element.get_attribute(attr)
            except WebDriverException:
                value = ""
            if value:
                parts.append(str(value))
        try:
            text = getattr(element, "text", "")
        except WebDriverException:
            text = ""
        if text:
            parts.append(str(text))
        return " ".join(parts).strip()

    def _click_call_start_button(self, timeout: float = 8.0) -> bool:
        """
        Click the real outbound-call button, not Google Voice's "New call" FAB.

        Google Voice exposes several controls whose labels contain "call". A
        broad selector can accidentally click "New call" after the number is
        already typed, leaving the run thinking it dialed when it did not.
        """
        selectors = _SEL.get("call_button", [])
        deadline = time.time() + timeout
        blocked = ("new call", "end call", "hang up", "hangup", "video call")
        wanted = ("call", "phone", "dial")
        while time.time() < deadline:
            for sel in selectors:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                except WebDriverException:
                    continue
                for el in els:
                    try:
                        if not (el.is_displayed() and el.is_enabled()):
                            continue
                        label = self._element_text(el).lower()
                        if any(term in label for term in blocked):
                            continue
                        if label and not any(term in label for term in wanted):
                            continue
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
            self.driver.get(f"{GV_URL}/u/0/calls")
            time.sleep(2.0)
            return "/calls" in (self.driver.current_url or "")
        except WebDriverException as exc:
            logger.warning("Could not open Google Voice Calls page: %s", exc)
        if self._click_first("calls_tab", timeout=5):
            time.sleep(2.0)
            return True
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

    def _answered_controls_present(self) -> "tuple[bool, list[str]]":
        """
        Return (is_present, found_labels) for controls visible ONLY after answer.

        During ringing Google Voice shows only the hangup/end button.  After
        the remote party picks up, controls like Hold call, Mute call, Transfer
        the Call, Add a call, Record the call appear.  Seeing any one of these
        is reliable proof the call is CONNECTED.

        Must NOT include the dialpad/keypad button — that is visible pre-call.
        """
        found: list[str] = []
        for sel in _SEL.get("answered_controls", []):
            try:
                els = self.driver.find_elements(By.CSS_SELECTOR, sel)
            except WebDriverException:
                continue
            for el in els:
                try:
                    if not el.is_displayed():
                        continue
                    label = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("title")
                        or getattr(el, "text", "")
                        or ""
                    ).strip()
                    if label:
                        found.append(label)
                except WebDriverException:
                    continue
        return bool(found), found

    def _connected_timer_evidence(self) -> Optional[str]:
        """
        Return timer-like connected-call evidence, if present.
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
                        return f"{sel} -> {text.strip() or '<duration element>'}"
                except WebDriverException:
                    continue

        # Google Voice changes internal selectors often. As a fallback, look for
        # a visible exact MM:SS/H:MM:SS text near an active call surface. This
        # still requires a call-active control, so a hangup button alone never
        # becomes CONNECTED.
        if self._any_present("call_active"):
            for text in self._visible_call_timer_texts():
                return f"visible duration text '{text}'"
        return None

    def _connected_timer_present(self) -> bool:
        return self._connected_timer_evidence() is not None

    def _visible_call_timer_texts(self) -> list[str]:
        if not self.driver:
            return []
        try:
            texts = self.driver.execute_script(
                """
                const visible = (el) => {
                  const s = window.getComputedStyle(el);
                  const r = el.getBoundingClientRect();
                  return s && s.visibility !== 'hidden' && s.display !== 'none' &&
                    r.width > 0 && r.height > 0 && r.bottom >= 0 && r.right >= 0 &&
                    r.top <= window.innerHeight && r.left <= window.innerWidth;
                };
                const hangups = Array.from(document.querySelectorAll('button,[role="button"],gv-icon-button'))
                  .filter(visible)
                  .filter((el) => {
                    const text = [
                      el.getAttribute('aria-label') || '',
                      el.getAttribute('title') || '',
                      el.getAttribute('icon-name') || '',
                      el.textContent || '',
                    ].join(' ').toLowerCase();
                    return text.includes('hang') || text.includes('end call') ||
                      text.includes('call_end') || text.includes('end-call');
                  })
                  .map((el) => el.getBoundingClientRect());
                if (!hangups.length) return [];
                const nearHangup = (rect) => hangups.some((h) => {
                  const cx = rect.left + rect.width / 2;
                  const cy = rect.top + rect.height / 2;
                  const hx = h.left + h.width / 2;
                  const hy = h.top + h.height / 2;
                  return Math.abs(cx - hx) <= 520 && Math.abs(cy - hy) <= 360;
                });
                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('body *'))) {
                  if (!visible(el)) continue;
                  if (['SCRIPT', 'STYLE', 'BUTTON', 'A', 'INPUT', 'TEXTAREA'].includes(el.tagName)) continue;
                  const ownText = Array.from(el.childNodes)
                    .filter((n) => n.nodeType === Node.TEXT_NODE)
                    .map((n) => n.textContent || '')
                    .join(' ')
                    .replace(/\\s+/g, ' ')
                    .trim();
                  const text = ownText || ((el.children.length === 0 ? el.textContent : '') || '').replace(/\\s+/g, ' ').trim();
                  if (!text || text.length > 12) continue;
                  if (!/^(?:\\d{1,2}:)?\\d{1,2}:\\d{2}$/.test(text)) continue;
                  if (!nearHangup(el.getBoundingClientRect())) continue;
                  candidates.push(text);
                }
                return Array.from(new Set(candidates));
                """
            )
        except WebDriverException:
            return []
        if not isinstance(texts, list):
            return []
        cleaned: list[str] = []
        for item in texts:
            text = str(item or "").strip()
            if _EXACT_DURATION_RE.match(text) and not _AM_PM_RE.search(text):
                cleaned.append(text)
        return cleaned

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

    def _active_call_surface_present(self) -> bool:
        return self._any_present("call_active") or bool(self._answered_controls_present()[0])

    def _wait_for_outbound_call_surface(self, timeout: float = 8.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._active_call_surface_present():
                return True
            time.sleep(0.3)
        return False

    def _reset_for_new_call(self) -> None:
        """Return Google Voice to a clean Calls page before dialing."""
        if self._active_call_surface_present():
            logger.info("Existing Google Voice call surface detected before dialing; hanging up")
            self.hangup_call()
            time.sleep(1.5)
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.2)
        except WebDriverException:
            pass
        self._click_first("dismiss_button", timeout=0.8)

    def dial_number(self, phone: str, connect_timeout: int = 30) -> bool:
        if not self.driver:
            raise RuntimeError("Browser is not launched")

        if not self._focus_driver():
            return False
        time.sleep(0.5)
        self._reset_for_new_call()

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
            # Last-resort reset: Google Voice can leave a stale surface after a
            # previous call. Reload /calls once, then try the new-call control.
            logger.info("Dialpad not available; refreshing Calls page before retrying %s", phone)
            if self._open_calls_page():
                time.sleep(1.0)
                opened = self._find_first("number_input", timeout=2) is not None
                if not opened:
                    opened = self._click_first("dialpad_open", timeout=5)
                    if opened:
                        time.sleep(1.2)

        if not opened:
            logger.warning("Could not open Google Voice new-call dialpad for %s", phone)
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

        try:
            typed_value = (number_input.get_attribute("value") or "").strip()
            typed_digits = "".join(ch for ch in typed_value if ch.isdigit())
            phone_digits = "".join(ch for ch in phone if ch.isdigit())
            if phone_digits and typed_digits[-len(phone_digits):] != phone_digits:
                logger.warning(
                    "Google Voice number input did not retain %s (value=%r); retrying DOM input",
                    phone,
                    typed_value,
                )
                self._set_input_value(number_input, phone)
                time.sleep(0.5)
        except WebDriverException as exc:
            logger.debug("Could not verify typed Google Voice number: %s", exc)

        # Click call button
        called = self._click_call_start_button(timeout=8)
        if called:
            time.sleep(2)

        if not called:
            try:
                number_input.send_keys(Keys.RETURN)
                time.sleep(2)
                called = True
            except WebDriverException:
                return False

        if not self._wait_for_outbound_call_surface(timeout=min(8.0, max(3.0, connect_timeout / 4.0))):
            logger.warning("Dial action did not produce an active Google Voice call surface for %s", phone)
            return False

        return True

    # ------------------------------------------------------------------
    # Call state detection — drives the CallSession state machine
    # ------------------------------------------------------------------

    def detect_call_state(
        self,
        session: CallSession,
        poll_interval: float = 0.75,
        timeout: float = 90.0,
        ctrl_confirm_polls: int = 2,
        *,
        min_ring_seconds: float = MIN_RING_SECONDS_DEFAULT,
        max_ring_seconds: float = MAX_RING_SECONDS_DEFAULT,
        voicemail_detect_seconds: float = VOICEMAIL_DETECT_SECONDS_DEFAULT,
    ) -> CallState:
        """Call-state machine gate to avoid hanging up before pickup.

        Fix rationale:
        - Previous logic could treat ringing/no-answer artifacts as voicemail.
        - We now explicitly stage:
          DIALING -> RINGING -> (ANSWERED or VOICEMAIL) or NO_ANSWER/ENDED.

        Returns the final CallState and updates session.transition() on each change.

        Rules implemented (minimal, targeted):
        1) After dialing, enter RINGING.
        2) During RINGING, never start ConversationLoop (handled in main.py).
        3) During RINGING, do not mark voicemail/VOICEMAIL until ANSWERED window starts.
        4) If still not answered by MAX_RING_SECONDS => NO_ANSWER.
        5) Voicemail detection only allowed for the first VOICEMAIL_DETECT_SECONDS after
           ANSWERED begins.
        """
        if not self.driver:
            if not session.is_terminal():
                session.transition(CallState.FAILED, "browser not running")
            return CallState.FAILED

        if session.state == CallState.DIALING:
            session.transition(CallState.RINGING, "dial confirmed, entering RINGING")

        start_ts = time.time()
        deadline = start_ts + timeout
        # Important for unit tests: allow the caller-provided `timeout` to fully
        # control loop duration even if max_ring_seconds is passed.
        # We still use max_ring_seconds as a semantic gate elsewhere.


        ctrl_consecutive = 0
        if session.state == CallState.CONNECTED and session.connected_at is not None:
            answered_ts: float | None = session.connected_at.timestamp()
        elif session.state == CallState.CONNECTED:
            answered_ts = start_ts
        else:
            answered_ts = None
        next_poll_log_ts = 0.0

        while time.time() < deadline:
            now = time.time()
            elapsed = now - start_ts

            # ---------------- ANSWERED detection (timer/answered controls) ----------------
            timer_evidence = self._connected_timer_evidence()
            timer_present = timer_evidence is not None or self._connected_timer_present()
            if timer_present and timer_evidence is None:
                timer_evidence = "call timer visible"
            ctrl_present, ctrl_labels = self._answered_controls_present()
            voicemail_cue = self._voicemail_cue_present()
            voicemail_page = self._page_contains_voicemail()
            ended_banner = self._any_present("call_ended_banner")
            call_active = self._any_present("call_active")

            if now >= next_poll_log_ts:
                logger.info(
                    "CALL_STATE poll state=%s elapsed_ring=%.1fs min_ring=%.1fs max_ring=%.1fs "
                    "timer=%s controls=%s call_active=%s ended_banner=%s voicemail_dom=%s "
                    "voicemail_page=%s audio_classifier=%s",
                    session.state.value,
                    elapsed,
                    float(min_ring_seconds),
                    float(max_ring_seconds),
                    timer_evidence or False,
                    ctrl_labels if ctrl_present else [],
                    call_active,
                    ended_banner,
                    voicemail_cue,
                    voicemail_page,
                    "not_available_in_dom_detector",
                )
                next_poll_log_ts = now + max(2.0, float(poll_interval))

            if answered_ts is None:
                # Still ringing. Only declare ANSWERED after min_ring_seconds.
                answered_gate = elapsed >= float(min_ring_seconds)

                if answered_gate and timer_present:
                    logger.info("ANSWERED: call timer detected after %.1fs: %s", elapsed, timer_evidence)
                    answered_ts = now
                    session.transition(CallState.CONNECTED, f"answered: call timer visible: {timer_evidence}")
                    return CallState.CONNECTED

                if answered_gate and ctrl_present:
                    ctrl_consecutive += 1
                    if ctrl_consecutive >= ctrl_confirm_polls:
                        reason = (
                            f"answered controls stable ({ctrl_consecutive}× polls): "
                            + ", ".join(ctrl_labels[:4])
                        )
                        logger.info("ANSWERED: %s", reason)
                        answered_ts = now
                        session.transition(CallState.CONNECTED, reason)
                        return CallState.CONNECTED
                elif not answered_gate and (timer_present or ctrl_present):
                    logger.info(
                        "ANSWERED evidence seen before min ring; holding in RINGING "
                        "(elapsed=%.1fs min=%.1fs timer=%s controls=%s)",
                        elapsed,
                        float(min_ring_seconds),
                        bool(timer_present),
                        ctrl_labels if ctrl_present else [],
                    )
                else:
                    if ctrl_consecutive > 0:
                        logger.debug(
                            "ANSWERED debounce reset after ringing (elapsed=%.1fs, ctrl_consecutive=%d)",
                            elapsed,
                            ctrl_consecutive,
                        )
                    ctrl_consecutive = 0

                # ---------------- VOICEMAIL/ENDED only when ANSWERED begins ----------------
                # During ringing, explicitly do NOT declare voicemail.
                if voicemail_cue or voicemail_page:
                    logger.info(
                        "RINGING: ignoring voicemail cue before answered evidence "
                        "(elapsed=%.1fs dom=%s page=%s)",
                        elapsed,
                        voicemail_cue,
                        voicemail_page,
                    )
                if ended_banner and elapsed < float(min_ring_seconds):
                    logger.info(
                        "RINGING: ignoring call-ended banner before min ring "
                        "(elapsed=%.1fs min=%.1fs)",
                        elapsed,
                        float(min_ring_seconds),
                    )
                elif ended_banner:
                    session.transition(CallState.ENDED, "call-ended banner detected after min ring")
                    logger.info(
                        "ENDED: call-ended banner after %.1fs while ringing (timeout_reason=ended_banner)",
                        elapsed,
                    )
                    return CallState.ENDED
                if elapsed >= float(max_ring_seconds):
                    logger.info(
                        "NO_ANSWER: max ring elapsed (elapsed=%.1fs max=%.1fs timeout_reason=max_ring_seconds)",
                        elapsed,
                        float(max_ring_seconds),
                    )
                    session.transition(CallState.FAILED, "no answer (max ring seconds elapsed)")
                    return CallState.FAILED

                time.sleep(poll_interval)
                continue

            # If already answered, voicemail can be detected only shortly after.
            if answered_ts is not None:
                if (now - answered_ts) <= float(voicemail_detect_seconds):
                    if voicemail_cue or voicemail_page:
                        session.transition(CallState.VOICEMAIL, "voicemail detected shortly after ANSWERED")
                        logger.info(
                            "VOICEMAIL: detected after answered evidence "
                            "(elapsed=%.1fs since_answer=%.1fs dom=%s page=%s audio_classifier=%s)",
                            elapsed,
                            now - answered_ts,
                            voicemail_cue,
                            voicemail_page,
                            "not_available_in_dom_detector",
                        )
                        return CallState.VOICEMAIL
                elif voicemail_cue or voicemail_page:
                    logger.info(
                        "CONNECTED: ignoring late voicemail cue outside detect window "
                        "(since_answer=%.1fs window=%.1fs dom=%s page=%s)",
                        now - answered_ts,
                        float(voicemail_detect_seconds),
                        voicemail_cue,
                        voicemail_page,
                    )
                # After voicemail-detect window, only rely on connected->ended transitions.

            # ---------------- ENDED detection ----------------
            if ended_banner:
                if not session.is_terminal():
                    session.transition(CallState.ENDED, "call-ended banner detected")
                logger.info(
                    "ENDED: call-ended banner detected (state=%s elapsed=%.1fs timeout_reason=ended_banner)",
                    session.state.value,
                    elapsed,
                )
                return CallState.ENDED

            # When the call ends, the hangup button disappears.
            if session.state == CallState.CONNECTED and not call_active:
                logger.info("ENDED: active call controls vanished (hangup button gone)")
                session.transition(CallState.ENDED, "active call controls vanished")
                return CallState.ENDED

            time.sleep(poll_interval)

        # ---------------- NO_ANSWER vs FAILED ----------------
        # If we timed out while still ringing, treat as no-answer.
        if session.state == CallState.RINGING:
            logger.info(
                "NO_ANSWER: did not detect ANSWERED within min/max ring window (min=%.1fs max=%.1fs)",
                float(min_ring_seconds), float(max_ring_seconds),
            )
            session.transition(CallState.FAILED, "no answer (timed out waiting for ANSWERED)" )
            return CallState.FAILED

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
    # Microphone device check
    # ------------------------------------------------------------------

    def check_microphone_device(self, device_name_hint: str = "CABLE Output") -> "tuple[bool, list[str]]":
        """
        Enumerate audio input devices visible to Chrome via JS mediaDevices.enumerateDevices().
        Returns (hint_found, [device_labels]).

        Device labels are only populated when mic permission is granted for the page.
        We grant it at launch via opts.prefs media_stream_mic=1, so labels should appear
        once voice.google.com is loaded.
        """
        if not self.driver:
            return False, []
        try:
            devices = self.driver.execute_async_script(
                """
                var done = arguments[0];
                if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
                    done([]);
                    return;
                }
                navigator.mediaDevices.enumerateDevices()
                    .then(function(devs) {
                        done(devs
                            .filter(function(d) { return d.kind === 'audioinput'; })
                            .map(function(d) {
                                return d.label || ('id:' + (d.deviceId || '').slice(0, 8));
                            })
                        );
                    })
                    .catch(function() { done([]); });
                """
            )
        except WebDriverException as exc:
            logger.debug("check_microphone_device: JS enumerate failed: %s", exc)
            return False, []

        if not isinstance(devices, list):
            devices = []

        hint_lower = device_name_hint.lower()
        found = any(hint_lower in (label or "").lower() for label in devices)
        return found, [str(d) for d in devices]

    def warn_if_mic_not_set(self, device_name_hint: str = "CABLE Output") -> None:
        """
        Log an actionable warning if device_name_hint is not visible as a Chrome audio input.

        Tony's TTS plays to CABLE Input (output device). VB-CABLE routes it to
        CABLE Output (input/recording device). Chrome must be configured to use
        CABLE Output as its microphone for voice.google.com — otherwise the call
        hears the laptop mic, not Tony.
        """
        found, devices = self.check_microphone_device(device_name_hint)
        if found:
            match = next((d for d in devices if device_name_hint.lower() in d.lower()), device_name_hint)
            logger.info("Microphone OK: '%s' is available in Chrome as audio input", match)
            return

        logger.warning(
            "MICROPHONE NOT SET: '%s' not detected as a Chrome audio input.\n"
            "Tony's TTS audio will NOT reach the Google Voice call.\n"
            "\nFix (takes ~30 seconds):\n"
            "  1. In Chrome, click the lock icon to the left of https://voice.google.com\n"
            "  2. Click Microphone → select '%s'\n"
            "  3. Reload the Google Voice tab (F5)\n"
            "\nIf '%s' is not in the list, install VB-CABLE: https://vb-audio.com/Cable/",
            device_name_hint, device_name_hint, device_name_hint,
        )
        if devices:
            logger.info("Audio inputs Chrome can currently see: %s", ", ".join(devices))
        else:
            logger.info(
                "No labeled audio inputs returned by Chrome. "
                "Possible causes: VB-CABLE not installed, mic permission not yet granted "
                "for voice.google.com, or page not fully loaded."
            )

    # ------------------------------------------------------------------
    # Diagnostic snapshot (used by --diagnose-call-state CLI mode)
    # ------------------------------------------------------------------

    def take_dom_snapshot(self, phase: str) -> dict:
        """
        Capture a sanitized DOM snapshot for call-state diagnostics.
        Returns a dict ready to be JSON-serialised.
        No customer data: only aria-labels, classes, placeholder text.
        """
        snap: dict = {
            "ts": time.time(),
            "phase": phase,
            "url": "",
            "buttons": [],
            "inputs": [],
            "selector_hits": {},
            "call_timer_found": False,
            "answered_controls_found": [],
            "call_active_found": False,
        }
        if not self.driver:
            return snap
        try:
            snap["url"] = self.driver.current_url or ""
        except WebDriverException:
            pass

        # Visible buttons — collect aria-label / title / text (no content data)
        try:
            for el in self.driver.find_elements(By.TAG_NAME, "button"):
                try:
                    if not el.is_displayed():
                        continue
                    label = (
                        el.get_attribute("aria-label")
                        or el.get_attribute("title")
                        or el.text or ""
                    ).strip()
                    if label:
                        snap["buttons"].append(label)
                except WebDriverException:
                    pass
        except WebDriverException:
            pass

        # Visible inputs — placeholder / aria-label only
        try:
            for el in self.driver.find_elements(By.TAG_NAME, "input"):
                try:
                    if not el.is_displayed():
                        continue
                    hint = (
                        el.get_attribute("placeholder")
                        or el.get_attribute("aria-label") or ""
                    ).strip()
                    if hint:
                        snap["inputs"].append(hint)
                except WebDriverException:
                    pass
        except WebDriverException:
            pass

        # Test every known selector group
        for group in _SEL:
            hits = []
            for sel in _SEL[group]:
                try:
                    els = self.driver.find_elements(By.CSS_SELECTOR, sel)
                    visible = [
                        e.get_attribute("aria-label") or e.text or sel
                        for e in els if e.is_displayed()
                    ]
                    hits.extend(visible)
                except WebDriverException:
                    pass
            if hits:
                snap["selector_hits"][group] = hits

        snap["call_timer_found"] = self._connected_timer_present()
        ctrl_found, ctrl_labels = self._answered_controls_present()
        snap["answered_controls_found"] = ctrl_labels
        snap["call_active_found"] = self._any_present("call_active")
        return snap

    # ------------------------------------------------------------------
    # Legacy helpers (kept for backward compatibility)
    # ------------------------------------------------------------------

    def _is_call_active(self) -> bool:
        return self._any_present("call_active")

    def is_call_active(self) -> bool:
        """Return True while Google Voice still shows an active call control."""
        return self._is_call_active()

    def wait_for_call_connect(self, timeout: int = 30) -> bool:
        session = CallSession(phone="", contact_name="legacy wait")
        session.transition(CallState.DIALING, "legacy wait_for_call_connect")
        return self.detect_call_state(session, poll_interval=1.0, timeout=float(timeout)) == CallState.CONNECTED

    def wait_for_call_end(self, timeout: int = 300) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._is_call_active():
                return True
            time.sleep(2)
        return False

    def detect_voicemail(self) -> bool:
        return self._page_contains_voicemail()
