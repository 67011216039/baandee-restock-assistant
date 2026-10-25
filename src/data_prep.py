"""Cleaning rules for the BaanDee Restock Assistant.

Principles
- Never modify files in data/raw or data/generated. Write results to data/cleaned.
- Fix a value only when a documented rule makes the answer certain.
- When the answer is not certain, keep the record but quarantine it for a human.
- Every change goes to correction_log; every issue gets before/after numbers in dq_report.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
GENERATED = ROOT / "data" / "generated"
CLEANED = ROOT / "data" / "cleaned"

DEMAND_WINDOW_DAYS = 30
STALE_CATALOG_DAYS = 90

CATEGORY_MAP = {"kitchen": "Kitchen", "bedding": "Bedding", "bed room": "Bedding",
                "bathroom": "Bathroom", "storage": "Storage"}

# Flags that stop the system from recommending an order automatically
BLOCKING_FLAGS = {"lead_time_unclear", "multiple_suppliers", "invalid_moq",
                  "cost_above_price", "supplier_on_hold"}


@dataclass
class Log:
    corrections: list[dict] = field(default_factory=list)
    report: list[dict] = field(default_factory=list)

    def fix(self, table: str, record: str, column: str, raw, clean, rule: str) -> None:
        self.corrections.append({"table": table, "record": record, "column": column,
                                 "raw": raw, "clean": clean, "rule": rule})

    def issue(self, **row) -> None:
        self.report.append(row)


# ---------------------------------------------------------------- small rules
SKU_PATTERN = re.compile(r"^SKU-(\d{4}|[A-Z]{2}-\d{3})$")


def normalise_sku(raw: str, known_ids: set[str]) -> str | None:
    """Return a product_id from products.csv, or None if not certain.

    Accepted repairs: trim, upper-case, space/missing dash after 'SKU',
    zero-pad a short number. The result must exist in products.csv,
    otherwise we do not guess.
    """
    if raw is None or pd.isna(raw):
        return None
    s = str(raw).strip().upper().replace(" ", "-")
    if s in known_ids:
        return s
    m = re.fullmatch(r"SKU-?(\d{1,4})", s)
    if m:
        candidate = f"SKU-{int(m.group(1)):04d}"
        return candidate if candidate in known_ids else None
    return None


def parse_lead_time(raw) -> int | None:
    """'7' / '7 days' / '7d' / '2 weeks' -> days. Ranges, TBC, blanks -> None."""
    if raw is None or pd.isna(raw):
        return None
    s = str(raw).strip().lower()
    if re.fullmatch(r"\d+", s):
        return int(s)
    m = re.fullmatch(r"(\d+)\s*(d|day|days)", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*(w|wk|week|weeks)", s)
    if m:
        return int(m.group(1)) * 7
    return None  # e.g. '1-2 weeks', 'TBC', ''


def parse_order_date(raw) -> tuple[pd.Timestamp | None, str]:
    """Return (date, quality). quality: ok | corrected | ambiguous | invalid."""
    if raw is None or pd.isna(raw) or str(raw).strip() == "":
        return None, "invalid"
    s = str(raw).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return pd.Timestamp(s), "ok"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:  # only day/month order is possible
            return pd.Timestamp(year=y, month=b, day=a), "corrected"
        if b > 12 and a <= 12:  # only month/day order is possible
            return pd.Timestamp(year=y, month=a, day=b), "corrected"
        return None, "ambiguous"  # both <= 12: cannot know without evidence
    return None, "invalid"


# ---------------------------------------------------------------- tables
def clean_products(log: Log) -> pd.DataFrame:
    p = pd.read_csv(RAW / "products.csv", dtype=str)
    before = p["category"].nunique()
    p["category_clean"] = p["category"].str.strip().str.lower().map(CATEGORY_MAP)
    for _, r in p[p["category"] != p["category_clean"]].iterrows():
        log.fix("products", r.product_id, "category", r.category, r.category_clean, "explicit category map")
    unmapped = p["category_clean"].isna().sum()
    p["unit_price_thb"] = p["unit_price_thb"].astype(int)
    log.issue(issue="Category spelling inconsistent", source="products.csv (BaanDee)",
              detected_by="value_counts() on category", rule="explicit map, e.g. 'Bed room' -> Bedding",
              before=f"{before} distinct categories", after=f"{p['category_clean'].nunique()} categories, {unmapped} unmapped",
              examples="Bed room, kitchen, bedding, storage")
    return p[["product_id", "product_name", "category_clean", "unit_price_thb"]].rename(
        columns={"category_clean": "category"})


def clean_orders(log: Log) -> tuple[pd.DataFrame, pd.DataFrame]:
    o = pd.read_csv(RAW / "orders.csv", dtype=str)
    ol = pd.read_csv(RAW / "order_lines.csv", dtype=str)
    parsed = o["order_date"].apply(parse_order_date)
    o["order_date_clean"] = parsed.apply(lambda t: t[0])
    o["date_quality"] = parsed.apply(lambda t: t[1])
    for _, r in o[o.date_quality == "corrected"].iterrows():
        log.fix("orders", r.order_id, "order_date", r.order_date,
                r.order_date_clean.date().isoformat(), "dd/mm/yyyy: day > 12 so only one reading")
    bad_before = (o.date_quality != "ok").sum()
    quarantine = o[o.date_quality.isin(["ambiguous", "invalid"])].copy()
    quarantine["reason"] = "order_date " + quarantine["date_quality"] + ": excluded from demand"
    quarantine = quarantine.merge(ol, on="order_id", how="left")
    good = o[o.date_quality.isin(["ok", "corrected"])].copy()
    lines = ol.merge(good[["order_id", "order_date_clean"]], on="order_id", how="inner")
    lines["quantity"] = lines["quantity"].astype(int)
    log.issue(issue="Order date in non-ISO / ambiguous format", source="orders.csv (BaanDee)",
              detected_by="ISO pattern check, then day/month test",
              rule="convert only when one reading is possible; otherwise quarantine",
              before=f"{bad_before} non-ISO dates",
              after=f"{(o.date_quality == 'corrected').sum()} corrected, {len(quarantine)} quarantined (not counted in demand)",
              examples=", ".join(o.loc[o.date_quality != 'ok', 'order_date'].astype(str)))
    out = lines.rename(columns={"order_date_clean": "order_date"})[["order_id", "product_id", "quantity", "order_date"]]
    return out, quarantine


def clean_inventory() -> pd.DataFrame:
    inv = pd.read_csv(RAW / "inventory_snapshot.csv", dtype={"product_id": str})
    inv["available_qty"] = inv["available_qty"].astype(int)
    return inv


def clean_supplier_catalog(products: pd.DataFrame, as_of: pd.Timestamp, log: Log
                           ) -> tuple[pd.DataFrame, pd.DataFrame]:
    c = pd.read_csv(GENERATED / "supplier_catalog.csv", dtype=str, keep_default_na=False)
    s = pd.read_csv(GENERATED / "suppliers.csv", dtype=str)
    known = set(products["product_id"])
    price = products.set_index("product_id")["unit_price_thb"]

    # 1) SKU format -------------------------------------------------------
    exact_before = c["sku"].isin(known).sum()
    missing_before = len(known - set(c["sku"]))
    c["product_id"] = c["sku"].apply(lambda v: normalise_sku(v, known))
    for _, r in c[(c.product_id.notna()) & (c.sku != c.product_id)].iterrows():
        log.fix("supplier_catalog", f"{r.supplier_id}/{r.sku!r}", "sku", r.sku, r.product_id,
                "trim/upper/dash/zero-pad; result must exist in products.csv")
    orphans = c[c.product_id.isna()].copy()
    orphans["reason"] = "SKU not found in products.csv"
    c = c[c.product_id.notna()].copy()
    log.issue(issue="SKU written in inconsistent formats", source="supplier_catalog.csv (generated)",
              detected_by="join to products.csv on exact id",
              rule="normalise format only if result exists in products; else quarantine",
              before=f"{exact_before}/{exact_before + (len(c) + len(orphans) - exact_before)} rows join; {missing_before} products without supplier",
              after=f"{len(c)}/{len(c) + len(orphans)} rows join; {len(known - set(c.product_id))} products without supplier; {len(orphans)} orphan row quarantined",
              examples="sku-0012, SKU0027, ' SKU-0033 ', sku 0045, SKU-61")

    # 2) lead time ----------------------------------------------------------
    numeric_before = c["lead_time"].str.fullmatch(r"\d+").sum()
    c["lead_time_days"] = c["lead_time"].apply(parse_lead_time).astype("Int64")
    for _, r in c[(c.lead_time_days.notna()) & (c.lead_time != c.lead_time_days.astype(str))].iterrows():
        log.fix("supplier_catalog", f"{r.supplier_id}/{r.product_id}", "lead_time", r.lead_time,
                int(r.lead_time_days), "unit conversion (days/d/weeks)")
    unclear = c.lead_time_days.isna()
    log.issue(issue="Lead time in mixed units or not a number", source="supplier_catalog.csv (generated)",
              detected_by="integer parse check", rule="convert clear units; ranges/TBC/blank -> human",
              before=f"{numeric_before}/{len(c)} usable as number",
              after=f"{c.lead_time_days.notna().sum()}/{len(c)} usable; {unclear.sum()} need buyer to confirm",
              examples=", ".join(repr(v) for v in c.loc[c.lead_time != c.lead_time_days.astype(str), 'lead_time']))

    # 3) other checks -------------------------------------------------------
    for col in ["moq", "pack_size", "unit_cost_thb"]:
        c[col] = pd.to_numeric(c[col], errors="coerce").astype("Int64")
    c["catalog_updated_at"] = pd.to_datetime(c["catalog_updated_at"])
    c = c.merge(s[["supplier_id", "supplier_name", "status"]].rename(columns={"status": "supplier_status"}),
                on="supplier_id", how="left")

    def flags(r) -> str:
        f = []
        if pd.isna(r.lead_time_days):
            f.append("lead_time_unclear")
        if pd.isna(r.moq) or r.moq <= 0:
            f.append("invalid_moq")
        if pd.notna(r.unit_cost_thb) and r.unit_cost_thb > price[r.product_id]:
            f.append("cost_above_price")
        if r.supplier_status != "active":
            f.append("supplier_on_hold")
        if (as_of - r.catalog_updated_at).days > STALE_CATALOG_DAYS:
            f.append("stale_catalog")
        return ";".join(f)

    c["flags"] = c.apply(flags, axis=1)
    dup = c["product_id"].duplicated(keep=False)
    c.loc[dup, "flags"] = c.loc[dup, "flags"].apply(lambda v: ";".join(filter(None, [v, "multiple_suppliers"])))
    log.issue(issue="Same SKU offered by two suppliers with different terms", source="supplier_catalog.csv (generated)",
              detected_by="duplicated(product_id)", rule="never auto-pick; buyer chooses preferred supplier",
              before=f"{c.loc[dup, 'product_id'].nunique()} SKUs with 2 suppliers (silently double-counted in a join)",
              after=f"{c.loc[dup, 'product_id'].nunique()} SKUs flagged multiple_suppliers -> human",
              examples=", ".join(sorted(c.loc[dup, "product_id"].unique())))
    invalid = c["flags"].str.contains("invalid_moq|cost_above_price")
    log.issue(issue="Impossible values (MOQ <= 0, cost above selling price)", source="supplier_catalog.csv (generated)",
              detected_by="range validation", rule="flag, no automatic fix",
              before=f"{invalid.sum()} rows accepted without check", after=f"{invalid.sum()} rows flagged -> human",
              examples=", ".join(c.loc[invalid, "product_id"]))
    stale = c["flags"].str.contains("stale_catalog")
    log.issue(issue="Stale supplier catalog", source="supplier_catalog.csv (generated)",
              detected_by=f"catalog_updated_at older than {STALE_CATALOG_DAYS} days vs inventory as_of",
              rule="keep, warn in UI; live Supplier API value wins when available",
              before="timestamp not shown", after=f"{stale.sum()} rows flagged stale_catalog",
              examples=", ".join(c.loc[stale, "supplier_id"] + "/" + c.loc[stale, "product_id"]))

    blocking = c["flags"].apply(lambda v: bool(set(v.split(";")) & BLOCKING_FLAGS))
    review = c[blocking].copy()
    review["reason"] = review["flags"]
    quarantine = pd.concat([orphans, review], ignore_index=True)
    cols = ["supplier_id", "supplier_name", "supplier_status", "sku", "product_id", "lead_time",
            "lead_time_days", "moq", "pack_size", "unit_cost_thb", "catalog_updated_at", "flags"]
    return c[cols].rename(columns={"sku": "sku_raw", "lead_time": "lead_time_raw"}), quarantine


def run() -> dict[str, pd.DataFrame]:
    CLEANED.mkdir(parents=True, exist_ok=True)
    log = Log()
    products = clean_products(log)
    inventory = clean_inventory()
    as_of = pd.to_datetime(inventory["as_of"]).max()
    demand_lines, q_orders = clean_orders(log)
    catalog, q_catalog = clean_supplier_catalog(products, as_of, log)

    out = {
        "products_clean": products,
        "inventory_clean": inventory,
        "order_lines_clean": demand_lines,
        "supplier_catalog_clean": catalog,
        "quarantine_orders": q_orders,
        "quarantine_supplier_catalog": q_catalog,
        "correction_log": pd.DataFrame(log.corrections),
        "dq_report": pd.DataFrame(log.report),
    }
    for name, df in out.items():
        df.to_csv(CLEANED / f"{name}.csv", index=False)
    return out


if __name__ == "__main__":
    for name, df in run().items():
        print(f"{name:32s} {len(df):5d} rows")
