<div align="center">

# Google Voice Dispatch Agent

<img src="https://readme-typing-svg.demolab.com?font=Inter&weight=700&size=28&duration=2800&pause=700&color=0EA5E9&center=true&vCenter=true&width=900&lines=Live+AI+Google+Voice+Calling+Console;Realtime+STT+%2B+LLM+%2B+TTS+Outbound+Agent;Windows+Desktop+App+%2B+FastAPI+Operator+Console;Built+for+careful+24%2F7+calling+workflows" alt="Typing SVG" />

<p>
  <img src="https://komarev.com/ghpvc/?username=mafzalkalwardev&label=Project%20Views&color=0ea5e9&style=for-the-badge" alt="Project views" />
  <img src="https://img.shields.io/github/stars/mafzalkalwardev/google-voice-dispatch-agent?style=for-the-badge&color=22c55e" alt="GitHub stars" />
  <img src="https://img.shields.io/github/forks/mafzalkalwardev/google-voice-dispatch-agent?style=for-the-badge&color=f59e0b" alt="GitHub forks" />
  <img src="https://img.shields.io/github/license/mafzalkalwardev/google-voice-dispatch-agent?style=for-the-badge&color=64748b" alt="License" />
</p>

<p>
  <img src="https://skillicons.dev/icons?i=python,fastapi,selenium,powershell,html,css,js,githubactions,windows" alt="Skill icons" />
</p>

<p>
  <strong>AI outbound calling automation, Google Voice browser control, realtime speech, CRM logging, and Windows app packaging.</strong>
</p>

</div>

---

## Project Showcase

Google Voice Dispatch Agent is a Windows-first live calling system that turns Google Voice into an AI-assisted outbound call console. It controls Google Voice in Chrome, dials contacts, waits for real answer evidence, speaks through a virtual audio cable, listens to call audio, transcribes speech, generates replies with Groq, and writes outcomes back to logs and CRM files.

It can run as a console app, a FastAPI operator console, or a packaged Windows EXE with Desktop and Startup shortcuts.

> Compliance note: this project can place real calls. You are responsible for consent, caller-ID rules, Do Not Call compliance, recording disclosure, spam prevention, local law, Google Voice terms, and carrier/account limits.

---

## Trophy Cards

<div align="center">
  <img src="https://github-profile-trophy.vercel.app/?username=mafzalkalwardev&theme=algolia&no-frame=true&no-bg=true&margin-w=12&margin-h=12&column=4" alt="GitHub trophy cards" />
</div>

---

## Stats Cards

<div align="center">
  <img height="165" src="https://github-readme-stats.vercel.app/api?username=mafzalkalwardev&show_icons=true&theme=tokyonight&hide_border=true&rank_icon=github" alt="GitHub stats" />
  <img height="165" src="https://github-readme-stats.vercel.app/api/top-langs/?username=mafzalkalwardev&layout=compact&theme=tokyonight&hide_border=true" alt="Top languages" />
</div>

---

## Activity Graph

<div align="center">
  <img src="https://github-readme-activity-graph.vercel.app/graph?username=mafzalkalwardev&theme=react-dark&hide_border=true&area=true" alt="GitHub activity graph" />
</div>

---

## Contribution Snake

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mafzalkalwardev/google-voice-dispatch-agent/output/github-contribution-grid-snake-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/mafzalkalwardev/google-voice-dispatch-agent/output/github-contribution-grid-snake.svg">
    <img alt="Contribution snake" src="https://raw.githubusercontent.com/mafzalkalwardev/google-voice-dispatch-agent/output/github-contribution-grid-snake.svg">
  </picture>
</div>

---

## SEO Keywords

`AI calling agent` `Google Voice automation` `outbound call automation` `realtime voice AI` `FastAPI call console` `Selenium Google Voice bot` `Groq voice assistant` `Python call center automation` `Windows desktop AI app` `VB-CABLE voice routing` `AI dispatcher assistant` `CRM call logging` `voicemail detection` `speech to text calling agent` `text to speech phone agent`

