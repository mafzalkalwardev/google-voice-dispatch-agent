You are a senior Python automation engineer taking over a Google Voice realtime dispatch-agent project.

Goal: make the app reliable for live calls, not demos. Keep mock tests, but optimize the runtime path for a real logged-in Google Voice account, real Groq API calls, and real Windows audio routing.

Current state:
- Entry point: `python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 1`
- Preflight entry point: `python -m src.main --preflight`
- Operator console entry point: `python -m src.web_app`
- Realtime mode is default. Static WAV mode is only `--static-playback`.
- Connected calls use `ConversationLoop` with `AudioCapture -> EnergyVAD -> GroqWhisperSTT -> ConversationAgent -> RealtimeTTS`.
- Voicemail still uses a generated WAV fallback.
- The app must not start the opening pitch while the outbound call is only ringing. Treat the Google Voice hangup button as "call active/ringing", not as "answered". Only start realtime speech after strong connected evidence, preferably a real connected-call timer.
- A previous live run failed with `Error opening OutputStream: Device unavailable [PaErrorCode -9985]`. Audio preflight must catch this before dialing and the app must pick a playable matching output device or stop before the first call.
- A previous live run crashed with Selenium `InvalidSessionIdException` after Chrome/session died. Catch browser session failures and stop the loop cleanly.
- Local-only files are ignored: `.env`, `dialer_config.json`, contacts, audio, logs, Chrome profiles.
- Tests currently pass.
- Public brand site: https://industransports.online/
- Brand logo is committed at `src/static/indus-logo.jpg`. Use it in any UI or packaged app.
- Brand colors observed from the public site CSS:
  - Navy: `#1b365d`
  - Deep slate: `#1e293b`
  - Primary blue: `#3b82f6`
  - Dark blue: `#1558b0`
  - LinkedIn/brand blue: `#0A66C2`
  - Amber accent: `#f59e0b`
  - Dark amber: `#d97706`
  - Success green: `#10b981`
  - Light surfaces: `#f8fafc`, `#f1f5f9`, `#e8f0fe`

Do this next:
1. Do not dial any live number until the user explicitly confirms. Start with `python -m src.main --preflight` and the test suite.
2. Run one live smoke test only against one safe test phone number after confirmation.
3. Fix Google Voice selectors in `src/google_voice.py` based on the current DOM, especially dialpad open, number input, call button, hangup button, call timer, answered-call timer, ringing state, and voicemail cues.
   - Do not use the hangup button as answered-call evidence.
   - Confirm that the opening line is not spoken while the call is still ringing.
   - If Google Voice lacks a reliable timer selector, add a safer multi-signal detector and tests.
3. Validate audio routing on Windows:
   - `LOOPBACK_DEVICE` / `--loopback-device` must be the playback device that Chrome receives as microphone input, usually `CABLE Input`.
   - `CAPTURE_DEVICE=default` should capture the prospect from speakers via WASAPI loopback. If echo is bad, document a dual-cable setup.
   - If multiple `CABLE Input` devices match, choose one that can be opened by sounddevice. Do not dial if all matching devices are unavailable.
4. Improve live call stopping:
   - Stop realtime loop immediately when Google Voice ends.
   - Hang up cleanly after the agent says goodbye.
   - Record accurate session outcome.
5. Reduce latency:
   - Prefer edge-tts streaming if possible.
   - Keep STT chunks short and avoid responding to TTS echo.
   - Tune `VAD_THRESHOLD`, silence frames, and calibration.
