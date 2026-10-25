@echo off
REM Terminal 2: Gradio app on http://127.0.0.1:7860
cd /d "%~dp0.."
python scripts\clean_data.py
python -m src.app
