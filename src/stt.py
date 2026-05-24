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
import wave
from typing import Optional

import numpy as np
from groq import Groq

logger = logging.getLogger("GoogleVoiceAgent")

_MIN_DURATION_S = 0.3     # drop segments shorter than this (likely noise clicks)
_MAX_DURATION_S = 60.0    # Groq Whisper max; truncate longer segments


class GroqWhisperSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "whisper-large-v3-turbo",
        language: str = "en",
    ):
        if not api_key:
            raise ValueError("api_key is required for GroqWhisperSTT")
        self._client = Groq(api_key=api_key)
        self.model = model
        self.language = language

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
        prompt     — optional text hint to improve accuracy (e.g. company name)

        Returns the transcript string, or "" if too short / empty result.
        """
        duration = len(audio) / max(samplerate, 1)

        if duration < _MIN_DURATION_S:
            logger.debug("STT skip: too short (%.2fs)", duration)
            return ""

        if duration > _MAX_DURATION_S:
            logger.warning("STT: truncating %.1fs audio to %.0fs", duration, _MAX_DURATION_S)
            audio = audio[: int(_MAX_DURATION_S * samplerate)]

        wav_bytes = _float32_to_wav(audio, samplerate)

        try:
            kwargs: dict = dict(
                file=("audio.wav", wav_bytes, "audio/wav"),
                model=self.model,
                language=self.language,
                response_format="text",
            )
            if prompt:
                kwargs["prompt"] = prompt

            result = self._client.audio.transcriptions.create(**kwargs)
            text = getattr(result, "text", result)
            return str(text).strip()
        except Exception as exc:
            logger.error("Groq STT error: %s", exc)
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
