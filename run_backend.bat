@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Run setup_project.bat first.
  pause
  exit /b 1
)

echo Starting SPECTRUM CODEX backend at http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn src.web_app:app --host 127.0.0.1 --port 8000
pause
