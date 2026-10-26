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
        return Assessment(product_id, "?", "HUMAN_REVIEW", "UNKNOWN", ["ไม่พบสินค้านี้ใน products"])
    name = prod.loc[product_id, "product_name"]
    a = Assessment(product_id, name, "HUMAN_REVIEW", "UNKNOWN", [])

    # --- stock and demand (BaanDee data) ---------------------------------------
    inv = t["inventory_clean"].set_index("product_id")
    if product_id not in inv.index:
        a.reasons.append("ไม่พบข้อมูลสต็อกของสินค้านี้")
        return a
    available = int(inv.loc[product_id, "available_qty"])
    inv_as_of = inv.loc[product_id, "as_of"]
    as_of = pd.Timestamp(inv_as_of)
    avg, units, d0, d1 = demand_for(product_id, t, as_of)
    a.avg_daily_demand = round(avg, 3)
    a.verified += [
        Fact("สต็อกคงเหลือ (BKK-DC)", f"{available} ชิ้น", "inventory_snapshot.csv", inv_as_of),
        Fact(f"ยอดขาย {d0} ถึง {d1}", f"{units} ชิ้น = {avg:.2f} ชิ้น/วัน", "orders.csv + order_lines.csv", f"ช่วง 30 วัน สิ้นสุด {d1}"),
    ]
    if avg > 0:
        a.days_of_cover = round(available / avg, 1)
    q = t["quarantine_orders"]
    n_excl = int((q["product_id"] == product_id).sum()) if "product_id" in q else 0
    if n_excl:
        a.uncertain.append(f"มี {n_excl} order ของสินค้านี้ที่วันที่กำกวม จึงไม่นับในยอดขาย (ดู quarantine_orders.csv)")

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
        a.uncertain.append("สต็อกเป็น 0 และไม่มียอดขายในช่วงที่คำนวณ จึงประเมินความต้องการไม่ได้")
    if opts.empty:
        a.reasons.append("ไม่มี supplier ในแคตตาล็อกสำหรับสินค้านี้ ผู้ซื้อต้องหา supplier เอง")
        a.risk = "HIGH" if available == 0 else "UNKNOWN"
        return a
    if len(opts) > 1:
        a.reasons.append(f"มี supplier {len(opts)} รายที่เงื่อนไขต่างกัน ผู้ซื้อต้องเลือก supplier หลักเอง")
        a.risk = "HIGH" if available == 0 else "UNKNOWN"
        return a

    opt = a.supplier_options[0]
    a.supplier_id = opt["supplier_id"]
    flags = set(filter(None, opt["flags"].split(";")))
    lead_time, lt_source, lt_as_of = opt["lead_time_days"], "supplier_catalog.csv (unverified)", opt["catalog_updated_at"]
    moq, status = opt["moq"], ("on_hold" if "supplier_on_hold" in flags else "active")
    if "stale_catalog" in flags:
        a.uncertain.append(f"แคตตาล็อก supplier เก่า (อัปเดตล่าสุด {opt['catalog_updated_at'][:10]})")

    # --- live supplier terms (system boundary) ---------------------------------
    if use_api:
        res = fetch_live_terms(opt["supplier_id"], product_id, base_url=api_url)
        a.api_outcome = res.outcome
        if res.outcome == "ok":
            live = res.data
            if lead_time is not None and live["lead_time_days"] != lead_time:
                a.uncertain.append(f"lead time ในแคตตาล็อก {lead_time} วัน ไม่ตรงกับข้อมูลล่าสุด {live['lead_time_days']} วัน ระบบใช้ค่าล่าสุดจาก API")
            lead_time, lt_source, lt_as_of = live["lead_time_days"], "Supplier API (live)", live["as_of"]
            if moq != live["moq"]:
                a.uncertain.append(f"MOQ ในแคตตาล็อก ({moq}) ไม่ตรงกับข้อมูลล่าสุด ({live['moq']}) ระบบใช้ค่าล่าสุดจาก API")
            moq, status = live["moq"], live["supplier_status"]
            if live.get("notice"):
                a.verified.append(Fact("ประกาศจาก supplier", live["notice"], "Supplier API (live)", live["as_of"]))
        else:
            a.uncertain.append(f"Supplier API {res.outcome}: {res.message} ระบบใช้ค่าจากแคตตาล็อกแทน (ยังไม่ยืนยันจาก supplier)")
    else:
        a.uncertain.append("ไม่ได้เรียก Supplier API ระบบใช้ค่าจากแคตตาล็อก (ยังไม่ยืนยันจาก supplier)")

    # --- stop conditions --------------------------------------------------------
    if lead_time is None:
        raw = opt["lead_time_raw"] if isinstance(opt["lead_time_raw"], str) and opt["lead_time_raw"] else "ว่าง"
        a.reasons.append(f"lead time ในแคตตาล็อกไม่ชัดเจน ('{raw}') และ Supplier API ไม่ได้ยืนยัน")
    if status != "active":
        a.reasons.append(f"Supplier {opt['supplier_id']} อยู่ในสถานะ {status} (ระงับการสั่งซื้อ)")
    if moq is None or moq <= 0:
        a.reasons.append(f"MOQ ไม่ถูกต้อง ({opt['moq']})")
    if "cost_above_price" in flags:
        a.reasons.append("ต้นทุนสูงกว่าราคาขาย ต้องตรวจแคตตาล็อกกับ supplier")
    if a.reasons:
        return a

    a.lead_time_days = int(lead_time)
    if lt_source.startswith("Supplier API"):
        a.verified.append(Fact("Lead time", f"{lead_time} วัน", lt_source, lt_as_of))
    else:
        a.uncertain.append(f"lead time {lead_time} วัน มาจากแคตตาล็อก ({lt_as_of[:10]}) เท่านั้น")

    if avg == 0:
        a.decision, a.risk = "NO_ACTION", "OK"
        a.reasons.append("ไม่มียอดขายในช่วงที่คำนวณ")
        return a
    a.risk = classify_risk(a.days_of_cover, a.lead_time_days)
    if a.risk == "OK":
        a.decision = "NO_ACTION"
        a.reasons.append(f"สต็อกพอขาย {a.days_of_cover} วัน มากกว่า lead time {a.lead_time_days} วัน + safety {SAFETY_DAYS} วัน")
    else:
        a.decision = "RECOMMEND"
        a.suggested_qty = suggest_qty(avg, available, a.lead_time_days, moq, opt["pack_size"])
        if a.risk == "HIGH":
            a.reasons.append(f"สต็อกจะหมดใน {a.days_of_cover} วัน ก่อนของล็อตใหม่จะมาถึง (lead time {a.lead_time_days} วัน)")
        else:
            a.reasons.append(f"สต็อกพอขาย {a.days_of_cover} วัน อยู่ในช่วง safety {SAFETY_DAYS} วัน หลัง lead time ({a.lead_time_days} วัน)")
        a.reasons.append(f"แนะนำ {a.suggested_qty} ชิ้น = พอขายช่วง lead time + {COVER_TARGET_DAYS} วัน + safety {SAFETY_DAYS} วัน "
                         f"(ไม่ต่ำกว่า MOQ {moq} ชิ้น และปัดขึ้นให้เป็นทวีคูณของแพ็กละ {opt['pack_size']} ชิ้น)")
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
