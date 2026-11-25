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
    r = client.post("/suppliers", json={"name": "Khaata Test", "flower_id": flower["id"]})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
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
    names = z.namelist()
    top = names[0].split("/")[0]
    assert top.startswith("PetalBoard backup ")
    for f in ("README.txt", "Summary.xlsx", "Daily entries.csv", "_restore/bills.json", "_restore/manifest.json"):
        assert f"{top}/{f}" in names, f
    assert any(n.startswith(f"{top}/Bills/") and n.endswith(".xlsx") for n in names)
    assert any(n.startswith(f"{top}/Suppliers/") for n in names) and any(n.startswith(f"{top}/Investors/") for n in names)
    assert "password_hash" not in z.read(f"{top}/_restore/users.json").decode()
    # Readable files use names, not ids.
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(z.read(f"{top}/Summary.xlsx")))
    assert wb["Suppliers"]["A1"].value == "Supplier" and "supplier_id" not in z.read(f"{top}/Daily entries.csv").decode("utf-8-sig")
    sid = client.get("/suppliers").json()[0]["id"]
    assert client.get(f"/export/supplier/{sid}.xlsx").status_code == 200
    iid = client.get("/investors").json()[0]["id"]
    assert client.get(f"/export/investor/{iid}.xlsx").status_code == 200


def test_session_renews_on_use(client):
    import db
    with db.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at = now() + interval '1 day'")
    assert client.get("/auth/me").status_code == 200
    with db.transaction() as conn:
        left = conn.execute("SELECT expires_at - now() AS d FROM sessions ORDER BY last_seen DESC NULLS LAST LIMIT 1").fetchone()["d"]
    assert left.days >= 300


def test_login_with_password_only(client):
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"password": "clerk-pass-1"})
        assert r.status_code == 200 and r.json()["user"]["role"] == "clerk"
        assert c.post("/auth/login", json={"password": "not-a-password"}).status_code == 401


def test_passwords_must_be_unique(client):
    r = client.post("/users", json={"display_name": "Second Clerk", "role": "clerk", "password": "clerk-pass-1"})
    assert r.status_code == 400
    r = client.post("/users", json={"display_name": "Second Clerk", "role": "clerk", "password": "another-pass-9"})
    assert r.status_code == 200 and r.json()["username"] == "secondclerk"


def test_wrong_passwords_are_limited(client):
    import routes_auth
    from fastapi.testclient import TestClient
    from main import app
    routes_auth._FAILS.clear()
    try:
        with TestClient(app) as c:
            codes = [c.post("/auth/login", json={"password": f"wrong-{i}"}).status_code for i in range(12)]
            assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
            assert c.post("/auth/login", json={"password": "owner-pass-1"}).status_code == 429   # even the right one waits
    finally:
        routes_auth._FAILS.clear()


def test_advance_cut_limit(client, flower):
    sid = client.post("/suppliers", json={"name": "Cut Limit", "flower_id": flower["id"]}).json()["id"]
    client.post(f"/suppliers/{sid}/advances", json={"on_date": "2025-03-01", "amount": 5000, "kind": "given"})
    bid = client.post("/bills", json={"supplier_id": sid, "month": "2025-03"}).json()["id"]
    client.put(f"/bills/{bid}/days/5", json={"weight": 100, "rate": 100})        # final 9,000
    r = client.post(f"/bills/{bid}/close", json={"advance_cut": 6000})
    assert r.status_code == 400 and "Only Rs 5,000 is outstanding" in r.json()["detail"]
    client.post(f"/suppliers/{sid}/advances", json={"on_date": "2025-03-02", "amount": 20000, "kind": "given"})
    r = client.post(f"/bills/{bid}/close", json={"advance_cut": 9001})
    assert r.status_code == 400 and "leaves Rs 9,000" in r.json()["detail"]
    assert client.post(f"/bills/{bid}/close", json={"advance_cut": 9000}).status_code == 200


