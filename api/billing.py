"""The bill arithmetic, exactly as the paper sheet does it, and loading a full bill.

    amount     = floor(weight * rate)                 per day (ٹوٹل رقم)
    total      = sum of amounts                       (ٹوٹل رقم box)
    commission = floor(total * commission_pct / 100)  (کمیشن)
    net        = total - commission                   (رقم)
    kharcha    = sum of kharcha items                 (خرچہ)
    final      = net - kharcha - opening_due          (ٹوٹل)

final > 0 means the shop owes the supplier; final < 0 means the supplier owes the shop.
"""

import calendar
import math
from datetime import date
from decimal import ROUND_FLOOR, Decimal

from fastapi import HTTPException


def day_amount(weight, rate) -> int:
    """Amount for one day, rounded down to whole rupees like the paper (232.5 x 55 = 12787)."""
    if weight is None or rate is None:
        return 0
    return int((Decimal(str(weight)) * Decimal(str(rate))).to_integral_value(rounding=ROUND_FLOOR))


def commission_of(total: int, pct) -> int:
    return int((Decimal(total) * Decimal(str(pct)) / 100).to_integral_value(rounding=ROUND_FLOOR))


def totals(amounts: list[int], kharcha: list[int], commission_pct, opening_due: int = 0) -> dict:
    total = sum(amounts)
    commission = commission_of(total, commission_pct)
    net = total - commission
    kh = sum(kharcha)
    return {
        "total": total,
        "commission": commission,
        "net": net,
        "kharcha": kh,
        "opening_due": opening_due,
        "final": net - kh - opening_due,
    }


def month_start(value: str | date) -> date:
    """'2026-10' or '2026-10-05' or a date -> date(2026, 10, 1)."""
    if isinstance(value, date):
        return value.replace(day=1)
    try:
        parts = [int(p) for p in str(value).split("-")]
        return date(parts[0], parts[1], 1)
    except (ValueError, IndexError):
        raise HTTPException(400, "Month must look like 2026-10")


def days_in(month: date) -> int:
    return calendar.monthrange(month.year, month.month)[1]


def next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def prev_month(month: date) -> date:
    return date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)


