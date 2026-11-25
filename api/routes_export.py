"""Downloads: a full backup of everything, and one supplier's or one investor's data as Excel.

    GET /export/backup                 owner only: .zip of readable Excel files (summary, every month, every
                                       supplier and investor, daily CSV) + raw tables in _restore/
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
from billing import bill_summaries, load_bill
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


def _readable_name(text: str) -> str:
    """A file name that keeps spaces and brackets, e.g. "Ajaz (Genda)"."""
    return re.sub(r'[<>:"/\|?*]+', "-", text).strip(" .") or "unnamed"


def _download(data: bytes, name: str, media: str) -> Response:
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/backup")
def backup(user: dict = Depends(require("owner"))):
    """A zip a person can open and read (Excel files with names, dates and rupees), plus the raw
    tables in _restore/ so the app could be loaded back from it."""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d_%H%M")
    top = f"PetalBoard backup {now:%Y-%m-%d}"
    buf = io.BytesIO()
    counts = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        with db.transaction() as conn:
            # Raw tables, only for restoring.
            for t in TABLES:
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                hide = SECRET_COLUMNS.get(t, set())
                rows = [{k: _plain(v) for k, v in r.items() if k not in hide} for r in rows]
                counts[t] = len(rows)
                z.writestr(f"{top}/_restore/{t}.json", json.dumps(rows, ensure_ascii=False, indent=1))
            z.writestr(f"{top}/_restore/manifest.json",
                       json.dumps({"made_at": stamp, "schema_version": 1, "tables": counts}, indent=1))
            # Readable files.
            z.writestr(f"{top}/Summary.xlsx", _summary_workbook(conn))
            months = [r["month"] for r in conn.execute("SELECT DISTINCT month FROM bills ORDER BY month").fetchall()]
            for m in months:
                z.writestr(f"{top}/Bills/{m:%Y-%m %B}.xlsx", _month_workbook(conn, m))
            z.writestr(f"{top}/Daily entries.csv", _daily_csv(conn))
            sup_ids = [r["id"] for r in conn.execute("SELECT id FROM suppliers ORDER BY name").fetchall()]
            inv_ids = [r["id"] for r in conn.execute("SELECT id FROM investors ORDER BY name").fetchall()]
        for sid in sup_ids:
            name, data = supplier_workbook(sid, user)
            z.writestr(f"{top}/Suppliers/{name}.xlsx", data)
        for iid in inv_ids:
            name, data = investor_workbook(iid, user)
            z.writestr(f"{top}/Investors/{name}.xlsx", data)
        z.writestr(f"{top}/README.txt", _readme(now, user, counts, len(months)).encode("utf-8-sig"))
    return _download(buf.getvalue(), f"petalboard-backup-{stamp}.zip", "application/zip")


def _month_label(m) -> str:
    return f"{m:%B %Y}" if isinstance(m, date) else datetime.strptime(m, "%Y-%m").strftime("%B %Y")


ADVANCE_KINDS = {"given": "Advance given", "recovered": "Paid back in cash", "bill_cut": "Cut from bill",
                 "opening": "Balance brought in", "written_off": "Written off"}


def _readme(now: datetime, user: dict, counts: dict, n_months: int) -> str:
    return f"""PetalBoard backup
Made {now:%d %B %Y, %H:%M} UTC by {user['display_name']}.

What is in this folder
----------------------
Summary.xlsx         Every supplier: where they stand now (who owes whom, advance outstanding),
                     every month's totals, and the investors.
Bills/               One Excel file per month ({n_months} months). The first sheet lists every bill of the
                     month; then one sheet per supplier, laid out like the paper bill.
Suppliers/           One Excel file per supplier: every bill, every day, the advance history, investors.
Investors/           One Excel file per investor: earnings by month and khaata, money in and out.
Daily entries.csv    Every day of every bill in one list (date, supplier, kg, rate, amount, kharcha).
                     Opens in Excel.
_restore/            The data exactly as the app stores it, only for loading it back into the app.
                     Do not edit these files. Passwords are not included.

