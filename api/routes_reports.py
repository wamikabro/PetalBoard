"""Numbers for the dashboard: a month at a glance, trends across months, who owes whom."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

import db
import forecast
from billing import as_num, bill_summaries, days_in, month_start, prev_month
from security import current_user

router = APIRouter(tags=["reports"])


def _kpis(bills: list[dict]) -> dict:
    total = sum(b["total"] for b in bills)
    weight = sum(float(b["weight"] or 0) for b in bills)
    return {
        "suppliers": sum(1 for b in bills if b["total"] or b["kharcha"]),
        "bills": len(bills),
        "weight": round(weight, 3),
        "total": total,
        "commission": sum(b["commission"] for b in bills),
        "net": sum(b["net"] for b in bills),
        "kharcha": sum(b["kharcha"] for b in bills),
        "opening_due": sum(b["opening_due"] for b in bills),
        "payable": sum(b["final"] for b in bills if b["final"] > 0),      # shop owes suppliers
        "receivable": -sum(b["final"] for b in bills if b["final"] < 0),  # suppliers owe shop
        "avg_rate": round(total / weight, 1) if weight else None,
        "open_bills": sum(1 for b in bills if b["status"] == "open"),
    }


@router.get("/reports/month")
def month_report(month: str, user: dict = Depends(current_user)):
    m = month_start(month)
    with db.transaction() as conn:
        bills = bill_summaries(conn, "b.month = %s", (m,))
        prev = bill_summaries(conn, "b.month = %s", (prev_month(m),))
        day_rows = conn.execute(
            """
            SELECT d.day, coalesce(f.name, 'Other') AS flower, sum(d.weight) AS weight, sum(d.amount) AS amount,
                   min(d.rate) AS min_rate, max(d.rate) AS max_rate, count(*) FILTER (WHERE d.weight > 0) AS suppliers
            FROM bill_days d JOIN bills b ON b.id = d.bill_id LEFT JOIN flowers f ON f.id = b.flower_id
            WHERE b.month = %s
            GROUP BY d.day, coalesce(f.name, 'Other') ORDER BY d.day
            """, (m,)).fetchall()
        kh_rows = conn.execute(
            "SELECT k.day, sum(k.amount) AS amount FROM kharcha k JOIN bills b ON b.id = k.bill_id "
            "WHERE b.month = %s GROUP BY k.day", (m,)).fetchall()

    n = days_in(m)
    flowers = sorted({r["flower"] for r in day_rows})
    daily = [{"day": d, "weight": 0.0, "amount": 0, "kharcha": 0, "suppliers": 0, "by_flower": {}} for d in range(1, n + 1)]
    flower_tot: dict[str, dict] = {}
    for r in day_rows:
        w, a = float(r["weight"] or 0), int(r["amount"] or 0)
        if r["day"] <= n:   # a row on e.g. 31 June (from the workbook) counts in the totals only
            row = daily[r["day"] - 1]
            row["weight"] = round(row["weight"] + w, 3)
            row["amount"] += a
            row["suppliers"] += r["suppliers"]
            row["by_flower"][r["flower"]] = {
                "weight": round(w, 3), "amount": a, "avg_rate": round(a / w, 1) if w else None,
                "min_rate": as_num(r["min_rate"]), "max_rate": as_num(r["max_rate"]),
            }
        ft = flower_tot.setdefault(r["flower"], {"flower": r["flower"], "weight": 0.0, "amount": 0,
                                                 "min_rate": None, "max_rate": None})
        ft["weight"] += w
        ft["amount"] += a
        for key, fn in (("min_rate", min), ("max_rate", max)):
            v = as_num(r[key])
            if v is not None:
                ft[key] = v if ft[key] is None else fn(ft[key], v)
    for r in kh_rows:
        if r["day"] <= n:
            daily[r["day"] - 1]["kharcha"] = int(r["amount"])
    for ft in flower_tot.values():
        ft["weight"] = round(ft["weight"], 3)
        ft["avg_rate"] = round(ft["amount"] / ft["weight"], 1) if ft["weight"] else None
        ft["suppliers"] = sum(1 for b in bills if (b["flower_name"] or "Other") == ft["flower"] and b["total"])

    return {
        "month": m.isoformat()[:7],
        "kpis": _kpis(bills),
        "previous": _kpis(prev) if prev else None,
        "flowers": flowers,
        "daily": daily,
        "by_flower": sorted(flower_tot.values(), key=lambda f: -f["amount"]),
        "by_supplier": sorted(bills, key=lambda b: -b["total"]),
    }


@router.get("/reports/entries")
def entries(date_from: date, date_to: date, supplier_id: int | None = None, flower_id: int | None = None,
            user: dict = Depends(current_user)):
    """Every day's entry (weight, rate, amount, kharcha) in a date range, for the flexible data view."""
    if date_to < date_from:
        raise HTTPException(400, "The end date is before the start date")
    if (date_to - date_from).days > 800:
        raise HTTPException(400, "Pick a range of two years or less")
    where = ["x.on_date BETWEEN %s AND %s"]
    args: list = [date_from, date_to]
    if supplier_id:
        where.append("b.supplier_id = %s")
        args.append(supplier_id)
    if flower_id:
        where.append("b.flower_id = %s")
        args.append(flower_id)
    with db.transaction() as conn:
        rows = conn.execute(
            f"""
            WITH x AS (
                SELECT bill_id, day FROM bill_days UNION SELECT bill_id, day FROM kharcha
            ), x2 AS (
                SELECT x.bill_id, x.day, (b.month + (x.day - 1) * interval '1 day')::date AS on_date
                FROM x JOIN bills b ON b.id = x.bill_id
            )
            SELECT x.on_date, b.id AS bill_id, s.id AS supplier_id, s.name AS supplier, coalesce(f.name, '') AS flower,
                   d.weight, d.rate, coalesce(d.amount, 0) AS amount,
                   coalesce((SELECT sum(k.amount) FROM kharcha k WHERE k.bill_id = b.id AND k.day = x.day), 0) AS kharcha
            FROM x2 x JOIN bills b ON b.id = x.bill_id JOIN suppliers s ON s.id = b.supplier_id
            LEFT JOIN flowers f ON f.id = b.flower_id
            LEFT JOIN bill_days d ON d.bill_id = b.id AND d.day = x.day
            WHERE {" AND ".join(where)}
            ORDER BY x.on_date, s.name, b.month, x.day
            """, args).fetchall()
    out = [{"date": r["on_date"].isoformat(), "bill_id": r["bill_id"], "supplier_id": r["supplier_id"],
            "supplier": r["supplier"], "flower": r["flower"], "weight": as_num(r["weight"]), "rate": as_num(r["rate"]),
            "amount": int(r["amount"]), "kharcha": int(r["kharcha"])} for r in rows]
    w = sum(float(r["weight"] or 0) for r in out)
    a = sum(r["amount"] for r in out)
    return {
        "rows": out,
        "totals": {"weight": round(w, 3), "amount": a, "kharcha": sum(r["kharcha"] for r in out),
                   "avg_rate": round(a / w, 1) if w else None, "days": len({r["date"] for r in out}),
                   "suppliers": len({r["supplier_id"] for r in out})},
    }


