"""Monthly bills, their days and kharcha, closing a month, and the daily entry screen."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from billing import as_num, bill_summaries, day_amount, days_in, load_bill, month_start
from security import current_user, require
from util import get_or_404, insert_row

router = APIRouter(tags=["bills"])


def _open_bill(conn, bill_id: int) -> dict:
    bill = get_or_404(conn, "bills", bill_id)
    if bill["status"] != "open":
        raise HTTPException(400, "This bill is closed. Reopen it to make changes.")
    return bill


def _check_day(bill: dict, day: int) -> None:
    if not 1 <= day <= days_in(bill["month"]):
        raise HTTPException(400, f"Day {day} is not in this month")


def _opening_from_previous(conn, supplier_id: int, month: date) -> int:
    """What the supplier still owed from the latest earlier bill, if it is closed."""
    prev = conn.execute(
        "SELECT status, carried FROM bills WHERE supplier_id = %s AND month < %s ORDER BY month DESC LIMIT 1",
        (supplier_id, month),
    ).fetchone()
    if prev and prev["status"] == "closed" and prev["carried"]:
        return -int(prev["carried"])
    return 0


def ensure_bill(conn, supplier_id: int, month: date) -> dict:
    """The supplier's bill for the month, created if missing (opening balance from last month)."""
    bill = conn.execute("SELECT * FROM bills WHERE supplier_id = %s AND month = %s", (supplier_id, month)).fetchone()
    if bill:
        return bill
    s = get_or_404(conn, "suppliers", supplier_id)
    return insert_row(conn, "bills", {
        "supplier_id": supplier_id, "month": month, "flower_id": s["flower_id"],
        "commission_pct": s["commission_pct"], "opening_due": _opening_from_previous(conn, supplier_id, month),
    })


def set_day(conn, bill: dict, day: int, weight, rate, amount, note, user_id: int, made_at: datetime | None = None) -> None:
    """Write one day's weight and rate. Empty weight, rate and amount removes the day."""
    if weight is None and rate is None and amount is None and not note:
        conn.execute("DELETE FROM bill_days WHERE bill_id = %s AND day = %s", (bill["id"], day))
        return
    if amount is None:
        amount = day_amount(weight, rate)
    conn.execute(
        """
        INSERT INTO bill_days (bill_id, day, weight, rate, amount, note, updated_by, made_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, LEAST(COALESCE(%s::timestamptz, now()), now()))
        ON CONFLICT (bill_id, day) DO UPDATE SET weight = EXCLUDED.weight, rate = EXCLUDED.rate,
            amount = EXCLUDED.amount, note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = now(),
            made_at = EXCLUDED.made_at
        """,
        (bill["id"], day, weight, rate, amount, note, user_id, made_at),
    )


# ---------- bills ----------

@router.get("/bills")
def list_bills(month: str | None = None, supplier_id: int | None = None, user: dict = Depends(current_user)):
    where, args = ["TRUE"], []
    if month:
        where.append("b.month = %s")
        args.append(month_start(month))
    if supplier_id:
        where.append("b.supplier_id = %s")
        args.append(supplier_id)
    with db.transaction() as conn:
        return bill_summaries(conn, " AND ".join(where), tuple(args))


@router.get("/bills/months")
def bill_months(user: dict = Depends(current_user)):
    with db.transaction() as conn:
        rows = conn.execute("SELECT DISTINCT month FROM bills ORDER BY month DESC").fetchall()
    return [r["month"].isoformat()[:7] for r in rows]


class NewBill(BaseModel):
    supplier_id: int
    month: str


@router.post("/bills")
def create_bill(body: NewBill, user: dict = Depends(require("clerk"))):
    month = month_start(body.month)
    with db.transaction(user["id"]) as conn:
        bill = ensure_bill(conn, body.supplier_id, month)
        return load_bill(conn, bill["id"])


