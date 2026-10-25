"""Test cases shared by pytest (tests/test_cases.py) and the report script
(scripts/run_test_report.py). Each case returns input, expected, actual, passed."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from src import store
from src.data_prep import normalise_sku, parse_lead_time, parse_order_date
from src.restock import assess, overview

ROOT = Path(__file__).resolve().parents[1]
DOWN_URL = "http://127.0.0.1:9"


def t01_sku_normalisation():
    known = {"SKU-0045", "SKU-0061"}
    inp = ["sku 0045", "SKU-61", "SKU-0099"]
    exp = ["SKU-0045", "SKU-0061", None]
    act = [normalise_sku(x, known) for x in inp]
    return inp, exp, act, act == exp


def t02_lead_time_units():
    inp = ["2 weeks", "7d", "1-2 weeks", "TBC", ""]
    exp = [14, 7, None, None, None]
    act = [parse_lead_time(x) for x in inp]
    return inp, exp, act, act == exp


def t03_ambiguous_date_not_guessed():
    inp = ["28/07/2026", "07/08/2026"]
    exp = [("2026-07-28", "corrected"), (None, "ambiguous")]
    act = [(d.date().isoformat() if d is not None else None, q) for d, q in map(parse_order_date, inp)]
    return inp, exp, act, act == exp


def t04_duplicate_supplier_goes_to_human():
    a = assess("SKU-KT-104")
    exp = {"decision": "HUMAN_REVIEW", "suggested_qty": None, "supplier_options": 2}
    act = {"decision": a.decision, "suggested_qty": a.suggested_qty, "supplier_options": len(a.supplier_options)}
    return "SKU-KT-104", exp, act, act == exp


def t05_no_supplier_goes_to_human():
    a = assess("SKU-0048")
    exp = {"decision": "HUMAN_REVIEW", "risk": "HIGH", "suggested_qty": None}
    act = {"decision": a.decision, "risk": a.risk, "suggested_qty": a.suggested_qty}
    return "SKU-0048 (stock 0, no supplier)", exp, act, act == exp


def t06_api_success_changes_result():
    a = assess("SKU-0001")
    lt_fact = next((f for f in a.verified if f.label == "Lead time"), None)
    exp = {"api": "ok", "lead_time_days": 13, "risk": "HIGH", "lead_time_source": "Supplier API (live)"}
    act = {"api": a.api_outcome, "lead_time_days": a.lead_time_days, "risk": a.risk,
           "lead_time_source": lt_fact.source if lt_fact else None}
    return "SKU-0001, Supplier API up (catalog says 7 d)", exp, act, act == exp


def t07_api_not_found_stops():
    a = assess("SKU-0021")
    exp = {"api": "not_found", "decision": "HUMAN_REVIEW"}
    act = {"api": a.api_outcome, "decision": a.decision}
    return "SKU-0021 (catalog lead time '1-2 weeks', no live record)", exp, act, act == exp


def t08_api_unavailable_fallback_is_labelled():
    a = assess("SKU-0001", api_url=DOWN_URL)
    labelled = any("not confirmed by supplier" in u for u in a.uncertain)
    lt_verified = any(f.label == "Lead time" for f in a.verified)
    exp = {"api": "unavailable", "decision": "RECOMMEND", "lead_time_days": 7,
           "warning_shown": True, "lead_time_in_verified": False}
    act = {"api": a.api_outcome, "decision": a.decision, "lead_time_days": a.lead_time_days,
           "warning_shown": labelled, "lead_time_in_verified": lt_verified}
    return "SKU-0001, Supplier API down", exp, act, act == exp


def t09_suggested_qty_respects_moq_and_pack():
    df = overview()
    rec = df[df.decision == "RECOMMEND"]
    bad = []
    for pid, qty in zip(rec.product_id, rec.suggested_qty):
        a = assess(pid)
        opt = a.supplier_options[0]
        if qty < opt["moq"] or qty % opt["pack_size"] != 0:
            bad.append(pid)
    exp = "every RECOMMEND qty >= MOQ and a multiple of pack_size"
    act = f"{len(rec)} recommendations checked, violations: {bad or 'none'}"
    return "all SKUs", exp, act, len(rec) > 0 and not bad


def t10_store_create_reload_edit_audit():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "t.db"
        con = store.connect(db)
        rid = store.create_request(con, product_id="SKU-0001", supplier_id="SUP-03", quantity=30,
                                   suggested_qty=30, decision="RECOMMEND", evidence={"x": 1})
        con.close()
        con = store.connect(db)  # new connection = data persisted on disk
        reloaded = store.get_request(con, rid)
        try:
            store.update_request(con, rid, quantity=50)  # override without note must fail
            blocked = False
        except store.ValidationError:
            blocked = True
        store.update_request(con, rid, quantity=50, buyer_note="campaign next month")
        after = store.get_request(con, rid)
        actions = [a["action"] for a in store.audit_for(con, rid)]
        con.close()
    exp = {"reloaded_qty": 30, "override_without_note_blocked": True, "edited_qty": 50,
           "audit": ["create", "update", "update"]}
    act = {"reloaded_qty": reloaded["quantity"], "override_without_note_blocked": blocked,
           "edited_qty": after["quantity"], "audit": actions}
    return "create PR 30 units -> reopen DB -> edit to 50", exp, act, act == exp


def t11_soft_delete_keeps_row():
    with tempfile.TemporaryDirectory() as d:
        con = store.connect(Path(d) / "t.db")
        rid = store.create_request(con, product_id="SKU-0048", supplier_id="SUP-04", quantity=12,
                                   suggested_qty=None, decision="HUMAN_REVIEW", evidence={},
                                   buyer_note="buyer found new supplier")
        store.soft_delete(con, rid)
        act = {"visible": store.get_request(con, rid) is not None,
               "still_in_db": store.get_request(con, rid, include_deleted=True) is not None,
               "status": store.get_request(con, rid, include_deleted=True)["status"]}
        con.close()
    exp = {"visible": False, "still_in_db": True, "status": "cancelled"}
    return "create then soft delete", exp, act, act == exp


def t12_raw_data_untouched():
    raw = ROOT / "data" / "raw"
    expected = {}
    for line in (ROOT / "data" / "raw_checksums.sha256").read_text().splitlines():
        h, name = line.split()
        expected[name] = h
    changed = [n for n, h in expected.items()
               if hashlib.sha256((raw / n).read_bytes()).hexdigest() != h]
    return "data/raw/* vs raw_checksums.sha256", "no file changed", f"changed: {changed or 'none'}", not changed


CASES = [t01_sku_normalisation, t02_lead_time_units, t03_ambiguous_date_not_guessed,
         t04_duplicate_supplier_goes_to_human, t05_no_supplier_goes_to_human,
         t06_api_success_changes_result, t07_api_not_found_stops,
         t08_api_unavailable_fallback_is_labelled, t09_suggested_qty_respects_moq_and_pack,
         t10_store_create_reload_edit_audit, t11_soft_delete_keeps_row, t12_raw_data_untouched]
