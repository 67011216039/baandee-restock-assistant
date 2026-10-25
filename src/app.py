"""Gradio UI for the BaanDee Restock Assistant.

Run (after the Supplier API is up):  python -m src.app   ->  http://127.0.0.1:7860
"""
from __future__ import annotations

import dataclasses
import json

import gradio as gr
import pandas as pd

from src import store
from src.restock import assess, load_tables, overview

DOWN_URL = "http://127.0.0.1:9"  # nothing listens here -> simulates Supplier API outage

DECISION_STYLE = {
    "RECOMMEND": ("#0f7b3f", "แนะนำให้สั่งซื้อ"),
    "NO_ACTION": ("#4b5563", "ยังไม่ต้องสั่ง"),
    "HUMAN_REVIEW": ("#b45309", "ระบบหยุด: ส่งให้ผู้ซื้อตัดสินใจ"),
}
RISK_TH = {"HIGH": "สูง", "MEDIUM": "ปานกลาง", "OK": "ปกติ", "UNKNOWN": "ประเมินไม่ได้"}

CSS = """
.banner {border-radius:10px;padding:14px 18px;color:#fff;margin-bottom:4px}
.banner h3 {margin:0 0 4px 0;color:#fff !important} .banner p, .banner b {margin:2px 0;color:#fff !important}
"""


def _api_url(simulate_down: bool) -> str | None:
    return DOWN_URL if simulate_down else None


def product_choices() -> list[tuple[str, str]]:
    p = load_tables()["products_clean"]
    return [(f"{r.product_id} — {r.product_name}", r.product_id) for r in p.itertuples()]


# ------------------------------------------------------------------ tab 1
def do_overview(simulate_down: bool):
    df = overview(api_url=_api_url(simulate_down))
    counts = df["decision"].value_counts().to_dict()
    summary = (f"**แนะนำให้สั่ง {counts.get('RECOMMEND', 0)}** · ส่งให้คนตรวจ {counts.get('HUMAN_REVIEW', 0)} · "
               f"ปกติ {counts.get('NO_ACTION', 0)} SKU" + ("  \n⚠️ Supplier API ล่ม (จำลอง): ใช้ค่าในแคตตาล็อกแทน" if simulate_down else ""))
    df = df.assign(risk=df["risk"].map(RISK_TH))
    return summary, df


# ------------------------------------------------------------------ tab 2
def do_assess(product_id: str, simulate_down: bool):
    if not product_id:
        raise gr.Error("เลือกสินค้าก่อน")
    a = assess(product_id, api_url=_api_url(simulate_down))
    color, label = DECISION_STYLE[a.decision]
    reasons = "".join(f"<p>• {r}</p>" for r in a.reasons)
    banner = (f"<div class='banner' style='background:{color}'><h3>{label} — {a.product_id} {a.product_name}</h3>"
              f"<p>ความเสี่ยงขาดสต็อก: <b>{RISK_TH[a.risk]}</b>"
              + (f" · เหลือพอขาย {a.days_of_cover} วัน" if a.days_of_cover is not None else "")
              + (f" · lead time {a.lead_time_days} วัน" if a.lead_time_days else "")
              + (f" · <b>แนะนำสั่ง {a.suggested_qty} ชิ้น</b>" if a.suggested_qty else "")
              + f"</p>{reasons}</div>")
    facts = pd.DataFrame([dataclasses.asdict(f) for f in a.verified]).rename(
        columns={"label": "ข้อเท็จจริง", "value": "ค่า", "source": "แหล่งที่มา", "as_of": "เวลาของข้อมูล"})
    uncertain = "\n".join(f"- {u}" for u in a.uncertain) or "- ไม่มี"
    api_line = {"ok": "✅ Supplier API: ได้ข้อมูลล่าสุด", "not_found": "❔ Supplier API: ไม่พบข้อมูล SKU นี้",
                "unavailable": "⚠️ Supplier API: เชื่อมต่อไม่ได้", "not_called": "— Supplier API: ไม่ได้เรียก (ต้องให้คนเลือก supplier ก่อน)"}[a.api_outcome]
    opts = pd.DataFrame(a.supplier_options)
    if not opts.empty:
        opts = opts[["supplier_id", "supplier_name", "lead_time_raw", "moq", "pack_size", "unit_cost_thb", "catalog_updated_at", "flags"]]
    sup_ids = [o["supplier_id"] for o in a.supplier_options]
    default_sup = a.supplier_id if a.supplier_id else None
    hint = ("ระบบแนะนำจำนวนแล้ว ถ้าแก้จำนวนต้องใส่เหตุผล" if a.decision == "RECOMMEND"
            else "ระบบไม่แนะนำ: ถ้าจะสร้างคำขอ ผู้ซื้อต้องเลือก supplier และใส่เหตุผลเอง")
    return (banner, facts, f"{api_line}\n\n**ยังไม่แน่นอน / ต้องระวัง**\n{uncertain}", opts,
            gr.update(choices=sup_ids, value=default_sup), a.suggested_qty, "", hint,
            dataclasses.asdict(a), "")


