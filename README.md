# Google Voice Sales Agent

A new project scaffold for a Google Voice sales agent that uses browser-based automation (Selenium/DOM) and Groq-generated talking scripts.

## What this project does

- Uses Selenium to control Google Voice in Chrome with persistent profiles
- Avoids active screen clicking wherever possible
- Loads contact lists from Excel/CSV
- Generates call scripts and voicemail text using Groq
- Converts scripts to local TTS audio files
- Logs dial actions and call outcomes

## What this is not

- This is not a telephony API system like Twilio
- It does not inject audio directly into Google Voice without an OS audio loopback setup
- It is a prototype architecture for Google Voice browser automation and AI-driven scripts

## Requirements

- Python 3.11+
- Chrome browser installed
- `GROQ_API_KEY` environment variable set
- `GROQ_API_URL` environment variable set to your Groq text generation endpoint

## Setup

1. Create a Python virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Set the Groq and caller environment variables

```powershell
$env:GROQ_API_KEY = "YOUR_KEY"
$env:GROQ_API_URL = "https://api.groq.ai/v1/text"
$env:CALLBACK_NUMBER = "+15551234567"
$env:AGENT_NAME = "Tony"
$env:COMPANY_NAME = "Indus Transports LLC"
```

You can also copy `dialer_config.example.json` to `dialer_config.json` for local JSON configuration.

3. Add your contact file in `data/contacts.xlsx` or supply a path to `main.py`

## Usage

```powershell
python -m src.main --contacts data/contacts.xlsx --profile sales1
```

This will launch Chrome, open Google Voice, and wait for login. Once logged in, it will run the first call flow.
Use `--callback-number` or `CALLBACK_NUMBER` so voicemail scripts include your business callback number. Generated script text is saved beside each `.wav` file in `audio/scripts/` and `audio/voicemails/`.

GitHub repository suggestion
--------------------------

- **Name:** google-voice-dispatch-agent
- **Description:** Selenium-driven Google Voice sales agent with Groq-generated call scripts and local TTS. Designed for low-cost Google Voice automation and voicemails.

To publish:

```powershell
git init
git add .
git commit -m "Initial GoogleVoiceAgent scaffold"
git remote add origin https://github.com/<your-username>/google-voice-dispatch-agent.git
git push -u origin main
```

Replace `<your-username>` with your GitHub username. I can update repository contents further once you create the remote.

## Notes

- For real auto-talk, you will need to route generated audio into Chrome's microphone input using a virtual audio cable or system loopback.
- The Selenium approach here is the best path for Google Voice automation without screen-only clicks.
- The actual Groq API endpoint may differ; update `GROQ_API_URL` accordingly.

Session persistence and lower memory usage
----------------------------------------

- This app now persists Chrome profiles under `chrome_profiles/` inside the project so you do not need to sign in every run. Use the `--profile` argument to pick a profile and the browser will reuse session cookies.
- To reduce RAM, Chrome is launched with options that disable images and background services. For very low-memory systems, run fewer concurrent browser instances and avoid headless mode with complex pages.

Claude prompt for future improvements
-----------------------------------

Use this prompt with Claude (or another reasoning LLM) to iterate call scripts and feature design:

"You are a senior product engineer helping design a Google Voice automation agent. Review the following goals: persistent Chrome sessions, low-RAM operation, reliable voicemail detection, high-quality AI-generated call scripts (Groq), and safe secret handling. Propose a prioritized implementation plan with concrete function interfaces for `CallSession`, `voice_playback`, and CI secrets management. Include test cases for mock dialing and recommendations for Windows loopback audio."

