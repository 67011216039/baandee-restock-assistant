@echo off
REM Terminal 1: Supplier API mock on http://127.0.0.1:8001
cd /d "%~dp0.."
python -m uvicorn api.main:app --port 8001
