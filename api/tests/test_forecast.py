from datetime import date, timedelta

import forecast


def series(values, start=date(2026, 9, 1)):
    return {start + timedelta(days=i): float(v) for i, v in enumerate(values)}


def test_flat_rate_forecasts_the_same_rate():
    p = forecast.predict(series([120] * 40), date(2026, 10, 11))
    assert p["rate"] == 120.0 and p["typical_miss"] == 0.0 and p["momentum"] == 0.0 and not p["stale"]


def test_momentum_is_learnt_from_back_to_back_changes():
    # Each change is 0.5 of the previous one, alternating sign so there is no weekday pattern.
    vals, c = [100.0], 40.0
    for _ in range(60):
        vals.append(vals[-1] + c)
        c = -0.5 * c if abs(c) > 2 else 40.0
    rates = series(vals)
    m = forecast.fit(rates)
    assert -0.6 < m["phi"] < -0.4


def test_weekday_pattern():
    # +10 every Friday, -10 every Saturday, otherwise flat.
    start = date(2026, 6, 1)   # a Monday
    vals, v = [], 100.0
    for i in range(70):
        d = start + timedelta(days=i)
        v += 10 if d.weekday() == 4 else -10 if d.weekday() == 5 else 0
        vals.append(v)
    rates = series(vals, start)
    friday = start + timedelta(days=74)   # the next Friday after the data
    thursday = friday - timedelta(days=1)
    rates = {d: r for d, r in rates.items() if d < thursday}
    p = forecast.predict(rates, friday)
    assert p["weekday_effect"] == 10.0
    assert abs(p["rate"] - (rates[max(rates)] + 10)) < 0.5


def test_gap_and_stale():
    rates = series([100] * 20)
    last = max(rates)
    assert forecast.predict(rates, last + timedelta(days=3))["days_since"] == 3
    p = forecast.predict(rates, last + timedelta(days=30))
    assert p["stale"] and p["rate"] == 100.0
    assert forecast.predict(rates, min(rates)) is None


def test_route(client):
    on = date(2026, 3, 20)
    f = client.post("/flowers", json={"name": "Forecast test"}).json()
    s = client.post("/suppliers", json={"name": "Forecast supplier", "flower_id": f["id"]}).json()
    for day in range(1, 20):
        r = client.put(f"/daily/2026-03-{day:02d}", json={"rows": [
            {"supplier_id": s["id"], "weight": 10, "rate": 100 + day}]})
        assert r.status_code == 200, r.text
    fc = client.get("/reports/forecast", params={"on": on.isoformat()}).json()
    row = next(x for x in fc["flowers"] if x["flower"] == "Forecast test")
    assert row["today"]["last_rate"] == 119.0 and row["today"]["days_since"] == 1
    assert 118 <= row["today"]["rate"] <= 122
    assert row["today_so_far"] is None and fc["tomorrow"] == "2026-03-21"
