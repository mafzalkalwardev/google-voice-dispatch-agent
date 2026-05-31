# GoogleVoiceAgent-Active

Live AI calling agent for Google Voice. It opens Google Voice in Chrome, dials contacts from a local CRM file, waits for real call evidence, speaks through a virtual audio cable, listens to the call audio, transcribes speech, generates replies with Groq, and writes call results back to logs.

This project is built as a practical console-first automation tool. A FastAPI web console is included for monitoring and configuration, but the main operating path is still the CLI.

## Important Notice

This software can place real phone calls. You are responsible for consent, caller-ID rules, Do Not Call rules, recording disclosure, spam prevention, local law, carrier terms, Google Voice terms, and any business compliance requirements. Test only with numbers you own or have permission to call.

Google Voice is not a carrier-grade outbound calling API. For 24/7 production calling, expect browser session expiry, rate limits, UI changes, audio-device drift, and account risk if calls are too frequent or too repetitive.

## What It Does

- Uses Selenium to control the Google Voice web app.
- Uses Chrome profile persistence so Google login can survive across runs.
- Dials contacts from CSV/CRM data.
- Waits for actual connected-call evidence before the AI speaks.
- Tracks call state with `CallState`: ringing, connected, voicemail, ended, failed.
- Supports realtime voice conversation using STT, LLM, and TTS.
- Detects voicemail after connection using DOM cues and audio classifier evidence.
- Can leave a voicemail message when voicemail is confirmed.
- Logs call outcomes, transcripts, diagnostics, and CRM updates.
- Includes a web console for settings, CRM review, logs, and manual controls.

## Current Ringing Safety

The call-state logic is intentionally conservative:

- Ringing does not instantly end just because a page banner appears.
- `min_ring_seconds` is respected before accepting connected, ended, or voicemail transitions.
- Voicemail DOM cues are ignored while the call is still only ringing.
- Connected state requires real evidence, such as call timer or active call controls.
- The AI opening line is only played after the call is confirmed connected.
- Silence during ringing does not end the call when `silence_does_not_end_call=True`.
- `max_ring_seconds` ends the attempt as no answer only after the configured ring window.

Detailed state logs include the current state, elapsed ringing time, DOM cues, audio classifier result, and timeout reason.

## Architecture

Core files:

- `src/main.py` - CLI entry point, batch runner, safe test mode, preflight, and call orchestration.
- `src/google_voice.py` - Google Voice browser automation and call-state detection.
- `src/call_session.py` - call state/session model.
- `src/conversation_loop.py` - realtime audio capture, VAD/silence handling, STT, LLM reply flow, and TTS playback.
- `src/voicemail_detector.py` - audio-based voicemail/greeting classifier.
- `src/agent_core.py` - AI response generation and conversation behavior.
- `src/config.py` - environment and JSON configuration loading.
- `src/web_app.py` - FastAPI web console and settings routes.
- `tests/` - automated tests for state handling, mock dialing, CRM, config, and audio helpers.

High-level flow:

1. Load `.env`, `dialer_config.json`, and CRM/contact data.
2. Run preflight checks for API keys, Chrome profile, contacts, and audio devices.
3. Open Google Voice with the configured Chrome profile.
4. Dial one contact.
5. Poll `detect_call_state()` until connected, voicemail, no answer, ended, or failed.
6. If connected, start `ConversationLoop`.
7. If voicemail is confirmed, play configured voicemail message.
8. Write logs, transcripts, status, and CRM updates.
9. Cool down, then continue to the next contact.

## Requirements

- Windows recommended.
- Python 3.10+.
- Google Chrome.
- A Google Voice account already logged in through the configured Chrome profile.
- VB-CABLE or equivalent virtual audio device.
- Groq API key.
- Microphone/audio routing configured so Chrome can receive the TTS audio and the agent can capture call audio.

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Create local config:

```powershell
Copy-Item .env.example .env
Copy-Item dialer_config.example.json dialer_config.json
```

Then edit `.env` and `dialer_config.json`.

## Key Configuration

Common `.env` values:

```env
GROQ_API_KEY=your_key_here
GOOGLE_VOICE_URL=https://voice.google.com/u/0/calls
CHROME_PROFILE_DIR=C:\Users\you\AppData\Local\Google\Chrome\User Data\GoogleVoiceAgent
PLAYBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
```

Common `dialer_config.json` values:

```json
{
  "min_ring_seconds": 2,
  "max_ring_seconds": 45,
  "voicemail_detect_seconds": 15,
  "silence_does_not_end_call": true,
  "call_cooldown_seconds": 10,
  "tts_warmup": true,
  "stt_retry_count": 2,
  "vad_silence_frames": 12,
  "vad_speech_frames": 2
}
```

For 24/7 calling, keep `min_ring_seconds` above zero and use a cooldown between calls. Do not run rapid retry loops against Google Voice.

## Audio Setup

Minimum VB-CABLE setup:

- Set the agent playback device to `CABLE Input`.
- Set Chrome microphone input to `CABLE Output`.
- Let Chrome speaker output play the call audio to a device the agent can capture.

Recommended 24/7 setup:

- Use separate virtual routes for TTS output and call capture.
- Avoid capturing the same speaker output that plays the agent voice.
- Keep Windows default devices stable and disable unused audio devices if they cause drift.
- Run `--list-audio-devices` after every driver or Windows audio change.

Commands:

```powershell
python -m src.main --list-audio-devices
python -m src.main --audio-route-test
python -m src.main --preflight
```