---

## Core Capabilities

- Browser automation for Google Voice using Selenium and persistent Chrome profiles.
- Live call-state detection with conservative ringing, connected, ended, failed, and voicemail transitions.
- Realtime speech capture with VAD, STT, LLM response generation, and TTS playback.
- Voicemail detection through DOM cues plus early-call audio classifier diagnostics.
- Pre-generated opening line and voicemail audio assets before dialing.
- Call logs, transcripts, recordings, connected-call archive, failed-call archive, and CRM enrichment.
- FastAPI web console for settings, preflight, audio tools, live runs, logs, recordings, leads, and carrier CRM.
- Windows EXE build with PyInstaller.
- Desktop shortcut and Windows Startup shortcut installer.
- Automated tests for state handling, mock dialing, CRM, audio helpers, web routes, and realtime loop behavior.

---

## Why This Exists

Most outbound AI voice demos assume a telephony API. This project is different: it works around the practical reality of Google Voice running in a browser. That means the hard parts are not only AI responses. The hard parts are state detection, browser DOM drift, audio routing, ringing safety, voicemail timing, and long-running operational reliability.

The project is designed around those real-world constraints:

- Never speak until the call is actually connected.
- Never treat immediate DOM noise as a completed call.
- Respect a minimum ringing duration.
- Detect voicemail only after connected/greeting evidence.
- Keep full logs so every call can be audited.
- Provide preflight checks before live dialing.

---

## Current Call-State Safety

The most important production fix is the conservative call-state gate in `src/google_voice.py`.

Current behavior:

- Ringing cannot instantly transition to ended.
- `min_ring_seconds` must pass before accepting connected, ended, or voicemail transitions.
- Active call controls seen too early are logged and held in ringing.
- Stale ended banners are ignored during the minimum ringing window.
- Voicemail page/DOM cues are ignored while still ringing.
- Connected state waits for stable evidence such as answered controls or call timer signals.
- The AI opening line starts only after confirmed connection.
- `max_ring_seconds` ends the attempt as no answer only after the configured ring timeout.
- Logs include current state, elapsed ringing time, DOM cues, answered controls, call-active status, voicemail cues, audio classifier result, and timeout reason.

---

## Architecture

```text
GoogleVoiceAgent-Active/
  src/
    main.py                  CLI runner, safe test, batch calling
    google_voice.py          Selenium Google Voice automation and call-state detection
    call_session.py          Call session model and CallState transitions
    conversation_loop.py     Realtime capture, VAD, STT, LLM, TTS, voicemail handoff
    voicemail_detector.py    Audio classifier for greeting/beep/live-call signals
    ai_groq.py               Groq-backed AI response behavior
    realtime_tts.py          Edge TTS playback and cache integration
    audio_capture.py         Input capture and VAD framing
    audio_routing.py         Windows audio-device discovery and playback routing
    crm.py                   Carrier CRM, connected call archive, transcripts, recordings
    web_app.py               FastAPI operator console and API routes
    desktop_app.py           PyInstaller app entrypoint
  tests/                     Automated test suite
  scripts/                   Build and shortcut installers
  packaging/                 PyInstaller spec
  installer/                 Optional Inno Setup installer config
```

High-level runtime flow:

1. Load `.env`, `dialer_config.json`, contacts, and CRM data.
2. Run preflight checks for Groq, Chrome profile, contacts, callback number, and audio devices.
3. Launch Google Voice with the configured Chrome profile.
4. Prepare opening line and voicemail audio before dialing.
5. Dial one contact.
6. Poll `detect_call_state()` until connected, voicemail, no answer, ended, or failed.
7. Start the realtime conversation loop only after confirmed connection.
8. Detect voicemail during the early connected-call window and hand off to voicemail playback when confirmed.
9. Save call logs, transcript, recording, CRM update, and call archive.
10. Apply cooldown and continue to the next contact.