def do_create(state: dict, supplier_id: str, qty, note: str):
    if not state:
        raise gr.Error("กด 'ตรวจสอบ' ก่อน")
    con = store.connect()
    try:
        rid = store.create_request(con, product_id=state["product_id"], supplier_id=supplier_id, quantity=qty,
                                   suggested_qty=state["suggested_qty"], decision=state["decision"],
                                   evidence=state, buyer_note=note)
    except store.ValidationError as e:
        return f"❌ บันทึกไม่ได้: {e}"
    finally:
        con.close()
    return f"✅ บันทึกคำขอ #{rid} แล้ว (สถานะ draft) — ไปที่แท็บ 'คำขอสั่งซื้อ' เพื่อดูหรือแก้ไข"


# ------------------------------------------------------------------ tab 3
def do_list(show_deleted: bool):
    con = store.connect()
    rows = store.list_requests(con, include_deleted=show_deleted)
    con.close()
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["id", "product_id", "supplier_id", "quantity", "status"])
    return df[["id", "product_id", "supplier_id", "quantity", "suggested_qty", "decision", "status",
               "buyer_note", "created_at", "updated_at", "is_deleted"]]


def do_load(rid):
    if not rid:
        raise gr.Error("ใส่เลขคำขอ")
    con = store.connect()
    r = store.get_request(con, int(rid))
    if r is None:
        con.close()
        return "❌ ไม่พบคำขอนี้ (หรือถูกยกเลิกแล้ว)", None, None, "", None, pd.DataFrame(), ""
    audit = pd.DataFrame(store.audit_for(con, int(rid)))
    con.close()
    ev = json.loads(r["evidence_json"])
    snap = (f"หลักฐาน ณ ตอนสร้าง: decision **{ev['decision']}**, risk {ev['risk']}, "
            f"เหลือพอขาย {ev['days_of_cover']} วัน, lead time {ev['lead_time_days']} วัน, แนะนำ {ev['suggested_qty']} ชิ้น")
    return (f"โหลดคำขอ #{rid} — {r['product_id']}", r["supplier_id"], r["quantity"], r["buyer_note"],
            r["status"], audit, snap)


def do_save(rid, supplier_id, qty, note, status):
    con = store.connect()
    try:
        store.update_request(con, int(rid), supplier_id=supplier_id, quantity=qty, buyer_note=note, status=status)
        msg = f"✅ บันทึกการแก้ไขคำขอ #{int(rid)} แล้ว"
    except (store.ValidationError, TypeError) as e:
        msg = f"❌ แก้ไขไม่ได้: {e}"
    audit = pd.DataFrame(store.audit_for(con, int(rid))) if rid else pd.DataFrame()
    con.close()
    return msg, audit


def do_delete(rid):
    con = store.connect()
    try:
        store.soft_delete(con, int(rid))
        msg = f"🗑️ ยกเลิกคำขอ #{int(rid)} แล้ว (soft delete: ยังเก็บใน database และ audit log)"
    except (store.ValidationError, TypeError) as e:
        msg = f"❌ {e}"
    con.close()
    return msg


