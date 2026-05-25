# Google Voice Dispatch Agent — INDUS TRANSPORTS LLC

Realtime Google Voice browser automation for freight dispatch outreach.
Includes a full operator console web frontend with Leads CRM, call logging,
and Groq-powered transcript extraction.

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
| `/leads` | Carrier leads CRM — search, filter, export, add/edit |

---

## Leads Page

The `/leads` page is a lightweight CRM for carrier leads extracted from call transcripts.

### How leads are created

After each connected realtime call, the agent reads the saved transcript and calls
Groq with a JSON extraction prompt to pull out structured lead data:

- Company Name, Contact Name, MC Number, Email
- Truck Type, Truck Length, Preferred Lanes
- Agreed Dispatcher Percentage
- Interested level (`Yes` / `Maybe` / `No` / `DNC`)
- Callback Time, Remarks, Call Outcome

The result is appended to `logs/leads.csv`. If the same phone number already has
a row, the new data is merged in (existing non-empty fields are preserved).

### Manual leads

Use the **+ Add Lead** button on `/leads` to enter a lead manually.
Click **Edit** on any row to update fields. All changes call `POST /api/leads`.

### API endpoints

| Method | URL | Description |
|--------|-----|-------------|
| `GET` | `/api/leads` | Return all leads as JSON (newest first) |
| `POST` | `/api/leads` | Upsert a lead (match by phone_number) |
| `GET` | `/api/leads/export` | Download `leads.csv` |

### Interest badges

| Badge | Meaning |
|-------|---------|
| Yes (green) | Carrier expressed genuine interest |
| Maybe (amber) | Interested but needs follow-up |
| No (red) | Not interested at this time |
| DNC (dark) | Do Not Call |

### Leads CSV columns

`timestamp`, `company_name`, `contact_name`, `phone_number`, `mc_number`,
`email`, `truck_type`, `truck_length`, `preferred_lanes`, `agreed_percentage`,
`interested`, `callback_time`, `remarks`, `call_outcome`, `transcript_file`

---

## Audio Routing (Windows)

Install **VB-CABLE** from https://vb-audio.com/Cable/.

### VB-CABLE setup (step by step)

1. Download and install **VB-CABLE Driver** from https://vb-audio.com/Cable/index.htm
2. Restart Windows after installation.
3. Open **Sound settings → Playback** — you should see **CABLE Input** (the virtual speaker).
4. Open **Sound settings → Recording** — you should see **CABLE Output** (the virtual microphone).
5. In Google Chrome settings (`chrome://settings/content/microphone`), set the microphone to **CABLE Output**.
6. In the Indus Dispatch Console → **Settings**, set:
   - **Loopback Device**: `CABLE Input`
   - **Capture Device**: `default` (system speakers, for single-cable) or `CABLE B Output` (dual-cable)

### Single-cable setup (default)

| Signal path | Device |
|---|---|
| Agent TTS → Chrome mic | `CABLE Input` (output) → `CABLE Output` (Chrome mic source) |
| Prospect voice → STT | System speakers captured via `CAPTURE_DEVICE=default` |

`.env` settings:
```
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=default
```

### Dual-cable setup (echo-free, recommended for production)

Install a second VB-CABLE pair. Route Chrome speaker output to **CABLE B Input**.
Capture from **CABLE B Output** so prospect audio is isolated from TTS echo:

```
LOOPBACK_DEVICE=CABLE Input
CAPTURE_DEVICE=CABLE B Output
```

### Audio troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Tony's voice not heard by prospect | Chrome mic is not set to CABLE Output | Chrome → Settings → Privacy → Microphone → select **CABLE Output** |
| Preflight reports `Device unavailable` | Windows exclusive-mode lock on CABLE Input | Right-click CABLE Input in Sound → Properties → Advanced → uncheck *Allow exclusive mode* |
| STT always empty / VAD not triggering | Capture device muted or wrong index | Audio page → check capture device index; run `--audio-route-test` |
| Echo / Tony hears himself | Single-cable setup picking up TTS playback | Use dual-cable setup (CABLE B for capture) |
| Call connected but no audio injected | loopback_device name mismatch | Audio page → find exact device name; update `LOOPBACK_DEVICE` in Settings |
| `validate_tts_output_device` error | Device index stale after USB reconnect | Re-run preflight or restart the app |

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

## CLI

### List audio devices

```powershell
python -m src.main --list-audio-devices
```

### Run preflight checks

```powershell
python -m src.main --preflight
```

### Audio route test (no call placed)

```powershell
python -m src.main --audio-route-test
```

### Dry run (no dialing, generates scripts only)

```powershell
python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 3 --dry-run
```

### Safe one-number test

```powershell
python -m src.main --safe-test +15551234567
```

### Full run

```powershell
python -m src.main `
  --contacts data/contacts.xlsx `
  --profile sales1 `
  --loopback-device "CABLE Input" `
  --capture-device default `
  --call-timeout 45 `
  --call-max-duration 120 `
  --callback-number "+15551234567" `
  --limit 10
```

