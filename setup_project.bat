@echo off
setlocal

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%setup_project.ps1"

if not exist "%SCRIPT%" (
  echo Missing installer script: %SCRIPT%
  pause
  exit /b 1
)

echo Starting SPECTRUM CODEX setup...
echo This installer requires Python 3.12 and will rebuild a broken copied .venv if needed.
echo If winget is available, it can also install/check Chrome, VC++ runtime, and FFmpeg.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Setup failed with exit code %EXITCODE%.
  pause
)

exit /b %EXITCODE%
