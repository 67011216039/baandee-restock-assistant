@echo off
REM Double-click to run the whole demo (Supplier API + app) on Windows.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.11+ from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during install.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo [1/3] Creating virtual environment - first run only...
  python -m venv .venv
)
echo [2/3] Installing packages - first run takes a few minutes...
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed. Check your internet connection.
  pause
  exit /b 1
)
echo [3/3] Cleaning data and starting the demo...
.venv\Scripts\python.exe scripts\clean_data.py
echo.
echo The app opens at http://127.0.0.1:7860  - keep this window open. Close it to stop.
.venv\Scripts\python.exe -m src.app
pause
