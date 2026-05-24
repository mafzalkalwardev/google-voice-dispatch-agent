# Backward-compatibility shim — use src.call_session for new code.
from src.call_session import CallSession, CallState, _ALLOWED as _TRANSITIONS

__all__ = ["CallSession", "CallState", "_TRANSITIONS"]
