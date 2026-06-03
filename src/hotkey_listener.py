"""
Global hotkey listener for manual call takeover and stop.

Hotkeys (system-wide, active even when Chrome has focus):
  Ctrl+Shift+T — TAKEOVER  pause AI; human speaks to the prospect
  Ctrl+Shift+R — RESUME    re-enable AI responses
  Ctrl+Shift+S — STOP      end the call and terminate the conversation loop

Requires the 'keyboard' package.  If it is not installed the listener degrades
gracefully to a no-op (hotkeys are simply unavailable).

Note: on Windows the keyboard package requires the script to run with admin
rights for system-wide key interception.  Running in a plain terminal usually
works for most use cases; Chrome windows may intercept Ctrl+Shift shortcuts
themselves — use the --takeover-key CLI flag to choose different keys if needed.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger("GoogleVoiceAgent")

HOTKEY_TAKEOVER = "ctrl+shift+t"
HOTKEY_RESUME   = "ctrl+shift+r"
HOTKEY_STOP     = "ctrl+shift+s"


def _keyboard_available() -> bool:
    try:
        import keyboard  # noqa: F401
        return True
    except ImportError:
        return False


class HotkeyListener:
    """
    Register global hotkeys.  Safe to instantiate even without 'keyboard' installed.
    """

    def __init__(
        self,
        on_takeover: Callable[[], None],
        on_resume: Callable[[], None],
        on_stop: Callable[[], None],
        takeover_key: str = HOTKEY_TAKEOVER,
        resume_key:   str = HOTKEY_RESUME,
        stop_key:     str = HOTKEY_STOP,
    ):
        self._on_takeover = on_takeover
        self._on_resume   = on_resume
        self._on_stop     = on_stop
        self.takeover_key = takeover_key
        self.resume_key   = resume_key
        self.stop_key     = stop_key
        self._registered  = False

    def start(self) -> None:
        if not _keyboard_available():
            logger.warning(
                "Hotkeys unavailable (keyboard package missing). "
                "Run: pip install keyboard"
            )
            return
        import keyboard as kb
        kb.add_hotkey(self.takeover_key, self._fire_takeover)
        kb.add_hotkey(self.resume_key,   self._fire_resume)
        kb.add_hotkey(self.stop_key,     self._fire_stop)
        self._registered = True
        logger.info(
            "Hotkeys active: %s=takeover  %s=resume  %s=stop",
            self.takeover_key, self.resume_key, self.stop_key,
        )

    def stop(self) -> None:
        if self._registered:
            try:
                import keyboard as kb
                kb.unhook_all_hotkeys()
            except Exception:
                pass
            self._registered = False

    def _debounced_fire(self, handler, key_name: str, cooldown_s: float = 0.75) -> None:
        now = time.monotonic()
        last = getattr(self, f"_last_{key_name}_monotonic", 0.0)
        if now - last < cooldown_s:
            return
        setattr(self, f"_last_{key_name}_monotonic", now)
        threading.Thread(target=handler, daemon=True).start()

    def _fire_takeover(self) -> None:
        self._debounced_fire(self._on_takeover, "takeover")

    def _fire_resume(self) -> None:
        self._debounced_fire(self._on_resume, "resume")

    def _fire_stop(self) -> None:
        self._debounced_fire(self._on_stop, "stop")