def test_khaata_close_renew_reopen(client, flower):
    r = client.post("/suppliers", json={"name": "Khaata Close Test", "flower_id": flower["id"]})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    client.post(f"/suppliers/{sid}/advances", json={"on_date": "2026-07-01", "amount": 8000, "kind": "given"})
    k = client.get(f"/suppliers/{sid}").json()["khaata"]
    assert k["status"] == "open" and k["outstanding"] == 8000 and k["current"]["opened_on"] == "2026-07-01"
    # Something is outstanding: closing needs an explicit write-off.
    r = client.post(f"/suppliers/{sid}/khaata/close", json={"on_date": "2026-09-30"})
    assert r.status_code == 400 and "Rs 8,000 is still outstanding" in r.json()["detail"]
    k = client.post(f"/suppliers/{sid}/khaata/close", json={"on_date": "2026-09-30", "write_off": True, "reason": "stopped"}).json()
    assert k["status"] == "closed" and k["outstanding"] == 0 and k["current"]["written_off"] == 8000
    adv = client.get(f"/suppliers/{sid}").json()["advances"]
    assert adv[-1]["kind"] == "written_off" and adv[-1]["amount"] == -8000
    assert client.delete(f"/advances/{adv[-1]['id']}").status_code == 400
    assert client.post(f"/suppliers/{sid}/khaata/close", json={"on_date": "2026-10-01"}).status_code == 400
    # Reopen (mistake) puts the outstanding back.
    k = client.post(f"/suppliers/{sid}/khaata/reopen").json()
    assert k["status"] == "open" and k["outstanding"] == 8000
    # Close again (write off), then renew on new terms with a new advance.
    client.post(f"/suppliers/{sid}/khaata/close", json={"on_date": "2026-09-30", "write_off": True})
    assert client.post(f"/suppliers/{sid}/khaata/renew", json={"on_date": "2026-09-01"}).status_code == 400  # before closing
    k = client.post(f"/suppliers/{sid}/khaata/renew", json={"on_date": "2026-10-01", "commission_pct": 8, "advance": 3000,
                                                          "terms": "8% from October"}).json()
    assert k["status"] == "open" and k["outstanding"] == 3000 and len(k["history"]) == 2
    assert client.get(f"/suppliers/{sid}").json()["supplier"]["commission_pct"] == 8
    assert client.post(f"/suppliers/{sid}/khaata/renew", json={"on_date": "2026-10-02"}).status_code == 400  # still open


def test_migration_from_version_1():
    """An old (version 1) database with data in it is brought up to date without losing anything."""
    import re
    import psycopg
    import db
    from conftest import ADMIN_URL
    name = "petalboard_migrate_test"
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name
    v1 = db.SCHEMA_FILE.read_text(encoding="utf-8").replace("VALUES (2)", "VALUES (1)").replace(", 'written_off'", "")
    v1 = re.sub(r"CREATE TABLE khaatas \(.*?CREATE UNIQUE INDEX khaatas_one_open[^;]*;", "", v1, flags=re.S)
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(v1)
            conn.execute("INSERT INTO flowers (name) VALUES ('Old flower')")
        db.migrate(url)
        db.migrate(url)  # safe to run again
        with db.transaction(url=url) as conn:
            assert conn.execute("SELECT version FROM schema_meta").fetchone()["version"] == max(db.MIGRATIONS)
            assert conn.execute("SELECT to_regclass('public.khaatas') AS t").fetchone()["t"]
            old = conn.execute("SELECT rev FROM flowers WHERE name = 'Old flower'").fetchone()
            assert old and old["rev"] > 0
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def test_sync_sends_changes_and_deletions(client, flower):
    full = client.get("/sync").json()
    assert full["db_id"] and full["rev"] > 0 and full["counts"]["bills"] == len(full["tables"]["bills"])
    assert all("password_hash" not in u for u in full["tables"]["users"])
    rev = full["rev"]
    sid = client.post("/suppliers", json={"name": "Sync Test", "flower_id": flower["id"]}).json()["id"]
    bid = client.post("/bills", json={"supplier_id": sid, "month": "2025-02"}).json()["id"]
    client.put(f"/bills/{bid}/days/3", json={"weight": 5, "rate": 100})
    k = client.post(f"/bills/{bid}/kharcha", json={"day": 3, "amount": 70}).json()["days"][2]["kharcha"][0]["id"]
    d = client.get("/sync", params={"since": rev}).json()
    assert [r["name"] for r in d["tables"]["suppliers"]] == ["Sync Test"]
    assert [r["_key"] for r in d["tables"]["bill_days"]] == [f"{bid}:3"]
    assert d["rev"] > rev and not d["deletions"]
    # Deleting is reported one by one, including rows removed by a cascade.
    client.delete(f"/kharcha/{k}")
    client.put(f"/bills/{bid}/days/3", json={"weight": None, "rate": None})
    d2 = client.get("/sync", params={"since": d["rev"]}).json()
    assert {(x["table"], x["key"]) for x in d2["deletions"]} == {("kharcha", str(k)), ("bill_days", f"{bid}:3")}
    assert client.get("/sync", params={"since": d2["rev"]}).json()["tables"]["bills"] == []


def test_row_on_a_day_the_month_lacks(client, flower):
    """The workbook has a row dated 31 June: it counts, the bill warns, and the month report still works."""
    import db
    s = client.post("/suppliers", json={"name": "Test June31", "flower_id": flower["id"]}).json()
    bill = client.post("/bills", json={"supplier_id": s["id"], "month": "2026-06"}).json()
    client.put(f"/bills/{bill['id']}/days/30", json={"weight": 2, "rate": 100})
    with db.transaction() as conn:   # the API refuses day 31 in June; only an import can write it
        conn.execute("INSERT INTO bill_days (bill_id, day, weight, rate, amount) VALUES (%s, 31, 3, 250, 750)", (bill["id"],))
    b = client.get(f"/bills/{bill['id']}").json()
    assert len(b["days"]) == 30
    assert b["totals"]["total"] == 200 + 750
    assert any("Day 31 is not in June 2026" in w for w in b["warnings"])
    r = client.get("/reports/month", params={"month": "2026-06"})
    assert r.status_code == 200, r.text
    assert len(r.json()["daily"]) == 30
