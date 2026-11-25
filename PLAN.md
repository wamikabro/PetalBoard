# PetalBoard work plan (from 2026-11-25)

Work through these one at a time, smallest first. Tick each step when it is done and deployed.

## 1. Autosave everything, smoothly (done 2026-11-25)

Problem: on the bill page, adding a kharcha redrew the whole grid from the server, so weight/rate typed on other
days (not saved yet) went back to the old values. The daily page redrew all rows after a kharcha, losing other
typed kharcha boxes and the cursor.

- [x] Bill page: saves update the page in place (`applyBill`), never redraw the grid; saves run in order (queue).
- [x] Bill page: kharcha adds on Enter or on leaving the box, no pop-up dialog, no toast. Shown faded until saved.
      Tag is typed after the amount: `1250B`.
- [x] Daily page: a save only refreshes its own row; typing during a slow save is not marked "saved".
      A kharcha on a row whose weight has no rate yet keeps the saved weight/rate and only adds the kharcha.
- [x] Checked in headless Chrome: typed-but-unsaved days survive kharcha saves, cursor stays, tab switch keeps typing. Pushed.

## 2. Backup that a person can read (done 2026-11-25)

Today `/export/backup` is a zip of raw database tables (`json/` and `csv/`, one file per table, with ids and
foreign keys such as `supplier_id = 17`). It can be restored, but nobody can read it.

New zip layout (Settings → Download backup):
- `PetalBoard backup 2026-11-25/`
  - `README.txt`: what each file is, in plain words (English + Urdu).
  - `Summary.xlsx`: suppliers (name, Urdu name, flower, khaata open/closed, outstanding, last bill),
    who owes whom right now, month-by-month totals, investors and their earnings.
  - `Bills/2026-10 October.xlsx`: one workbook per month, one sheet per supplier laid out like the paper bill
    (day, weight, rate, amount, kharcha, then commission, net, kharcha, final, paid, carried), plus a
    first sheet listing every bill of the month with totals.
  - `Bills/2026-10 October.pdf`: the printed bills (same as Print all), optional because it makes the zip larger.
  - `Suppliers/<name>.xlsx`: each supplier's full history and outstanding history (reuses `/export/supplier`).
  - `Investors/<name>.xlsx`: reuses `/export/investor`.
  - `Daily entries.csv`: every day row with names and dates, not ids, for Excel.
  - `_restore/`: the raw tables as now (json), kept only so the app can be restored from it. README says not to edit it.
- Names, dates and rupees everywhere; no ids or column names like `opening_due` outside `_restore/`.
- Test: open every file in Excel; the totals in `Summary.xlsx` match the app's dashboard and bills.
- Later (backlog): "Restore from backup" reads `_restore/`.

Done: everything above except the PDF of printed bills (printing happens in the browser, so the server
cannot make it yet; the Excel month files have the same numbers). Takes about 20 s on the dev PC.

## 3. Outstanding (advance) control and khaata open/close

Today: the bill shows "Advance outstanding" (`advances` table, running sum). Closing a month has a free
"Cut from the advance" number with no limit check. Cuts are stored as `advances` rows of kind `bill_cut`.

### 2a. Cut from the outstanding when settling a bill
- In the Close-month dialog, the cut box gets three buttons: **50%**, **Full**, **Custom**.
- Limit = the smaller of the outstanding advance and the money the shop owes on this bill (positive final).
  50% and Full are of that limit, rounded down to whole rupees.
- Over the limit: the box turns red, Close is disabled, and the reason is shown, e.g.
  "Only Rs 12,400 can be cut: the bill leaves Rs 12,400 for the supplier" or
  "Outstanding is only Rs 8,000".
- The API checks the same limit (`POST /bills/{id}/close`) and refuses with the same reason, so nothing can
  get around the screen check. Add tests.
- Never cut on its own: the editor always chooses (default 0, as now).

