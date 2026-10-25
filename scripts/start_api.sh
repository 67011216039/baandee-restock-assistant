#!/usr/bin/env bash
# Terminal 1: Supplier API mock on http://127.0.0.1:8001 (docs: /docs)
cd "$(dirname "$0")/.." && python -m uvicorn api.main:app --port 8001
