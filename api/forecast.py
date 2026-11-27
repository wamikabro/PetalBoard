"""Today's (and tomorrow's) rate forecast per flower, worked out by hand: no ML library.

The model is a SARIMA(1,1,0) with a weekly (7-day) seasonal part, fitted on each flower's daily market rate
(the kg-weighted average rate of all suppliers that day = amount / kg):

    change_t = rate_t - rate_(t-1)                  (the "1" in the middle: work on day-to-day changes)
    change_t = phi * change_(t-1) + week[weekday_t] + noise

  * phi (the AR(1) "momentum") = sum(change_(t-1) * change_t) / sum(change_(t-1)^2), least squares over all
    pairs of back-to-back days. Kept between -0.9 and 0.9; 0 when there are fewer than MIN_PAIRS pairs.
  * week[w] (the seasonal part) = the average of what phi leaves unexplained, on that weekday (Mon=0).
    0 for a weekday seen fewer than MIN_WEEKDAY times.
  * forecast for the next day = last rate + phi * last change + week[next weekday]. Further ahead (a day
    with no entries in between), the change is rolled forward one day at a time. More than MAX_GAP days
    after the last entry, only the last rate is given.
  * Self-check per flower: over the last 60 days, if the model's one-day misses add up to more than those of
    "same as the last day", the last day's rate is given instead (`method` = "last", else "model").
    A flower with little or patchy history (Motia) then falls back to its last rate.
  * "usually within" = the average size of the chosen method's one-day misses over the last 60 days.

Tested on the data up to Oct 2026 (May-Oct 2026, each day predicted only from the days before), average miss
in Rs/kg: Gulab 20.4 (153 days) against 22.7 for "same as yesterday", 22.4 for LightGBM, 27.2 for exponential
smoothing; Genda 12.1 against 12.5; Motia 259 against 258 (it falls back to the last rate). Prophet and the
statsmodels SARIMA search were not run (see the notes in CLAUDE.md).

web/src/lib/local.ts has the same arithmetic (`forecast`) so the dashboard works offline; keep them equal
(scripts/check-local.mjs compares them).
"""

from datetime import date, timedelta

MIN_PAIRS = 14
MIN_WEEKDAY = 4
MAX_GAP = 7
ERROR_WINDOW = 60
PHI_LIMIT = 0.9


def daily_rates(points: list[tuple[date, int, int]]) -> dict[date, float]:
    """(date, amount, weight in grams) rows -> {date: amount / kg}, for days with weight."""
    amt: dict[date, int] = {}
    grams: dict[date, int] = {}
    for d, a, g in points:
        amt[d] = amt.get(d, 0) + a
        grams[d] = grams.get(d, 0) + g
    return {d: amt[d] * 1000 / grams[d] for d in sorted(grams) if grams[d] > 0}


def fit(rates: dict[date, float]) -> dict:
    """phi, weekday effects and the one-day misses, from the daily rates."""
    days = sorted(rates)
    change = {d: rates[d] - rates[d - timedelta(days=1)] for d in days if d - timedelta(days=1) in rates}
    pairs = [(change[d - timedelta(days=1)], change[d], d) for d in sorted(change) if d - timedelta(days=1) in change]
    num = sum(a * b for a, b, _ in pairs)
    den = sum(a * a for a, _, _ in pairs)
    phi = num / den if len(pairs) >= MIN_PAIRS and den > 0 else 0.0
    phi = max(-PHI_LIMIT, min(PHI_LIMIT, phi))
    # Seasonal part: what momentum leaves over, by weekday. Every day with a change counts (the first day after
    # a gap has no previous change, so only the weekday part applies to it).
    left: dict[int, list[float]] = {}
    for d in sorted(change):
        prev = change.get(d - timedelta(days=1), 0.0)
        left.setdefault(d.weekday(), []).append(change[d] - phi * prev)
    week = [sum(left[w]) / len(left[w]) if len(left.get(w, [])) >= MIN_WEEKDAY else 0.0 for w in range(7)]
    misses = []
    for d in sorted(change):
        prev = change.get(d - timedelta(days=1), 0.0)
        misses.append((d, abs(change[d] - phi * prev - week[d.weekday()])))
    return {"phi": phi, "week": week, "change": change, "misses": misses}


def predict(rates: dict[date, float], target: date) -> dict | None:
    """Forecast for `target` from the rates before it (entries on or after `target` are ignored)."""
    past = {d: r for d, r in rates.items() if d < target}
    if not past:
        return None
    m = fit(past)
    last = max(past)
    gap = (target - last).days
    level = past[last]
    # Self-check: over the last ERROR_WINDOW days, did the model miss by less than "same as the last day"?
    since = last - timedelta(days=ERROR_WINDOW)
    model_miss = [e for d, e in m["misses"] if d >= since]
    last_miss = [abs(m["change"][d]) for d, _ in m["misses"] if d >= since]
    use_model = bool(model_miss) and sum(model_miss) < sum(last_miss)
    value = level
    stale = gap > MAX_GAP
    if use_model and not stale:
        c = m["change"].get(last, 0.0)
        for k in range(1, gap + 1):
            c = m["phi"] * c + m["week"][(last + timedelta(days=k)).weekday()]
            value += c
    recent = model_miss if use_model else last_miss
    return {
        "rate": round(max(value, 0.0), 1),
        "method": "model" if use_model else "last",
        "typical_miss": round(sum(recent) / len(recent), 1) if recent else None,
        "last_date": last.isoformat(),
        "last_rate": round(level, 1),
        "days_since": gap,
        "stale": stale,
        "momentum": round(m["phi"], 3),
        "weekday_effect": round(m["week"][target.weekday()], 1),
        "history_days": len(past),
    }


def forecast(points_by_flower: dict[str, list[tuple[date, int, int]]], today: date) -> dict:
    """The dashboard's answer: per flower with entries in the last 30 days, today's and tomorrow's forecast."""
    flowers = []
    for name, points in points_by_flower.items():
        rates = daily_rates(points)
        recent = [d for d in rates if today - timedelta(days=30) <= d <= today]
        if not recent:
            continue
        kg_total = sum(g for _, _, g in points)
        so_far = [(a, g) for d, a, g in points if d == today]
        so_far_g = sum(g for _, g in so_far)
        # Tomorrow: from today's entries if there are any, else two days on from the last entry.
        flowers.append({
            "flower": name,
            "today": predict(rates, today),
            "tomorrow": predict(rates, today + timedelta(days=1)),
            "today_so_far": {
                "rate": round(sum(a for a, _ in so_far) * 1000 / so_far_g, 1),
                "weight": so_far_g / 1000,
                "suppliers": len(so_far),
            } if so_far_g else None,
            "_kg": kg_total,
        })
    flowers.sort(key=lambda f: (-f["_kg"], f["flower"]))
    for f in flowers:
        del f["_kg"]
    return {"date": today.isoformat(), "tomorrow": (today + timedelta(days=1)).isoformat(), "flowers": flowers}
