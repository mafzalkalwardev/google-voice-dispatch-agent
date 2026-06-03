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
    "llm_model_realtime": "llama-3.1-8b-instant",
    "llm_model_batch": "",
    "loopback_device": "CABLE Input",
    "call_timeout": 60,
    "call_max_duration": 90,
    # Realtime conversation settings
    "capture_device": "default",
    "tts_voice": "en-US-GuyNeural",
    "stt_model": "whisper-large-v3-turbo",
    "vad_threshold": 0.015,
    # Answer detection — controls when Tony first speaks after CONNECTED is detected
    "answered_speak_delay_seconds": 1.5,  # reduced: 4.0s caused early hang-ups; 1.5s is better
    "wait_for_human_audio": True,         # listen for inbound audio before speaking
    "human_audio_timeout_seconds": 8.0,   # max seconds to listen before speaking anyway
    "answer_confirm_polls": 2,            # consecutive DOM polls required to confirm CONNECTED
    "min_ring_seconds": 2.0,              # ignore transient DOM cues immediately after dialing
    "max_ring_seconds": 45.0,             # stop waiting for no-answer calls after this window
    "voicemail_detect_seconds": 15.0,     # active voicemail cue window after answer evidence
    "call_cooldown_seconds": 10.0,        # pause between live calls
    # VAD tuning — operators can adjust from Settings page
    "vad_silence_frames": 8,              # frames of silence before utterance ends (8×30ms=240ms)
    "listen_after_tts_delay_ms": 150,
    "use_thinking_fillers": True,
    "filler_probability": 0.7,
    "stream_llm_replies": True,
    "vad_speech_frames": 2,               # frames of speech required to start utterance
    # STT reliability
    "stt_retry_count": 2,                 # number of STT retries on empty/failure
    # TTS pre-warming
    "tts_warmup": True,                   # pre-generate common phrases at startup
    "tts_allow_sapi_fallback": False,     # keep one neural voice; avoid Windows Zira mixing in
    "silence_does_not_end_call": True,
    "use_stt_context": True,
    "max_silence_seconds": 8.0,
    "voicemail_max_wait_seconds": 8.0,
    "voicemail_play_on_greeting": True,
    "voicemail_play_after_seconds": 4.0,
    "voicemail_message_max_seconds": 28.0,
    "voicemail_greeting_frames_required": 6,
    "screening_purpose_text": "freight dispatch and load support",
    "chrome_restart_every_n_calls": 75,
    "max_calls_per_run": 0,
    "groq_max_retries_per_minute": 60,
    "avoid_gv_page_reload": True,
    "use_call_intelligence": True,
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
        from src.groq_pool import load_groq_api_keys

        self.groq_api_keys: list[str] = load_groq_api_keys()
        self.groq_api_key: str = (
            self.groq_api_keys[0] if self.groq_api_keys else os.getenv("GROQ_API_KEY", "")
        )
        self.groq_model: str = os.getenv(
            "GROQ_MODEL", j.get("groq_model", _DEFAULTS["groq_model"])
        )
        self.llm_model_realtime: str = os.getenv(
            "LLM_MODEL_REALTIME",
            j.get("llm_model_realtime", _DEFAULTS["llm_model_realtime"]),
        ) or self.groq_model
        batch_model = os.getenv("LLM_MODEL_BATCH", j.get("llm_model_batch", _DEFAULTS["llm_model_batch"]))
        self.llm_model_batch: str = batch_model or self.groq_model
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
        self.answered_speak_delay_seconds: float = float(
            os.getenv("ANSWERED_SPEAK_DELAY_SECONDS", j.get("answered_speak_delay_seconds", _DEFAULTS["answered_speak_delay_seconds"]))
        )
        self.wait_for_human_audio: bool = os.getenv(
            "WAIT_FOR_HUMAN_AUDIO", str(j.get("wait_for_human_audio", _DEFAULTS["wait_for_human_audio"]))
        ).lower() not in ("false", "0", "no")
        self.human_audio_timeout_seconds: float = float(
            os.getenv("HUMAN_AUDIO_TIMEOUT_SECONDS", j.get("human_audio_timeout_seconds", _DEFAULTS["human_audio_timeout_seconds"]))
        )
        self.answer_confirm_polls: int = int(
            os.getenv("ANSWER_CONFIRM_POLLS", j.get("answer_confirm_polls", _DEFAULTS["answer_confirm_polls"]))
        )
        self.min_ring_seconds: float = float(
            os.getenv("MIN_RING_SECONDS", j.get("min_ring_seconds", _DEFAULTS["min_ring_seconds"]))
        )
        self.max_ring_seconds: float = float(
            os.getenv("MAX_RING_SECONDS", j.get("max_ring_seconds", _DEFAULTS["max_ring_seconds"]))
        )
        self.voicemail_detect_seconds: float = float(
            os.getenv("VOICEMAIL_DETECT_SECONDS", j.get("voicemail_detect_seconds", _DEFAULTS["voicemail_detect_seconds"]))
        )
        self.call_cooldown_seconds: float = float(
            os.getenv("CALL_COOLDOWN_SECONDS", j.get("call_cooldown_seconds", _DEFAULTS["call_cooldown_seconds"]))
        )
        self.vad_silence_frames: int = int(
            os.getenv("VAD_SILENCE_FRAMES", j.get("vad_silence_frames", _DEFAULTS["vad_silence_frames"]))
        )
        self.vad_speech_frames: int = int(
            os.getenv("VAD_SPEECH_FRAMES", j.get("vad_speech_frames", _DEFAULTS["vad_speech_frames"]))
        )
        self.stt_retry_count: int = int(
            os.getenv("STT_RETRY_COUNT", j.get("stt_retry_count", _DEFAULTS["stt_retry_count"]))
        )
        self.tts_warmup: bool = os.getenv(
            "TTS_WARMUP", str(j.get("tts_warmup", _DEFAULTS["tts_warmup"]))
        ).lower() not in ("false", "0", "no")
        self.tts_allow_sapi_fallback: bool = os.getenv(
            "TTS_ALLOW_SAPI_FALLBACK",
            str(j.get("tts_allow_sapi_fallback", _DEFAULTS["tts_allow_sapi_fallback"])),
        ).lower() in ("true", "1", "yes")
        self.silence_does_not_end_call: bool = os.getenv(
            "SILENCE_DOES_NOT_END_CALL",
            str(j.get("silence_does_not_end_call", _DEFAULTS["silence_does_not_end_call"])),
        ).lower() not in ("false", "0", "no")
        self.use_stt_context: bool = os.getenv(
            "USE_STT_CONTEXT", str(j.get("use_stt_context", _DEFAULTS["use_stt_context"]))
        ).lower() not in ("false", "0", "no")
        self.max_silence_seconds: float = float(
            os.getenv("MAX_SILENCE_SECONDS", j.get("max_silence_seconds", _DEFAULTS["max_silence_seconds"]))
        )
        self.listen_after_tts_delay_ms: int = int(
            os.getenv(
                "LISTEN_AFTER_TTS_DELAY_MS",
                j.get("listen_after_tts_delay_ms", _DEFAULTS["listen_after_tts_delay_ms"]),
            )
        )
        self.use_thinking_fillers: bool = os.getenv(
            "USE_THINKING_FILLERS",
            str(j.get("use_thinking_fillers", _DEFAULTS["use_thinking_fillers"])),
        ).lower() not in ("false", "0", "no")
        self.filler_probability: float = float(
            os.getenv("FILLER_PROBABILITY", j.get("filler_probability", _DEFAULTS["filler_probability"]))
        )
        self.stream_llm_replies: bool = os.getenv(
            "STREAM_LLM_REPLIES",
            str(j.get("stream_llm_replies", _DEFAULTS["stream_llm_replies"])),
        ).lower() not in ("false", "0", "no")
        self.voicemail_max_wait_seconds: float = float(
            os.getenv(
                "VOICEMAIL_MAX_WAIT_SECONDS",
                j.get("voicemail_max_wait_seconds", _DEFAULTS["voicemail_max_wait_seconds"]),
            )
        )
        self.voicemail_play_on_greeting: bool = os.getenv(
            "VOICEMAIL_PLAY_ON_GREETING",
            str(j.get("voicemail_play_on_greeting", _DEFAULTS["voicemail_play_on_greeting"])),
        ).lower() not in ("false", "0", "no")
        self.voicemail_play_after_seconds: float = float(
            os.getenv(
                "VOICEMAIL_PLAY_AFTER_SECONDS",
                j.get("voicemail_play_after_seconds", _DEFAULTS["voicemail_play_after_seconds"]),
            )
        )
        self.voicemail_message_max_seconds: float = float(
            os.getenv(
                "VOICEMAIL_MESSAGE_MAX_SECONDS",
                j.get("voicemail_message_max_seconds", _DEFAULTS["voicemail_message_max_seconds"]),
            )
        )
        self.voicemail_greeting_frames_required: int = int(
            os.getenv(
                "VOICEMAIL_GREETING_FRAMES_REQUIRED",
                j.get("voicemail_greeting_frames_required", _DEFAULTS["voicemail_greeting_frames_required"]),
            )
        )
        self.screening_purpose_text: str = os.getenv(
            "SCREENING_PURPOSE_TEXT",
            j.get("screening_purpose_text", _DEFAULTS["screening_purpose_text"]),
        )
        self.chrome_restart_every_n_calls: int = int(
            os.getenv(
                "CHROME_RESTART_EVERY_N_CALLS",
                j.get("chrome_restart_every_n_calls", _DEFAULTS["chrome_restart_every_n_calls"]),
            )
        )
        self.max_calls_per_run: int = int(
            os.getenv("MAX_CALLS_PER_RUN", j.get("max_calls_per_run", _DEFAULTS["max_calls_per_run"]))
        )
        self.groq_max_retries_per_minute: int = int(
            os.getenv(
                "GROQ_MAX_RETRIES_PER_MINUTE",
                j.get("groq_max_retries_per_minute", _DEFAULTS["groq_max_retries_per_minute"]),
            )
        )
        self.avoid_gv_page_reload: bool = os.getenv(
            "AVOID_GV_PAGE_RELOAD",
            str(j.get("avoid_gv_page_reload", _DEFAULTS["avoid_gv_page_reload"])),
        ).lower() not in ("false", "0", "no")
        self.use_call_intelligence: bool = os.getenv(
            "USE_CALL_INTELLIGENCE",
            str(j.get("use_call_intelligence", _DEFAULTS["use_call_intelligence"])),
        ).lower() not in ("false", "0", "no")

    def validate(self) -> None:
        if not self.groq_api_keys:
            raise SystemExit(
                "No Groq API keys configured. Copy .env.example to .env and set GROQ_API_KEY "
                "(and optionally GROQ_API_KEY_2, GROQ_API_KEY_3, or GROQ_API_KEYS)."
            )
        if self.groq_model in ("groq-alpha", ""):
            raise SystemExit(
                f"GROQ_MODEL '{self.groq_model}' is not valid. "
                "Use e.g. llama-3.3-70b-versatile. See console.groq.com for model list."
            )

    @classmethod
    def load(cls) -> "Config":
        return cls()
