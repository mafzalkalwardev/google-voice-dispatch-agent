@echo off
setlocal

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\client_setup.ps1"

if not exist "%SCRIPT%" (
  echo Missing installer script: %SCRIPT%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
  echo.
  echo Install failed with exit code %EXITCODE%.
  pause
)

exit /b %EXITCODE%
