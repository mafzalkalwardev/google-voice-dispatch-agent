# Google Voice Dispatch Agent — INDUS TRANSPORTS LLC

Realtime Google Voice browser automation for freight dispatch outreach.
Includes a full operator console web frontend.

---

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in real values (at minimum `GROQ_API_KEY` and `CALLBACK_NUMBER`).

---

## Web Frontend (Operator Console)

Quick Windows launcher:

```powershell
.\Start-IndusConsole.ps1
```

This starts the local backend and opens the Live Run console automatically.
If PowerShell blocks scripts, run:

```powershell
powershell -ExecutionPolicy Bypass -File .\Start-IndusConsole.ps1
```

Manual start:

```powershell
python -m src.web_app
```

Then open **http://127.0.0.1:8000** in your browser.

Or with auto-reload during development:

```powershell
uvicorn src.web_app:app --reload --port 8000
```

### Console pages

| URL | Description |
|-----|-------------|
| `/` | Dashboard — recent call stats and quick actions |
| `/preflight` | Run all environment checks before dialing |
| `/settings` | Configure profiles, models, audio, timing |
| `/contacts` | Upload or preview CSV/XLSX contact list |
| `/audio` | Detected audio devices, loopback quick-set |
| `/run` | Start / stop live runs, dry runs, live log stream |
| `/logs` | Searchable call log viewer |

---

## Windows EXE / Installer Build

Build a portable Windows EXE:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1
```

Build output:

- `dist\IndusDispatchConsole.exe`
- `release\IndusDispatchConsole-portable.zip`

If port 8000 is busy, launch the EXE with a different console port:

```powershell
.\dist\IndusDispatchConsole.exe --port 8787
```

Optional installer build, if Inno Setup is installed:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1 -BuildInstaller
```

Installed/runtime data is stored outside the app binary:

```text
%LOCALAPPDATA%\IndusDispatchAgent
```

That folder is where the installed EXE reads/writes `.env`, `dialer_config.json`, contacts, Chrome profiles, logs, transcripts, and generated audio. These files are not bundled into the EXE and are not committed to Git.

Required on each computer:

- Google Chrome
- Google Voice account login
- VB-CABLE or equivalent audio routing for live calls
- A local `.env` with `GROQ_API_KEY` and `CALLBACK_NUMBER`

For source runs, the helper also accepts a custom port:

```powershell
.\Start-IndusConsole.ps1 -Port 8787
```

---

## CLI

### List audio devices

```powershell
python -m src.main --list-audio-devices
```

### Run preflight checks

```powershell
python -m src.main --preflight
```

### Dry run (no dialing, generates scripts only)

```powershell
python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 3 --dry-run
```

### One live test call (confirm a safe number first)

```powershell
python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 1
```

### Common options

```powershell
python -m src.main `
  --contacts data/contacts.xlsx `
  --profile sales1 `
  --loopback-device "CABLE Input" `
  --capture-device default `
  --call-timeout 45 `
  --call-max-duration 120 `
  --callback-number "+15551234567" `
  --limit 1
```

Realtime conversation is the default. Use `--static-playback` only for the older pregenerated WAV flow.
The app now waits for answered-call timer evidence before the realtime opening line; the hangup button alone is treated as ringing, not connected.

---

## Audio Routing (Windows)

Install **VB-CABLE** from https://vb-audio.com/Cable/.

### Single-cable setup (default)

| Signal path | Device |
|---|---|
| TTS → Chrome mic | `CABLE Input` (output device) → `CABLE Output` set as Chrome microphone |
| Prospect → STT | System speakers loopback captured via `CAPTURE_DEVICE=default` |

Set in `.env`:
```
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
```

If preflight reports `Device unavailable`, select a different matching `CABLE Input` index/name from the Audio page or disable Windows exclusive-mode access for that playback device.

### Dual-cable setup (recommended for echo-free capture)

Use a second VB-CABLE pair (CABLE B). Route Chrome speaker output to CABLE B Input.
Capture from CABLE B Output instead of the system default:
```
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=CABLE B Output
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

```env
GROQ_API_KEY=gsk_...
CALLBACK_NUMBER=+15551234567
AGENT_NAME=Tony
COMPANY_NAME=Indus Transports LLC
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
```

---

## Tests

```powershell
python -m pytest tests/ -v
```

---

## Records, Replies, and Outcomes

The app saves operational records locally under `logs/`, which is ignored by Git.

- Call outcome CSV: `logs/call_logs.csv`
- Customer/agent transcript text: `logs/transcripts/<phone>_<timestamp>.txt`
- App runtime logs: `logs/`

A call is treated as picked up only after Google Voice exposes answered-call timer evidence. The hangup button alone is treated as ringing, not connected, so Tony should not speak while the outbound call is still ringing.

Customer interest and details are currently captured in the transcript file and call log notes. For production sales work, the next improvement should add a structured lead summary file, for example `logs/leads.csv`, with fields like interest level, equipment type, lanes, callback time, objections, and follow-up action.

---

## Safety & Compliance

- Keep `.env`, `dialer_config.json`, contacts, audio, logs, and Chrome profiles out of Git.
- Test with your own phone first using `--limit 1`.
- Follow TCPA, FDCPA, DNC registry rules, state call-recording laws, Google Voice Terms of Service,
  and all applicable regulations before dialing third parties.
- The web console shows a compliance acknowledgement before enabling live dialing.

---

*Developer: **Muhammad Afzal** — WhatsApp: +923079670503*