@router.get("/reports/overview")
def overview(user: dict = Depends(current_user)):
    """Every month's totals, for the trend charts."""
    with db.transaction() as conn:
        bills = bill_summaries(conn)
    months: dict[str, list] = {}
    for b in bills:
        months.setdefault(b["month"], []).append(b)
    return [{"month": k, **_kpis(v)} for k, v in sorted(months.items())]


@router.get("/reports/forecast")
def rate_forecast(on: date, user: dict = Depends(current_user)):
    """Today's and tomorrow's rate per flower (forecast.py). `on` is the phone's date (the server runs on UTC)."""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT b.month, d.day, coalesce(f.name, 'Other') AS flower, d.amount, d.weight
            FROM bill_days d JOIN bills b ON b.id = d.bill_id LEFT JOIN flowers f ON f.id = b.flower_id
            WHERE d.weight > 0 AND d.rate IS NOT NULL AND b.month <= %s
            """, (on,)).fetchall()
    points: dict[str, list] = {}
    for r in rows:
        if r["day"] > days_in(r["month"]):   # e.g. 31 June from the workbook: no such date
            continue
        d = r["month"] + timedelta(days=r["day"] - 1)
        if d > on:
            continue
        points.setdefault(r["flower"], []).append((d, int(r["amount"]), int(r["weight"] * 1000)))
    return forecast.forecast(points, on)


@router.get("/reports/balances")
def balances(user: dict = Depends(current_user)):
    """Where every supplier stands: latest bill balance and advance outstanding."""
    with db.transaction() as conn:
        bills = bill_summaries(conn)
        adv = {r["supplier_id"]: int(r["b"]) for r in conn.execute(
            "SELECT supplier_id, sum(amount) AS b FROM advances GROUP BY supplier_id").fetchall()}
        sups = conn.execute("SELECT id, name, active FROM suppliers ORDER BY name").fetchall()
    latest: dict[int, dict] = {}
    for b in bills:
        latest.setdefault(b["supplier_id"], b)
    rows = []
    for s in sups:
        last = latest.get(s["id"])
        bal = 0 if not last else (last["carried"] or 0) if last["status"] == "closed" else last["final"]
        rows.append({"supplier_id": s["id"], "name": s["name"], "active": s["active"], "balance": bal,
                     "advance": adv.get(s["id"], 0), "last_month": last["month"] if last else None,
                     "last_status": last["status"] if last else None})
    return {
        "rows": rows,
        "payable": sum(r["balance"] for r in rows if r["balance"] > 0),
        "receivable": -sum(r["balance"] for r in rows if r["balance"] < 0),
        "advances": sum(r["advance"] for r in rows),
    }
