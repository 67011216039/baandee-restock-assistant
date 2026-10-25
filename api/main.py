"""Supplier API (mock) — simulates the suppliers' own system of record.

This is the project's system boundary: the Restock Assistant does NOT read this
data from a file; it sends an HTTP request and must handle three outcomes.

    GET /health
    GET /suppliers/{supplier_id}/skus/{sku}
        200  live terms (lead_time_days, moq, supplier_status, notice, as_of)
        404  supplier has no live record for this SKU
        503  supplier portal unavailable (SUP-06 in the fixture)

Run:  uvicorn api.main:app --port 8001
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "supplier_live.json"
_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
RECORDS: dict[str, dict] = _data["records"]
DOWN_SUPPLIERS: set[str] = set(_data["down_suppliers"])

app = FastAPI(title="BaanDee Supplier API (mock)", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "supplier-api-mock", "records": len(RECORDS)}


@app.get("/suppliers/{supplier_id}/skus/{sku}")
def get_supplier_sku(supplier_id: str, sku: str) -> dict:
    supplier_id, sku = supplier_id.upper(), sku.upper()
    if supplier_id in DOWN_SUPPLIERS:
        raise HTTPException(status_code=503, detail=f"Supplier portal {supplier_id} is unavailable")
    record = RECORDS.get(f"{supplier_id}|{sku}")
    if record is None:
        raise HTTPException(status_code=404, detail=f"No live record for {supplier_id}/{sku}")
    return record
