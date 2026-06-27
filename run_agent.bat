@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m src.main --agent-type spectrum --realtime %*
pause
