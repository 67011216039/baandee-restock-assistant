#!/usr/bin/env bash
# Run the whole demo (Supplier API + app) on macOS / Linux / Codespaces:  bash run_demo.sh
set -e
cd "$(dirname "$0")"
PY=python3; command -v python3 >/dev/null || PY=python
if [ -z "$CODESPACES" ] && [ ! -x .venv/bin/python ]; then
  echo "[1/3] Creating virtual environment (first run only)..."; $PY -m venv .venv
fi
[ -x .venv/bin/python ] && PY=.venv/bin/python
echo "[2/3] Installing packages..."
$PY -m pip install -q --disable-pip-version-check -r requirements.txt
echo "[3/3] Cleaning data and starting the demo..."
$PY scripts/clean_data.py
echo "The app runs at http://127.0.0.1:7860  (Ctrl+C to stop)"
$PY -m src.app
