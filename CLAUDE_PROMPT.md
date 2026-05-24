You are a senior Python automation engineer taking over a Google Voice realtime dispatch-agent project.

Goal: make the app reliable for live calls, not demos. Keep mock tests, but optimize the runtime path for a real logged-in Google Voice account, real Groq API calls, and real Windows audio routing.

Current state:
- Entry point: `python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 1`
- Realtime mode is default. Static WAV mode is only `--static-playback`.
- Connected calls use `ConversationLoop` with `AudioCapture -> EnergyVAD -> GroqWhisperSTT -> ConversationAgent -> RealtimeTTS`.
- Voicemail still uses a generated WAV fallback.
- Local-only files are ignored: `.env`, `dialer_config.json`, contacts, audio, logs, Chrome profiles.
- Tests currently pass.

Do this next:
1. Run a live smoke test against one safe test phone number.
2. Fix Google Voice selectors in `src/google_voice.py` based on the current DOM, especially dialpad open, number input, call button, hangup button, call timer, and voicemail cues.
3. Validate audio routing on Windows:
   - `LOOPBACK_DEVICE` / `--loopback-device` must be the playback device that Chrome receives as microphone input, usually `CABLE Input`.
   - `CAPTURE_DEVICE=default` should capture the prospect from speakers via WASAPI loopback. If echo is bad, document a dual-cable setup.
4. Improve live call stopping:
   - Stop realtime loop immediately when Google Voice ends.
   - Hang up cleanly after the agent says goodbye.
   - Record accurate session outcome.
5. Reduce latency:
   - Prefer edge-tts streaming if possible.
   - Keep STT chunks short and avoid responding to TTS echo.
   - Tune `VAD_THRESHOLD`, silence frames, and calibration.
6. Add a `--preflight` command that verifies `.env`, contacts file, Chrome profile path, Groq connectivity, audio devices, and Google Voice login before dialing.
7. Keep compliance guardrails visible: consent, DNC, recording laws, robocall/telemarketing rules, Google Voice terms.

Constraints:
- Do not commit secrets, contact lists, audio, logs, or browser profiles.
- Do not replace Selenium with Twilio or another telephony API.
- Keep changes small and testable.
- Preserve existing tests and add focused tests for any new runtime behavior.

When done, provide:
- Exact command to run one live test call.
- Audio routing instructions for the detected devices.
- Remaining risks that need human validation.
