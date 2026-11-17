"""Investors: who put money into which khaata, their share of its commission, and what they earned.

Earned for a month = the khaata's commission that month x the investor's pct (khaata_shares).
Payable to the investor = earned - payouts. Capital = invested - withdrawn.
"""

from datetime import date
from decimal import ROUND_FLOOR, Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from billing import as_num, bill_summaries, month_start
from security import current_user, require
from util import get_or_404, insert_row, update_row

router = APIRouter(tags=["investors"])

FAR = date(9999, 12, 1)


def share_of(commission: int, pct) -> int:
    return int((Decimal(commission) * Decimal(str(pct)) / 100).to_integral_value(rounding=ROUND_FLOOR))


def earnings(conn, investor_id: int | None = None, supplier_id: int | None = None) -> list[dict]:
    """One row per (investor, bill) that falls inside a share period."""
    where, args = ["TRUE"], []
    if investor_id:
        where.append("ks.investor_id = %s")
        args.append(investor_id)
    if supplier_id:
        where.append("ks.supplier_id = %s")
        args.append(supplier_id)
    shares = conn.execute(
        f"""SELECT ks.*, i.name AS investor_name FROM khaata_shares ks JOIN investors i ON i.id = ks.investor_id
            WHERE {' AND '.join(where)}""", args).fetchall()
    if not shares:
        return []
    sup_ids = list({s["supplier_id"] for s in shares})
    bills = bill_summaries(conn, "b.supplier_id = ANY(%s)", (sup_ids,))
    out = []
    for s in shares:
        lo, hi = s["from_month"], s["to_month"] or FAR
        for b in bills:
            m = month_start(b["month"])
            if b["supplier_id"] == s["supplier_id"] and lo <= m <= hi:
                out.append({
                    "investor_id": s["investor_id"], "investor_name": s["investor_name"],
                    "supplier_id": b["supplier_id"], "supplier_name": b["supplier_name"], "month": b["month"],
                    "bill_id": b["id"], "total": b["total"], "commission": b["commission"],
                    "pct": as_num(s["pct"]), "earned": share_of(b["commission"], s["pct"]), "status": b["status"],
                })
    out.sort(key=lambda r: (r["month"], r["supplier_name"]))
    return out


def _entry_totals(conn, investor_id: int | None = None) -> dict[int, dict]:
    q = """SELECT investor_id,
                  coalesce(sum(amount) FILTER (WHERE kind = 'invested'), 0) AS invested,
                  coalesce(sum(amount) FILTER (WHERE kind = 'withdrawn'), 0) AS withdrawn,
                  coalesce(sum(amount) FILTER (WHERE kind = 'payout'), 0) AS paid
           FROM investor_entries"""
    args: list = []
    if investor_id:
        q += " WHERE investor_id = %s"
        args.append(investor_id)
    q += " GROUP BY investor_id"
    return {r["investor_id"]: {k: int(r[k]) for k in ("invested", "withdrawn", "paid")} for r in conn.execute(q, args).fetchall()}


def _summary(inv: dict, earned_rows: list[dict], money: dict) -> dict:
    earned = sum(r["earned"] for r in earned_rows)
    m = money or {"invested": 0, "withdrawn": 0, "paid": 0}
    return {
        **inv,
        "earned": earned,
        "paid": m["paid"],
        "payable": earned - m["paid"],
        "invested": m["invested"],
        "withdrawn": m["withdrawn"],
        "capital": m["invested"] - m["withdrawn"],
        "khaatas": len({r["supplier_id"] for r in earned_rows}),
    }


# ---------- investors ----------

class InvestorIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str | None = None
    notes: str | None = None
    active: bool | None = None


class InvestorChange(InvestorIn):
    name: str | None = Field(default=None, min_length=1)


@router.get("/investors")
def list_investors(user: dict = Depends(current_user)):
    with db.transaction() as conn:
        invs = conn.execute("SELECT * FROM investors ORDER BY active DESC, name").fetchall()
        rows = earnings(conn)
        money = _entry_totals(conn)
        open_shares = conn.execute(
            "SELECT investor_id, count(*) AS n FROM khaata_shares WHERE to_month IS NULL GROUP BY investor_id").fetchall()
    n_open = {r["investor_id"]: r["n"] for r in open_shares}
    out = []
    for inv in invs:
        mine = [r for r in rows if r["investor_id"] == inv["id"]]
        s = _summary(inv, mine, money.get(inv["id"]))
        s["active_khaatas"] = n_open.get(inv["id"], 0)
        months: dict[str, int] = {}
        for r in mine:
            months[r["month"]] = months.get(r["month"], 0) + r["earned"]
        s["by_month"] = [{"month": k, "earned": v} for k, v in sorted(months.items())]
        out.append(s)
    return out


