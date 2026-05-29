from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

import numpy as np

from src.call_session import CallSession, CallState
from src.conversation_loop import ConversationLoop
from src.paths import runtime_base
from src.vad import VADConfig

logger = logging.getLogger("GoogleVoiceAgent")

_SAMPLERATE = 16000
_FRAME_MS = 30
_FRAME_SIZE = int(_SAMPLERATE * _FRAME_MS / 1000)


class ScriptedAudioCapture:
    """AudioCapture-compatible fake that injects carrier speech frames."""

    def __init__(
        self,
        device_name_hint: str = "simulated",
        samplerate: int = _SAMPLERATE,
        utterance_seconds: tuple[float, ...] = (0.9, 1.0, 1.1),
    ):
        self.device_name_hint = device_name_hint
        self.samplerate = samplerate
        self.frame_ms = _FRAME_MS
        self.frame_size = int(samplerate * self.frame_ms / 1000)
        self.selected_device_index = 0
        self.selected_device_name = "Simulated carrier audio"
        self.capture_mode = "scripted_fake"
        self.last_error = None
        self._utterance_seconds = utterance_seconds
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames_captured = 0
        self.frames_dropped = 0

    def start(self) -> None:
        self._stop.clear()
        self._ready.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ScriptedAudioCapture")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._ready.wait(timeout)

    def read(self, timeout: float = 0.1):
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def clear(self) -> int:
        count = 0
        while True:
            try:
                self._q.get_nowait()
                count += 1
            except queue.Empty:
                break
        return count

    def diagnostics(self) -> dict:
        return {
            "device_hint": self.device_name_hint,
            "selected_device_index": self.selected_device_index,
            "selected_device_name": self.selected_device_name,
            "capture_mode": self.capture_mode,
            "samplerate": self.samplerate,
            "frame_ms": self.frame_ms,
            "frames_captured": self.frames_captured,
            "frames_dropped": self.frames_dropped,
            "queue_size": self._q.qsize(),
            "ready": self._ready.is_set(),
            "last_error": "",
        }

    def _run(self) -> None:
        self._inject_silence(0.7)
        for seconds in self._utterance_seconds:
            if self._stop.is_set():
                return
            self._inject_silence(0.35)
            self._inject_speech(seconds)
            self._inject_silence(1.05)
        while not self._stop.wait(0.1):
            self._inject_silence(0.1)

    def _put(self, frame: np.ndarray) -> None:
        if self._stop.is_set():
            return
        try:
            self._q.put(frame.astype(np.float32), timeout=0.2)
            self.frames_captured += 1
        except queue.Full:
            self.frames_dropped += 1

    def _inject_silence(self, seconds: float) -> None:
        frames = max(1, int(seconds * 1000 / self.frame_ms))
        for _ in range(frames):
            if self._stop.is_set():
                return
            self._put(np.zeros(self.frame_size, dtype=np.float32))
            time.sleep(self.frame_ms / 1000.0)

    def _inject_speech(self, seconds: float) -> None:
        frames = max(1, int(seconds * 1000 / self.frame_ms))
        t = np.arange(self.frame_size, dtype=np.float32) / float(self.samplerate)
        for idx in range(frames):
            if self._stop.is_set():
                return
            tone = np.sin(2 * np.pi * (220 + idx) * t).astype(np.float32)
            noise = (np.random.rand(self.frame_size).astype(np.float32) - 0.5) * 0.01
            self._put((tone * 0.08 + noise).astype(np.float32))
            time.sleep(self.frame_ms / 1000.0)


class FakeSTT:
    def __init__(self):
        self._texts = queue.Queue()
        for text in (
            "I run a dry van out of Dallas.",
            "Mostly OTR lanes, sometimes Midwest.",
            "What do you charge for dispatch?",
        ):
            self._texts.put(text)
        self.last_empty_reason = ""

    def transcribe(self, audio, samplerate: int = _SAMPLERATE, prompt: str | None = None) -> str:
        try:
            text = self._texts.get_nowait()
        except queue.Empty:
            self.last_empty_reason = "script exhausted"
            return ""
        logger.info("[SIM] STT -> %s", text)
        return text


class FakeAgent:
    def __init__(self):
        self._replies = [
            "Great, are you mainly running dry van loads regionally or over the road?",
            "That works. Which lanes do you like most when rates are decent?",
            "We tailor the percentage by setup, but a quick onboarding call gets exact terms.",
        ]
        self._turn = 0

    def opening_line(self) -> str:
        return "Hi, this is Tony with Indus Transports. How are you doing today?"

    def respond_to(self, text: str) -> str:
        reply = self._replies[min(self._turn, len(self._replies) - 1)]
        self._turn += 1
        logger.info("[SIM] LLM -> %s", reply)
        return reply

    def should_end_call(self) -> bool:
        return self._turn >= 3

    def goodbye_line(self) -> str:
        return "Thanks, I appreciate your time."


class FakeTTS:
    def __init__(self):
        self._speaking = threading.Event()
        self.spoken: list[str] = []

    def speak(self, text: str, interrupt: bool = True) -> None:
        self._speaking.set()
        self.spoken.append(text)
        logger.info("[SIM] TTS playing -> %s", text)
        time.sleep(min(0.35, max(0.05, len(text) / 300.0)))
        self._speaking.clear()

    def speak_async(self, text: str, interrupt: bool = True) -> threading.Thread:
        thread = threading.Thread(target=self.speak, args=(text, interrupt), daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._speaking.clear()

    def is_speaking(self) -> bool:
        return self._speaking.is_set()


def run_simulated_conversation(logger: logging.Logger | None = None) -> dict:
    log = logger or logging.getLogger("GoogleVoiceAgent")
    base = runtime_base()
    ts = time.strftime("%Y%m%d_%H%M%S")
    transcript = base / "logs" / "simulated_realtime" / f"transcript_{ts}.txt"
    recording = base / "logs" / "simulated_realtime" / f"recording_{ts}.wav"
    diagnostics = base / "logs" / "simulated_realtime" / f"diagnostics_{ts}.json"

    session = CallSession(phone="+15550000000", contact_name="Simulated Carrier")
    session.transition(CallState.DIALING, "simulation")
    session.transition(CallState.RINGING, "simulation")
    session.transition(CallState.CONNECTED, "simulation")

    vad_cfg = VADConfig(
        speech_threshold=0.015,
        speech_trigger_frames=2,
        silence_trigger_frames=8,
        max_speech_seconds=5.0,
        pre_speech_pad_frames=2,
    )
    loop = ConversationLoop(
        capture_device_hint="simulated",
        tts=FakeTTS(),
        agent=FakeAgent(),
        stt=FakeSTT(),
        vad_config=vad_cfg,
        calibrate_frames=4,
        transcript_path=transcript,
        recording_path=recording,
        listen_after_tts_delay_ms=50,
        min_speech_seconds=0.2,
        max_silence_seconds=2.0,
        silence_does_not_end_call=True,
        capture_rms_log_interval_seconds=0.5,
        realtime_debug=True,
        diagnostics_path=diagnostics,
        capture_factory=ScriptedAudioCapture,
    )
    log.info("[SIM] Starting local realtime simulation")
    loop.run(session=session, auto_opening=True)
    log.info("[SIM] Finished local realtime simulation transcript=%s diagnostics=%s", transcript, diagnostics)
    return {
        "turns": loop.diagnostics_snapshot()["loop_iteration"],
        "transcript_path": str(transcript),
        "recording_path": str(recording),
        "diagnostics_path": str(diagnostics),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = run_simulated_conversation()
    print(result)


if __name__ == "__main__":
    main()