Money is in whole rupees. "Final" above 0 means the shop owes the supplier; below 0 means the supplier
owes the shop.

اس فولڈر میں کیا ہے
--------------------
Summary.xlsx         ہر سپلائر کا حساب: کس کا کتنا لینا دینا ہے، ایڈوانس، ہر مہینے کا ٹوٹل، انویسٹر۔
Bills/               ہر مہینے کی ایک فائل، ہر سپلائر کا بل الگ شیٹ میں۔
Suppliers/           ہر سپلائر کی ایک فائل، پوری تاریخ کے ساتھ۔
Investors/           ہر انویسٹر کی ایک فائل۔
Daily entries.csv    ہر دن کا وزن، ریٹ، رقم اور خرچہ۔
_restore/            صرف ایپ میں واپس ڈالنے کے لیے۔ ان فائلوں کو نہ بدلیں۔

Records: {counts.get('suppliers', 0)} suppliers, {counts.get('bills', 0)} bills, {counts.get('bill_days', 0)} days,
{counts.get('kharcha', 0)} kharcha entries, {counts.get('advances', 0)} advance entries, {counts.get('investors', 0)} investors.
"""


def _summary_workbook(conn) -> bytes:
    from routes_reports import _kpis
    from routes_investors import _entry_totals, _summary, earnings
    bills = bill_summaries(conn)
    adv = {r["supplier_id"]: int(r["b"]) for r in conn.execute(
        "SELECT supplier_id, sum(amount) AS b FROM advances GROUP BY supplier_id").fetchall()}
    sups = conn.execute("""SELECT s.*, f.name AS flower_name FROM suppliers s
                           LEFT JOIN flowers f ON f.id = s.flower_id ORDER BY s.name""").fetchall()
    latest: dict[int, dict] = {}
    count: dict[int, int] = {}
    for b in bills:  # newest first
        latest.setdefault(b["supplier_id"], b)
        count[b["supplier_id"]] = count.get(b["supplier_id"], 0) + 1
    rows = []
    for s in sups:
        last = latest.get(s["id"])
        bal = 0 if not last else (last["carried"] or 0) if last["status"] == "closed" else last["final"]
        rows.append([s["name"], s.get("name_ur") or "", s["flower_name"] or "", "Yes" if s["active"] else "No",
                     bal, "Shop owes supplier" if bal > 0 else "Supplier owes shop" if bal < 0 else "Settled",
                     adv.get(s["id"], 0), count.get(s["id"], 0),
                     _month_label(last["month"]) if last else "", ("Open" if last["status"] == "open" else "Closed") if last else ""])
    wb = Workbook()
    payable = sum(r[4] for r in rows if r[4] > 0)
    receivable = -sum(r[4] for r in rows if r[4] < 0)
    _summary_sheet(wb, "PetalBoard: summary", [
        ("Suppliers", len(rows)), ("Active suppliers", sum(1 for s in sups if s["active"])),
        ("Bills", len(bills)), ("Shop owes suppliers (now)", payable), ("Suppliers owe shop (now)", receivable),
        ("Advance outstanding (all suppliers)", sum(adv.values())),
    ])
    _sheet(wb, "Suppliers", ["Supplier", "Name (Urdu)", "Flower", "Active", "Balance now", "Who owes",
                             "Advance outstanding", "Months", "Last bill", "Last bill status"], rows,
           money_cols={"Balance now", "Advance outstanding"})
    by_month: dict[str, list] = {}
    for b in bills:
        by_month.setdefault(b["month"], []).append(b)
    _sheet(wb, "Months", ["Month", "Suppliers", "kg", "Total amount", "Commission", "Net", "Kharcha",
                          "Shop owes suppliers", "Suppliers owe shop", "Average rate", "Open bills"],
           [[_month_label(m), k["suppliers"], k["weight"], k["total"], k["commission"], k["net"], k["kharcha"],
             k["payable"], k["receivable"], k["avg_rate"], k["open_bills"]]
            for m, k in ((m, _kpis(v)) for m, v in sorted(by_month.items()))],
           money_cols={"Total amount", "Commission", "Net", "Kharcha", "Shop owes suppliers", "Suppliers owe shop"})
    invs = conn.execute("SELECT * FROM investors ORDER BY name").fetchall()
    earned_rows, money = earnings(conn), _entry_totals(conn)
    inv_rows = []
    for inv in invs:
        s = _summary(inv, [r for r in earned_rows if r["investor_id"] == inv["id"]], money.get(inv["id"]))
        inv_rows.append([s["name"], s["invested"], s["withdrawn"], s["capital"], s["earned"], s["paid"], s["payable"], s["khaatas"]])
    _sheet(wb, "Investors", ["Investor", "Invested", "Withdrawn", "Capital", "Earned", "Paid out", "Still to pay", "Khaatas"],
           inv_rows, money_cols={"Invested", "Withdrawn", "Capital", "Earned", "Paid out", "Still to pay"})
    return _xlsx(wb)


def _sheet_name(name: str, used: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", name)[:31] or "Sheet"
    out, i = base, 2
    while out.lower() in used:
        out = f"{base[:28]} {i}"
        i += 1
    used.add(out.lower())
    return out


def _month_workbook(conn, month: date) -> bytes:
    ids = [r["id"] for r in conn.execute(
        "SELECT b.id FROM bills b JOIN suppliers s ON s.id = b.supplier_id WHERE b.month = %s ORDER BY s.name",
        (month,)).fetchall()]
    bills = [load_bill(conn, i) for i in ids]
    wb = Workbook()
    wb.remove(wb.active)
    label = _month_label(month)
    list_rows = []
    for b in bills:
        t, st = b["totals"], b["settlement"]
        list_rows.append([b["supplier"]["name"], b["flower"]["name"] if b["flower"] else "", "Open" if b["status"] == "open" else "Closed",
                          t["days_supplied"], t["weight"], t["total"], t["commission"], t["net"], t["kharcha"],
                          b["opening_due"] or None, t["final"], st["paid_cash"], st["advance_cut"], st["carried"],
                          b["advance_balance"], "; ".join(b["warnings"])])
    money = {"Total amount", "Commission", "Net", "Kharcha", "From last month", "Final", "Paid cash", "Cut from advance",
             "Carried to next month", "Advance outstanding"}
    _sheet(wb, f"All bills {label}", ["Supplier", "Flower", "Status", "Days", "kg", "Total amount", "Commission", "Net",
                                      "Kharcha", "From last month", "Final", "Paid cash", "Cut from advance",
                                      "Carried to next month", "Advance outstanding", "To check"],
           list_rows, money_cols=money, widths={"To check": 60})
    used = {f"all bills {label}".lower()[:31]}
    bold = Font(bold=True)
    for b in bills:
        t, st = b["totals"], b["settlement"]
        ws = wb.create_sheet(_sheet_name(b["supplier"]["name"], used))
        ws.append([b["supplier"]["name"], b["supplier"].get("name_ur") or ""])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"{label} · {b['flower']['name'] if b['flower'] else ''} · commission {b['commission_pct']}% · "
                   f"{'open' if b['status'] == 'open' else 'closed'}"])
        ws.append([])
        ws.append(["Day", "Weight kg", "Rate", "Amount", "Kharcha", "Kharcha items", "Note"])
        for c in ws[ws.max_row]:
            c.fill, c.font = HEAD_FILL, HEAD_FONT
        for d in b["days"]:
            items = "+".join(f"{k['amount']}{k['tag'] or ''}" for k in d["kharcha"])
            if d["day"] == 1 and b["opening_due"] > 0:
                items = f"{b['opening_due']} (last month)" + ("+" + items if items else "")
            ws.append([d["day"], _plain(d["weight"]), _plain(d["rate"]), d["amount"], d["kharcha_total"] or None, items or None, d["note"]])
        ws.append(["Total", t["weight"], t["avg_rate"], t["total"], t["kharcha"]])
        for c in ws[ws.max_row]:
            c.font = bold
        ws.append([])
        panel = [("Total amount", t["total"]), (f"Commission ({b['commission_pct']}%)", t["commission"]),
                 ("Net amount", t["net"]), ("Kharcha", t["kharcha"])]
        if b["opening_due"]:
            panel.append(("Owed from last month" if b["opening_due"] > 0 else "Due to supplier from last month", abs(b["opening_due"])))
        panel += [("Final (+ shop owes supplier, − supplier owes shop)", t["final"]),
                  ("Advance outstanding", b["advance_balance"])]
        if b["status"] == "closed":
            panel += [("Paid cash", st["paid_cash"]), ("Cut from advance", st["advance_cut"]), ("Carried to next month", st["carried"])]
        for k, v in panel:
            ws.append([k, None, None, v])
            ws.cell(ws.max_row, 1).font = bold
        if b["note"]:
            ws.append([])
            ws.append(["Note", b["note"]])
        if b["warnings"]:
            ws.append(["To check", "; ".join(b["warnings"])])
        for row in ws.iter_rows(min_row=5, min_col=4, max_col=5):
            for c in row:
                c.number_format = "#,##0"
        for col, w in zip("ABCDEFG", (10, 11, 9, 12, 12, 22, 40)):
            ws.column_dimensions[col].width = w
        ws.column_dimensions["A"].width = 14
    return _xlsx(wb)


def _daily_csv(conn) -> bytes:
    rows = conn.execute("""
        WITH x AS (SELECT bill_id, day FROM bill_days UNION SELECT bill_id, day FROM kharcha)
        SELECT b.month, x.day, s.name AS supplier, f.name AS flower, d.weight, d.rate, d.amount, d.note,
               k.items, k.total AS kharcha
        FROM x JOIN bills b ON b.id = x.bill_id JOIN suppliers s ON s.id = b.supplier_id
        LEFT JOIN flowers f ON f.id = b.flower_id
        LEFT JOIN bill_days d ON d.bill_id = x.bill_id AND d.day = x.day
        LEFT JOIN (SELECT bill_id, day, string_agg(amount::text || coalesce(tag, ''), '+' ORDER BY id) AS items,
                          sum(amount) AS total FROM kharcha GROUP BY bill_id, day) k
             ON k.bill_id = x.bill_id AND k.day = x.day
        ORDER BY b.month, x.day, s.name""").fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Supplier", "Flower", "Weight kg", "Rate", "Amount", "Kharcha", "Kharcha items", "Note"])
    for r in rows:
        day = r["day"]
        w.writerow([f"{r['month']:%Y-%m}-{day:02d}" if day else "", r["supplier"], r["flower"] or "", _plain(r["weight"]),
                    _plain(r["rate"]), r["amount"], r["kharcha"], r["items"], r["note"] or ""])
    return ("﻿" + out.getvalue()).encode("utf-8")


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
    name, data = supplier_workbook(supplier_id, user)
    return _download(data, f"supplier-{_file_name(name)}.xlsx", XLSX)


def supplier_workbook(supplier_id: int, user: dict) -> tuple[str, bytes]:
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
           [[a["on_date"], ADVANCE_KINDS.get(a["kind"], a["kind"]), a["amount"], a["running"], a["note"]] for a in d["advances"]], money_cols={"Amount", "Balance"})
    _sheet(wb, "Investors", ["Investor", "Share %", "From", "To", "Note"],
           [[x["investor_name"], x["pct"], x["from_month"], x["to_month"] or "now", x["note"]] for x in d["shares"]])
    _sheet(wb, "Investor earnings", ["Month", "Investor", "Commission", "Share %", "Earned"],
           [[r["month"], r["investor_name"], r["commission"], r["pct"], r["earned"]] for r in d["investor_earnings"]],
           money_cols={"Commission", "Earned"})
    return _readable_name(s["name"]), _xlsx(wb)


@router.get("/export/investor/{investor_id}.xlsx")
def export_investor(investor_id: int, user: dict = Depends(current_user)):
    name, data = investor_workbook(investor_id, user)
    return _download(data, f"investor-{_file_name(name)}.xlsx", XLSX)


def investor_workbook(investor_id: int, user: dict) -> tuple[str, bytes]:
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
    return _readable_name(inv["name"]), _xlsx(wb)