@router.post("/investors")
def create_investor(body: InvestorIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        if conn.execute("SELECT 1 FROM investors WHERE lower(name) = lower(%s)", (body.name.strip(),)).fetchone():
            raise HTTPException(400, "An investor with that name already exists")
        return insert_row(conn, "investors", {**body.model_dump(exclude_none=True), "name": body.name.strip()})


@router.patch("/investors/{investor_id}")
def change_investor(investor_id: int, body: InvestorChange, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        return update_row(conn, "investors", investor_id, body.model_dump(exclude_none=True), {"name", "phone", "notes", "active"})


@router.get("/investors/{investor_id}")
def get_investor(investor_id: int, user: dict = Depends(current_user)):
    with db.transaction() as conn:
        inv = get_or_404(conn, "investors", investor_id)
        rows = earnings(conn, investor_id=investor_id)
        money = _entry_totals(conn, investor_id).get(investor_id)
        shares = conn.execute(
            """SELECT ks.*, s.name AS supplier_name FROM khaata_shares ks JOIN suppliers s ON s.id = ks.supplier_id
               WHERE ks.investor_id = %s ORDER BY s.name, ks.from_month""", (investor_id,)).fetchall()
        entries = conn.execute(
            """SELECT e.*, s.name AS supplier_name FROM investor_entries e LEFT JOIN suppliers s ON s.id = e.supplier_id
               WHERE e.investor_id = %s ORDER BY e.on_date, e.id""", (investor_id,)).fetchall()
        advances = conn.execute(
            """SELECT supplier_id, sum(amount) AS b FROM advances
               WHERE supplier_id IN (SELECT supplier_id FROM khaata_shares WHERE investor_id = %s) GROUP BY supplier_id""",
            (investor_id,)).fetchall()
    adv = {r["supplier_id"]: int(r["b"]) for r in advances}
    for s in shares:
        s["pct"] = as_num(s["pct"])
        s["from_month"] = s["from_month"].isoformat()[:7]
        s["to_month"] = s["to_month"].isoformat()[:7] if s["to_month"] else None
    # Per khaata totals
    khaatas: dict[int, dict] = {}
    for r in rows:
        k = khaatas.setdefault(r["supplier_id"], {"supplier_id": r["supplier_id"], "supplier_name": r["supplier_name"],
                                                  "commission": 0, "earned": 0, "invested": 0, "months": 0})
        k["commission"] += r["commission"]
        k["earned"] += r["earned"]
        k["months"] += 1
    for e in entries:
        if e["supplier_id"]:
            k = khaatas.setdefault(e["supplier_id"], {"supplier_id": e["supplier_id"], "supplier_name": e["supplier_name"],
                                                      "commission": 0, "earned": 0, "invested": 0, "months": 0})
            k["invested"] += e["amount"] if e["kind"] == "invested" else -e["amount"] if e["kind"] == "withdrawn" else 0
    for k in khaatas.values():
        k["advance"] = adv.get(k["supplier_id"], 0)
    months: dict[str, dict] = {}
    for r in rows:
        m = months.setdefault(r["month"], {"month": r["month"], "commission": 0, "earned": 0, "khaatas": 0})
        m["commission"] += r["commission"]
        m["earned"] += r["earned"]
        m["khaatas"] += 1
    paid_by_month: dict[str, int] = {}
    for e in entries:
        if e["kind"] == "payout":
            key = e["on_date"].isoformat()[:7]
            paid_by_month[key] = paid_by_month.get(key, 0) + e["amount"]
    for k, v in paid_by_month.items():
        months.setdefault(k, {"month": k, "commission": 0, "earned": 0, "khaatas": 0})
    running = 0
    for k in sorted(months):
        months[k]["paid"] = paid_by_month.get(k, 0)
        running += months[k]["earned"] - months[k]["paid"]
        months[k]["payable_after"] = running
    return {
        "investor": _summary(inv, rows, money),
        "shares": shares,
        "khaatas": sorted(khaatas.values(), key=lambda k: -k["earned"]),
        "months": [months[k] for k in sorted(months)],
        "rows": rows,
        "entries": entries,
    }


# ---------- shares ----------

class ShareIn(BaseModel):
    supplier_id: int
    investor_id: int
    pct: float = Field(gt=0, le=100)
    from_month: str
    to_month: str | None = None
    note: str | None = None


def _share_fields(body) -> dict:
    f = body.model_dump(exclude_unset=True)
    if "from_month" in f:
        f["from_month"] = month_start(f["from_month"])
    if f.get("to_month"):
        f["to_month"] = month_start(f["to_month"])
    return f


def _check_overlap(conn, supplier_id, investor_id, lo, hi, skip_id=None):
    row = conn.execute(
        """SELECT 1 FROM khaata_shares WHERE supplier_id = %s AND investor_id = %s AND id <> coalesce(%s, -1)
           AND from_month <= %s AND coalesce(to_month, '9999-12-01') >= %s""",
        (supplier_id, investor_id, skip_id, hi or FAR, lo)).fetchone()
    if row:
        raise HTTPException(400, "This investor already has a share in this khaata for some of those months")
    total = conn.execute(
        """SELECT coalesce(max(t), 0) AS m FROM (
             SELECT g.m, sum(ks.pct) AS t FROM generate_series(%s::date, least(%s::date, %s::date + interval '5 years'), interval '1 month') g(m)
             JOIN khaata_shares ks ON ks.supplier_id = %s AND ks.id <> coalesce(%s, -1)
               AND ks.from_month <= g.m AND coalesce(ks.to_month, '9999-12-01') >= g.m GROUP BY g.m) x""",
        (lo, hi or FAR, lo, supplier_id, skip_id)).fetchone()["m"]
    return float(total)


@router.post("/khaata-shares")
def add_share(body: ShareIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        f = _share_fields(body)
        if f.get("to_month") and f["to_month"] < f["from_month"]:
            raise HTTPException(400, "The end month is before the start month")
        others = _check_overlap(conn, body.supplier_id, body.investor_id, f["from_month"], f.get("to_month"))
        if others + body.pct > 100:
            raise HTTPException(400, f"Shares in this khaata would add up to more than 100% ({others + body.pct:g}%)")
        row = insert_row(conn, "khaata_shares", f)
    row["pct"] = as_num(row["pct"])
    return row


class ShareChange(BaseModel):
    pct: float | None = Field(default=None, gt=0, le=100)
    from_month: str | None = None
    to_month: str | None = None
    note: str | None = None


@router.patch("/khaata-shares/{share_id}")
def change_share(share_id: int, body: ShareChange, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        cur = get_or_404(conn, "khaata_shares", share_id)
        f = _share_fields(body)
        if "to_month" in f and not f["to_month"]:
            f["to_month"] = None
        lo = f.get("from_month", cur["from_month"])
        hi = f["to_month"] if "to_month" in f else cur["to_month"]
        if hi and hi < lo:
            raise HTTPException(400, "The end month is before the start month")
        others = _check_overlap(conn, cur["supplier_id"], cur["investor_id"], lo, hi, share_id)
        if others + float(f.get("pct", cur["pct"])) > 100:
            raise HTTPException(400, "Shares in this khaata would add up to more than 100%")
        if not f:
            raise HTTPException(400, "Nothing to change")
        sets = ", ".join(f"{k} = %s" for k in f)
        row = conn.execute(f"UPDATE khaata_shares SET {sets} WHERE id = %s RETURNING *", (*f.values(), share_id)).fetchone()
    row["pct"] = as_num(row["pct"])
    return row


@router.delete("/khaata-shares/{share_id}")
def delete_share(share_id: int, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "khaata_shares", share_id)
        conn.execute("DELETE FROM khaata_shares WHERE id = %s", (share_id,))
    return {"ok": True}


# ---------- money in and out ----------

class EntryIn(BaseModel):
    kind: str = Field(pattern="^(invested|withdrawn|payout)$")
    amount: int = Field(gt=0)
    on_date: date
    supplier_id: int | None = None
    note: str | None = None


@router.post("/investors/{investor_id}/entries")
def add_entry(investor_id: int, body: EntryIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "investors", investor_id)
        if body.supplier_id:
            get_or_404(conn, "suppliers", body.supplier_id)
        return insert_row(conn, "investor_entries", {**body.model_dump(), "investor_id": investor_id, "created_by": user["id"]})


@router.delete("/investor-entries/{entry_id}")
def delete_entry(entry_id: int, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "investor_entries", entry_id)
        conn.execute("DELETE FROM investor_entries WHERE id = %s", (entry_id,))
    return {"ok": True}
