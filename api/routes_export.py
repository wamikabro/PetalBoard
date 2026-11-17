"""Downloads: a full backup of everything, and one supplier's or one investor's data as Excel.

    GET /export/backup                 owner only: .zip with every table as JSON and CSV + a README
    GET /export/supplier/{id}.xlsx     one supplier: summary, bills, every day, kharcha, advance, investor shares
    GET /export/investor/{id}.xlsx     one investor: summary, months, khaatas, every bill share, money in/out
"""

import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import db
from billing import load_bill
from routes_investors import get_investor
from routes_suppliers import get_supplier
from security import current_user, require

router = APIRouter(tags=["export"])

# Every table, in an order that can be loaded back (parents first). Sessions are left out.
TABLES = ["shop_settings", "users", "flowers", "suppliers", "investors", "bills", "bill_days", "kharcha",
          "advances", "khaata_shares", "investor_entries"]
SECRET_COLUMNS = {"users": {"password_hash"}}


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _file_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "export"


def _download(data: bytes, name: str, media: str) -> Response:
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/backup")
def backup(user: dict = Depends(require("owner"))):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    buf = io.BytesIO()
    counts = {}
    with db.transaction() as conn, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in TABLES:
            rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            hide = SECRET_COLUMNS.get(t, set())
            rows = [{k: _plain(v) for k, v in r.items() if k not in hide} for r in rows]
            counts[t] = len(rows)
            z.writestr(f"json/{t}.json", json.dumps(rows, ensure_ascii=False, indent=1))
            out = io.StringIO()
            if rows:
                w = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for r in rows:
                    w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in r.items()})
            z.writestr(f"csv/{t}.csv", "﻿" + out.getvalue())
        z.writestr("README.txt", (
            f"PetalBoard backup, made {stamp} UTC by {user['display_name']}.\n\n"
            "json/ has every table exactly as stored; csv/ has the same tables for Excel.\n"
            "Passwords are not included: after restoring, set them again with manage_users.py.\n\n"
            "Rows per table:\n" + "".join(f"  {t}: {n}\n" for t, n in counts.items())
        ))
        z.writestr("manifest.json", json.dumps({"made_at": stamp, "schema_version": 1, "tables": counts}, indent=1))
    return _download(buf.getvalue(), f"petalboard-backup-{stamp}.zip", "application/zip")


# ---------- Excel helpers ----------

HEAD_FILL = PatternFill("solid", fgColor="23356F")
HEAD_FONT = Font(bold=True, color="FFFFFF")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list], money_cols=(), widths=None):
    ws = wb.create_sheet(title[:31])
    ws.append(header)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append([_plain(v) for v in r])
    for i, h in enumerate(header, 1):
        col = get_column_letter(i)
        width = (widths or {}).get(h) or max(10, min(40, max([len(str(h))] + [len(str(r[i - 1] or "")) for r in rows[:300]]) + 2))
        ws.column_dimensions[col].width = width
        if h in money_cols:
            for cell in ws[col][1:]:
                cell.number_format = "#,##0"
    ws.freeze_panes = "A2"
    return ws


def _summary_sheet(wb: Workbook, title: str, pairs: list[tuple]):
    ws = wb.active
    ws.title = "Summary"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Downloaded {datetime.now():%d %b %Y %H:%M} from PetalBoard"])
    ws.append([])
    for k, v in pairs:
        ws.append([k, _plain(v)])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        if isinstance(v, (int, float, Decimal)):
            ws.cell(ws.max_row, 2).number_format = "#,##0"
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 28


