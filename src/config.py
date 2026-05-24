import os
import json
from pathlib import Path
from dotenv import load_dotenv
from src.paths import runtime_base

BASE_DIR = runtime_base()
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = BASE_DIR / "dialer_config.json"

load_dotenv(BASE_DIR / ".env")

_DEFAULTS = {
    "contacts_file": str(DATA_DIR / "contacts.xlsx"),
    "profile_name": "sales_profile",
    "callback_number": "",
    "agent_name": "Tony",
    "company_name": "Indus Transports LLC",
    "company_website": "https://industransports.online/",
    "company_context": (
        "Indus Transports LLC helps owner-operators and small trucking companies "
        "find high-paying freight loads nationwide with dedicated dispatchers, "
        "rate negotiation, paperwork/payment support, all equipment types, "
        "48-state coverage, and 24/7 support. Website says carriers can average "
        "$12K+ weekly gross, but do not guarantee earnings."
    ),
    "groq_model": "llama-3.3-70b-versatile",
    "rotation_minutes": 60,
    "autocut_seconds": 15,
    "loopback_device": "CABLE Input",
    "call_timeout": 60,
    "call_max_duration": 90,
    # Realtime conversation settings
    "capture_device": "default",
    "tts_voice": "en-US-GuyNeural",
    "stt_model": "whisper-large-v3-turbo",
    "vad_threshold": 0.015,
}


def _load_json() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


class Config:
    def __init__(self):
        j = _load_json()

        self.contacts_file = Path(
            os.getenv("CONTACTS_FILE", j.get("contacts_file", _DEFAULTS["contacts_file"]))
        )
        self.profile_name = os.getenv(
            "PROFILE_NAME", j.get("profile_name", _DEFAULTS["profile_name"])
        )
        self.callback_number = os.getenv(
            "CALLBACK_NUMBER",
            os.getenv("GOOGLE_VOICE_NUMBER", j.get("callback_number", _DEFAULTS["callback_number"])),
        )
        self.agent_name = os.getenv(
            "AGENT_NAME", j.get("agent_name", _DEFAULTS["agent_name"])
        )
        self.company_name = os.getenv(
            "COMPANY_NAME", j.get("company_name", _DEFAULTS["company_name"])
        )
        self.company_website = os.getenv(
            "COMPANY_WEBSITE", j.get("company_website", _DEFAULTS["company_website"])
        )
        self.company_context = os.getenv(
            "COMPANY_CONTEXT", j.get("company_context", _DEFAULTS["company_context"])
        )
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "")
        self.groq_model: str = os.getenv(
            "GROQ_MODEL", j.get("groq_model", _DEFAULTS["groq_model"])
        )
        self.rotation_minutes: int = int(
            os.getenv("ROTATION_MINUTES", j.get("rotation_minutes", _DEFAULTS["rotation_minutes"]))
        )
        self.autocut_seconds: int = int(
            os.getenv("AUTOCUT_SECONDS", j.get("autocut_seconds", _DEFAULTS["autocut_seconds"]))
        )
        self.loopback_device: str = os.getenv(
            "LOOPBACK_DEVICE", j.get("loopback_device", _DEFAULTS["loopback_device"])
        )
        self.call_timeout: int = int(
            os.getenv("CALL_TIMEOUT", j.get("call_timeout", _DEFAULTS["call_timeout"]))
        )
        self.call_max_duration: int = int(
            os.getenv("CALL_MAX_DURATION", j.get("call_max_duration", _DEFAULTS["call_max_duration"]))
        )
        self.capture_device: str = os.getenv(
            "CAPTURE_DEVICE", j.get("capture_device", _DEFAULTS["capture_device"])
        )
        self.tts_voice: str = os.getenv(
            "TTS_VOICE", j.get("tts_voice", _DEFAULTS["tts_voice"])
        )
        self.stt_model: str = os.getenv(
            "STT_MODEL", j.get("stt_model", _DEFAULTS["stt_model"])
        )
        self.vad_threshold: float = float(
            os.getenv("VAD_THRESHOLD", j.get("vad_threshold", _DEFAULTS["vad_threshold"]))
        )

    def validate(self) -> None:
        if not self.groq_api_key or self.groq_api_key.startswith("your_"):
            raise SystemExit(
                "GROQ_API_KEY not configured. Copy .env.example to .env and set a real key."
            )
        if self.groq_model in ("groq-alpha", ""):
            raise SystemExit(
                f"GROQ_MODEL '{self.groq_model}' is not valid. "
                "Use e.g. llama-3.3-70b-versatile. See console.groq.com for model list."
            )

    @classmethod
    def load(cls) -> "Config":
        return cls()
