"""SQLite store for purchase requests (create / read / update / soft delete).

Data persists in data/app.db (path from DB_PATH) after the app stops.
Every change is written to audit_log. Records are never physically deleted.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.getenv("DB_PATH", ROOT / "data" / "app.db"))
STATUSES = ("draft", "submitted", "cancelled")
EDITABLE = ("quantity", "supplier_id", "buyer_note", "status")
MAX_QTY = 10_000


class ValidationError(ValueError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS purchase_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        supplier_id TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        suggested_qty INTEGER,
        decision TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        buyer_note TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        is_deleted INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        field TEXT,
        old_value TEXT,
        new_value TEXT,
        at TEXT NOT NULL
    );""")
    return con


def _check_qty(qty) -> int:
    try:
        q = int(qty)
    except (TypeError, ValueError):
        raise ValidationError("Quantity must be a whole number")
    if q <= 0 or q > MAX_QTY:
        raise ValidationError(f"Quantity must be between 1 and {MAX_QTY}")
    return q


def create_request(con, *, product_id: str, supplier_id: str, quantity, suggested_qty: int | None,
                   decision: str, evidence: dict, buyer_note: str = "") -> int:
    q = _check_qty(quantity)
    note = (buyer_note or "").strip()
    if not supplier_id:
        raise ValidationError("Choose a supplier")
    # Guardrail: a human must explain any override of the system
    if decision != "RECOMMEND" and not note:
        raise ValidationError(f"Decision is {decision}: a buyer note explaining the choice is required")
    if suggested_qty and q != suggested_qty and not note:
        raise ValidationError(f"Quantity differs from suggested {suggested_qty}: add a buyer note")
    now = _now()
    cur = con.execute(
        "INSERT INTO purchase_requests (product_id, supplier_id, quantity, suggested_qty, decision, status,"
        " buyer_note, evidence_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (product_id, supplier_id, q, suggested_qty, decision, "draft", note,
         json.dumps(evidence, ensure_ascii=False, default=str), now, now))
    rid = cur.lastrowid
    con.execute("INSERT INTO audit_log (request_id, action, at) VALUES (?,?,?)", (rid, "create", now))
    con.commit()
    return rid


def get_request(con, request_id: int, include_deleted: bool = False) -> dict | None:
    sql = "SELECT * FROM purchase_requests WHERE id = ?" + ("" if include_deleted else " AND is_deleted = 0")
    row = con.execute(sql, (int(request_id),)).fetchone()
    return dict(row) if row else None


def list_requests(con, include_deleted: bool = False) -> list[dict]:
    sql = "SELECT * FROM purchase_requests" + ("" if include_deleted else " WHERE is_deleted = 0") + " ORDER BY id DESC"
    return [dict(r) for r in con.execute(sql).fetchall()]


def update_request(con, request_id: int, **changes) -> dict:
    current = get_request(con, request_id)
    if current is None:
        raise ValidationError(f"Request {request_id} not found")
    if current["status"] == "cancelled":
        raise ValidationError("Cancelled requests cannot be edited")
    unknown = set(changes) - set(EDITABLE)
    if unknown:
        raise ValidationError(f"Cannot edit: {sorted(unknown)}")
    if "quantity" in changes:
        changes["quantity"] = _check_qty(changes["quantity"])
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValidationError(f"Status must be one of {STATUSES}")
    note = changes.get("buyer_note", current["buyer_note"]) or ""
    new_qty = changes.get("quantity", current["quantity"])
    if current["suggested_qty"] and new_qty != current["suggested_qty"] and not note.strip():
        raise ValidationError(f"Quantity differs from suggested {current['suggested_qty']}: add a buyer note")
    now = _now()
    for key, val in changes.items():
        if str(current[key]) != str(val):
            con.execute(f"UPDATE purchase_requests SET {key} = ?, updated_at = ? WHERE id = ?", (val, now, request_id))
            con.execute("INSERT INTO audit_log (request_id, action, field, old_value, new_value, at) VALUES (?,?,?,?,?,?)",
                        (request_id, "update", key, str(current[key]), str(val), now))
    con.commit()
    return get_request(con, request_id)


def soft_delete(con, request_id: int) -> None:
    """Cancel + hide. The row stays in the database and in audit_log."""
    if get_request(con, request_id) is None:
        raise ValidationError(f"Request {request_id} not found")
    now = _now()
    con.execute("UPDATE purchase_requests SET is_deleted = 1, status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, request_id))
    con.execute("INSERT INTO audit_log (request_id, action, at) VALUES (?,?,?)", (request_id, "soft_delete", now))
    con.commit()


def audit_for(con, request_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT action, field, old_value, new_value, at FROM audit_log WHERE request_id = ? ORDER BY id", (request_id,))]
