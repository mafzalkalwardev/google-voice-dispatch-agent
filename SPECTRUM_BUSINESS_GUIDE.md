# Spectrum Business AI Sales Agent

This document explains how to run the Google Voice Dispatch Agent in Spectrum Business mode to execute outbound B2B sales calls for fiber internet, phone services, and bundled packages.

## Overview

The AI agent has been converted to support **Spectrum Business** B2B sales calls. All original freight dispatch functionality remains intact. You can now switch between:
- **dispatch** (default): Freight dispatch agent (original Tony persona)
- **spectrum**: Spectrum Business sales agent (Jason persona)

## Key Changes

### New Files
- `src/spectrum_business_agent.py` — Spectrum Business conversation agent with integrated sales script
- `spectrum_business_quickstart.sh` — Linux/Mac launcher
- `spectrum_business_quickstart.bat` — Windows launcher

### Modified Files
- `src/main.py` — Added `--agent-type` parameter with "dispatch" and "spectrum" options
- All agent instantiation points updated to conditionally use either agent based on type

## The Spectrum Business Script

The Spectrum Business agent follows this call flow:

```
1. Warm greeting
   "Hi, this is Jason calling from Spectrum Business. How are you doing today?"

2. Reason for call
   "I'm calling to let you know we've expanded our fiber network in your area."

3. Value proposition
   "This means we can now offer faster internet speeds, more reliable phone services, 
    and cost-effective bundled packages with improved overall performance."

4. Call to action
   "What we'd like to do is schedule a quick visit from one of our technicians 
    to install the services and get everything set up for your business."

5. Appointment scheduling
   "We currently have appointments available Monday through Friday, 8 AM to 4 PM 
    over the next two weeks. What day and time would work best for you?"
```

## Running Spectrum Business Agent

### Quick Start (Windows)
```batch
spectrum_business_quickstart.bat contacts.csv "Jason" "+15551234567"
```

### Quick Start (Linux/Mac)
```bash
./spectrum_business_quickstart.sh contacts.csv "Jason" "+15551234567"
```

### Manual Command Line
```bash
# Dry run (no dialing, generate scripts only)
python -m src.main --agent-type spectrum --contacts contacts.csv --dry-run

# Realtime conversation mode
python -m src.main --agent-type spectrum --contacts contacts.csv --realtime --limit 5

# Static playback mode
python -m src.main --agent-type spectrum --contacts contacts.csv --static-playback --limit 10
```

### All Parameters
```bash
python -m src.main \
  --agent-type spectrum \
  --contacts contacts.csv \
  --agent-name "Jason" \
  --callback-number "+15551234567" \
  --profile "Default" \
  --realtime \
  --limit 10 \
  --loopback-device "CABLE Output" \
  --capture-device "CABLE Input"
```

## Conversation Agent Differences

### Spectrum Business Agent
- **Personality**: Jason from Spectrum Business
- **Goal**: Schedule technician visit for fiber/phone service installation
- **Timeframe**: Next 2 weeks, Monday-Friday, 8 AM-4 PM
- **Value Props**: Faster speeds, reliability, cost savings
- **Objection Handling**: Coverage-aware, service comparison focused
- **No Guarantees**: Honest about service availability

### Dispatch Agent (Original)
- **Personality**: Tony from Indus Transports
- **Goal**: Qualify carrier, book dispatch onboarding call
- **Timeframe**: This week, flexible
- **Value Props**: Load rates, dispatch support, 48-state coverage
- **Objection Handling**: Load guarantees, rate comparison

## Contact File Format

The contact list can be CSV or Excel (.xlsx). Required columns:
- `Phone` or `phone` — Business phone number to call
- `Name` or `name` — Business/contact name (optional but recommended)
- `Company` or `company` — Company name (optional)
- `Email` or `email` — Email address for follow-up (optional)

Example CSV:
```csv
Name,Company,Phone,Email
John Smith,ABC Corp,+1-555-123-4567,john@abccorp.com
Sarah Jones,XYZ LLC,+1-555-987-6543,sarah@xyzllc.com
```

## Audio & Conversation Features

### Chrome & Google Voice (Keep Open)
- Chrome browser remains open throughout the call
- Google Voice dialing interface fully functional
- Manual dial capability preserved
- Call history visible in Google Voice

### Realtime Conversation Loop
When `--realtime` is used:
1. Agent generates opening line
2. Waits for call pickup
3. Agent speaks via virtual audio cable
4. Captures call audio and transcribes with Whisper
5. Generates real-time responses with Groq
6. Continues until call end or max duration

### Static Playback Mode
When `--static-playback` is used:
1. Scripts generated in advance
2. Audio files created with edge-tts
3. Plays back audio during call (no realtime LLM)
4. No speech recognition during call

## Configuration

