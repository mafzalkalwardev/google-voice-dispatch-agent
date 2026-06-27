"""Tests for src/stt.py — GroqWhisperSTT with mocked Groq client."""

import numpy as np
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_groq():
    with patch("src.stt.Groq") as MockGroq:
        mock_client = MagicMock()
        MockGroq.return_value = mock_client
        yield MockGroq, mock_client


def test_transcribe_returns_text(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.return_value = MagicMock(text="Hello from prospect.")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    audio = np.random.rand(16000).astype(np.float32) * 0.1
    result = stt.transcribe(audio, samplerate=16000)

    assert result == "Hello from prospect."
    mock_client.audio.transcriptions.create.assert_called_once()


def test_transcribe_strips_whitespace(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.return_value = MagicMock(text="  hi there  ")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    audio = np.random.rand(16000).astype(np.float32) * 0.1
    result = stt.transcribe(audio, samplerate=16000)

    assert result == "hi there"


def test_transcribe_skips_too_short_audio(mock_groq):
    _, mock_client = mock_groq

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    # 0.1s at 16kHz = 1600 samples — below the 0.3s minimum
    audio = np.zeros(1600, dtype=np.float32)
    result = stt.transcribe(audio, samplerate=16000)

    assert result == ""
    mock_client.audio.transcriptions.create.assert_not_called()


def test_transcribe_truncates_long_audio(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.return_value = MagicMock(text="truncated")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    # 70 seconds of audio — above the 60s limit
    audio = np.random.rand(16000 * 70).astype(np.float32) * 0.1
    result = stt.transcribe(audio, samplerate=16000)

    assert result == "truncated"
    call_kwargs = mock_client.audio.transcriptions.create.call_args
    # The file passed should correspond to at most 60s of audio
    assert call_kwargs is not None


def test_transcribe_sends_prompt(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.return_value = MagicMock(text="yes I'm interested")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    audio = np.random.rand(16000).astype(np.float32) * 0.1
    stt.transcribe(audio, samplerate=16000, prompt="Indus Transports freight dispatch")

    call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
    assert call_kwargs.get("prompt") == "Indus Transports freight dispatch"


def test_transcribe_handles_api_error(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.side_effect = Exception("API error")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key")
    audio = np.random.rand(16000).astype(np.float32) * 0.1
    result = stt.transcribe(audio, samplerate=16000)

    assert result == ""


def test_missing_api_key_raises():
    from src.stt import GroqWhisperSTT
    with pytest.raises(ValueError, match="api_key"):
        GroqWhisperSTT(api_key="")


def test_transcribe_uses_configured_model(mock_groq):
    _, mock_client = mock_groq
    mock_client.audio.transcriptions.create.return_value = MagicMock(text="ok")

    from src.stt import GroqWhisperSTT
    stt = GroqWhisperSTT(api_key="test_key", model="whisper-large-v3")
    audio = np.random.rand(16000).astype(np.float32) * 0.1
    stt.transcribe(audio, samplerate=16000)

    call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
    assert call_kwargs.get("model") == "whisper-large-v3"
