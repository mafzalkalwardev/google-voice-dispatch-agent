# Google Voice Dispatch Agent

Realtime Google Voice browser automation for freight dispatch outreach.

## Live Mode

The default runtime is now live realtime conversation:

- Selenium opens Google Voice with a persistent Chrome profile.
- The app dials contacts from Excel/CSV.
- Connected calls use Groq STT, Groq chat, and realtime TTS routed into Google Voice.
- Voicemail still uses a generated fallback WAV after voicemail is detected.
- Calls are logged to `logs/call_logs.csv`.

This is not a telephony API. It depends on Google Voice in Chrome plus Windows audio routing.

## Requirements

- Python 3.11+
- Chrome
- A logged-in Google Voice account
- `GROQ_API_KEY`
- VB-CABLE or equivalent virtual audio cable
- Chrome microphone set to the cable output used by Google Voice

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set your real values. You can also copy
`dialer_config.example.json` to `dialer_config.json` for local JSON config.

Check audio devices:

```powershell
python -m src.main --list-audio-devices
python -m src.audio_diagnostics
```

## Run Live

```powershell
python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 1
```

Realtime mode is the default. Use `--static-playback` only if you want the older pregenerated WAV flow.

Common options:

```powershell
python -m src.main `
  --contacts data/contacts.xlsx `
  --profile sales1 `
  --loopback-device "CABLE Input" `
  --capture-device default `
  --limit 1
```

For a dual-cable setup, set Chrome speaker output to a second cable input and pass its paired output as `--capture-device`.

## Safety Notes

- Keep `.env`, `dialer_config.json`, contacts, generated audio, logs, and Chrome profiles out of Git.
- Test with your own phone first using `--limit 1`.
- Follow consent, telemarketing, robocall, call recording, DNC, and Google Voice terms that apply to your use.

## Claude Handoff

See `CLAUDE_PROMPT.md` for the next focused engineering pass.
