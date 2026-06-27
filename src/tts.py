import logging
from pathlib import Path

import pyttsx3

logger = logging.getLogger("GoogleVoiceAgent")

# Preferred voice keywords in priority order (Windows SAPI voices)
_PREFERRED_VOICE_KEYWORDS = ["Zira", "David", "Mark", "English"]
_EDGE_VOICE = "en-US-GuyNeural"


def _pick_voice(engine: pyttsx3.Engine) -> None:
    voices = engine.getProperty("voices")
    for keyword in _PREFERRED_VOICE_KEYWORDS:
        for voice in voices:
            if keyword.lower() in voice.name.lower():
                engine.setProperty("voice", voice.id)
                return
    # Fallback: first available voice
    if voices:
        engine.setProperty("voice", voices[0].id)


def save_text_to_speech(text: str, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return _save_with_edge_tts(text, output_path)
    except Exception as exc:
        logger.warning("edge-tts file synthesis failed (%s); falling back to pyttsx3", exc)

    return _save_with_pyttsx3(text, output_path)


def _save_with_edge_tts(text: str, output_path: Path) -> Path:
    import soundfile as sf

    from src.realtime_tts import _edge_synthesize

    data, samplerate = _edge_synthesize(text, _EDGE_VOICE)
    sf.write(str(output_path), data, samplerate, subtype="PCM_16")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"edge-tts produced no output at {output_path}")

    logger.info(
        "TTS file generated: %s (%d bytes, engine=edge-tts)",
        output_path,
        output_path.stat().st_size,
    )
    return output_path


def _save_with_pyttsx3(text: str, output_path: Path) -> Path:
    engine = pyttsx3.init()
    _pick_voice(engine)
    engine.setProperty("rate", 155)   # slightly slower than default for clarity
    engine.setProperty("volume", 1.0)

    engine.save_to_file(text, str(output_path))
    engine.runAndWait()
    engine.stop()

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"TTS produced no output at {output_path}")

    logger.info("TTS file generated: %s (%d bytes, engine=pyttsx3)", output_path, output_path.stat().st_size)
    return output_path


def ensure_audio_dir(path: str | Path) -> Path:
    output_path = Path(path)
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path
