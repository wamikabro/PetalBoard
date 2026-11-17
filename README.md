# PetalBoard

Billing app for a flower shop that replaces its handwritten monthly supplier bills.

Suppliers bring flowers every day. The shop keeps one bill per supplier per month and settles it at month end. PetalBoard records the daily entries, calculates the bill the same way the paper does, and prints it in Urdu on the same layout as the paper form. The app itself is in English. Amounts are in whole Pakistani rupees.

## Features

- **Daily entry**: one row per supplier for the day (weight, rate, amount, kharcha). Rows save automatically, and the rate is filled in from the supplier's last rate.
- **Monthly bills**: the paper sheet shown digitally (Urdu header, table, side panel) for print or PDF, plus an edit grid. Closing a month carries the balance into the next month.
- **Advances**: kept as a running balance per supplier, separate from the bill.
- **Investors**: the investor behind each khaata, the share of commission they receive, and their earnings by month.
- **Dashboard and reports**: KPIs, charts, supplier histories, balances, and entries for any date range, with CSV export.
- **Backup and export**: a full backup, or one person's data.
- **Users and roles**: owner > manager > clerk > viewer.

## How a bill is calculated

| Field | Rule |
|---|---|
| amount | `floor(weight × rate)` |
| commission | `floor(total × pct / 100)`; pct is 10 by default and set per supplier |
| net | `total − commission` |
| final | `net − kharcha − opening_due` |

If final is positive, the shop owes the supplier. If it is negative, the supplier owes the shop. Advances are not subtracted on the bill. When a month is closed, `carried = final − paid_cash − advance_cut` becomes the next month's `opening_due`.

## Stack

| Part | Tech | Runs on |
|---|---|---|
| `web/` | Astro 7 static site, Tailwind CSS 3.4, Chart.js | Cloudflare Workers static assets |
| `api/` | FastAPI, PostgreSQL (psycopg 3) | Linux server (uvicorn behind a reverse proxy) |

The web pages are static. They load data from the API in the browser and read `?id=` / `?month=` from the URL.

## Running locally

### API

Requires Python 3.11+ and PostgreSQL.

```sh
cd api
pip install -r requirements.txt
python main.py            # http://127.0.0.1:8000
```

The schema is created from `schema.sql` the first time the API starts.

| Env var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres@127.0.0.1:55432/petalboard_dev` | Database connection |
| `ALLOWED_ORIGINS` | `http://localhost:4321` | CORS origins, comma separated |
| `SESSION_DAYS` | `30` | Login session length |

Create a user (the password is asked for interactively):

```sh
python manage_users.py create <username> "<Display Name>" owner|manager|clerk|viewer
```

Tests (`TEST_DATABASE_ADMIN_URL` must point at a Postgres server where the tests can create and drop a test database):

```sh
python -m pytest tests -q
```

### Web

Requires Node 22.12+.

```sh
cd web
npm install
cp .env.example .env      # set PUBLIC_API_URL to the API address
npm run dev               # http://localhost:4321
npm run build
```

## Deployment

- **Web**: `npm run deploy` (builds, then `wrangler deploy` to Cloudflare).
- **API**: run uvicorn as a service behind an HTTPS reverse proxy, and set `ALLOWED_ORIGINS` to the web app's URL.

## Data

The shop's real data is not in this repository: scanned bills, transcriptions, import scripts and tests built on real bills are all kept out by `.gitignore`. Do not commit files that contain real supplier or investor names or amounts.