@router.post("/bills/start-month")
def start_month(body: dict, user: dict = Depends(require("manager"))):
    """Create this month's bill for every active supplier who does not have one yet."""
    month = month_start(body.get("month", ""))
    with db.transaction(user["id"]) as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM suppliers WHERE active AND id NOT IN (SELECT supplier_id FROM bills WHERE month = %s)",
            (month,)).fetchall()]
        made = [{"supplier_id": sid, "id": ensure_bill(conn, sid, month)["id"]} for sid in ids]
    return {"created": len(ids), "bills": made}


@router.get("/bills/{bill_id}")
def get_bill(bill_id: int, user: dict = Depends(current_user)):
    with db.transaction() as conn:
        return load_bill(conn, bill_id)


@router.get("/bills/{bill_id}/neighbours")
def bill_neighbours(bill_id: int, user: dict = Depends(current_user)):
    """Previous and next bill (by supplier name) in the same month, for paging through bills."""
    with db.transaction() as conn:
        b = get_or_404(conn, "bills", bill_id)
        rows = conn.execute(
            "SELECT b.id FROM bills b JOIN suppliers s ON s.id = b.supplier_id WHERE b.month = %s ORDER BY s.name",
            (b["month"],)).fetchall()
    ids = [r["id"] for r in rows]
    i = ids.index(bill_id)
    return {"prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None,
            "position": i + 1, "count": len(ids)}


class BillChange(BaseModel):
    commission_pct: float | None = Field(default=None, ge=0, le=100)
    opening_due: int | None = None
    flower_id: int | None = None
    note: str | None = None


