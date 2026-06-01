# Client Install

Use this when installing the source version on a Windows client machine.

## Client Requirements

- Windows 10 or 11
- Python 3.10 or newer with `python.exe` added to PATH
- Google Chrome
- VB-CABLE or equivalent virtual audio cable for live call audio routing
- Groq API key
- Google Voice account that can place calls

## Install

1. Copy this project folder to the client computer.
2. Double-click `Install-Client.bat`.
3. Wait for setup to finish. It will:
   - create `.venv`
   - install Python dependencies
   - create `.env` if missing
   - create `dialer_config.json` if missing
   - create runtime folders
   - create a Desktop shortcut
   - launch the web console
4. Edit `.env` and `dialer_config.json`.
5. Open the console and run Preflight before dialing.

The console opens at:

```text
http://127.0.0.1:8000/run
```

## Required Config Values

In `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
CALLBACK_NUMBER=your_google_voice_callback_number
CONTACTS_FILE=data/contacts.xlsx
PROFILE_NAME=sales_profile
CAPTURE_DEVICE=default
```

In `dialer_config.json`, confirm:

```json
{
  "contacts_file": "data/contacts.xlsx",
  "profile_name": "sales_profile",
  "callback_number": "+15551234567",
  "loopback_device": "CABLE Input",
  "capture_device": "default"
}
```

## Useful Commands

Install without launching:

```bat
Install-Client.bat -NoLaunch
```

Install without creating a shortcut:

```bat
Install-Client.bat -NoShortcut
```

Launch later:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-IndusConsole.ps1
```

Use another port:

```bat
Install-Client.bat -Port 8787
```

## Before Live Dialing

- Log into Google Voice in the Chrome profile used by the app.
- Confirm VB-CABLE appears in Windows sound devices.
- Put the contacts file at the configured path.
- Run the Preflight page.
- Make sure the client is responsible for consent, DNC rules, recording disclosure, and Google Voice terms.
