"""
Audio loopback injection for Google Voice calls on Windows.

Setup:
  1. Install VB-CABLE from https://vb-audio.com/Cable/
  2. Set "CABLE Input" as a playback/output device.
  3. Set "CABLE Output" as the microphone/input Chrome uses for Google Voice.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("GoogleVoiceAgent")


def list_audio_devices() -> list[dict]:
    """Return all audio devices visible to sounddevice."""
    try:
        import sounddevice as sd

        return [
            {
                "index": i,
                "name": d["name"],
                "max_input_channels": d["max_input_channels"],
                "max_output_channels": d["max_output_channels"],
                "default_samplerate": int(d["default_samplerate"]),
            }
            for i, d in enumerate(sd.query_devices())
        ]
    except ImportError:
        logger.warning("sounddevice not installed - run: pip install sounddevice soundfile")
        return []


def find_loopback_devices(name_hint: str = "CABLE Input") -> list[int]:
    """Return all output device indexes matching name_hint, in discovery order."""
    matches = []
    for dev in list_audio_devices():
        if name_hint.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
            matches.append(dev["index"])
            logger.debug("Loopback device candidate: [%d] %s", dev["index"], dev["name"])
    return matches


def find_loopback_device(name_hint: str = "CABLE Input") -> Optional[int]:
    """Return the first matching output device index, or None."""
    matches = find_loopback_devices(name_hint)
    if matches:
        return matches[0]
    logger.debug("No loopback device matching '%s'", name_hint)
    return None


def probe_output_device(device_index: int, duration_s: float = 0.03) -> tuple[bool, str]:
    """
    Open an output stream and write a tiny silent buffer.
    This catches "device unavailable" before the app starts dialing.
    """
    try:
        import numpy as np
        import sounddevice as sd

        device = sd.query_devices(device_index)
        channels = max(1, min(int(device.get("max_output_channels") or 1), 2))
        samplerate = int(device.get("default_samplerate") or 44100)
        frames = max(1, int(samplerate * duration_s))
        silence = np.zeros((frames, channels), dtype="float32")
        with sd.OutputStream(
            device=device_index,
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
        ) as stream:
            stream.write(silence)
        return True, f"device [{device_index}] opened at {samplerate} Hz"
    except Exception as exc:
        return False, str(exc)


def find_playable_loopback_device(name_hint: str = "CABLE Input") -> Optional[int]:
    """Return the first matching output device that can actually be opened."""
    for device_index in find_loopback_devices(name_hint):
        ok, detail = probe_output_device(device_index)
        if ok:
            logger.info("Loopback device ready: %s", detail)
            return device_index
        logger.warning("Loopback device [%d] is not playable: %s", device_index, detail)
    return None


def _device_default_samplerate(sd, device_index: int, fallback: int) -> int:
    try:
        device = sd.query_devices(device_index)
        return int(device.get("default_samplerate") or fallback)
    except Exception:
        return fallback


def _device_output_channels(sd, device_index: int) -> int:
    """Return a conservative output channel count for a PortAudio device."""
    try:
        device = sd.query_devices(device_index)
        return max(1, min(int(device.get("max_output_channels") or 1), 2))
    except Exception:
        return 1


def _match_output_channels(data, channels: int):
    """Return float32 audio shaped as (frames, channels)."""
    import numpy as np

    arr = np.asarray(data, dtype="float32")
    if arr.ndim == 0:
        arr = arr.reshape(1, 1)
    elif arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    elif arr.ndim > 2:
        arr = arr.reshape(arr.shape[0], -1)

    if arr.shape[1] == channels:
        return np.ascontiguousarray(arr, dtype="float32")

    if arr.shape[1] > channels:
        if channels == 1:
            arr = arr.mean(axis=1, keepdims=True)
        else:
            arr = arr[:, :channels]
        return np.ascontiguousarray(arr, dtype="float32")

    mono = arr.mean(axis=1, keepdims=True)
    return np.ascontiguousarray(
        np.repeat(mono, channels, axis=1),
        dtype="float32",
    )


def _resample_audio(data, source_rate: int, target_rate: int):
    if int(source_rate) == int(target_rate):
        return data

    try:
        import numpy as np
    except ImportError:
        logger.warning(
            "numpy not available; playing at source sample rate %s instead of %s",
            source_rate,
            target_rate,
        )
        return data

    arr = np.asarray(data, dtype="float32")
    if arr.size == 0:
        return arr

    duration = len(arr) / float(source_rate)
    target_len = max(1, int(round(duration * target_rate)))
    old_x = np.linspace(0.0, duration, num=len(arr), endpoint=False)
    new_x = np.linspace(0.0, duration, num=target_len, endpoint=False)

    if arr.ndim == 1:
        return np.interp(new_x, old_x, arr).astype("float32")

    channels = [
        np.interp(new_x, old_x, arr[:, i]).astype("float32")
        for i in range(arr.shape[1])
    ]
    return np.stack(channels, axis=1)


def _stream_audio_to_device(
    data,
    samplerate: int,
    device_index: int,
    block: bool = True,
) -> None:
    """Write numpy-compatible audio to an explicit sounddevice OutputStream."""
    import sounddevice as sd

    target_rate = _device_default_samplerate(sd, device_index, int(samplerate))
    if target_rate != int(samplerate):
        data = _resample_audio(data, int(samplerate), target_rate)
    channels = _device_output_channels(sd, device_index)
    data = _match_output_channels(data, channels)

    def _write() -> None:
        with sd.OutputStream(
            device=device_index,
            samplerate=target_rate,
            channels=channels,
            dtype="float32",
        ) as stream:
            stream.write(data)

    if block:
        _write()
    else:
        threading.Thread(target=_write, daemon=True, name="AudioDeviceWriter").start()


def play_wav_to_device(
    wav_path: str | Path,
    device_index: int,
    block: bool = True,
) -> float:
    """
    Play a WAV file to a specific output device by index.
    Returns playback duration in seconds.
    """
    import sounddevice as sd
    import soundfile as sf

    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio file not found: {wav_path}")

    data, samplerate = sf.read(str(wav_path), dtype="float32")
    duration = len(data) / samplerate
    playback_rate = _device_default_samplerate(sd, device_index, int(samplerate))

    logger.info(
        "Playing %.1fs -> device [%d] at %s Hz: %s",
        duration,
        device_index,
        playback_rate,
        wav_path.name,
    )
    _stream_audio_to_device(data, int(samplerate), device_index, block=block)
    return duration


def _play_default_output(wav_path: str | Path) -> float:
    try:
        import sounddevice as sd
        import soundfile as sf

        data, samplerate = sf.read(str(wav_path), dtype="float32")
        sd.play(data, samplerate=samplerate)
        sd.wait()
        return len(data) / samplerate
    except ImportError:
        return _play_ffplay_fallback(wav_path)


def play_wav_loopback(
    wav_path: str | Path,
    device_hint: str = "CABLE Input",
    fallback_to_default: bool = False,
) -> float:
    """
    Play a WAV file to the virtual loopback cable so it appears as mic input.
    """
    errors = []
    for device_index in find_loopback_devices(device_hint):
        try:
            return play_wav_to_device(wav_path, device_index, block=True)
        except Exception as exc:
            errors.append(f"[{device_index}] {exc}")
            logger.warning(
                "Loopback device [%d] failed, trying next match: %s",
                device_index,
                exc,
            )

    detail = "; ".join(errors) if errors else "no matching output device"
    if fallback_to_default:
        logger.warning(
            "Loopback device '%s' not available (%s) - playing to system default.",
            device_hint,
            detail,
        )
        return _play_default_output(wav_path)

    raise RuntimeError(
        f"Loopback device '{device_hint}' not available: {detail}\n"
        "Install or repair VB-CABLE from https://vb-audio.com/Cable/, "
        "or pass --loopback-device with the exact output device name."
    )


def _play_ffplay_fallback(wav_path: str | Path) -> float:
    """Use ffplay as a last resort if sounddevice is unavailable."""
    ffplay = shutil.which("ffplay")
    if not ffplay:
        raise RuntimeError(
            "Neither sounddevice nor ffplay is available. "
            "Run: pip install sounddevice soundfile"
        )

    wav_path = Path(wav_path)
    try:
        import soundfile as sf

        duration = sf.info(str(wav_path)).duration
    except Exception:
        duration = 0.0

    subprocess.call([ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(wav_path)])
    return duration


def print_devices() -> None:
    devices = list_audio_devices()
    if not devices:
        print("No devices found. Install sounddevice: pip install sounddevice soundfile")
        return

    print(f"{'Idx':>4}  {'Name':<45}  {'In':>3}  {'Out':>3}  {'Rate':>6}")
    print("-" * 68)
    for d in devices:
        print(
            f"{d['index']:>4}  {d['name']:<45}  "
            f"{d['max_input_channels']:>3}  {d['max_output_channels']:>3}  "
            f"{d['default_samplerate']:>6}"
        )


if __name__ == "__main__":
    print_devices()