---

## Tech Stack

| Layer | Tools |
|---|---|
| Language | Python |
| Web console | FastAPI, Jinja2, HTML, CSS, JavaScript |
| Browser automation | Selenium, webdriver-manager, Google Chrome |
| AI provider | Groq |
| Speech/TTS | Groq STT, Edge TTS, pyttsx3 fallback |
| Audio routing | VB-CABLE, sounddevice, soundcard, soundfile |
| Data | CSV/XLSX contacts, JSON config, local CRM artifacts |
| Desktop packaging | PyInstaller, PowerShell |
| CI/testing | pytest, GitHub Actions |

---

## Requirements

- Windows 10/11 recommended.
- Python 3.10+ for source runs.
- Google Chrome.
- Google Voice account logged in through the configured Chrome profile.
- VB-CABLE or similar virtual audio device.
- Groq API key.
- Contacts file or CRM data.
- A callback number configured in Google Voice when required by the account.

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

---

## Configuration

Common `.env` values:

```env
GROQ_API_KEY=your_key_here
# Optional failover (separate Groq accounts): GROQ_API_KEY_2=... GROQ_API_KEY_3=...
PROFILE_NAME=sales_profile
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
CALLBACK_NUMBER=your_callback_number
```

Common `dialer_config.json` values:

```json
{
  "min_ring_seconds": 2,
  "max_ring_seconds": 45,
  "voicemail_detect_seconds": 15,
  "silence_does_not_end_call": true,
  "use_stt_context": true,
  "max_silence_seconds": 8,
  "call_cooldown_seconds": 10,
  "tts_warmup": true,
  "stt_retry_count": 2,
  "vad_silence_frames": 12,
  "vad_speech_frames": 2
}
```

For 24/7 calling, keep `min_ring_seconds` above zero, use a cooldown, and do not rapid-fire failed numbers.

---

## Audio Routing

Minimum VB-CABLE setup:

- Agent playback device: `CABLE Input`.
- Chrome microphone input: `CABLE Output`.
- Chrome speaker output: a device the agent can capture.

Recommended unattended setup:

- Use separate routes for agent TTS and inbound call capture.
- Avoid capturing the same speaker output that plays the agent voice.
- Keep Windows default audio devices stable.
- Re-run audio discovery after Windows updates or driver changes.

Useful commands:

```powershell
python -m src.main --list-audio-devices
python -m src.main --audio-route-test
python -m src.main --preflight
```

Important: `CAPTURE_DEVICE=default` uses Windows speaker loopback. It works for testing, but for nonstop calling a dedicated capture route is more reliable because it reduces echo and self-transcription risk.

---

## Run From Source

Preflight:

```powershell
python -m src.main --preflight
```

Safe one-number live test:

```powershell
python -m src.main --safe-test +15551234567
```

Start normal calling:

```powershell
python -m src.main
```

Resume a partially completed list (skips numbers already dialed in the last run):

```powershell
python -m src.main
```

Start fresh on the same list:

```powershell
python -m src.main --reset-batch-progress
```

Start the web console:

```powershell
python -m src.web_app
```

Open:

```text
http://127.0.0.1:8000/run
```

---

## Run Like A Windows App

Build the EXE:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1
```

Fast rebuild after tests already passed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1 -SkipTests
```

Build outputs:

- `dist\IndusDispatchConsole.exe`
- `release\IndusDispatchConsole-portable.zip`

Install Desktop and Windows Startup shortcuts:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1
```

Manual app launch:

```powershell
.\dist\IndusDispatchConsole.exe
```

Custom port:

```powershell
.\dist\IndusDispatchConsole.exe --port 8787
```

Startup without opening the browser immediately:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_shortcuts.ps1 -StartupNoBrowser
```

---

## Web Console

The FastAPI console provides operational pages for:

- Live run control.
- Preflight checks.
- Settings.
- Contacts upload/review.
- Audio diagnostics.
- Logs.
- Leads.
- Recordings.
- Connected calls.
- Carrier CRM.

