"""The main flows through the API: suppliers, daily entry, bills, closing a month, reports."""

import pytest


@pytest.fixture(scope="module")
def flower(client):
    return client.post("/flowers", json={"name": "Gulab", "name_ur": "گلاب"}).json()


@pytest.fixture(scope="module")
def supplier(client, flower):
    r = client.post("/suppliers", json={"name": "Test Ayaz", "flower_id": flower["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_login_required(client):
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as anon:
        assert anon.get("/suppliers").status_code == 401
        assert anon.post("/auth/login", json={"username": "owner", "password": "nope"}).status_code == 401


def test_new_supplier_gets_default_commission(supplier):
    assert supplier["commission_pct"] == 10


def test_duplicate_supplier_refused(client, supplier):
    assert client.post("/suppliers", json={"name": "test ayaz"}).status_code == 400


def test_bill_days_and_kharcha(client, supplier):
    bill = client.post("/bills", json={"supplier_id": supplier["id"], "month": "2026-11"}).json()
    assert bill["month"] == "2026-11"
    assert len(bill["days"]) == 30          # November has 30 days
    bid = bill["id"]

    client.put(f"/bills/{bid}/days/1", json={"weight": 3, "rate": 150})
    client.put(f"/bills/{bid}/days/2", json={"weight": 3.5, "rate": 150})
    b = client.put(f"/bills/{bid}/days/27", json={"weight": 3.5, "rate": 90, "amount": 319}).json()
    assert b["days"][0]["amount"] == 450
    assert b["days"][1]["amount"] == 525
    assert b["days"][26]["mismatch"] == 315   # written 319, weight x rate is 315
    assert any("Day 27" in w for w in b["warnings"])

    client.post(f"/bills/{bid}/kharcha", json={"day": 1, "amount": 100})
    b = client.post(f"/bills/{bid}/kharcha", json={"day": 1, "amount": 250, "tag": "B"}).json()
    assert b["days"][0]["kharcha_total"] == 350
    t = b["totals"]
    assert t["total"] == 450 + 525 + 319
    assert t["commission"] == 129
    assert t["final"] == t["total"] - 129 - 350

    # Clearing a day removes it.
    b = client.put(f"/bills/{bid}/days/27", json={}).json()
    assert b["days"][26]["amount"] is None


def test_day_outside_month_refused(client, supplier):
    bid = client.post("/bills", json={"supplier_id": supplier["id"], "month": "2026-11"}).json()["id"]
    assert client.put(f"/bills/{bid}/days/31", json={"weight": 1, "rate": 1}).status_code == 400


def test_close_carries_balance_to_next_month(client, flower):
    sid = client.post("/suppliers", json={"name": "Carry Test", "flower_id": flower["id"]}).json()["id"]
    bid = client.post("/bills", json={"supplier_id": sid, "month": "2026-09"}).json()["id"]
    client.put(f"/bills/{bid}/days/1", json={"weight": 10, "rate": 100})        # 1,000, net 900
    client.post(f"/bills/{bid}/kharcha", json={"day": 2, "amount": 5000})       # final -4,100
    nov = client.post("/bills", json={"supplier_id": sid, "month": "2026-10"}).json()
    assert nov["opening_due"] == 0                                               # September still open

    # Supplier pays 1,000 back in cash; the rest carries.
    closed = client.post(f"/bills/{bid}/close", json={"paid_cash": -1000}).json()
    assert closed["status"] == "closed"
    assert closed["settlement"]["carried"] == -3100
    nov = client.get(f"/bills/{nov['id']}").json()
    assert nov["opening_due"] == 3100
    assert nov["totals"]["final"] == -3100

    # Closed bills cannot be edited until reopened.
    assert client.put(f"/bills/{bid}/days/3", json={"weight": 1, "rate": 1}).status_code == 400
    client.post(f"/bills/{bid}/reopen")
    assert client.get(f"/bills/{nov['id']}").json()["opening_due"] == 0


def test_advance_cut_on_close(client, flower):
    sid = client.post("/suppliers", json={"name": "Advance Test", "flower_id": flower["id"]}).json()["id"]
    a = client.post(f"/suppliers/{sid}/advances", json={"on_date": "2026-10-01", "amount": 30000, "kind": "given"}).json()
    assert a["balance"] == 30000
    bid = client.post("/bills", json={"supplier_id": sid, "month": "2026-10"}).json()["id"]
    client.put(f"/bills/{bid}/days/5", json={"weight": 100, "rate": 100})        # 10,000, net 9,000
    b = client.post(f"/bills/{bid}/close", json={"advance_cut": 9000}).json()
    assert b["settlement"]["carried"] == 0
    detail = client.get(f"/suppliers/{sid}").json()
    assert detail["position"]["advance"] == 21000
    assert detail["advances"][-1]["kind"] == "bill_cut"
    client.post(f"/bills/{bid}/reopen")
    assert client.get(f"/suppliers/{sid}").json()["position"]["advance"] == 30000


def test_daily_entry_creates_bills(client, flower):
    sid = client.post("/suppliers", json={"name": "Daily Test", "flower_id": flower["id"]}).json()["id"]
    r = client.put("/daily/2026-12-03", json={"rows": [
        {"supplier_id": sid, "weight": 12.5, "rate": 110, "add_kharcha": 200, "kharcha_tag": "B"},
    ]}).json()
    assert r["saved"] == 1
    day = client.get("/daily/2026-12-03").json()
    row = next(x for x in day["rows"] if x["supplier_id"] == sid)
    assert row["amount"] == 1375 and row["kharcha"][0]["amount"] == 200
    nxt = client.get("/daily/2026-12-04").json()
    assert next(x for x in nxt["rows"] if x["supplier_id"] == sid)["last_rate"] == 110


def test_reports(client):
    rep = client.get("/reports/month", params={"month": "2026-11"}).json()
    assert rep["kpis"]["total"] > 0
    assert len(rep["daily"]) == 30
    assert rep["by_flower"][0]["flower"] == "Gulab"
    assert client.get("/reports/overview").status_code == 200
    bal = client.get("/reports/balances").json()
    assert "payable" in bal and "receivable" in bal


def test_viewer_cannot_write(client, supplier):
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as v:
        token = v.post("/auth/login", json={"username": "viewer", "password": "viewer-pass-1"}).json()["token"]
        v.headers["Authorization"] = f"Bearer {token}"
        assert v.get("/suppliers").status_code == 200
        assert v.post("/suppliers", json={"name": "Nope"}).status_code == 403


def test_entries_range(client):
    r = client.get("/reports/entries", params={"date_from": "2026-11-01", "date_to": "2026-11-30"}).json()
    assert r["totals"]["amount"] == sum(x["amount"] for x in r["rows"])
    assert all(x["date"].startswith("2026-11") for x in r["rows"])
    assert client.get("/reports/entries", params={"date_from": "2026-11-30", "date_to": "2026-11-01"}).status_code == 400


def test_investor_share_and_earnings(client, flower):
    sid = client.post("/suppliers", json={"name": "Khaata Test", "flower_id": flower["id"]}).json()["id"]
    inv = client.post("/investors", json={"name": "Test Investor"}).json()
    r = client.post("/khaata-shares", json={"supplier_id": sid, "investor_id": inv["id"], "pct": 30, "from_month": "2026-05"})
    assert r.status_code == 200, r.text
    assert client.post("/khaata-shares", json={"supplier_id": sid, "investor_id": inv["id"], "pct": 10,
                                               "from_month": "2026-06"}).status_code == 400   # overlaps
    for month in ("2026-04", "2026-05"):
        bid = client.post("/bills", json={"supplier_id": sid, "month": month}).json()["id"]
        client.put(f"/bills/{bid}/days/1", json={"weight": 100, "rate": 100})        # 10,000 -> commission 1,000
    d = client.get(f"/investors/{inv['id']}").json()
    assert d["investor"]["earned"] == 300                                              # only May counts
    client.post(f"/investors/{inv['id']}/entries", json={"kind": "payout", "amount": 100, "on_date": "2026-06-01"})
    client.post(f"/investors/{inv['id']}/entries", json={"kind": "invested", "amount": 50000, "on_date": "2026-05-01",
                                                         "supplier_id": sid})
    d = client.get(f"/investors/{inv['id']}").json()
    assert d["investor"]["payable"] == 200 and d["investor"]["capital"] == 50000
    assert d["khaatas"][0]["invested"] == 50000
    assert client.get(f"/suppliers/{sid}").json()["shares"][0]["pct"] == 30


def test_exports(client):
    r = client.get("/export/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "json/bills.json" in z.namelist() and "README.txt" in z.namelist()
    assert "password_hash" not in z.read("json/users.json").decode()
    sid = client.get("/suppliers").json()[0]["id"]
    assert client.get(f"/export/supplier/{sid}.xlsx").status_code == 200
    iid = client.get("/investors").json()[0]["id"]
    assert client.get(f"/export/investor/{iid}.xlsx").status_code == 200
