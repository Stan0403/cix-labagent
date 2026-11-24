@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment is missing. Run scripts\setup.ps1 with Python 3.11 or newer.
  pause
  exit /b 1
)
set PYTHONUTF8=1
".venv\Scripts\python.exe" -m labagent
if errorlevel 1 pause
