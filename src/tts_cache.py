"""
TTS pre-warming cache.

Pre-generates audio for common short phrases used at call start (greetings,
filler phrases, fallbacks) so that the very first words of a connected call
play instantly rather than waiting for an edge-tts network round-trip.

Usage:
    cache = TTSCache(tts_voice="en-US-GuyNeural")
    cache.warm()                          # run at startup / before dialing
    audio = cache.get("Sorry, say that again?")  # bytes or None if miss
"""

from __future__ import annotations

import asyncio
import io
import logging
import random
import threading
from typing import Dict, Optional

logger = logging.getLogger("GoogleVoiceAgent")

# Common phrases to pre-generate at startup.
# Keep this list short (< 20 entries) so warmup completes quickly.
FILLER_PHRASES = [
    "Got it.",
    "Sure.",
    "Mm-hmm.",
    "Right.",
    "One sec.",
    "Okay.",
]

_WARMUP_PHRASES = [
    # Filler / thinking acknowledgements
    *FILLER_PHRASES,
    "Absolutely.",
    "Of course.",
    # Mishear fallbacks
    "Sorry, could you say that again?",
    "I didn't catch that — could you repeat?",
    # Common objection starters
    "No problem at all.",
    "I understand.",
    "That makes sense.",
    # Common goodbye
    "Thanks for your time, take care!",
    "Have a great day!",
]


class TTSCache:
    """
    Thread-safe in-memory cache of pre-generated TTS audio bytes.

    The cache uses the phrase text as key (case-insensitive, stripped).
    Audio is stored as raw MP3 bytes as returned by edge-tts.
    """

    def __init__(self, tts_voice: str = "en-US-GuyNeural"):
        self.tts_voice = tts_voice
        self._cache: Dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._warming = False

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def warm(self, extra_phrases: Optional[list[str]] = None) -> None:
        """
        Pre-generate audio for all warmup phrases.
        Runs in a background thread so it does not block startup.
        """
        phrases = list(_WARMUP_PHRASES)
        if extra_phrases:
            phrases.extend(extra_phrases)

        thread = threading.Thread(
            target=self._warm_background,
            args=(phrases,),
            daemon=True,
            name="TTSCacheWarm",
        )
        thread.start()
        logger.info("[TTS_CACHE] warmup started in background (%d phrases)", len(phrases))

    def get(self, text: str) -> Optional[bytes]:
        """Return cached MP3 bytes for the given phrase, or None on cache miss."""
        key = text.strip().lower()
        with self._lock:
            return self._cache.get(key)

    def put(self, text: str, audio_bytes: bytes) -> None:
        """Store audio bytes under the normalised phrase key."""
        key = text.strip().lower()
        with self._lock:
            self._cache[key] = audio_bytes

    def ensure(self, text: str) -> bool:
        """Synchronously generate and cache one phrase (same voice as warmup). Returns True if cached."""
        phrase = text.strip()
        if not phrase:
            return False
        if self.get(phrase):
            return True
        try:
            import edge_tts  # type: ignore
        except ImportError:
            return False

        async def _one() -> bytes:
            communicate = edge_tts.Communicate(phrase, self.tts_voice)
            buf = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
            return buf.getvalue()

        try:
            loop = asyncio.new_event_loop()
            try:
                audio_bytes = loop.run_until_complete(_one())
            finally:
                loop.close()
            if audio_bytes:
                self.put(phrase, audio_bytes)
                logger.info("[TTS_CACHE] pre-cached call phrase (%d bytes): %s", len(audio_bytes), phrase[:60])
                return True
        except Exception as exc:
            logger.warning("[TTS_CACHE] could not pre-cache phrase: %s", exc)
        return False

    def size(self) -> int:
        """Number of cached phrases."""
        with self._lock:
            return len(self._cache)

    def random_filler(self) -> str:
        return random.choice(FILLER_PHRASES)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _warm_background(self, phrases: list[str]) -> None:
        """Background thread: generate TTS for every phrase not yet cached."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._warm_async(phrases))
            loop.close()
        except Exception as exc:
            logger.warning("[TTS_CACHE] warmup error: %s", exc)

    async def _warm_async(self, phrases: list[str]) -> None:
        try:
            import edge_tts  # type: ignore
        except ImportError:
            logger.warning("[TTS_CACHE] edge-tts not installed; cache warmup skipped")
            return

        generated = 0
        for phrase in phrases:
            key = phrase.strip().lower()
            with self._lock:
                if key in self._cache:
                    continue
            try:
                communicate = edge_tts.Communicate(phrase, self.tts_voice)
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                audio_bytes = buf.getvalue()
                if audio_bytes:
                    self.put(phrase, audio_bytes)
                    generated += 1
            except Exception as exc:
                logger.debug("[TTS_CACHE] failed to generate '%s': %s", phrase[:40], exc)

        logger.info(
            "[TTS_CACHE] warmup complete: %d/%d phrases generated (cache size=%d)",
            generated,
            len(phrases),
            self.size(),
        )
