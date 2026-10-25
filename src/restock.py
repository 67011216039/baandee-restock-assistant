"""Restock decision rules. Pure rules, no AI.

decision:
    RECOMMEND     risk HIGH/MEDIUM, all inputs usable -> suggest a quantity
    NO_ACTION     enough stock for lead time + safety days (or no demand)
    HUMAN_REVIEW  the system must stop: inputs missing, conflicting or invalid
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

from src.data_prep import CLEANED, DEMAND_WINDOW_DAYS
from src.supplier_client import fetch_live_terms

SAFETY_DAYS = 7          # safety stock = 7 days of average demand
COVER_TARGET_DAYS = 30   # after arrival, aim to cover 30 more days


@dataclass
class Fact:
    label: str
    value: str
    source: str
    as_of: str


@dataclass
class Assessment:
    product_id: str
    product_name: str
    decision: str
    risk: str
    reasons: list[str]
    verified: list[Fact] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    supplier_options: list[dict] = field(default_factory=list)
    supplier_id: str | None = None
    lead_time_days: int | None = None
    avg_daily_demand: float | None = None
    days_of_cover: float | None = None
    suggested_qty: int | None = None
    api_outcome: str = "not_called"


@lru_cache(maxsize=1)
def load_tables(cleaned_dir: str = str(CLEANED)) -> dict[str, pd.DataFrame]:
    d = Path(cleaned_dir)
    if not (d / "supplier_catalog_clean.csv").exists():
        from src import data_prep
        data_prep.run()
    t = {name: pd.read_csv(d / f"{name}.csv", dtype={"product_id": str})
         for name in ["products_clean", "inventory_clean", "order_lines_clean",
                      "supplier_catalog_clean", "quarantine_orders"]}
    t["supplier_catalog_clean"]["flags"] = t["supplier_catalog_clean"]["flags"].fillna("")
    return t


def demand_for(product_id: str, t: dict, as_of: pd.Timestamp) -> tuple[float, int, str, str]:
    lines = t["order_lines_clean"]
    lines_date = pd.to_datetime(lines["order_date"])
    start = (as_of.tz_localize(None) if as_of.tzinfo else as_of).normalize() - pd.DateOffset(days=DEMAND_WINDOW_DAYS)
    end = (as_of.tz_localize(None) if as_of.tzinfo else as_of).normalize()
    win = lines[(lines.product_id == product_id) & (lines_date >= start) & (lines_date < end)]
    units = int(win["quantity"].sum())
    return units / DEMAND_WINDOW_DAYS, units, start.date().isoformat(), (end - pd.DateOffset(days=1)).date().isoformat()


def suggest_qty(avg_daily: float, available: int, lead_time: int, moq: int, pack: int) -> int:
    target = avg_daily * (lead_time + COVER_TARGET_DAYS + SAFETY_DAYS)
    need = max(math.ceil(target - available), 0)
    qty = max(need, int(moq))
    pack = max(int(pack or 1), 1)
    return int(math.ceil(qty / pack) * pack)


def classify_risk(days_of_cover: float, lead_time: int) -> str:
    if days_of_cover < lead_time:
        return "HIGH"
    if days_of_cover < lead_time + SAFETY_DAYS:
        return "MEDIUM"
    return "OK"


def assess(product_id: str, use_api: bool = True, api_url: str | None = None,
           tables: dict | None = None) -> Assessment:
    t = tables or load_tables()
    prod = t["products_clean"].set_index("product_id")
    if product_id not in prod.index:
        return Assessment(product_id, "?", "HUMAN_REVIEW", "UNKNOWN", ["Product not found in products"])
    name = prod.loc[product_id, "product_name"]
    a = Assessment(product_id, name, "HUMAN_REVIEW", "UNKNOWN", [])

    # --- stock and demand (BaanDee data) ---------------------------------------
    inv = t["inventory_clean"].set_index("product_id")
    if product_id not in inv.index:
        a.reasons.append("No inventory record")
        return a
    available = int(inv.loc[product_id, "available_qty"])
    inv_as_of = inv.loc[product_id, "as_of"]
    as_of = pd.Timestamp(inv_as_of)
    avg, units, d0, d1 = demand_for(product_id, t, as_of)
    a.avg_daily_demand = round(avg, 3)
    a.verified += [
        Fact("Available stock (BKK-DC)", f"{available} units", "inventory_snapshot.csv", inv_as_of),
        Fact(f"Units sold {d0} to {d1}", f"{units} units = {avg:.2f}/day", "orders.csv + order_lines.csv", f"30-day window ending {d1}"),
    ]
    if avg > 0:
        a.days_of_cover = round(available / avg, 1)
    q = t["quarantine_orders"]
    n_excl = int((q["product_id"] == product_id).sum()) if "product_id" in q else 0
    if n_excl:
        a.uncertain.append(f"{n_excl} order(s) for this SKU have ambiguous dates and are excluded from demand (quarantine_orders.csv).")

    # --- supplier options (generated catalog) ----------------------------------
    cat = t["supplier_catalog_clean"]
    opts = cat[cat.product_id == product_id]
    a.supplier_options = [
        {"supplier_id": r.supplier_id, "supplier_name": r.supplier_name, "lead_time_raw": r.lead_time_raw,
         "lead_time_days": None if pd.isna(r.lead_time_days) else int(r.lead_time_days),
         "moq": None if pd.isna(r.moq) else int(r.moq), "pack_size": None if pd.isna(r.pack_size) else int(r.pack_size),
         "unit_cost_thb": None if pd.isna(r.unit_cost_thb) else int(r.unit_cost_thb),
         "catalog_updated_at": str(r.catalog_updated_at), "flags": r.flags}
        for r in opts.itertuples()]

    if available == 0 and avg == 0:
        a.uncertain.append("Stock is 0 and there were no sales in the window: demand cannot be estimated.")
    if opts.empty:
        a.reasons.append("No supplier in catalog for this SKU: buyer must find a supplier.")
        a.risk = "HIGH" if available == 0 else "UNKNOWN"
        return a
    if len(opts) > 1:
        a.reasons.append(f"{len(opts)} suppliers with different terms: buyer must choose the preferred supplier.")
        a.risk = "HIGH" if available == 0 else "UNKNOWN"
        return a

    opt = a.supplier_options[0]
    a.supplier_id = opt["supplier_id"]
    flags = set(filter(None, opt["flags"].split(";")))
    lead_time, lt_source, lt_as_of = opt["lead_time_days"], "supplier_catalog.csv (unverified)", opt["catalog_updated_at"]
    moq, status = opt["moq"], ("on_hold" if "supplier_on_hold" in flags else "active")
    if "stale_catalog" in flags:
        a.uncertain.append(f"Supplier catalog is old (updated {opt['catalog_updated_at'][:10]}).")

    # --- live supplier terms (system boundary) ---------------------------------
    if use_api:
        res = fetch_live_terms(opt["supplier_id"], product_id, base_url=api_url)
        a.api_outcome = res.outcome
        if res.outcome == "ok":
            live = res.data
            if lead_time is not None and live["lead_time_days"] != lead_time:
                a.uncertain.append(f"Catalog lead time {lead_time} d differs from live {live['lead_time_days']} d; live value used.")
            lead_time, lt_source, lt_as_of = live["lead_time_days"], "Supplier API (live)", live["as_of"]
            if moq != live["moq"]:
                a.uncertain.append(f"Catalog MOQ {moq} differs from live MOQ {live['moq']}; live value used.")
            moq, status = live["moq"], live["supplier_status"]
            if live.get("notice"):
                a.verified.append(Fact("Supplier notice", live["notice"], "Supplier API (live)", live["as_of"]))
        else:
            a.uncertain.append(f"Supplier API {res.outcome}: {res.message}. Using catalog values, not confirmed by supplier.")
    else:
        a.uncertain.append("Supplier API not called. Using catalog values, not confirmed by supplier.")

    # --- stop conditions --------------------------------------------------------
    if lead_time is None:
        raw = opt["lead_time_raw"] if isinstance(opt["lead_time_raw"], str) and opt["lead_time_raw"] else "blank"
        a.reasons.append(f"Lead time unclear in catalog ('{raw}') and not confirmed by supplier API.")
    if status != "active":
        a.reasons.append(f"Supplier {opt['supplier_id']} is {status}.")
    if moq is None or moq <= 0:
        a.reasons.append(f"Invalid MOQ ({opt['moq']}).")
    if "cost_above_price" in flags:
        a.reasons.append("Unit cost is above selling price: check catalog with supplier.")
    if a.reasons:
        return a

    a.lead_time_days = int(lead_time)
    if lt_source.startswith("Supplier API"):
        a.verified.append(Fact("Lead time", f"{lead_time} days", lt_source, lt_as_of))
    else:
        a.uncertain.append(f"Lead time {lead_time} days comes from the catalog ({lt_as_of[:10]}) only.")

    if avg == 0:
        a.decision, a.risk = "NO_ACTION", "OK"
        a.reasons.append("No sales in the demand window.")
        return a
    a.risk = classify_risk(a.days_of_cover, a.lead_time_days)
    if a.risk == "OK":
        a.decision = "NO_ACTION"
        a.reasons.append(f"{a.days_of_cover} days of cover >= lead time {a.lead_time_days} + {SAFETY_DAYS} safety days.")
    else:
        a.decision = "RECOMMEND"
        a.suggested_qty = suggest_qty(avg, available, a.lead_time_days, moq, opt["pack_size"])
        if a.risk == "HIGH":
            a.reasons.append(f"Stock runs out in {a.days_of_cover} days, before a new order can arrive ({a.lead_time_days} days).")
        else:
            a.reasons.append(f"{a.days_of_cover} days of cover is inside the {SAFETY_DAYS}-day safety buffer after lead time ({a.lead_time_days} days).")
        a.reasons.append(f"Suggested {a.suggested_qty} units = cover lead time + {COVER_TARGET_DAYS} days + {SAFETY_DAYS} safety days, "
                         f"at least MOQ {moq}, rounded to pack of {opt['pack_size']}.")
    return a


def overview(use_api: bool = True, api_url: str | None = None) -> pd.DataFrame:
    t = load_tables()
    rows = []
    for pid in t["products_clean"]["product_id"]:
        a = assess(pid, use_api=use_api, api_url=api_url, tables=t)
        rows.append({"product_id": pid, "product_name": a.product_name, "decision": a.decision, "risk": a.risk,
                     "days_of_cover": a.days_of_cover, "lead_time_days": a.lead_time_days,
                     "suggested_qty": a.suggested_qty, "supplier_api": a.api_outcome,
                     "reason": " ".join(a.reasons)})
    order = {"HUMAN_REVIEW": 0, "RECOMMEND": 1, "NO_ACTION": 2}
    risk_order = {"HIGH": 0, "MEDIUM": 1, "UNKNOWN": 2, "OK": 3}
    df = pd.DataFrame(rows)
    return df.sort_values(by=["decision", "risk", "days_of_cover"],
                          key=lambda s: s.map(order) if s.name == "decision" else (s.map(risk_order) if s.name == "risk" else s)
                          ).reset_index(drop=True)