@router.patch("/bills/{bill_id}")
def change_bill(bill_id: int, body: BillChange, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        _open_bill(conn, bill_id)
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            raise HTTPException(400, "Nothing to change")
        sets = ", ".join(f"{k} = %s" for k in fields)
        conn.execute(f"UPDATE bills SET {sets} WHERE id = %s", (*fields.values(), bill_id))
        return load_bill(conn, bill_id)


@router.delete("/bills/{bill_id}")
def delete_bill(bill_id: int, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        _open_bill(conn, bill_id)
        conn.execute("DELETE FROM bills WHERE id = %s", (bill_id,))
    return {"ok": True}


class DayIn(BaseModel):
    weight: float | None = Field(default=None, ge=0)
    rate: float | None = Field(default=None, ge=0)
    amount: int | None = Field(default=None, description="Only to override weight x rate")
    note: str | None = None


@router.put("/bills/{bill_id}/days/{day}")
def put_day(bill_id: int, day: int, body: DayIn, user: dict = Depends(require("clerk"))):
    with db.transaction(user["id"]) as conn:
        bill = _open_bill(conn, bill_id)
        _check_day(bill, day)
        set_day(conn, bill, day, body.weight, body.rate, body.amount, body.note, user["id"])
        return load_bill(conn, bill_id)


class KharchaIn(BaseModel):
    day: int
    amount: int = Field(description="Negative only to correct an earlier entry")
    tag: str | None = Field(default=None, max_length=8)
    note: str | None = None
    client_id: str | None = Field(default=None, max_length=64, description="Made by the device; the same id is saved once")


def kharcha_exists(conn, client_id: str | None) -> bool:
    return bool(client_id) and conn.execute("SELECT 1 FROM kharcha WHERE client_id = %s", (client_id,)).fetchone() is not None


@router.post("/bills/{bill_id}/kharcha")
def add_kharcha(bill_id: int, body: KharchaIn, user: dict = Depends(require("clerk"))):
    if body.amount == 0:
        raise HTTPException(400, "Amount cannot be zero")
    with db.transaction(user["id"]) as conn:
        bill = _open_bill(conn, bill_id)
        _check_day(bill, body.day)
        if not kharcha_exists(conn, body.client_id):
            insert_row(conn, "kharcha", {"bill_id": bill_id, "day": body.day, "amount": body.amount, "tag": body.tag,
                                         "note": body.note, "created_by": user["id"], "client_id": body.client_id})
        return load_bill(conn, bill_id)


@router.delete("/kharcha/{kharcha_id}")
def delete_kharcha(kharcha_id: int, user: dict = Depends(require("clerk"))):
    with db.transaction(user["id"]) as conn:
        k = get_or_404(conn, "kharcha", kharcha_id)
        _open_bill(conn, k["bill_id"])
        conn.execute("DELETE FROM kharcha WHERE id = %s", (kharcha_id,))
        return load_bill(conn, k["bill_id"])


@router.delete("/kharcha/by-client/{client_id}")
def delete_kharcha_by_client(client_id: str, user: dict = Depends(require("clerk"))):
    """Remove a kharcha by the id the device gave it (it may not know the server's id yet)."""
    with db.transaction(user["id"]) as conn:
        k = conn.execute("SELECT * FROM kharcha WHERE client_id = %s", (client_id,)).fetchone()
        if not k:
            raise HTTPException(404, "Kharcha not found")
        _open_bill(conn, k["bill_id"])
        conn.execute("DELETE FROM kharcha WHERE id = %s", (k["id"],))
        return load_bill(conn, k["bill_id"])


# ---------- closing the month ----------

class CloseIn(BaseModel):
    paid_cash: int = Field(default=0, description="> 0 shop paid the supplier, < 0 supplier paid the shop")
    advance_cut: int = Field(default=0, description="> 0 taken from the bill to repay the advance, "
                                                     "< 0 the supplier's debt moved into the advance")
    note: str | None = None


def advance_cut_limit(outstanding: int, final: int) -> int:
    """The most that can be cut from the advance when closing a bill: no more than is outstanding,
    and no more than the bill leaves for the supplier."""
    return max(0, min(outstanding, final))


def check_advance_cut(cut: int, outstanding: int, final: int) -> None:
    if cut <= advance_cut_limit(outstanding, final):
        return
    if outstanding <= 0:
        raise HTTPException(400, "Nothing can be cut: this supplier has no advance outstanding.")
    if final <= 0:
        raise HTTPException(400, f"Nothing can be cut: this bill leaves nothing for the supplier (final Rs {final:,}).")
    if cut > outstanding:
        raise HTTPException(400, f"Only Rs {outstanding:,} is outstanding, so no more than that can be cut.")
    raise HTTPException(400, f"This bill leaves Rs {final:,} for the supplier, so no more than that can be cut.")


def _sync_next_opening(conn, bill: dict, carried: int | None) -> None:
    nxt = conn.execute(
        "SELECT id, status FROM bills WHERE supplier_id = %s AND month > %s ORDER BY month LIMIT 1",
        (bill["supplier_id"], bill["month"]),
    ).fetchone()
    if nxt and nxt["status"] == "open":
        conn.execute("UPDATE bills SET opening_due = %s WHERE id = %s", (-(carried or 0), nxt["id"]))


@router.post("/bills/{bill_id}/close")
def close_bill(bill_id: int, body: CloseIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        bill = _open_bill(conn, bill_id)
        loaded = load_bill(conn, bill_id)
        final = loaded["totals"]["final"]
        if body.advance_cut > 0:
            check_advance_cut(body.advance_cut, loaded["advance_balance"], final)
        carried = final - body.paid_cash - body.advance_cut
        conn.execute(
            """UPDATE bills SET status = 'closed', paid_cash = %s, advance_cut = %s, carried = %s,
               closed_at = now(), closed_by = %s, note = coalesce(%s, note) WHERE id = %s""",
            (body.paid_cash, body.advance_cut, carried, user["id"], body.note, bill_id),
        )
        if body.advance_cut:
            last_day = date(bill["month"].year, bill["month"].month, days_in(bill["month"]))
            insert_row(conn, "advances", {
                "supplier_id": bill["supplier_id"], "on_date": last_day, "amount": -body.advance_cut,
                "kind": "bill_cut", "bill_id": bill_id, "created_by": user["id"],
                "note": f"From the {bill['month']:%B %Y} bill",
            })
        _sync_next_opening(conn, bill, carried)
        return load_bill(conn, bill_id)


@router.post("/bills/{bill_id}/reopen")
def reopen_bill(bill_id: int, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        bill = get_or_404(conn, "bills", bill_id)
        if bill["status"] != "closed":
            raise HTTPException(400, "This bill is already open")
        later_closed = conn.execute(
            "SELECT 1 FROM bills WHERE supplier_id = %s AND month > %s AND status = 'closed'",
            (bill["supplier_id"], bill["month"])).fetchone()
        if later_closed:
            raise HTTPException(400, "A later month is already closed for this supplier. Reopen that first.")
        conn.execute("DELETE FROM advances WHERE bill_id = %s AND kind = 'bill_cut'", (bill_id,))
        conn.execute("""UPDATE bills SET status = 'open', paid_cash = NULL, advance_cut = NULL, carried = NULL,
                        closed_at = NULL, closed_by = NULL WHERE id = %s""", (bill_id,))
        _sync_next_opening(conn, bill, 0)
        return load_bill(conn, bill_id)


# ---------- daily entry: one day, every supplier ----------

@router.get("/daily/{on_date}")
def get_daily(on_date: date, user: dict = Depends(current_user)):
    month = on_date.replace(day=1)
    with db.transaction() as conn:
        suppliers = conn.execute(
            """
            SELECT s.id, s.name, s.flower_id, f.name AS flower_name, s.active
            FROM suppliers s LEFT JOIN flowers f ON f.id = s.flower_id
            WHERE s.active OR s.id IN (SELECT supplier_id FROM bills WHERE month = %s)
            ORDER BY s.name
            """, (month,)).fetchall()
        bills = {r["supplier_id"]: r for r in conn.execute(
            "SELECT id, supplier_id, status, flower_id FROM bills WHERE month = %s", (month,)).fetchall()}
        days = {r["bill_id"]: r for r in conn.execute(
            "SELECT d.* FROM bill_days d JOIN bills b ON b.id = d.bill_id WHERE b.month = %s AND d.day = %s",
            (month, on_date.day)).fetchall()}
        kh: dict[int, list] = {}
        for r in conn.execute(
                "SELECT k.* FROM kharcha k JOIN bills b ON b.id = k.bill_id WHERE b.month = %s AND k.day = %s ORDER BY k.id",
                (month, on_date.day)).fetchall():
            kh.setdefault(r["bill_id"], []).append({"id": r["id"], "client_id": r["client_id"], "amount": int(r["amount"]),
                                                   "tag": r["tag"], "note": r["note"]})
        # Yesterday's rate per supplier, to fill in quickly.
        prev = {r["supplier_id"]: as_num(r["rate"]) for r in conn.execute(
            """
            SELECT DISTINCT ON (b.supplier_id) b.supplier_id, d.rate
            FROM bill_days d JOIN bills b ON b.id = d.bill_id
            WHERE d.rate IS NOT NULL AND (b.month + (d.day - 1) * interval '1 day')::date < %s
            ORDER BY b.supplier_id, b.month DESC, d.day DESC
            """, (on_date,)).fetchall()}
    rows = []
    for s in suppliers:
        b = bills.get(s["id"])
        d = days.get(b["id"]) if b else None
        rows.append({
            "supplier_id": s["id"], "supplier_name": s["name"], "flower_name": s["flower_name"],
            "bill_id": b["id"] if b else None, "bill_status": b["status"] if b else None,
            "weight": as_num(d["weight"]) if d else None, "rate": as_num(d["rate"]) if d else None,
            "amount": int(d["amount"]) if d else None, "note": d["note"] if d else None,
            "kharcha": kh.get(b["id"], []) if b else [], "last_rate": prev.get(s["id"]),
        })
    return {"date": on_date.isoformat(), "rows": rows}


class DayValues(BaseModel):
    weight: float | None = None
    rate: float | None = None


class DailyRow(BaseModel):
    """One supplier's day. Leave out weight, rate and note (the keys) to only add a kharcha."""
    supplier_id: int
    weight: float | None = Field(default=None, ge=0)
    rate: float | None = Field(default=None, ge=0)
    note: str | None = None
    add_kharcha: int | None = None
    kharcha_tag: str | None = Field(default=None, max_length=8)
    kharcha_note: str | None = None
    kharcha_client_id: str | None = Field(default=None, max_length=64)
    # From a phone's outbox: when the change was made, and the weight/rate the phone showed before it.
    made_at: datetime | None = None
    base: DayValues | None = None


def _same(a, b, places: int) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return round(float(a), places) == round(float(b), places)


def _same_day(cur, weight, rate) -> bool:
    return _same(cur["weight"] if cur else None, weight, 3) and _same(cur["rate"] if cur else None, rate, 2)


class DailyIn(BaseModel):
    rows: list[DailyRow]


@router.put("/daily/{on_date}")
def put_daily(on_date: date, body: DailyIn, user: dict = Depends(require("clerk"))):
    month = on_date.replace(day=1)
    saved, skipped, conflicts, bill_ids = 0, [], [], {}
    with db.transaction(user["id"]) as conn:
        for r in body.rows:
            existing = conn.execute("SELECT * FROM bills WHERE supplier_id = %s AND month = %s",
                                    (r.supplier_id, month)).fetchone()
            nothing = r.weight is None and r.rate is None and not r.add_kharcha
            if existing is None and nothing:
                continue
            bill = existing or ensure_bill(conn, r.supplier_id, month)
            bill_ids[str(r.supplier_id)] = bill["id"]
            if bill["status"] != "open":
                skipped.append(r.supplier_id)
                continue
            if r.model_fields_set & {"weight", "rate", "note"}:
                cur = conn.execute(
                    """SELECT d.weight, d.rate, d.note, COALESCE(d.made_at, d.updated_at) AS made_at, u.display_name AS by_name
                       FROM bill_days d LEFT JOIN users u ON u.id = d.updated_by WHERE d.bill_id = %s AND d.day = %s""",
                    (bill["id"], on_date.day)).fetchone()
                theirs = {"weight": as_num(cur["weight"]), "rate": as_num(cur["rate"]),
                          "by": cur["by_name"], "at": cur["made_at"].isoformat()} if cur else None
                change = None
                if not _same_day(cur, r.weight, r.rate):
                    if cur and r.made_at and cur["made_at"] > r.made_at:
                        change = "kept_newer"     # changed on the server after this change was made: the later one wins
                    elif r.base is not None and not _same_day(cur, r.base.weight, r.base.rate):
                        change = "replaced"       # changed by someone else in between, but earlier than this change
                if change:
                    conflicts.append({"supplier_id": r.supplier_id, "date": on_date.isoformat(), "what": change,
                                      "yours": {"weight": r.weight, "rate": r.rate}, "theirs": theirs})
                if change != "kept_newer":
                    set_day(conn, bill, on_date.day, r.weight, r.rate, None, r.note, user["id"], r.made_at)
            if r.add_kharcha and not kharcha_exists(conn, r.kharcha_client_id):
                insert_row(conn, "kharcha", {"bill_id": bill["id"], "day": on_date.day, "amount": r.add_kharcha,
                                             "tag": r.kharcha_tag, "note": r.kharcha_note, "created_by": user["id"],
                                             "client_id": r.kharcha_client_id})
            saved += 1
    return {"saved": saved, "skipped_closed": skipped, "conflicts": conflicts, "bill_ids": bill_ids}