def as_num(v):
    """Decimal -> float/int for JSON, keeping 7.5 as 7.5 and 8 as 8."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f == math.floor(f) else f


def advance_balance(conn, supplier_id: int, upto: date | None = None) -> int:
    q = "SELECT coalesce(sum(amount), 0) AS b FROM advances WHERE supplier_id = %s"
    args: list = [supplier_id]
    if upto:
        q += " AND on_date <= %s"
        args.append(upto)
    return int(conn.execute(q, args).fetchone()["b"])


def load_bill(conn, bill_id: int) -> dict:
    """A bill with its days, kharcha, totals, warnings and what is needed to print it."""
    bill = conn.execute(
        """
        SELECT b.*, s.name AS supplier_name, s.name_ur AS supplier_name_ur, s.phone AS supplier_phone,
               f.name AS flower_name, f.name_ur AS flower_name_ur
        FROM bills b JOIN suppliers s ON s.id = b.supplier_id LEFT JOIN flowers f ON f.id = b.flower_id
        WHERE b.id = %s
        """,
        (bill_id,),
    ).fetchone()
    if not bill:
        raise HTTPException(404, "Bill not found")

    days = conn.execute(
        "SELECT day, weight, rate, amount, note FROM bill_days WHERE bill_id = %s ORDER BY day", (bill_id,)
    ).fetchall()
    kh = conn.execute(
        "SELECT id, day, amount, tag, note FROM kharcha WHERE bill_id = %s ORDER BY day, id", (bill_id,)
    ).fetchall()

    n_days = days_in(bill["month"])
    by_day = {d["day"]: d for d in days}
    kh_by_day: dict[int, list] = {}
    for k in kh:
        kh_by_day.setdefault(k["day"], []).append({"id": k["id"], "amount": int(k["amount"]), "tag": k["tag"], "note": k["note"]})

    rows, warnings = [], []
    for day in range(1, n_days + 1):
        d = by_day.get(day)
        items = kh_by_day.get(day, [])
        row = {
            "day": day,
            "weight": as_num(d["weight"]) if d else None,
            "rate": as_num(d["rate"]) if d else None,
            "amount": int(d["amount"]) if d else None,
            "note": d["note"] if d else None,
            "kharcha": items,
            "kharcha_total": sum(i["amount"] for i in items),
        }
        if d and d["weight"] is not None and d["rate"] is not None:
            expected = day_amount(d["weight"], d["rate"])
            if expected != int(d["amount"]):
                row["mismatch"] = expected
                warnings.append(
                    f"Day {day}: {row['weight']} × {row['rate']} = {expected:,} but the bill says {int(d['amount']):,}"
                )
        rows.append(row)

    t = totals([int(d["amount"]) for d in days], [int(k["amount"]) for k in kh], bill["commission_pct"], int(bill["opening_due"]))
    weights = [Decimal(str(d["weight"])) for d in days if d["weight"] is not None]
    t["weight"] = as_num(sum(weights)) if weights else 0
    t["days_supplied"] = sum(1 for d in days if d["weight"])
    t["avg_rate"] = round(t["total"] / float(sum(weights)), 1) if weights and sum(weights) else None

    paper = bill["paper_totals"]
    if paper:
        for key, label in (("total", "Total amount"), ("commission", "Commission"), ("net", "Net amount"),
                           ("kharcha", "Kharcha"), ("final", "Final total")):
            pv = paper.get(key)
            mine = t[key] if key != "kharcha" else t["kharcha"] + t["opening_due"]
            if key == "final" and paper.get("advance_cut"):
                mine -= paper["advance_cut"]   # the workbook takes the month's advance cut off the final
            if pv is not None and pv != mine:
                warnings.append(f"Paper bill wrote {label} {pv:,}; the rows give {mine:,}")

    supplier_id = bill["supplier_id"]
    month_end = date(bill["month"].year, bill["month"].month, n_days)
    return {
        "id": bill["id"],
        "supplier": {"id": supplier_id, "name": bill["supplier_name"], "name_ur": bill["supplier_name_ur"],
                     "phone": bill["supplier_phone"]},
        "month": bill["month"].isoformat()[:7],
        "flower": {"id": bill["flower_id"], "name": bill["flower_name"], "name_ur": bill["flower_name_ur"]}
        if bill["flower_id"] else None,
        "commission_pct": as_num(bill["commission_pct"]),
        "opening_due": int(bill["opening_due"]),
        "note": bill["note"],
        "paper_totals": paper,
        "status": bill["status"],
        "settlement": {
            "paid_cash": bill["paid_cash"], "advance_cut": bill["advance_cut"], "carried": bill["carried"],
            "closed_at": bill["closed_at"].isoformat() if bill["closed_at"] else None,
        },
        "advance_balance": advance_balance(conn, supplier_id, month_end),
        "days": rows,
        "totals": t,
        "warnings": warnings,
    }


def bill_summaries(conn, where: str = "TRUE", args: tuple = ()) -> list[dict]:
    """Totals for many bills in one query (for lists and reports)."""
    rows = conn.execute(
        f"""
        SELECT b.id, b.supplier_id, s.name AS supplier_name, b.month, b.status, b.commission_pct, b.opening_due,
               b.flower_id, f.name AS flower_name, b.carried, b.paid_cash, b.advance_cut,
               (b.paper_totals IS NOT NULL) AS imported,
               coalesce(d.total, 0) AS total, coalesce(d.weight, 0) AS weight, coalesce(d.days, 0) AS days_supplied,
               coalesce(k.kharcha, 0) AS kharcha
        FROM bills b
        JOIN suppliers s ON s.id = b.supplier_id
        LEFT JOIN flowers f ON f.id = b.flower_id
        LEFT JOIN (SELECT bill_id, sum(amount) AS total, sum(weight) AS weight,
                          count(*) FILTER (WHERE weight > 0) AS days
                   FROM bill_days GROUP BY bill_id) d ON d.bill_id = b.id
        LEFT JOIN (SELECT bill_id, sum(amount) AS kharcha FROM kharcha GROUP BY bill_id) k ON k.bill_id = b.id
        WHERE {where}
        ORDER BY b.month DESC, s.name
        """,
        args,
    ).fetchall()
    out = []
    for r in rows:
        t = totals([int(r["total"])], [int(r["kharcha"])], r["commission_pct"], int(r["opening_due"]))
        out.append({
            "id": r["id"], "supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
            "month": r["month"].isoformat()[:7], "status": r["status"],
            "flower_id": r["flower_id"], "flower_name": r["flower_name"],
            "commission_pct": as_num(r["commission_pct"]), "weight": as_num(r["weight"]),
            "days_supplied": r["days_supplied"], "imported": r["imported"],
            "carried": r["carried"], "paid_cash": r["paid_cash"], "advance_cut": r["advance_cut"],
            **t,
        })
    return out
