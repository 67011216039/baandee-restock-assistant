"""Client for the Supplier API. Never raises: always returns a SupplierResult.

outcome:
    ok           200, data validated
    not_found    404
    unavailable  503, timeout, connection refused, or invalid response
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

DEFAULT_URL = os.getenv("SUPPLIER_API_URL", "http://127.0.0.1:8001")
DEFAULT_TIMEOUT = float(os.getenv("SUPPLIER_API_TIMEOUT_SECONDS", "3"))
REQUIRED_FIELDS = {"supplier_id", "sku", "lead_time_days", "moq", "supplier_status", "as_of"}


@dataclass
class SupplierResult:
    outcome: str               # ok | not_found | unavailable
    data: dict | None
    message: str
    checked_at: str            # when WE asked the API


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def fetch_live_terms(supplier_id: str, sku: str, base_url: str | None = None,
                     timeout: float | None = None) -> SupplierResult:
    url = f"{(base_url or DEFAULT_URL).rstrip('/')}/suppliers/{supplier_id}/skus/{sku}"
    try:
        resp = requests.get(url, timeout=timeout or DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        return SupplierResult("unavailable", None, f"Cannot reach Supplier API ({type(exc).__name__})", _now())

    if resp.status_code == 404:
        return SupplierResult("not_found", None, "Supplier has no live record for this SKU", _now())
    if resp.status_code != 200:
        return SupplierResult("unavailable", None, f"Supplier API returned HTTP {resp.status_code}", _now())
    try:
        data = resp.json()
    except ValueError:
        return SupplierResult("unavailable", None, "Supplier API returned invalid JSON", _now())
    # Validate before we let the response change any decision
    missing = REQUIRED_FIELDS - set(data)
    if missing or not isinstance(data.get("lead_time_days"), int) or data["lead_time_days"] <= 0:
        return SupplierResult("unavailable", None, f"Supplier API response failed validation ({sorted(missing) or 'lead_time_days'})", _now())
    if data["sku"].upper() != sku.upper() or data["supplier_id"].upper() != supplier_id.upper():
        return SupplierResult("unavailable", None, "Supplier API returned a different record than requested", _now())
    return SupplierResult("ok", data, "Live terms received", _now())
