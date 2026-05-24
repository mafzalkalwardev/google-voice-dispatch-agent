You are a senior Python automation engineer taking over a Google Voice realtime dispatch-agent project.

Goal: make the app reliable for live calls, not demos. Keep mock tests, but optimize the runtime path for a real logged-in Google Voice account, real Groq API calls, and real Windows audio routing.

Current state:
- Entry point: `python -m src.main --contacts data/contacts.xlsx --profile sales1 --limit 1`
- Realtime mode is default. Static WAV mode is only `--static-playback`.
- Connected calls use `ConversationLoop` with `AudioCapture -> EnergyVAD -> GroqWhisperSTT -> ConversationAgent -> RealtimeTTS`.
- Voicemail still uses a generated WAV fallback.
- Local-only files are ignored: `.env`, `dialer_config.json`, contacts, audio, logs, Chrome profiles.
- Tests currently pass.
- Public brand site: https://industransports.online/
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
8. Build a fully workable frontend for the app. This must be an actual operator console, not a marketing landing page.

Frontend requirements:
- The UI must visibly say `INDUS TRANSPORTS LLC` in the main header and browser title.
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
- Audio routing instructions for the detected devices.
- What was implemented in the frontend and what is still intentionally disabled.
- Remaining risks that need human validation.
