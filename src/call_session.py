from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class CallState(Enum):
    IDLE      = "IDLE"
    DIALING   = "DIALING"
    RINGING   = "RINGING"
    CONNECTED = "CONNECTED"
    VOICEMAIL = "VOICEMAIL"
    ENDED     = "ENDED"
    FAILED    = "FAILED"


# Every legal one-step transition. ENDED and FAILED are terminal (empty sets).
_ALLOWED: dict[CallState, set[CallState]] = {
    CallState.IDLE:      {CallState.DIALING, CallState.FAILED},
    CallState.DIALING:   {CallState.RINGING, CallState.CONNECTED, CallState.VOICEMAIL,
                          CallState.ENDED, CallState.FAILED},
    CallState.RINGING:   {CallState.CONNECTED, CallState.VOICEMAIL,
                          CallState.ENDED, CallState.FAILED},
    CallState.CONNECTED: {CallState.VOICEMAIL, CallState.ENDED, CallState.FAILED},
    CallState.VOICEMAIL: {CallState.ENDED, CallState.FAILED},
    CallState.ENDED:     set(),
    CallState.FAILED:    set(),
}


@dataclass
class CallSession:
    phone: str
    contact_name: str
    state: CallState = CallState.IDLE
    started_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    voicemail_detected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    outcome: str = ""
    notes: list[str] = field(default_factory=list)
    transcript_path: Optional[Path] = None

    def transition(self, new_state: CallState, note: str = "") -> None:
        allowed = _ALLOWED.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Illegal transition {self.state.value} → {new_state.value}. "
                f"Allowed from {self.state.value}: {[s.value for s in allowed]}"
            )
        now = datetime.now()
        if new_state == CallState.DIALING:
            self.started_at = now
        elif new_state == CallState.CONNECTED and self.connected_at is None:
            self.connected_at = now
        elif new_state == CallState.VOICEMAIL and self.voicemail_detected_at is None:
            self.voicemail_detected_at = now
        elif new_state in (CallState.ENDED, CallState.FAILED) and self.ended_at is None:
            self.ended_at = now

        self.state = new_state
        if note:
            self.notes.append(note)

    def is_terminal(self) -> bool:
        return self.state in (CallState.ENDED, CallState.FAILED)

    def connected_duration_seconds(self) -> Optional[float]:
        if self.connected_at is None or self.ended_at is None:
            return None
        return (self.ended_at - self.connected_at).total_seconds()

    def total_duration_seconds(self) -> Optional[float]:
        if self.started_at is None or self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()

    def to_log_dict(self) -> dict:
        return {
            "phone": self.phone,
            "contact_name": self.contact_name,
            "state": self.state.value,
            "outcome": self.outcome,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "connected_at": self.connected_at.isoformat() if self.connected_at else "",
            "voicemail_detected_at": (
                self.voicemail_detected_at.isoformat() if self.voicemail_detected_at else ""
            ),
            "ended_at": self.ended_at.isoformat() if self.ended_at else "",
            "connected_duration_s": self.connected_duration_seconds(),
            "total_duration_s": self.total_duration_seconds(),
            "notes": "; ".join(self.notes),
        }