If `CAPTURE_DEVICE=default`, Windows WASAPI loopback may capture the agent's own TTS voice. That can create echo/self-transcription. A proper dual-route setup is strongly recommended for unattended operation.

## Console Commands

Preflight:

```powershell
python -m src.main --preflight
```

List audio devices:

```powershell
python -m src.main --list-audio-devices
```

Run a safe one-number test:

```powershell
python -m src.main --safe-test +15551234567
```

Diagnose current Google Voice call state:

```powershell
python -m src.main --diagnose-call-state
```

Dry run without dialing:

```powershell
python -m src.main --dry-run
```

Start normal calling:

```powershell
python -m src.main
```

Start the web console:

```powershell
python -m src.web_app
```

Then open the local URL printed by FastAPI.

## Run It Like A Windows App

Build the desktop EXE:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1
```

For a faster rebuild after tests already passed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1 -SkipTests
```

Build outputs:

- `dist\IndusDispatchConsole.exe`
- `release\IndusDispatchConsole-portable.zip`

Install desktop and Windows Startup shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1
```

The Desktop shortcut opens the operator console at `http://127.0.0.1:8000/run`.
The Startup shortcut launches the console automatically when Windows signs in.

To start the app manually:

```powershell
.\dist\IndusDispatchConsole.exe
```

To run on another port:

```powershell
.\dist\IndusDispatchConsole.exe --port 8787
```

To start at login without opening the browser immediately:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1 -StartupNoBrowser
```

## Web Console

The web console is useful for operations, not required for the console app.

Typical areas:

- Dashboard/status page.
- Settings editor.
- Contact/CRM review.
- Logs and transcripts.
- Manual call/test actions.

The backend routes are defined in `src/web_app.py`. Static assets and templates live under the web/static/template folders used by that module.

## Testing

Run the full test suite:

```powershell
python -m pytest tests -q
```

Optional compile check:

```powershell
python -m compileall src tests
```

Recommended before a live calling session:

```powershell
python -m src.main --preflight
python -m src.main --safe-test +15551234567
python -m pytest tests -q
```

## Logs And Runtime Files

Runtime output is written under project log/output folders such as:

- `logs/`
- `connected_calls/`
- `failed_calls/`
- `voicemail_calls/`
- `recordings/`
- generated TTS/audio files

Do not commit secrets, recordings, private call logs, generated transcripts, or local Chrome profile data.

## 24/7 Operation Guidance

For reliable long-running calling:

- Keep Chrome profile persistent and verify login before each dialing batch.
- Use a conservative `call_cooldown_seconds`.
- Keep `max_ring_seconds` realistic for your target numbers.
- Avoid calling the same failed number repeatedly.
- Watch Groq rate limits and add backoff if you scale call volume.
- Use a process supervisor or scheduled restart.
- Rotate logs and archive transcripts.
- Monitor Google Voice account health manually.
- Keep manual fallback access to Chrome in case Google requires re-login.

Useful observability upgrades:

- Langfuse for AI prompt/response tracing.
- OpenTelemetry for process metrics and distributed traces.
- Silero VAD for stronger speech detection.
- Deepgram for lower-latency realtime STT.

Heavier tools are not automatically better:

- WhisperX is excellent for offline/recorded transcription, but it is usually too heavy for low-latency live calls.
- pyannote-audio helps with speaker diarization, but it adds GPU/CPU cost and is not required for basic two-party calls.
- LiveKit is powerful realtime voice infrastructure, but adopting it would be an architecture change. Use it only if you move away from browser-based Google Voice automation.

## Troubleshooting

Call ends while still ringing:

- Check `min_ring_seconds`.
- Check the detailed `detect_call_state()` logs.
- Confirm Google Voice DOM has not changed.
- Confirm no stale ended-call banner is being mistaken for the active call.

AI talks before pickup:

- Confirm connected evidence is logged before conversation starts.
- Run `--safe-test` and inspect state transition logs.

Voicemail triggers too early:

- Confirm voicemail cues are ignored during ringing.
- Confirm voicemail is detected only after connected state or clear greeting/beep evidence.

No audio into Google Voice:

- Check Chrome microphone is set to `CABLE Output`.
- Check agent playback is set to `CABLE Input`.
- Run `--audio-route-test`.

Agent hears itself:

- Avoid `CAPTURE_DEVICE=default` for unattended runs.
- Use a dedicated capture path for remote call audio.

Chrome login expires:

- Open the configured Chrome profile manually.
- Log into Google Voice again.
- Rerun `--preflight`.

## Manual Live-Call Test Checklist

Use a number you own:

1. Run `python -m src.main --preflight`.
2. Run `python -m src.main --safe-test +15551234567`.
3. Let the phone ring for several seconds before answering.
4. Confirm the AI does not speak while ringing.
5. Answer the call.
6. Confirm the AI speaks only after connection.
7. Say a short phrase and confirm it responds naturally.
8. Repeat once and decline/send to voicemail.
9. Confirm voicemail is detected only after greeting or beep.
10. Review `logs/`, transcripts, and call result CSVs.

## Development Notes

Keep changes small around the call-state machine. The browser DOM, call timer, active controls, audio classifier, silence logic, and timeout logic all interact. Tests should cover ringing, connected, voicemail, ended, timeout, and stale-DOM cases whenever that area changes.

Before pushing:

```powershell
python -m pytest tests -q
python -m src.main --preflight
```
