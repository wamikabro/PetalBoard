"""Suppliers, flowers, advances and the shop details printed on bills."""

import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import db
from billing import advance_balance, as_num, bill_summaries
from security import current_user, require
from util import get_or_404, insert_row, update_row

router = APIRouter(tags=["suppliers"])


# ---------- shop settings ----------

class SettingsChange(BaseModel):
    name_ur: str | None = None
    name_en: str | None = None
    tagline_ur: str | None = None
    address_ur: str | None = None
    proprietor_label_ur: str | None = None
    proprietor1_name_ur: str | None = None
    proprietor1_phone: str | None = None
    proprietor2_name_ur: str | None = None
    proprietor2_phone: str | None = None
    default_commission_pct: float | None = Field(default=None, ge=0, le=100)


@router.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    with db.transaction() as conn:
        s = conn.execute("SELECT * FROM shop_settings WHERE id = 1").fetchone()
    s["default_commission_pct"] = as_num(s["default_commission_pct"])
    return s


@router.patch("/settings")
def change_settings(body: SettingsChange, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        row = update_row(conn, "shop_settings", 1, body.model_dump(exclude_none=True), set(SettingsChange.model_fields))
    row["default_commission_pct"] = as_num(row["default_commission_pct"])
    return row


# ---------- flowers ----------

class FlowerIn(BaseModel):
    name: str = Field(min_length=1)
    name_ur: str | None = None
    active: bool | None = None


@router.get("/flowers")
def list_flowers(user: dict = Depends(current_user)):
    with db.transaction() as conn:
        return conn.execute("SELECT * FROM flowers ORDER BY active DESC, name").fetchall()


@router.post("/flowers")
def create_flower(body: FlowerIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        if conn.execute("SELECT 1 FROM flowers WHERE lower(name) = lower(%s)", (body.name,)).fetchone():
            raise HTTPException(400, "That flower already exists")
        return insert_row(conn, "flowers", {"name": body.name.strip(), "name_ur": body.name_ur, "active": True})


@router.patch("/flowers/{flower_id}")
def change_flower(flower_id: int, body: FlowerIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        return update_row(conn, "flowers", flower_id, body.model_dump(exclude_none=True), {"name", "name_ur", "active"})


# ---------- suppliers ----------

class SupplierIn(BaseModel):
    name: str = Field(min_length=1)
    name_ur: str | None = None
    phone: str | None = None
    place: str | None = None
    flower_id: int | None = None
    commission_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str | None = None
    active: bool | None = None


class SupplierChange(SupplierIn):
    name: str | None = Field(default=None, min_length=1)


SUPPLIER_FIELDS = {"name", "name_ur", "phone", "place", "flower_id", "commission_pct", "notes", "active"}


def _position(conn, supplier_id: int) -> dict:
    """Where the supplier stands now: the latest bill's balance and the advance."""
    latest = bill_summaries(conn, "b.supplier_id = %s", (supplier_id,))
    last = latest[0] if latest else None
    if last is None:
        balance = 0
    elif last["status"] == "closed":
        balance = last["carried"] or 0
    else:
        balance = last["final"]
    return {
        "balance": balance,           # > 0: shop owes supplier, < 0: supplier owes shop
        "advance": advance_balance(conn, supplier_id),
        "last_bill": last,
    }


@router.get("/suppliers")
def list_suppliers(include_inactive: bool = False, user: dict = Depends(current_user)):
    with db.transaction() as conn:
        rows = conn.execute(
            f"""
            SELECT s.*, f.name AS flower_name,
                   (SELECT count(*) FROM bills b WHERE b.supplier_id = s.id) AS bill_count
            FROM suppliers s LEFT JOIN flowers f ON f.id = s.flower_id
            {"" if include_inactive else "WHERE s.active"}
            ORDER BY s.active DESC, s.name
            """
        ).fetchall()
        summaries = bill_summaries(conn)
        adv = {r["supplier_id"]: int(r["b"]) for r in conn.execute(
            "SELECT supplier_id, sum(amount) AS b FROM advances GROUP BY supplier_id").fetchall()}
    latest: dict[int, dict] = {}
    for b in summaries:  # newest month first
        latest.setdefault(b["supplier_id"], b)
    for r in rows:
        r["commission_pct"] = as_num(r["commission_pct"])
        last = latest.get(r["id"])
        r["last_bill"] = last
        if last is None:
            r["balance"] = 0
        elif last["status"] == "closed":
            r["balance"] = last["carried"] or 0
        else:
            r["balance"] = last["final"]
        r["advance"] = adv.get(r["id"], 0)
    return rows


@router.post("/suppliers")
def create_supplier(body: SupplierIn, user: dict = Depends(require("clerk"))):
    with db.transaction(user["id"]) as conn:
        if conn.execute("SELECT 1 FROM suppliers WHERE lower(name) = lower(%s)", (body.name.strip(),)).fetchone():
            raise HTTPException(400, "A supplier with that name already exists")
        fields = body.model_dump(exclude_none=True)
        fields["name"] = fields["name"].strip()
        if "commission_pct" not in fields:
            fields["commission_pct"] = conn.execute(
                "SELECT default_commission_pct FROM shop_settings").fetchone()["default_commission_pct"]
        row = insert_row(conn, "suppliers", fields)
    row["commission_pct"] = as_num(row["commission_pct"])
    return row


@router.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: int, user: dict = Depends(current_user)):
    """Everything for the supplier page: details, every bill, month-by-month and day-by-day history."""
    with db.transaction() as conn:
        s = conn.execute(
            "SELECT s.*, f.name AS flower_name FROM suppliers s LEFT JOIN flowers f ON f.id = s.flower_id WHERE s.id = %s",
            (supplier_id,),
        ).fetchone()
        if not s:
            raise HTTPException(404, "Supplier not found")
        s["commission_pct"] = as_num(s["commission_pct"])
        bills = bill_summaries(conn, "b.supplier_id = %s", (supplier_id,))
        days = conn.execute(
            """
            SELECT (b.month + (d.day - 1) * interval '1 day')::date AS on_date, d.weight, d.rate, d.amount
            FROM bill_days d JOIN bills b ON b.id = d.bill_id
            WHERE b.supplier_id = %s AND d.weight IS NOT NULL
            ORDER BY on_date
            """,
            (supplier_id,),
        ).fetchall()
        kharcha = conn.execute(
            """
            SELECT (b.month + (k.day - 1) * interval '1 day')::date AS on_date, k.amount, k.tag, k.note, b.id AS bill_id
            FROM kharcha k JOIN bills b ON b.id = k.bill_id
            WHERE b.supplier_id = %s ORDER BY on_date DESC, k.id DESC
            """,
            (supplier_id,),
        ).fetchall()
        advances = conn.execute(
            """SELECT a.id, a.on_date, a.amount, a.kind, a.bill_id, a.note, to_char(b.month, 'YYYY-MM') AS bill_month,
                      u.display_name AS by_name
               FROM advances a LEFT JOIN bills b ON b.id = a.bill_id LEFT JOIN users u ON u.id = a.created_by
               WHERE a.supplier_id = %s ORDER BY a.on_date, a.id""",
            (supplier_id,),
        ).fetchall()
        khaata = khaata_info(conn, supplier_id)
        position = _position(conn, supplier_id)
        shares = conn.execute(
            """SELECT ks.id, ks.investor_id, i.name AS investor_name, ks.pct, ks.from_month, ks.to_month, ks.note
               FROM khaata_shares ks JOIN investors i ON i.id = ks.investor_id
               WHERE ks.supplier_id = %s ORDER BY ks.from_month""", (supplier_id,)).fetchall()
        from routes_investors import earnings
        inv_rows = earnings(conn, supplier_id=supplier_id)
        # The market rate the same days, to compare this supplier's rate with everyone's.
        market = conn.execute(
            """
            SELECT (b.month + (d.day - 1) * interval '1 day')::date AS on_date,
                   sum(d.amount)::float / nullif(sum(d.weight), 0) AS avg_rate
            FROM bill_days d JOIN bills b ON b.id = d.bill_id
            WHERE d.weight > 0 AND b.flower_id IS NOT DISTINCT FROM %s::int
              AND b.month IN (SELECT month FROM bills WHERE supplier_id = %s)
            GROUP BY 1 ORDER BY 1
            """,
            (s["flower_id"], supplier_id),
        ).fetchall()

    running = 0
    for a in advances:
        running += int(a["amount"])
        a["running"] = running
    return {
        "supplier": s,
        "position": position,
        "bills": bills,
        "days": [{"date": d["on_date"].isoformat(), "weight": as_num(d["weight"]), "rate": as_num(d["rate"]),
                  "amount": int(d["amount"])} for d in days],
        "market": [{"date": m["on_date"].isoformat(), "avg_rate": round(m["avg_rate"], 1) if m["avg_rate"] else None}
                   for m in market],
        "kharcha": [{"date": k["on_date"].isoformat(), "amount": int(k["amount"]), "tag": k["tag"], "note": k["note"],
                     "bill_id": k["bill_id"]} for k in kharcha],
        "advances": advances,
        "khaata": khaata,
        "shares": [{**x, "pct": as_num(x["pct"]), "from_month": x["from_month"].isoformat()[:7],
                    "to_month": x["to_month"].isoformat()[:7] if x["to_month"] else None} for x in shares],
        "investor_earnings": inv_rows,
    }


@router.patch("/suppliers/{supplier_id}")
def change_supplier(supplier_id: int, body: SupplierChange, user: dict = Depends(require("clerk"))):
    with db.transaction(user["id"]) as conn:
        fields = body.model_dump(exclude_none=True)
        if "name" in fields:
            fields["name"] = fields["name"].strip()
            if conn.execute("SELECT 1 FROM suppliers WHERE lower(name) = lower(%s) AND id <> %s",
                            (fields["name"], supplier_id)).fetchone():
                raise HTTPException(400, "A supplier with that name already exists")
        row = update_row(conn, "suppliers", supplier_id, fields, SUPPLIER_FIELDS)
    row["commission_pct"] = as_num(row["commission_pct"])
    return row


# ---------- advances ----------

class AdvanceIn(BaseModel):
    on_date: date
    amount: int = Field(gt=0, description="Always positive; kind says which way it goes")
    kind: str = Field(pattern="^(given|recovered|opening)$")
    note: str | None = None


@router.post("/suppliers/{supplier_id}/advances")
def add_advance(supplier_id: int, body: AdvanceIn, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "suppliers", supplier_id)
        signed = body.amount if body.kind in ("given", "opening") else -body.amount
        row = insert_row(conn, "advances", {"supplier_id": supplier_id, "on_date": body.on_date, "amount": signed,
                                            "kind": body.kind, "note": body.note, "created_by": user["id"]})
        row["balance"] = advance_balance(conn, supplier_id)
    return row


@router.delete("/advances/{advance_id}")
def delete_advance(advance_id: int, user: dict = Depends(require("owner"))):
    with db.transaction(user["id"]) as conn:
        row = get_or_404(conn, "advances", advance_id)
        if row["kind"] == "bill_cut":
            raise HTTPException(400, "This was cut from a bill. Reopen that bill instead.")
        if row["kind"] == "written_off":
            raise HTTPException(400, "This was written off when the khaata was closed. Reopen the khaata instead.")
        conn.execute("DELETE FROM advances WHERE id = %s", (advance_id,))
    return {"ok": True}


# ---------- khaata: close, renew, reopen ----------
# A khaata is never closed on its own (some suppliers never ask for what is left); the editor decides.

def _first_date(conn, supplier_id: int) -> date | None:
    return conn.execute(
        """SELECT least((SELECT min(month) FROM bills WHERE supplier_id = %s),
                        (SELECT min(on_date) FROM advances WHERE supplier_id = %s)) AS d""",
        (supplier_id, supplier_id)).fetchone()["d"]


def khaata_info(conn, supplier_id: int) -> dict:
    """The khaata now (open unless the latest one was closed) and every earlier one."""
    rows = conn.execute(
        """SELECT k.*, uc.display_name AS closed_by_name, uo.display_name AS created_by_name
           FROM khaatas k LEFT JOIN users uc ON uc.id = k.closed_by LEFT JOIN users uo ON uo.id = k.created_by
           WHERE k.supplier_id = %s ORDER BY k.opened_on, k.id""", (supplier_id,)).fetchall()
    history = [{
        "id": r["id"], "opened_on": r["opened_on"].isoformat(), "terms": r["terms"],
        "commission_pct": as_num(r["commission_pct"]) if r["commission_pct"] is not None else None,
        "closed_on": r["closed_on"].isoformat() if r["closed_on"] else None, "close_reason": r["close_reason"],
        "written_off": r["written_off"], "closed_by": r["closed_by_name"], "opened_by": r["created_by_name"],
    } for r in rows]
    if not history:
        first = _first_date(conn, supplier_id)
        history = [{"id": None, "opened_on": first.isoformat() if first else None, "terms": None, "commission_pct": None,
                    "closed_on": None, "close_reason": None, "written_off": None, "closed_by": None, "opened_by": None}]
    current = history[-1]
    return {"status": "closed" if current["closed_on"] else "open", "current": current, "history": history,
            "outstanding": advance_balance(conn, supplier_id)}


class KhaataClose(BaseModel):
    on_date: date
    reason: str | None = None
    write_off: bool = Field(default=False, description="Must be true when something is still outstanding")


@router.post("/suppliers/{supplier_id}/khaata/close")
def close_khaata(supplier_id: int, body: KhaataClose, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "suppliers", supplier_id)
        info = khaata_info(conn, supplier_id)
        if info["status"] == "closed":
            raise HTTPException(400, "This khaata is already closed. Renew it to start a new one.")
        cur = info["current"]
        if cur["opened_on"] and body.on_date.isoformat() < cur["opened_on"]:
            raise HTTPException(400, f"The khaata was opened on {cur['opened_on']}; it cannot close before that.")
        left = info["outstanding"]
        if left and not body.write_off:
            raise HTTPException(400, f"Rs {left:,} is still outstanding. Choose to write it off to close the khaata, "
                                     "or recover it first.")
        if left:
            insert_row(conn, "advances", {
                "supplier_id": supplier_id, "on_date": body.on_date, "amount": -left, "kind": "written_off",
                "note": "Written off when the khaata was closed" + (f": {body.reason}" if body.reason else ""),
                "created_by": user["id"]})
        # Investor shares of this khaata end with the closing month (kept so a reopen can put them back).
        month = body.on_date.replace(day=1)
        ended = conn.execute(
            """SELECT id, to_month FROM khaata_shares
               WHERE supplier_id = %s AND (to_month IS NULL OR to_month > %s) AND from_month <= %s""",
            (supplier_id, month, month)).fetchall()
        if ended:
            conn.execute("UPDATE khaata_shares SET to_month = %s WHERE id = ANY(%s)", (month, [r["id"] for r in ended]))
        closing = {"closed_on": body.on_date, "close_reason": body.reason, "written_off": left or None,
                   "closed_by": user["id"],
                   "ended_shares": json.dumps([{"id": r["id"], "to_month": r["to_month"].isoformat() if r["to_month"] else None}
                                               for r in ended])}
        if cur["id"]:
            sets = ", ".join(f"{k} = %s" for k in closing)
            conn.execute(f"UPDATE khaatas SET {sets} WHERE id = %s", (*closing.values(), cur["id"]))
        else:
            opened = date.fromisoformat(cur["opened_on"]) if cur["opened_on"] else body.on_date
            insert_row(conn, "khaatas", {"supplier_id": supplier_id, "opened_on": min(opened, body.on_date), **closing})
        return khaata_info(conn, supplier_id)


class KhaataRenew(BaseModel):
    on_date: date
    terms: str | None = None
    commission_pct: float | None = Field(default=None, ge=0, le=100)
    advance: int | None = Field(default=None, gt=0, description="New advance given with the renewed khaata")


@router.post("/suppliers/{supplier_id}/khaata/renew")
def renew_khaata(supplier_id: int, body: KhaataRenew, user: dict = Depends(require("manager"))):
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "suppliers", supplier_id)
        info = khaata_info(conn, supplier_id)
        if info["status"] == "open":
            raise HTTPException(400, "The khaata is still open. Close it before starting a new one.")
        if body.on_date.isoformat() < info["current"]["closed_on"]:
            raise HTTPException(400, f"The last khaata closed on {info['current']['closed_on']}; "
                                     "the new one cannot start before that.")
        insert_row(conn, "khaatas", {"supplier_id": supplier_id, "opened_on": body.on_date, "terms": body.terms,
                                     "commission_pct": body.commission_pct, "created_by": user["id"]})
        if body.commission_pct is not None:
            conn.execute("UPDATE suppliers SET commission_pct = %s WHERE id = %s", (body.commission_pct, supplier_id))
        if body.advance:
            insert_row(conn, "advances", {"supplier_id": supplier_id, "on_date": body.on_date, "amount": body.advance,
                                          "kind": "given", "note": "Advance with the renewed khaata",
                                          "created_by": user["id"]})
        return khaata_info(conn, supplier_id)


@router.post("/suppliers/{supplier_id}/khaata/reopen")
def reopen_khaata(supplier_id: int, user: dict = Depends(require("owner"))):
    """Undo a closing done by mistake: removes the write-off and puts the investor shares back."""
    with db.transaction(user["id"]) as conn:
        get_or_404(conn, "suppliers", supplier_id)
        info = khaata_info(conn, supplier_id)
        if info["status"] == "open":
            raise HTTPException(400, "The khaata is already open.")
        k = conn.execute("SELECT * FROM khaatas WHERE id = %s", (info["current"]["id"],)).fetchone()
        conn.execute("DELETE FROM advances WHERE supplier_id = %s AND kind = 'written_off' AND on_date = %s",
                     (supplier_id, k["closed_on"]))
        for sh in k["ended_shares"] or []:
            conn.execute("UPDATE khaata_shares SET to_month = %s WHERE id = %s", (sh["to_month"], sh["id"]))
        conn.execute("""UPDATE khaatas SET closed_on = NULL, close_reason = NULL, written_off = NULL, closed_by = NULL,
                        ended_shares = NULL WHERE id = %s""", (k["id"],))
        return khaata_info(conn, supplier_id)
