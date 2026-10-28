@echo off
REM Spectrum Business Sales Agent Quick Start (Windows)
REM Usage: spectrum_business_quickstart.bat [contacts_file] [agent_name]

setlocal enabledelayedexpansion

set CONTACTS_FILE=%1
if "!CONTACTS_FILE!"=="" set CONTACTS_FILE=contacts.csv

set AGENT_NAME=%2
if "!AGENT_NAME!"=="" set AGENT_NAME=Jason

set CALLBACK_NUMBER=%3
if "!CALLBACK_NUMBER!"=="" set CALLBACK_NUMBER=+15551234567

echo.
echo ================================
echo Spectrum Business Agent Launcher
echo ================================
echo.
echo Configuration:
echo   Agent Name: !AGENT_NAME!
echo   Callback Number: !CALLBACK_NUMBER!
echo   Contacts File: !CONTACTS_FILE!
echo.
echo Starting Google Voice spectrum Agent in Spectrum Business mode...
echo.

REM Run with Spectrum Business agent type
python -m src.main ^
  --agent-type spectrum ^
  --contacts !CONTACTS_FILE! ^
  --agent-name !AGENT_NAME! ^
  --callback-number !CALLBACK_NUMBER! ^
  --realtime ^
  --limit 10

echo.
echo Spectrum Business campaign complete!
pause
