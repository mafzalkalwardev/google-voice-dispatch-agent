@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)

set "PATH=%CD%\.venv\Scripts;%PATH%"
call "%~dp0spectrum_business_quickstart.bat" %*