Realtime conversation is the default. Use `--static-playback` only for the older
pregenerated WAV flow.

---

## Connected Calls + Carrier CRM Backend

The CRM backend is SQLite-backed and keeps legacy CSV compatibility. A call is
promoted to **Connected Calls** only when Google Voice has connected evidence and
the transcript contains a real prospect/carrier turn. Voicemail, failed, and
silent connected calls are archived separately and excluded from connected-call
views.

Runtime storage:

| Path | Purpose |
|------|---------|
| `logs/carrier_crm.sqlite3` | Permanent relational CRM database |
| `connected_calls/<call_id>/` | Real connected conversations with transcript, recording, metadata, summary |
| `voicemail_calls/<call_id>/` | Voicemail call metadata/artifacts |
| `failed_calls/<call_id>/` | Failed and silent connected call metadata/artifacts |
| `logs/leads.csv` | Backward-compatible lead export, updated after connected calls |

Core relationships:

```text
carriers
  -> connected_calls
  -> call_artifacts
  -> notes
  -> follow_ups
```

Carrier duplicate merging uses normalized `phone`, `mc_number`, or `email`.
Search indexing covers company, carrier, phone, MC/DOT, email, transcripts,
summaries, notes, and follow-up records.

Main APIs:

| API | Description |
|-----|-------------|
| `GET /api/connected-calls` | List real answered conversations |
| `GET /api/connected-calls/{id}` | Connected call detail |
| `GET /api/connected-calls/search?q=` | Search connected calls |
| `GET /api/connected-calls/export` | Export connected calls CSV |
| `GET /api/carrier-crm` | List carrier profiles |
| `GET /api/carrier-crm/{id}` | Carrier profile with history |
| `GET /api/carrier-crm/search?q=` | Global carrier CRM search |
| `PATCH /api/carrier-crm/{id}` | Edit carrier/lead fields |
| `POST /api/carrier-crm/{id}/notes` | Add note |
| `POST /api/carrier-crm/{id}/follow-up` | Schedule follow-up |
| `POST /api/carrier-crm/{id}/assign-dispatcher` | Assign dispatcher |
| `GET /api/carrier-crm/{id}/export` | Export one profile as JSON |
| `GET /api/carrier-crm/export` | Export CRM CSV |
| `GET /api/recordings/{call_id}` | Download connected-call recording |

Sample connected-call transcript:

```text
[12:00:00] Tony: What truck are you running right now?
[12:00:05] Prospect: I run a 53 foot dry van, mostly Midwest to Texas.
[12:00:18] Tony: Got it. Are you booking loads yourself or using a dispatcher?
[12:00:24] Prospect: Booking myself, but deadhead has been killing us.
```

Sample CRM summary:

```json
{
  "sentiment": "positive",
  "close_probability": "75%",
  "urgency": "medium",
  "pain_points": "deadhead and inconsistent Midwest reloads",
  "best_follow_up_strategy": "Lead with Midwest-to-Texas lane planning and 6% dry van dispatch terms"
}
```

Sample carrier record:

```json
{
  "company_name": "Road Star Logistics",
  "carrier_name": "Sam Carrier",
  "phone": "+15551234567",
  "mc_number": "MC-123456",
  "dot_number": "DOT-987654",
  "email": "sam@roadstar.example",
  "truck_type": "Dry Van",
  "truck_length": "53ft",
  "preferred_lanes": "Midwest to Texas",
  "agreed_percentage": "6%",
  "follow_up_status": "Interested",
  "callback_time": "Thursday 2pm"
}
```

---

## Windows EXE / Installer Build

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1
```

Build output:

- `dist\IndusDispatchConsole.exe`
- `release\IndusDispatchConsole-portable.zip`

Optional installer build (requires Inno Setup):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows_exe.ps1 -BuildInstaller
```

Runtime data is stored outside the binary:

```text
%LOCALAPPDATA%\IndusDispatchAgent
```

---

## Tests

```powershell
python -m pytest tests/ -v
```

---

## Records and Logs

All operational records are stored under `logs/` (git-ignored):

| File | Description |
|------|-------------|
| `logs/call_logs.csv` | Per-call outcome log |
| `logs/leads.csv` | Structured carrier leads CRM |
| `logs/transcripts/<phone>_<ts>.txt` | Full conversation transcripts |
| `logs/recordings/<phone>_<ts>.wav` | Temporary incoming-call recordings before CRM archival |

---

## Safety & Compliance

- Keep `.env`, `dialer_config.json`, contacts, audio, logs, and Chrome profiles out of Git.
- Test with your own phone first using `--safe-test +1XXXXXXXXXX`.
- Follow TCPA, FDCPA, DNC registry rules, state call-recording laws, Google Voice Terms of Service,
  and all applicable regulations before dialing third parties.
- The web console shows a compliance acknowledgement before enabling live dialing.

---

*Developer: **Muhammad Afzal** — WhatsApp: +923079670503*
