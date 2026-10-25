"""Start the Supplier API on a test port for the whole test session."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEST_PORT = 8011
os.environ["SUPPLIER_API_URL"] = f"http://127.0.0.1:{TEST_PORT}"


def start_test_api() -> None:
    import uvicorn
    from api.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=TEST_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            requests.get(f"http://127.0.0.1:{TEST_PORT}/health", timeout=0.5)
            return
        except requests.RequestException:
            time.sleep(0.1)
    raise RuntimeError("Supplier API did not start")


@pytest.fixture(scope="session", autouse=True)
def supplier_api():
    from src import data_prep
    data_prep.run()  # rebuild data/cleaned from raw + generated
    start_test_api()
    yield
