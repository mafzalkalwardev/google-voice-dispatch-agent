"""
Low-latency TTS routed directly to a sounddevice output device (CABLE Input).

Engine priority:
  1. edge-tts  — Microsoft Neural TTS (internet required, ~400 ms latency, high quality)
  2. pyttsx3   — Windows SAPI TTS (offline, ~800 ms latency, adequate quality)

The best voice for Tony is "en-US-GuyNeural" (edge-tts).
Fallback SAPI voice: first English male found, else Zira.

Usage:
    tts = RealtimeTTS(device_index=1, voice="en-US-GuyNeural")
    tts.speak("Hi, this is Tony from Indus Transports.")    # blocks
    t = tts.speak_async("Are you still dispatching solo?")  # non-blocking thread
    tts.stop()    # interrupt immediately
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("GoogleVoiceAgent")

VOICE_GUY       = "en-US-GuyNeural"          # primary — natural male
VOICE_CHRIS     = "en-US-ChristopherNeural"   # backup male


# ------------------------------------------------------------------ #
# Main public class
# ------------------------------------------------------------------ #

class RealtimeTTS:
    """Thread-safe TTS that routes synthesised speech to a sounddevice output."""

    def __init__(
        self,
        device_index: int,
        voice: str = VOICE_GUY,
        use_edge_tts: bool = True,
    ):
        self.device_index = device_index
        self.voice = voice
        self._use_edge = use_edge_tts and _edge_available()
        self._lock = threading.Lock()
        self._speaking = threading.Event()

        engine = "edge-tts" if self._use_edge else "pyttsx3"
        from src.voice_playback import describe_audio_device

        logger.info(
            "RealtimeTTS: engine=%s voice=%s selected_output_device=%s",
            engine,
            voice,
            describe_audio_device(device_index),
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def speak(self, text: str, interrupt: bool = True) -> None:
        """Synthesise and play synchronously. Blocks until playback completes."""
        if not text.strip():
            logger.info("RealtimeTTS: empty text skipped")
            return
        if interrupt:
            self.stop()
        with self._lock:
            self._speaking.set()
            try:
                self._play(text)
            finally:
                self._speaking.clear()

    def speak_async(self, text: str, interrupt: bool = True) -> threading.Thread:
        """Synthesise and play in a daemon thread. Returns the thread."""
        if interrupt and self.is_speaking():
            self.stop()
        t = threading.Thread(target=self.speak, args=(text, False), daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        """Stop any in-progress playback immediately."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self._speaking.clear()

    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _play(self, text: str) -> None:
        if self._use_edge:
            try:
                logger.info("RealtimeTTS: generating speech with edge-tts (%d chars)", len(text))
                data, rate = _edge_synthesize(text, self.voice)
            except Exception as exc:
                logger.warning("edge-tts synthesis failed (%s); falling back to pyttsx3", exc)
            else:
                duration = len(data) / float(rate) if rate else 0.0
                logger.info(
                    "RealtimeTTS: audio generated in memory (engine=edge-tts, rate=%d, duration=%.2fs)",
                    rate,
                    duration,
                )
                _play_numpy_to_device(data, rate, self.device_index)
                return
        logger.info("RealtimeTTS: generating fallback TTS WAV with pyttsx3 (%d chars)", len(text))
        _pyttsx3_to_device(text, self.device_index)


def validate_tts_output_device(device_index: int) -> None:
    """Raise a clear error if the configured output device cannot be opened."""
    from src.voice_playback import probe_output_device

    ok, detail = probe_output_device(device_index)
    if not ok:
        raise RuntimeError(
            f"TTS output device [{device_index}] is unavailable: {detail}. "
            "Pick a playable CABLE Input/output device from the Audio Devices page."
        )


# ------------------------------------------------------------------ #
# edge-tts helpers
# ------------------------------------------------------------------ #

def _edge_available() -> bool:
    try:
        import edge_tts   # noqa: F401
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


def _edge_synthesize(text: str, voice: str) -> tuple[np.ndarray, int]:
    """Call edge-tts and decode the returned MP3 to a float32 numpy array."""
    import asyncio
    import edge_tts

    async def _gather() -> bytes:
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks)

    mp3_bytes = asyncio.run(_gather())
    return _decode_mp3(mp3_bytes)


def _decode_mp3(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """
    Decode MP3 bytes to float32 numpy.
    Tries soundfile first (works if libsndfile ≥ 1.1.0 built with MP3).
    Falls back to an ffmpeg subprocess if not.
    """
    import soundfile as sf

    try:
        data, rate = sf.read(io.BytesIO(mp3_bytes), dtype="float32")
        return data, rate
    except Exception:
        pass

    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "edge-tts returned MP3 but soundfile cannot decode it and ffmpeg is missing. "
            "Install ffmpeg (https://ffmpeg.org) or use pyttsx3 engine instead."
        )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3f:
        mp3f.write(mp3_bytes)
        mp3_path = Path(mp3f.name)

    wav_path = mp3_path.with_suffix(".wav")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", str(mp3_path), "-ar", "24000", "-ac", "1", str(wav_path)],
            capture_output=True, check=True,
        )
        import soundfile as sf
        data, rate = sf.read(str(wav_path), dtype="float32")
        return data, rate
    finally:
        mp3_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)


# ------------------------------------------------------------------ #
# pyttsx3 fallback
# ------------------------------------------------------------------ #

def _pyttsx3_to_device(text: str, device_index: int) -> None:
    """Synthesise via pyttsx3 to a temp WAV, then play to target device."""
    from src.tts import save_text_to_speech
    from src.voice_playback import play_wav_to_device

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        save_text_to_speech(text, tmp)
        logger.info("RealtimeTTS: TTS file generated for playback: %s", tmp)
        play_wav_to_device(tmp, device_index, block=True)
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ #
# Shared playback util
# ------------------------------------------------------------------ #

def _play_numpy_to_device(data: np.ndarray, samplerate: int, device_index: int) -> None:
    from src.voice_playback import _stream_audio_to_device

    _stream_audio_to_device(data.astype(np.float32), samplerate, device_index, block=True)