def _xlsx(wb: Workbook) -> bytes:
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/export/supplier/{supplier_id}.xlsx")
def export_supplier(supplier_id: int, user: dict = Depends(current_user)):
    d = get_supplier(supplier_id, user)
    s, p = d["supplier"], d["position"]
    with db.transaction() as conn:
        full = [load_bill(conn, b["id"]) for b in sorted(d["bills"], key=lambda b: b["month"])]
    wb = Workbook()
    _summary_sheet(wb, f"Supplier: {s['name']}", [
        ("Name", s["name"]), ("Name (Urdu)", s.get("name_ur") or ""), ("Phone", s.get("phone") or ""),
        ("Flower", s.get("flower_name") or ""), ("Commission %", s["commission_pct"]),
        ("Balance now (+ shop owes, − supplier owes)", p["balance"]), ("Advance outstanding", p["advance"]),
        ("Months", len(full)),
        ("Flowers bought, kg", sum(b["totals"]["weight"] for b in full)),
        ("Total amount", sum(b["totals"]["total"] for b in full)),
        ("Commission", sum(b["totals"]["commission"] for b in full)),
        ("Kharcha", sum(b["totals"]["kharcha"] for b in full)),
        ("Investors in this khaata", ", ".join(f"{x['investor_name']} {x['pct']}%" for x in d["shares"]) or "none"),
    ])
    money = {"Total", "Commission", "Net", "Kharcha", "Brought forward", "Final", "Amount", "Paid cash", "Advance cut", "Carried"}
    _sheet(wb, "Bills", ["Month", "Status", "Days", "kg", "Total", "Commission", "Net", "Kharcha", "Brought forward",
                         "Final", "Paid cash", "Advance cut", "Carried", "Note"],
           [[b["month"], b["status"], b["totals"]["days_supplied"], b["totals"]["weight"], b["totals"]["total"],
             b["totals"]["commission"], b["totals"]["net"], b["totals"]["kharcha"], b["opening_due"], b["totals"]["final"],
             b["settlement"]["paid_cash"], b["settlement"]["advance_cut"], b["settlement"]["carried"], b["note"]] for b in full],
           money_cols=money, widths={"Note": 60})
    days = []
    for b in full:
        for r in b["days"]:
            if r["amount"] is not None or r["kharcha"]:
                days.append([f"{b['month']}-{r['day']:02d}", r["weight"], r["rate"], r["amount"],
                             "+".join(str(k["amount"]) for k in r["kharcha"]), r["kharcha_total"] or None, r["note"]])
    _sheet(wb, "Every day", ["Date", "Weight kg", "Rate", "Amount", "Kharcha items", "Kharcha", "Note"], days,
           money_cols={"Amount", "Kharcha"})
    _sheet(wb, "Advance", ["Date", "What", "Amount", "Balance", "Note"],
           [[a["on_date"], a["kind"], a["amount"], a["running"], a["note"]] for a in d["advances"]], money_cols={"Amount", "Balance"})
    _sheet(wb, "Investors", ["Investor", "Share %", "From", "To", "Note"],
           [[x["investor_name"], x["pct"], x["from_month"], x["to_month"] or "now", x["note"]] for x in d["shares"]])
    _sheet(wb, "Investor earnings", ["Month", "Investor", "Commission", "Share %", "Earned"],
           [[r["month"], r["investor_name"], r["commission"], r["pct"], r["earned"]] for r in d["investor_earnings"]],
           money_cols={"Commission", "Earned"})
    return _download(_xlsx(wb), f"supplier-{_file_name(s['name'])}.xlsx", XLSX)


@router.get("/export/investor/{investor_id}.xlsx")
def export_investor(investor_id: int, user: dict = Depends(current_user)):
    d = get_investor(investor_id, user)
    inv = d["investor"]
    wb = Workbook()
    _summary_sheet(wb, f"Investor: {inv['name']}", [
        ("Name", inv["name"]), ("Phone", inv.get("phone") or ""),
        ("Money invested", inv["invested"]), ("Money withdrawn", inv["withdrawn"]), ("Capital in the business", inv["capital"]),
        ("Commission share earned (all time)", inv["earned"]), ("Paid out", inv["paid"]), ("Still to pay", inv["payable"]),
        ("Khaatas", inv["khaatas"]),
    ])
    _sheet(wb, "By month", ["Month", "Khaatas", "Commission of khaatas", "Earned", "Paid", "Still to pay after"],
           [[m["month"], m["khaatas"], m["commission"], m["earned"], m["paid"], m["payable_after"]] for m in d["months"]],
           money_cols={"Commission of khaatas", "Earned", "Paid", "Still to pay after"})
    _sheet(wb, "By khaata", ["Khaata (supplier)", "Months", "Commission", "Earned", "Invested", "Advance outstanding"],
           [[k["supplier_name"], k["months"], k["commission"], k["earned"], k["invested"], k["advance"]] for k in d["khaatas"]],
           money_cols={"Commission", "Earned", "Invested", "Advance outstanding"})
    _sheet(wb, "Every bill", ["Month", "Khaata", "Bill total", "Commission", "Share %", "Earned", "Bill status"],
           [[r["month"], r["supplier_name"], r["total"], r["commission"], r["pct"], r["earned"], r["status"]] for r in d["rows"]],
           money_cols={"Bill total", "Commission", "Earned"})
    _sheet(wb, "Shares", ["Khaata", "Share %", "From", "To", "Note"],
           [[x["supplier_name"], x["pct"], x["from_month"], x["to_month"] or "now", x["note"]] for x in d["shares"]])
    _sheet(wb, "Money in and out", ["Date", "What", "Khaata", "Amount", "Note"],
           [[e["on_date"], e["kind"], e["supplier_name"] or "", e["amount"], e["note"]] for e in d["entries"]], money_cols={"Amount"})
    return _download(_xlsx(wb), f"investor-{_file_name(inv['name'])}.xlsx", XLSX)
