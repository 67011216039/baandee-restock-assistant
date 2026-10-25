"""Generate the synthetic supplier source for the BaanDee Restock Assistant.

This is the team's NEW data source (role: who supplies each SKU, how long it
takes, and the minimum order). It is deliberately "messy" in documented ways so
that the cleaning step has real problems to detect. Every injected problem is
listed in INJECTED_ISSUES and written to data/generated/INJECTED_ISSUES.md.

Run:  python scripts/generate_supplier_data.py
Output is deterministic (fixed seed), so rerunning gives identical files.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "generated"
SEED = 20261024

SUPPLIERS = [
    # supplier_id, name, base lead time (days), status, updated_at
    ("SUP-01", "Siam Kitchenware Co. (synthetic)", 7, "active", "2026-07-25T09:00:00+07:00"),
    ("SUP-02", "Northern Textile Partners (synthetic)", 14, "active", "2026-07-20T09:00:00+07:00"),
    ("SUP-03", "Bangna Bath & Home (synthetic)", 5, "active", "2026-07-28T09:00:00+07:00"),
    ("SUP-04", "Easy Storage Supply (synthetic)", 10, "active", "2026-07-15T09:00:00+07:00"),
    ("SUP-05", "Eastern Appliance Import (synthetic)", 21, "on_hold", "2026-07-29T09:00:00+07:00"),
    ("SUP-06", "Old Catalog Trading (synthetic)", 12, "active", "2026-03-01T09:00:00+07:00"),
]

# Main supplier per normalised category
CATEGORY_SUPPLIER = {"kitchen": "SUP-01", "bedding": "SUP-02", "bathroom": "SUP-03", "storage": "SUP-04"}
CATEGORY_MAP = {"kitchen": "kitchen", "bedding": "bedding", "bed room": "bedding",
                "bathroom": "bathroom", "storage": "storage"}

INJECTED_ISSUES: list[tuple[str, str, str]] = []  # (issue, records, how the cleaner should treat it)


def note(issue: str, records: str, treatment: str) -> None:
    INJECTED_ISSUES.append((issue, records, treatment))


def main() -> None:
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    with (RAW / "products.csv").open(encoding="utf-8") as f:
        products = list(csv.DictReader(f))

    base_lt = {s[0]: s[2] for s in SUPPLIERS}
    sup_updated = {s[0]: s[4] for s in SUPPLIERS}
    rows: list[dict] = []
    for p in products:
        cat = CATEGORY_MAP[p["category"].strip().lower()]
        sup = CATEGORY_SUPPLIER[cat]
        price = int(p["unit_price_thb"])
        rows.append({
            "supplier_id": sup,
            "sku": p["product_id"],
            "lead_time": str(max(2, base_lt[sup] + rng.randint(-2, 3))),
            "moq": str(rng.choice([6, 10, 12, 20, 24])),
            "pack_size": str(rng.choice([1, 2, 6])),
            "unit_cost_thb": str(round(price * rng.uniform(0.45, 0.65))),
            "catalog_updated_at": sup_updated[sup],
        })

    by_sku = {r["sku"]: r for r in rows}
    # Supplier's own "true" terms, captured before any mess is injected.
    # Used only to build the Supplier API fixture (the live system of record).
    true_terms = {r["sku"]: (r["supplier_id"], int(r["lead_time"]), int(r["moq"])) for r in rows}

    # --- Issue 1: SKU written in inconsistent formats -------------------------
    variants = {"SKU-0012": "sku-0012", "SKU-0027": "SKU0027", "SKU-0033": " SKU-0033 ",
                "SKU-0045": "sku 0045", "SKU-0061": "SKU-61"}
    for real, messy in variants.items():
        by_sku[real]["sku"] = messy
    note("SKU format inconsistent (lowercase, missing dash, spaces, unpadded number)",
         ", ".join(repr(v) for v in variants.values()),
         "Normalise only when the result matches ^SKU-\\d{4}$ AND exists in products.csv; log in correction_log.")

    # --- Issue 2: lead_time written with mixed units / unparseable ------------
    lt_messy = {"SKU-0003": "5 days", "SKU-0008": "2 weeks", "SKU-0016": "7d",
                "SKU-0021": "1-2 weeks", "SKU-0039": "TBC", "SKU-0050": ""}
    for sku, val in lt_messy.items():
        by_sku[sku]["lead_time"] = val
    note("lead_time mixed units or not a number",
         ", ".join(f"{k}={v!r}" for k, v in lt_messy.items()),
         "Convert clear units (days/d/weeks) to integer days. Ranges ('1-2 weeks'), 'TBC' and blanks are "
         "NOT guessed: quarantine for buyer to confirm.")

    # --- Issue 3: same SKU offered by two suppliers with conflicting terms ----
    dup_skus = ["SKU-KT-104", "SKU-0005", "SKU-0025"]
    for sku in dup_skus:
        base = by_sku[sku]
        rows.append({**base, "supplier_id": "SUP-05" if sku == "SKU-KT-104" else "SUP-06",
                     "lead_time": str(int(base["lead_time"] or 7) + 9),
                     "moq": "48", "catalog_updated_at": sup_updated["SUP-05" if sku == "SKU-KT-104" else "SUP-06"]})
    note("Duplicate SKU: two suppliers with different lead time / MOQ",
         ", ".join(dup_skus),
         "Do not pick a supplier automatically. Flag 'needs preferred supplier' for human review.")

    # --- Issue 4: catalog SKU that does not exist + product with no supplier ---
    rows.append({"supplier_id": "SUP-04", "sku": "SKU-0099", "lead_time": "10", "moq": "12",
                 "pack_size": "1", "unit_cost_thb": "150", "catalog_updated_at": sup_updated["SUP-04"]})
    no_supplier = "SKU-0048"
    rows = [r for r in rows if r["sku"] != no_supplier]
    note("Referential mismatch: orphan catalog row and product without any supplier",
         f"SKU-0099 (not in products.csv); {no_supplier} (in products, no catalog row)",
         "Orphan row goes to quarantine. Product without supplier gets status 'cannot recommend - human'.")

    # --- Issue 5: impossible values -----------------------------------------
    by_sku["SKU-0030"]["moq"] = "0"
    by_sku["SKU-0057"]["moq"] = "-12"
    by_sku["SKU-0070"]["unit_cost_thb"] = str(int(next(p for p in products if p["product_id"] == "SKU-0070")["unit_price_thb"]) * 3)
    note("Impossible values: MOQ <= 0, unit cost above selling price",
         "SKU-0030 moq=0, SKU-0057 moq=-12, SKU-0070 cost = 3x selling price",
         "Validation rule rejects the value; row kept but flagged, no automatic fix.")

    # --- Issue 6: stale catalog --------------------------------------------
    note("Stale source: SUP-06 catalog last updated 2026-03-01 (~5 months before inventory snapshot)",
         "All SUP-06 rows",
         "Keep, but mark as 'stale' and show the timestamp in the UI; live Supplier API value wins when available.")

    rng.shuffle(rows)
    fields = ["supplier_id", "sku", "lead_time", "moq", "pack_size", "unit_cost_thb", "catalog_updated_at"]
    with (OUT / "supplier_catalog.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    with (OUT / "suppliers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["supplier_id", "supplier_name", "contact_email", "status", "updated_at"])
        for sid, name, _lt, status, upd in SUPPLIERS:
            w.writerow([sid, name, f"{sid.lower()}@example.test", status, upd])

    lines = ["# Injected data-quality issues (generated source)", "",
             f"Generated by `scripts/generate_supplier_data.py` with seed {SEED}. Synthetic data only.", "",
             "| # | Issue | Records | Intended treatment |", "|---|---|---|---|"]
    for i, (issue, recs, treat) in enumerate(INJECTED_ISSUES, 1):
        lines.append(f"| {i} | {issue} | {recs} | {treat} |")
    (OUT / "INJECTED_ISSUES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_api_fixture(true_terms)
    print(f"supplier_catalog.csv: {len(rows)} rows; suppliers.csv: {len(SUPPLIERS)} rows")


def write_api_fixture(true_terms: dict) -> None:
    """Live supplier records served by api/main.py (the Supplier API mock).

    Design of the fixture (so the integration visibly changes the result):
    - Most SKUs: live lead time equals the supplier's true value.
    - SKU-0039 (catalog says 'TBC'): API confirms 9 days -> system can recommend.
    - SKU-0001: supplier posted a delay notice, live lead time +6 days vs catalog.
    - SKU-0021 and SKU-0050: supplier has no live record -> API returns 404.
    - SKU-0048: no supplier at all -> 404.
    - SUP-06 products: supplier portal down -> API returns 503 (handled in api/main.py).
    - SUP-05 is on hold.
    """
    live_as_of = "2026-07-31T08:00:00+07:00"
    status = {s[0]: s[3] for s in SUPPLIERS}
    live: dict[str, dict] = {}
    for sku, (sup, lt, moq) in true_terms.items():
        if sku in {"SKU-0021", "SKU-0050", "SKU-0048"}:
            continue
        rec = {"supplier_id": sup, "sku": sku, "lead_time_days": lt, "moq": moq,
               "supplier_status": status[sup], "notice": None, "as_of": live_as_of}
        if sku == "SKU-0039":
            rec["lead_time_days"] = 9
            rec["notice"] = "Lead time confirmed by supplier (catalog said TBC)."
        if sku == "SKU-0001":
            rec["lead_time_days"] = lt + 6
            rec["notice"] = "Delay notice: factory maintenance, +6 days."
        live[f"{sup}|{sku}"] = rec
    # Second suppliers of duplicate SKUs
    live["SUP-05|SKU-KT-104"] = {"supplier_id": "SUP-05", "sku": "SKU-KT-104",
                                 "lead_time_days": true_terms["SKU-KT-104"][1] + 9, "moq": 48,
                                 "supplier_status": "on_hold", "notice": "Supplier account on hold.",
                                 "as_of": live_as_of}
    fixture_dir = ROOT / "api" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    import json
    (fixture_dir / "supplier_live.json").write_text(
        json.dumps({"down_suppliers": ["SUP-06"], "records": live}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


if __name__ == "__main__":
    main()
