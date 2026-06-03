"""TTS voice consistency — one neural voice per call."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.tts_cache import TTSCache


def test_tts_cache_ensure_stores_phrase() -> None:
    cache = TTSCache(tts_voice="en-US-GuyNeural")
    with patch("edge_tts.Communicate") as mock_comm:
        instance = MagicMock()

        async def _stream():
            yield {"type": "audio", "data": b"\x00\x01"}

        instance.stream = _stream
        mock_comm.return_value = instance
        ok = cache.ensure("Hey, this is Tony.")
    assert ok is True
    assert cache.get("Hey, this is Tony.") == b"\x00\x01"


def test_realtime_tts_prewarm_line_delegates_to_cache() -> None:
    from src.realtime_tts import RealtimeTTS

    tts = RealtimeTTS(device_index=0, use_cache=True, allow_sapi_fallback=False)
    tts._cache = MagicMock()
    tts.prewarm_line("Hello there.")
    tts._cache.ensure.assert_called_once_with("Hello there.")


def test_realtime_tts_skips_sapi_when_fallback_disabled() -> None:
    from src.realtime_tts import RealtimeTTS

    tts = RealtimeTTS(device_index=0, use_edge_tts=True, use_cache=False, allow_sapi_fallback=False)
    tts._use_edge = True
    with patch("src.realtime_tts._edge_synthesize", side_effect=RuntimeError("network")):
        with patch("src.realtime_tts._pyttsx3_to_device") as mock_sapi:
            tts.speak("Test line.")
    mock_sapi.assert_not_called()


def test_save_edge_tts_wav_writes_file(tmp_path: Path) -> None:
    from src.realtime_tts import save_edge_tts_wav

    data = np.zeros(4800, dtype=np.float32)
    with patch("src.realtime_tts._edge_synthesize", return_value=(data, 24000)):
        out = save_edge_tts_wav("Voicemail test.", tmp_path / "vm.wav")
    assert out.exists()
    assert out.stat().st_size > 0
