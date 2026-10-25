"""Run all test cases and write tests/TEST_RESULTS.md (input / expected / actual / pass-fail).

Run:  python scripts/run_test_report.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import conftest  # noqa: E402  (sets SUPPLIER_API_URL before src is imported)
from src import data_prep  # noqa: E402

data_prep.run()
conftest.start_test_api()
from tests.cases import CASES  # noqa: E402


def cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


rows, n_pass = [], 0
for i, case in enumerate(CASES, 1):
    try:
        inp, exp, act, ok = case()
    except Exception as exc:  # a crash is a failure, not a missing row
        inp, exp, act, ok = "-", "-", f"ERROR {type(exc).__name__}: {exc}", False
    n_pass += ok
    rows.append(f"| T{i:02d} | {case.__name__[4:]} | {cell(inp)} | {cell(exp)} | {cell(act)} | {'PASS' if ok else 'FAIL'} |")

out = ["# Test results", "",
       f"Run at {datetime.now().astimezone().isoformat(timespec='seconds')} · {n_pass}/{len(CASES)} passed", "",
       "| ID | Case | Input | Expected | Actual | Result |", "|---|---|---|---|---|---|", *rows]
(ROOT / "tests" / "TEST_RESULTS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
print("\n".join(out))
sys.exit(0 if n_pass == len(CASES) else 1)