6. Finish and harden `--preflight`: verify `.env`, contacts file, Chrome profile path, Groq connectivity, audio output device playability, capture device, and Google Voice login before dialing.
7. Keep compliance guardrails visible: consent, DNC, recording laws, robocall/telemarketing rules, Google Voice terms.
8. Finish the fully workable frontend/operator console. This must be an actual operator console, not a marketing landing page.
9. If the user does not want to operate through a browser at `localhost`, provide a Windows-friendly console/desktop launcher:
   - Minimum: a `Start-IndusConsole.ps1` or `.bat` launcher that starts the app and opens the console automatically.
   - Better: a small desktop wrapper using `pywebview`, `PySide6`, or another reliable Windows approach, while reusing the same backend.
   - The user should see a branded INDUS TRANSPORTS LLC console app experience, not be forced to manually type a localhost URL.
10. Improve the agent's sales brain without committing private old call data:
   - Add a structured dispatch sales playbook / knowledge base loaded from safe repo files.
   - Include objections and rebuttals for: already have dispatch, not interested, how much do you charge, send info, how did you get my number, guarantee loads, bad market, busy, remove me, what lanes/equipment do you handle, factoring/paperwork, rate negotiation, onboarding steps.
   - Add tests that the system prompt includes the playbook and still forbids guaranteed earnings.
   - If using "old data", sanitize it first and store only safe examples with no private phone numbers, names, or secrets.

Frontend requirements:
- The UI must visibly say `INDUS TRANSPORTS LLC` in the main header and browser title.
- Use `src/static/indus-logo.jpg` in the header/sidebar/favicon and any desktop wrapper.
- Add developer credit in the footer or settings/about panel:
  - Developer: `Muhammad Afzal`
  - WhatsApp: `+923079670503`
- Use the Indus Transports LLC brand colors listed above. Recommended balance:
  - Navy/slate for the shell/sidebar/header.
  - Primary blue for main actions.
  - Amber for highlights/warnings/live badges.
  - Green only for ready/success/connected states.
  - Light surfaces for panels and data tables.
- Build it as a real working control panel for this Python app:
  - Preflight page/button that checks env, Groq key presence/connectivity, contacts file, Chrome profile, Google Voice login state, and audio devices.
  - Contacts upload/selection for CSV/XLSX.
  - Settings form for profile, limit, loopback device, capture device, callback number, agent/company fields, model names, VAD threshold, call timeout, and max call duration.
  - Audio devices page using the existing `voice_playback.print_devices` / device discovery logic, presented as selectable outputs/inputs.
  - Live run page with Start, Stop, and safe one-call test controls.
  - Real-time status cards: Ready, Dialing, Ringing, Connected, Voicemail, Ended, Failed.
  - Log viewer for `logs/call_logs.csv` and app logs.
  - Clear compliance warning before enabling live dialing.
- The frontend should call the existing Python logic instead of duplicating call logic.
- Prefer a simple local web app stack that runs reliably on Windows. Suggested approach: FastAPI + Jinja2 templates + static CSS/JS, with a command such as `python -m src.web_app` or `uvicorn src.web_app:app --reload`.
- Add required dependencies to `requirements.txt` if needed, for example `fastapi`, `uvicorn`, `jinja2`, and `python-multipart`.
- Add tests for the frontend routes and preflight helpers where practical.
- Update `README.md` with exact commands to run the frontend and the CLI.
- Do not make a fake UI. Buttons must either work, call real backend endpoints, or be disabled with a clear reason.

Constraints:
- Do not commit secrets, contact lists, audio, logs, or browser profiles.
- Do not replace Selenium with Twilio or another telephony API.
- Keep changes small and testable.
- Preserve existing tests and add focused tests for any new runtime behavior.
- Do not expose `.env` values or API keys in the frontend.
- Do not put private contact-list contents in screenshots, generated assets, or committed fixtures.

When done, provide:
- Exact command to run one live test call.
- Exact command to run the frontend locally.
- Exact command to launch the branded console/desktop experience without manually opening a localhost URL, if implemented.
- Audio routing instructions for the detected devices.
- What was implemented in the frontend and what is still intentionally disabled.
- Evidence that the app waits for answered-call evidence before the opening line.
- Evidence that audio output device unavailability is caught before dialing.
- Remaining risks that need human validation.