# ------------------------------------------------------------------ layout
def build() -> gr.Blocks:
    with gr.Blocks(title="BaanDee Restock Assistant") as demo:
        gr.Markdown("# BaanDee Restock Assistant\nผู้ช่วยฝ่ายจัดซื้อ: หาสินค้าเสี่ยงขาดสต็อกและร่างคำขอสั่งซื้อ · "
                    "ข้อมูลสังเคราะห์ทั้งหมด · ระบบไม่ส่งคำสั่งซื้อจริง")
        simulate_down = gr.Checkbox(label="จำลอง Supplier API ล่ม (สำหรับสาธิต failure case)", value=False)

        with gr.Tab("1. ภาพรวมความเสี่ยง"):
            btn_ov = gr.Button("คำนวณภาพรวม", variant="primary")
            ov_sum = gr.Markdown()
            ov_tbl = gr.Dataframe(interactive=False, wrap=True)
            btn_ov.click(do_overview, simulate_down, [ov_sum, ov_tbl])

        with gr.Tab("2. ตรวจสินค้าและสร้างคำขอ"):
            with gr.Row():
                pick = gr.Dropdown(product_choices(), label="เลือกสินค้า", scale=4)
                btn_check = gr.Button("ตรวจสอบ", variant="primary", scale=1)
            banner = gr.HTML()
            gr.Markdown("### ข้อเท็จจริงที่ยืนยันแล้ว (มีแหล่งที่มาและเวลา)")
            facts = gr.Dataframe(interactive=False, wrap=True)
            uncertain = gr.Markdown()
            gr.Markdown("### ตัวเลือก supplier ในแคตตาล็อก")
            opts = gr.Dataframe(interactive=False, wrap=True)
            gr.Markdown("### สร้างคำขอสั่งซื้อ (draft)")
            hint = gr.Markdown()
            with gr.Row():
                f_sup = gr.Dropdown(label="Supplier", choices=[])
                f_qty = gr.Number(label="จำนวน", precision=0)
            f_note = gr.Textbox(label="เหตุผล / หมายเหตุของผู้ซื้อ")
            btn_create = gr.Button("บันทึกคำขอ", variant="primary")
            create_msg = gr.Markdown()
            state = gr.State({})
            btn_check.click(do_assess, [pick, simulate_down],
                            [banner, facts, uncertain, opts, f_sup, f_qty, f_note, hint, state, create_msg])
            btn_create.click(do_create, [state, f_sup, f_qty, f_note], create_msg)

        with gr.Tab("3. คำขอสั่งซื้อ (เรียกดู / แก้ไข)"):
            with gr.Row():
                show_del = gr.Checkbox(label="แสดงรายการที่ยกเลิกแล้ว", value=False)
                btn_list = gr.Button("รีเฟรชรายการ")
            req_tbl = gr.Dataframe(interactive=False, wrap=True)
            with gr.Row():
                e_id = gr.Number(label="เลขคำขอ (id)", precision=0)
                btn_load = gr.Button("เรียกข้อมูล")
            load_msg = gr.Markdown()
            snap = gr.Markdown()
            with gr.Row():
                e_sup = gr.Textbox(label="Supplier")
                e_qty = gr.Number(label="จำนวน", precision=0)
                e_status = gr.Dropdown(list(store.STATUSES[:2]) + ["cancelled"], label="สถานะ")
            e_note = gr.Textbox(label="เหตุผล / หมายเหตุ")
            with gr.Row():
                btn_save = gr.Button("บันทึกการแก้ไข", variant="primary")
                btn_del = gr.Button("ยกเลิกคำขอ (soft delete)", variant="stop")
            save_msg = gr.Markdown()
            gr.Markdown("### ประวัติการแก้ไข (audit log)")
            audit = gr.Dataframe(interactive=False)
            btn_list.click(do_list, show_del, req_tbl)
            btn_load.click(do_load, e_id, [load_msg, e_sup, e_qty, e_note, e_status, audit, snap])
            btn_save.click(do_save, [e_id, e_sup, e_qty, e_note, e_status], [save_msg, audit]).then(do_list, show_del, req_tbl)
            btn_del.click(do_delete, e_id, save_msg).then(do_list, show_del, req_tbl)
            demo.load(do_list, show_del, req_tbl)
    return demo


if __name__ == "__main__":
    build().launch(server_name="127.0.0.1", server_port=7860, css=CSS, theme=gr.themes.Soft())