### Default Settings (`.env` / `config.json`)
```json
{
  "groq_api_key": "gsk_...",
  "contacts_file": "contacts.csv",
  "loopback_device": "CABLE Output",
  "capture_device": "CABLE Input",
  "stt_model": "whisper-large-v3-turbo",
  "call_timeout": 45,
  "call_max_duration": 600
}
```

### Override at Runtime
```bash
python -m src.main --agent-type spectrum \
  --loopback-device "VB-Audio Virtual Cable Output" \
  --capture-device "VB-Audio Virtual Cable Input" \
  --call-max-duration 900
```

## Logging & Transcripts

All calls are logged to:
- **Logs**: `logs/call_logs.json`
- **Transcripts**: `logs/transcripts/<phone>_<timestamp>.txt`
- **Recordings**: `logs/recordings/<phone>_<timestamp>.wav`
- **Audio Scripts**: `audio/<phone>_<timestamp>.wav`

## Switching Between Agent Types

Both agents remain fully functional:

```bash
# Run original freight dispatch agent
python -m src.main --agent-type dispatch --contacts carriers.csv

# Run Spectrum Business agent
python -m src.main --agent-type spectrum --contacts businesses.csv
```

## Compliance & Legal

**Important**: This tool makes real calls. You are responsible for:
- ✓ Business owner/manager consent
- ✓ Google Voice account terms of service
- ✓ Caller ID regulations & spoofing laws
- ✓ Do Not Call (DNC) list compliance
- ✓ Recording disclosure (if applicable)
- ✓ State & local telemarketing laws
- ✓ Spectrum Business brand compliance
- ✓ No spam, harassment, or excessive calling

## Objection Handling (Spectrum)

The agent responds naturally to common objections:

| Objection | Agent Response |
|-----------|-----------------|
| "Who is this?" | "Hi, this is Jason from Spectrum Business. We recently expanded our fiber network..." |
| "Not interested" | "I understand. Many businesses see savings from our bundled packages. Could we set up a quick visit?" |
| "Already have service" | "Got it. We often help businesses compare providers and find better rates. Could we set up a visit?" |
| "What's the cost?" | "Pricing varies based on location and needs. Our tech can give you exact pricing during a brief setup visit." |
| "Too expensive" | "Many businesses see savings from our bundles. Let's have a tech assess your current setup." |
| "Busy" | "No problem — when works better? We have slots Monday through Friday, 8 AM to 4 PM." |
| "Remove me" | "I'll note that and mark you do-not-call. Thanks for your time." |

## Troubleshooting

### Agent Not Using Spectrum Script
Check that you're using `--agent-type spectrum` (not `dispatch`):
```bash
python -m src.main --agent-type spectrum --contacts contacts.csv
```

### Google Voice Not Picking Up
- Chrome must be in focus or Google Voice must be open
- Ensure virtual audio cable is working (loopback device)
- Check that Chrome microphone is set to your loopback output

### Speech Recognition Issues
- Install latest `openai-whisper`: `pip install --upgrade openai-whisper`
- Check audio input device is correctly set: `--capture-device "your device name"`
- Run diagnostic: `python -m src.main --list-audio-devices`

### API Rate Limits
- Groq API calls are made per turn; rate limits are generous
- If hitting limits, reduce `--limit` or add delays between calls

## Advanced Usage

### Custom Agent Parameters
```bash
python -m src.main --agent-type spectrum \
  --contacts contacts.csv \
  --agent-name "David" \
  --callback-number "+1-800-SPECTRUM" \
  --objective "Schedule a fiber assessment and quote" \
  --offer "Fiber speeds up to 1 Gbps, enterprise SIP phone, email, and cloud backup"
```

### Dry Run With Spectrum Agent
```bash
python -m src.main --agent-type spectrum \
  --contacts contacts.csv \
  --dry-run
# Generates scripts and audio without calling
```

### Single Contact Test
```bash
python -m src.main --agent-type spectrum \
  --contacts contacts.csv \
  --limit 1 \
  --realtime
```

## Switching Back to Dispatch

Original dispatch agent remains unchanged:
```bash
python -m src.main --agent-type dispatch \
  --contacts carriers.csv \
  --agent-name "Tony" \
  --company-name "Indus Transports LLC"
```

## Support & Feedback

For issues or improvements to the Spectrum agent:
1. Check logs: `logs/call_logs.json`
2. Review transcripts: `logs/transcripts/`
3. Test with `--dry-run` first
4. Verify audio setup: `python -m src.main --list-audio-devices`

---

**Agent Type**: Spectrum Business  
**Persona**: Jason  
**Call Type**: B2B (Business-to-Business)  
**Service**: Fiber Internet, Phone, Bundled Packages  
**Chrome**: Remains open (Google Voice)  
**Audio**: Virtual cable (VB-Audio, CABLE, or Voicemeeter)