### 2b. History of the outstanding
- Supplier page and bill page: "Outstanding history" table: date, what happened (given / cut from bill
  "Oct 2026" with link / cash returned / written off / opening), amount, balance after, who entered it.
- Comes from the `advances` rows; add `created_by` names to `GET /suppliers/{id}` history output.

### 2c. Khaata close and renew
- New table `khaatas`: supplier, opened on, closed on, terms (commission %, note), closed reason, closed by.
  Every existing supplier gets one open khaata starting at their first bill (migration).
- **Close khaata** (manager/owner, button on the supplier page): asks for a reason and shows the outstanding.
  If it is not 0, the editor confirms writing it off; a `written_off` advances row takes it to 0
  (kept in history). Investor shares on that khaata end at the closing month.
- When a cut brings the outstanding to 0, the app only *offers* "Outstanding is now 0. Close this khaata?".
  It never closes by itself (some suppliers never ask for theirs; the editor decides).
- **Renew khaata**: opens a new khaata with new terms (new advance given, commission %, investor shares).
  The old one stays in history.
- Questions for the owner (add to `data/QUESTIONS.md`, section A): does a closed khaata mean the supplier
  stops bringing flowers (no more bills), or can bills continue without a khaata? Does a write-off reduce
  investors' money?

## 4. Works offline on phones (iPhone and Android)

Goal: the app opens and works with no internet; all data is kept on the phone; changes upload by themselves
when the internet comes back.

**Why (owner, 2026-11-25):** if the server is ever gone, the data must be completely safe on the phones.
A server error must never overwrite the real data. So:
- The phone copy is only ever *added to* or *updated* by a successful sync. An error, timeout, empty reply,
  wrong address or a reply from a different/blank database never deletes or replaces anything on the phone.
- Each sync checks it is talking to the same database (a database id from the server, saved on first sync) and
  that counts did not drop sharply; if either fails, sync stops and shows a warning instead of applying it.
- Deletions only come through the server's deletions log, one by one, never by "the server no longer has it".
- The phone can export its own copy as the readable backup zip (section 2) with no server at all, and the
  server can be refilled from a phone copy (restore).

### 3a. App works without internet (installable)
- Web app manifest + icons, so it can be added to the home screen (iPhone: Safari > Share > Add to Home
  Screen; Android: Chrome install prompt).
- Service worker caches every page, script, font and the blank form image. Pages with `?id=` are served
  from the same cached page.
- Ask the browser to keep the data (`navigator.storage.persist()`); on iPhone this only holds for the
  home-screen app, so the install step matters there.

### 3b. Whole data kept on the phone
- New API `GET /sync?since=<cursor>`: everything changed since the last sync (all tables the app reads),
  using `updated_at` columns (add where missing) plus a deletions log.
- The phone stores it in IndexedDB. Pages read from there first, then refresh from the server when online.
- Bill totals must be worked out on the phone too: port the arithmetic in `api/billing.py`
  (amount, commission, net, final, warnings) to TypeScript, with tests that compare both on every
  imported bill.

### 3c. Changes made offline upload later
- Every change goes to an "outbox" in IndexedDB first, shown at once on screen, then sent in order.
- Sent when the connection returns (`online` event, app opening, Background Sync on Android; iPhone has no
  background sync, so it sends when the app is opened).
- Each change carries a unique id, so a change sent twice is saved once (API needs this for kharcha and
  day rows).
- Two phones changing the same day: the later change wins, and the earlier one is listed on a
  "Sync problems" screen to check.
- A small status in the header: "Offline · 3 changes waiting" / "All saved".
- Closing months, khaata close and advances stay online-only at first (money decisions, need fresh balances).

### Order
3a → 3b read-only → offline daily entry and kharcha → offline bill editing → the rest.

## Earlier backlog
See the "Next steps" in `CLAUDE.md` (owner questions, August 2026, closing months in order, deployment items).
