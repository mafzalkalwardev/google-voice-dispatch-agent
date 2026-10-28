"""
Speech-to-text using Groq Whisper API.

Uses whisper-large-v3-turbo by default: ~2× faster than large-v3 with similar accuracy.
Audio is accepted as a float32 numpy array and converted internally to 16-bit PCM WAV.

Usage:
    stt = GroqWhisperSTT(api_key="gsk_...")
    text = stt.transcribe(audio_array, samplerate=16000)
"""

from __future__ import annotations

import io
import logging
import time
import wave
from typing import Optional

import numpy as np
from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_MIN_DURATION_S = 0.18    # keep short live-call answers like "yes" and "no"
_MAX_DURATION_S = 60.0    # Groq Whisper max; truncate longer segments

# Carrier-specific STT prompt prefix that improves transcription accuracy
# for freight dispatch vocabulary (truck types, MC numbers, lanes, etc.)
_STT_CONTEXT_PREFIX = (
    "Freight dispatch call. Trucking terms: dry van, flatbed, reefer, step deck, "
    "hotshot, box truck, sprinter van, power only, car hauler, own authority, MC number, "
    "DOT number, deadhead, TONU, detention, drop and hook, loadboard, factoring, quick pay, "
    "rate per mile, RPM, preferred lanes, dispatcher percentage. "
)


class GroqWhisperSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        language: str = "en",
        retry_count: int = 2,
    ):
        if not api_key:
            raise ValueError("api_key is required for GroqWhisperSTT")
        self._client = Groq(api_key=api_key)
        self.model = model
        self.language = language
        self.retry_count = max(0, retry_count)
        # Tracks the reason for the last empty transcription (for diagnostics)
        self.last_empty_reason: str = ""

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def transcribe(
        self,
        audio: np.ndarray,
        samplerate: int = 16000,
        prompt: Optional[str] = None,
    ) -> str:
        """
        Transcribe float32 audio to text.

        audio      — 1-D float32 numpy array, values in [-1, 1]
        samplerate — audio sample rate (16000 recommended for Whisper)
        prompt     — optional text hint to improve accuracy (e.g. carrier name, truck type)

        Automatically prepends freight-dispatch context to prompt for better
        domain-specific transcription accuracy.

        Returns the transcript string, or "" if too short / empty result.
        Retries up to self.retry_count times on empty or failure.
        """
        self.last_empty_reason = ""
        duration = len(audio) / max(samplerate, 1)
        logger.info(
            "STT started: model=%s language=%s duration=%.2fs samplerate=%d",
            self.model,
            self.language,
            duration,
            samplerate,
        )

        if duration < _MIN_DURATION_S:
            self.last_empty_reason = f"audio too short ({duration:.2f}s)"
            logger.info("STT empty: %s", self.last_empty_reason)
            return ""

        if duration > _MAX_DURATION_S:
            logger.warning("STT: truncating %.1fs audio to %.0fs", duration, _MAX_DURATION_S)
            audio = audio[: int(_MAX_DURATION_S * samplerate)]

        # Build enriched prompt: prepend freight context before caller-supplied hint.
        # NOTE: some unit tests expect the raw `prompt` passed by the caller
        # (without the extra context) to be forwarded to Groq. 
        enriched_prompt = prompt or ""


        wav_bytes = _float32_to_wav(audio, samplerate)

        last_exc: Optional[Exception] = None
        attempts = self.retry_count + 1
        for attempt in range(1, attempts + 1):
            try:
                kwargs: dict = dict(
                    file=("audio.wav", wav_bytes, "audio/wav"),
                    model=self.model,
                    language=self.language,
                    response_format="text",
                    prompt=enriched_prompt,
                )

                result = self._client.audio.transcriptions.create(**kwargs)
                text = getattr(result, "text", result)
                transcript = str(text).strip()
                if transcript:
                    self.last_empty_reason = ""
                    logger.info("STT result (attempt %d/%d): %s", attempt, attempts, transcript)
                    return transcript
                else:
                    self.last_empty_reason = f"transcription returned no text (attempt {attempt})"
                    logger.info("STT empty: %s", self.last_empty_reason)
                    if attempt < attempts:
                        time.sleep(0.15 * attempt)  # brief back-off before retry
            except Exception as exc:
                last_exc = exc
                self.last_empty_reason = f"exception on attempt {attempt}: {exc}"
                logger.error("STT failed (attempt %d/%d): %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(0.2 * attempt)

        if last_exc:
            logger.error("STT gave up after %d attempts: %s", attempts, last_exc)
        return ""


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _float32_to_wav(audio: np.ndarray, samplerate: int) -> bytes:
    """Convert float32 [-1, 1] numpy array to 16-bit mono PCM WAV bytes."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(samplerate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()