Primary API and page routes live in `src/web_app.py`.

---

## Testing

Run the full suite:

```powershell
python -m pytest tests -q
```

Compile check:

```powershell
python -m compileall src tests
```

Recommended before live calling:

```powershell
python -m src.main --preflight
python -m src.main --safe-test +15551234567
python -m pytest tests -q
```

Current local verification:

```text
231 tests passed
Packaged EXE served /run with HTTP 200
Desktop and Startup shortcuts installed successfully
```

---

## Logs And Runtime Data

Runtime files are intentionally not committed:

- `.env`
- `dialer_config.json`
- `logs/`
- `connected_calls/`
- `failed_calls/`
- `voicemail_calls/`
- `audio/voicemails/`
- `audio/scripts/`
- `chrome_profiles/`
- `dist/`
- `build/`
- `release/`

These can contain secrets, phone numbers, recordings, transcripts, local browser sessions, and generated build artifacts.

---

## 24/7 Operations

For long-running calling:

- Keep the Chrome profile persistent.
- Confirm Google Voice login before dialing batches.
- Use conservative cooldowns between calls.
- Avoid repeated attempts to failed or busy numbers.
- Monitor Groq rate limits.
- Rotate logs and archive recordings.
- Use a dedicated audio route instead of default speaker loopback.
- Watch Google Voice account health.
- Keep a manual Chrome fallback for login challenges.
- Restart the app on a schedule if the browser session drifts.

Recommended future upgrades:

- Silero VAD for stronger speech detection.
- Deepgram for lower-latency realtime STT.
- Langfuse for AI prompt/response tracing.
- OpenTelemetry for runtime metrics.

Tools to delay unless the architecture changes:

- WhisperX is better for offline recordings than live low-latency calls.
- pyannote-audio is useful for diarization, but it adds compute cost.
- LiveKit is powerful, but it would be a larger move away from browser-based Google Voice automation.

---

## Troubleshooting

Call ends while still ringing:

- Check `min_ring_seconds`.
- Review `CALL_STATE` logs.
- Confirm Google Voice DOM did not change.
- Confirm no stale ended banner is being treated as the current call.

AI speaks before pickup:

- Confirm connected evidence appears before `ConversationLoop` starts.
- Run a safe test and inspect state transition logs.

Voicemail triggers too early:

- Confirm voicemail cues are ignored while ringing.
- Confirm voicemail is only accepted after connected/greeting/beep evidence.

No audio into Google Voice:

- Set Chrome microphone to `CABLE Output`.
- Set agent playback to `CABLE Input`.
- Run `--audio-route-test`.

Agent hears itself:

- Avoid `CAPTURE_DEVICE=default` for unattended runs.
- Use a dedicated inbound capture path.

Chrome login expires:

- Open the configured Chrome profile manually.
- Log into Google Voice.
- Run `python -m src.main --preflight`.

---

## Manual Live-Call Test

Use a number you own or have permission to call:

1. Run `python -m src.main --preflight`.
2. Run `python -m src.main --safe-test +15551234567`.
3. Let the phone ring for several seconds.
4. Confirm the AI does not speak while ringing.
5. Answer the call.
6. Confirm the AI speaks only after connection.
7. Say a short phrase and confirm it responds.
8. Decline/send one call to voicemail.
9. Confirm voicemail is detected after greeting or beep.
10. Review logs, transcript, recording, and call archive.

---

## Repository Hygiene

The repository keeps source, tests, packaging scripts, workflows, and examples. It intentionally excludes local runtime data, browser profiles, build folders, logs, recordings, generated voicemails, secrets, and private contact data.

Local editor folders, generated test caches, temporary build folders, private runtime data, and one-off planning notes are not part of the product repository.

---

<div align="center">
  <strong>Built for practical AI calling automation: careful state detection, real logs, real audio routing, and a Windows app path.</strong>
</div>
