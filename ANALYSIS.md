# GoogleVoiceAgent — Architecture & Status

## 1. Project Purpose

Browser-driven Google Voice dispatch sales agent.
Generates AI call scripts (Groq), converts them to speech (pyttsx3 TTS),
injects audio into live calls via Windows virtual loopback cable (VB-CABLE + sounddevice),
and tracks every call outcome in a structured CSV log.

---

## 2. Implemented Modules

| File | Purpose |
|------|---------|
| `src/config.py` | Env + JSON config loader; validates secrets on startup |
| `src/call_session.py` | `CallSession` state machine: IDLE→DIALING→RINGING→CONNECTED/VOICEMAIL→ENDED/FAILED |
| `src/google_voice.py` | Selenium Chrome automation: dial, `detect_call_state()`, voicemail DOM detection, hangup |
| `src/ai_groq.py` | Official `groq` SDK wrapper; generates call scripts and voicemails via Llama 3.3 70B |
| `src/tts.py` | pyttsx3 TTS → WAV; selects best Windows SAPI voice automatically |
| `src/voice_playback.py` | sounddevice loopback injection; ffplay fallback; device discovery CLI |
| `src/call_log.py` | CSV call logger; `log_session()` for full `CallSession` structured rows |
| `src/contacts.py` | Excel/CSV contact loading with phone normalization to `+1XXXXXXXXXX` |
| `src/logger.py` | Dual console + file logging |
| `src/main.py` | CLI entry point; full call loop with dry-run mode |
| `src/call_state.py` | Backward-compat shim → re-exports from `call_session` |

---

## 3. Test Suite (57 tests, all passing)

```
tests/test_call_session.py   — state machine: legal/illegal transitions, durations, log dict
tests/test_mock_dial.py      — Selenium browser with mocked driver
tests/test_groq_agent.py     — Groq SDK with mocked HTTP
tests/test_voice_playback.py — device discovery and loopback routing
```

Run with:
```powershell
.venv\Scripts\python -m pytest tests/ -v
```

---

## 4. Secrets Management

| Context | Method |
|---------|--------|
| Local dev | `.env` file (gitignored) — copy from `.env.example` |
| CI (GitHub Actions) | `secrets.GROQ_API_KEY` in repository Settings → Secrets |
| Runtime validation | `Config.validate()` raises on placeholder or wrong-host values |

**Never commit `.env`. Never put real keys in `.env.example`.**

---

## 5. Audio Loopback Setup (Windows, one-time)

1. Download and install **VB-CABLE** (free): https://vb-audio.com/Cable/
2. Windows Sound → **Playback** → set `CABLE Input` as default
3. Windows Sound → **Recording** → set `CABLE Output` as default
4. Chrome → voice.google.com site settings → Microphone → `CABLE Output`
5. Verify device index: `python -m src.voice_playback`

---

## 6. Usage

### Dry run (generate audio only, no dialing)
```powershell
.venv\Scripts\python -m src.main --contacts data/contacts.xlsx --dry-run
```

### Live calls
```powershell
.venv\Scripts\python -m src.main `
    --contacts data/contacts.xlsx `
    --profile  sales1 `
    --objective "book a dispatch sales appointment" `
    --offer     "10% freight cost reduction guarantee" `
    --limit     20
```

### First run (login required)
Chrome opens to voice.google.com. Sign in manually. Session persists in `chrome_profiles/`.

---

## 7. Call Flow

```
load contacts
    │
    ▼
generate AI script + voicemail text (Groq)
    │
    ▼
TTS → WAV files (audio/scripts/ and audio/voicemails/)
    │
    ▼
browser.dial_number(phone)
    │
    ▼
detect_call_state() polls DOM
    ├── CONNECTED  → play script WAV via loopback → wait for end
    ├── VOICEMAIL  → wait for beep → play voicemail WAV → hangup
    └── FAILED     → log and skip
    │
    ▼
CallSession → call_log.log_session() → logs/call_logs.csv
```

---

## 8. Known Limitations

- **Google Voice DOM selectors** may change with UI updates. Maintain `_SEL` in `google_voice.py`.
- **Voicemail beep detection** is DOM + page-source heuristic only; no audio analysis.
- **Single account** per run. Multi-profile rotation not yet implemented.
- **headless mode** works for testing but Google Voice may block auth challenges without a visible browser.

---

## 9. Checklist

- [x] Structured CLI entry point
- [x] `CallSession` state machine with validated transitions
- [x] Groq SDK integration (llama-3.3-70b-versatile)
- [x] TTS audio generation (pyttsx3)
- [x] Windows loopback audio injection (sounddevice + VB-CABLE)
- [x] Voicemail detection (DOM + page-source)
- [x] Structured CSV call logging
- [x] Secret validation at startup
- [x] 57 unit tests (all passing)
- [x] GitHub Actions CI with secrets management
- [x] Dry-run mode
- [ ] Multi-account Chrome profile rotation
- [ ] Audio-based voicemail beep detection (tone analysis)
- [ ] Real-time call transcription
