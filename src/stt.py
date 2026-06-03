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
from groq import Groq  # re-export for tests that patch src.stt.Groq

from src.groq_pool import GroqKeyPool, get_groq_pool, load_groq_api_keys

logger = logging.getLogger("GoogleVoiceAgent")

_MIN_DURATION_S = 0.3     # drop segments shorter than this (likely noise clicks)
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
        use_stt_context: bool = True,
    ):
        if api_key:
            self._pool = GroqKeyPool([api_key.strip()])
        elif load_groq_api_keys():
            self._pool = get_groq_pool()
        else:
            raise ValueError("api_key is required for GroqWhisperSTT")
        self.model = model
        self.language = language
        self.retry_count = max(0, retry_count)
        self.use_stt_context = use_stt_context
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

        if self.use_stt_context:
            if prompt:
                enriched_prompt = _STT_CONTEXT_PREFIX + prompt
            else:
                enriched_prompt = _STT_CONTEXT_PREFIX.strip()
        else:
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

                result = self._pool.execute(
                    lambda client: client.audio.transcriptions.create(**kwargs)
                )
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
