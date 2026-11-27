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

## 3. Outstanding (advance) control and khaata open/close (done 2026-11-25)

Today: the bill shows "Advance outstanding" (`advances` table, running sum). Closing a month has a free
"Cut from the advance" number with no limit check. Cuts are stored as `advances` rows of kind `bill_cut`.

### 3a. Cut from the outstanding when settling a bill
- In the Close-month dialog, the cut box gets three buttons: **50%**, **Full**, **Custom**.
- Limit = the smaller of the outstanding advance and the money the shop owes on this bill (positive final).
  50% and Full are of that limit, rounded down to whole rupees.
- Over the limit: the box turns red, Close is disabled, and the reason is shown, e.g.
  "Only Rs 12,400 can be cut: the bill leaves Rs 12,400 for the supplier" or
  "Outstanding is only Rs 8,000".
- The API checks the same limit (`POST /bills/{id}/close`) and refuses with the same reason, so nothing can
  get around the screen check. Add tests.
- Never cut on its own: the editor always chooses (default 0, as now).

### 3b. History of the outstanding
- Supplier page and bill page: "Outstanding history" table: date, what happened (given / cut from bill
  "Oct 2026" with link / cash returned / written off / opening), amount, balance after, who entered it.
- Comes from the `advances` rows; add `created_by` names to `GET /suppliers/{id}` history output.

### 3c. Khaata close and renew
- New table `khaatas`: supplier, opened on, closed on, terms (commission %, note), closed reason, closed by.
  Every existing supplier gets one open khaata starting at their first bill (migration).
- **Close khaata** (manager/owner, button on the supplier page): asks for a reason and shows the outstanding.
  If it is not 0, the editor confirms writing it off; a `written_off` advances row takes it to 0
  (kept in history). Investor shares on that khaata end at the closing month.
- When a cut brings the outstanding to 0, the app only *offers* "Outstanding is now 0. Close this khaata?".
  It never closes by itself (some suppliers never ask for theirs; the editor decides).
- **Renew khaata**: opens a new khaata with new terms (new advance given, commission %, investor shares).
  The old one stays in history.
- Questions for the owner: Q34 and Q35 in `data/QUESTIONS.md` (section A).

Done: cut buttons with limit (screen and API, tests), outstanding history with bill link and who entered it,
khaata close (write-off only when ticked) / renew (new terms, commission, advance) / undo closing (owner),
offer to close after a cut brings the outstanding to 0. Database migration v2 (`db.MIGRATIONS`) runs on start.
Not done: outstanding history on the bill page itself (the bill links to the supplier page's history instead).

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

### 4a. App works without internet (installable) (done 2026-11-25)
- Web app manifest + icons, so it can be added to the home screen (iPhone: Safari > Share > Add to Home
  Screen; Android: Chrome install prompt).
- Service worker caches every page, script, font and the blank form image. Pages with `?id=` are served
  from the same cached page.
- Ask the browser to keep the data (`navigator.storage.persist()`); on iPhone this only holds for the
  home-screen app, so the install step matters there.
- Done: `public/manifest.webmanifest`, icons in `public/icons/`, `scripts/build-sw.mjs` writes `dist/sw.js`
  after every `npm run build` (precaches all 37 files; pages network-first with a 4 s timeout, then the saved
  copy). Checked in headless Chrome: every page opens offline. Pages still need the server for data (4b).

### 4b. Whole data kept on the phone (read-only part done 2026-11-25)
- New API `GET /sync?since=<cursor>`: everything changed since the last sync (all tables the app reads),
  using `updated_at` columns (add where missing) plus a deletions log.
- The phone stores it in IndexedDB. Pages read from there first, then refresh from the server when online.
- Bill totals must be worked out on the phone too: port the arithmetic in `api/billing.py`
  (amount, commission, net, final, warnings) to TypeScript, with tests that compare both on every
  imported bill.

Done (2026-11-25):
- Server: migration v3 gives every row a change number (`rev`, one sequence `change_seq`) and logs deletes in
  `deletions` (triggers); `schema_meta.db_id` identifies the database. `GET /sync?since=` (`api/routes_sync.py`)
  returns changed rows, deletions, counts and db_id in one consistent snapshot. Test: `test_sync_sends_changes_and_deletions`.
- Phone: `web/src/lib/offline.ts` keeps every table in IndexedDB (`petalboard-offline`), syncs 1.5 s after a page
  opens, every 5 min and when the connection returns; asks again for the last 500 changes each time (overlap) and
  does a full check once a day. Safety rules are at the top of that file. Then it saves the data of the pages
  people use (lists, last two months' bills, every supplier and investor) so they open offline.
- `api()` saves every GET answer and, with no server (network error or 502/503/504), returns the saved one and
  shows a yellow notice at the top. Settings → "Data on this device": status, counts, Update now, download the
  device's copy (.json, same tables as the backup's `_restore/`), and (owner) accept a refused reply.
- Checked in headless Chrome on the dev data: full copy 164 bills / 4,349 days; 203 pages saved in ~40 s;
  offline bills list and bill open with the notice; add and delete of a kharcha reach the device; a 500, a
  strange reply, an empty database and another db_id each change nothing (the last two show "Update stopped").

Done (2026-11-25, second part):
- `web/src/lib/local.ts`: the server's arithmetic in TypeScript (bill, bill lists, neighbours, daily, month report,
  entries in any range, overview, balances, flowers). With no server, `api()` uses it when the device copy is newer
  than the saved page or the page was never saved. Supplier and investor pages still rely on saved pages (all are
  saved after each sync).
- `node scripts/check-local.mjs` (from `web/`, dev API running) compares local and server answers: 409 answers, 0 differ.
  Run it after changing the arithmetic on either side.
- It found a server crash: the workbook has a row on 31 June (Baloch, 750), which broke the June month report.
  Fixed; the bill now warns about it; noted in Q24. Checked in headless Chrome with the API stopped: March bills,
  a March bill, Reports Feb–Mar, daily 10 March and the February dashboard all open from the device.

Restore (server side done 2026-11-25): `POST /restore` (owner, `api/routes_restore.py`) loads a device copy (.json)
or a backup zip (`_restore/`) into a **new, empty** server only (409 if it has any supplier or bill). Users are not
restored (no passwords in copies): create the owner login on the new server first; "entered by" links to unknown
users become empty. Test `test_restore_from_a_device_copy`; the full dev data (164 bills) restored into a fresh
database gave identical bills.

Restore button (done 2026-11-25): Settings → "Restore a new server" (owner): pick a device copy (.json) or backup
zip, the counts and months in the file are shown, then it uploads to `POST /restore`. Checked in headless Chrome
against an empty database: a wrong file is refused before upload, the full dev copy loads (164 bills), a second try
is refused with "This server already has data".

### 4c. Changes made offline upload later
- Every change goes to an "outbox" in IndexedDB first, shown at once on screen, then sent in order.
- Sent when the connection returns (`online` event, app opening, Background Sync on Android; iPhone has no
  background sync, so it sends when the app is opened).
- Each change carries a unique id, so a change sent twice is saved once (API needs this for kharcha and
  day rows).
- Two phones changing the same day: the later change wins, and the earlier one is listed on a
  "Sync problems" screen to check.
- A small status in the header: "Offline · 3 changes waiting" / "All saved".
- Closing months, khaata close and advances stay online-only at first (money decisions, need fresh balances).

Done for the daily entry page (2026-11-25):
- Server (migration v4): `kharcha.client_id` (unique; a kharcha sent twice is saved once), `bill_days.made_at`
  (when the change was made, capped at the server's time). `PUT /daily/{date}` rows take `made_at`, `base`
  (the weight/rate the phone showed) and `kharcha_client_id`; leaving out weight/rate/note only adds the kharcha.
  A change made earlier than the saved one is not applied (`kept_newer`); one that replaces someone else's value
  is applied and reported (`replaced`); both come back in `conflicts`. `DELETE /kharcha/by-client/{id}`.
  Test: `test_outbox_replays_are_safe_and_later_change_wins`.
- Phone: `web/src/lib/outbox.ts`. A save goes straight to the server; with no connection (or anything already
  waiting) it goes to the `outbox` store and is sent in order on: app open, `online`, the page coming back to
  view, every minute, and before any read. Waiting changes are drawn on top of the device copy
  (`applyPending` in `local.ts`), so pages and reloads show them. A kharcha removed before it was sent just
  leaves the outbox. Refused changes and conflicts go to the `problems` store: Settings → "Changes made on this
  device" lists both, with "Checked" to clear. Header/sidebar chip: "Offline · 3 changes waiting", "Sending…",
  "All saved", "1 to check". The device copy download includes `waiting_changes`.
- Checked in headless Chrome with the API stopped: weight/rate and kharcha on three rows show "Waiting", survive a
  reload, the bills list shows the new (not yet created) November bills; after the API starts they all reach the
  server once, chips turn to "Saved". Conflict: an offline change to a day that another device changed later is
  not applied and is listed to check.
- Not done yet: Background Sync on Android (sending while the app is closed), offline editing on the bill page
  (it still saves online only), offline "New supplier".

### Order
4a → 4b read-only → offline daily entry and kharcha (done) → offline bill editing → the rest.

## Earlier backlog
See the "Next steps" in `CLAUDE.md` (owner questions, August 2026, closing months in order, deployment items).

## 5. Almost everything works offline (asked 2026-11-25)

Owner: "make almost everything work offline". Builds on 4c (daily entry already works offline).

- [x] 5a. Server: every save may carry `X-Change-Id`. Recorded in `applied_changes` (migration v5) in the same
      transaction as the change, with its answer; sent again, the first answer comes back (`X-Change-Replayed`).
      Middleware in `main.py` (CORS stays outermost). Kept 180 days. Test `test_a_save_sent_twice_is_applied_once`.
- [x] 5b. Phone reads `/settings`, `/suppliers`, `/suppliers/{id}`, `/investors`, `/investors/{id}` (`local.ts`).
      `check-local.mjs`: 468 answers, 0 differ (the entries report got a fixed order for ties on both sides).
- [x] 5c. `local.ts` `Writer` applies every kind of waiting save like the server (refusals too). New rows get a
      temporary negative id (`createdTable`, `createdIds`, `remapItem`); daily entry and start-month answers now
      list the bills they made. A sent save stays drawn on top until the next update of the device copy.
- [x] 5d. Pages use `save()`/`submit()` from `outbox.ts`: bill (days go through `/daily` so "later wins" holds there
      too), bills, daily, supplier(s), investor(s), settings (shop, flowers). Ids known to be real replace temporary
      ones in new saves and in page addresses (`rememberId`/`realIds` in `api.ts`).
- [x] 5e. `web/scripts/check-writes.mjs` (needs a throwaway database, see its header): 9 scenarios, 41 answers,
      0 differ. Headless Chrome with the API stopped: new supplier + advance, daily entry, bill kharcha + day edit +
      close with a 5,000 cut, new investor + money; reload; API back: all 8 saves arrived once, the database matches.

Stays online only: log in / out, users, restore, backup and Excel downloads (made by the server).

## 6. Offline that works on a real phone; logout keeps the data (asked 2026-11-27)

Owner: "the offline version doesn't seem to work well. Make everything work offline. Logout shouldn't remove the
data from the mobile at all; it should be an offline copy forever."

Found by running the production build in headless Chrome as a phone, with the web server and API stopped and the
page offline: pages waited ~10 s and several (bill, supplier, investor) stayed on "Loading…"; phones had no Log out
button; logging out (or a login that ended) sent every page to /login, so the data on the phone could not be seen.

- [x] Service worker: pages open from the saved copy at once and are refreshed in the background (was: network
      first, 4 s wait). Saved files are found even when the server adds `Vary: Origin` (`ignoreVary`).
- [x] `api()`: with no connection, or for 20 s after the server failed, reads come from the device at once; reads
      with a device copy wait 6 s at most for the server; waiting saves are sent in the background instead of
      holding the page. Saves made while the server is known to be down wait on the device at once.
- [x] The "save every page ahead of time" pass after each update is gone (~200 requests): every page is worked out
      from the device copy (`local.ts`), which `check-local.mjs` keeps equal to the server.
- [x] Log out removes only the login. The data and any waiting changes stay; with a device copy
      (`localStorage.petalboard_device_copy`) every page still opens, read only, with a "Logged out · Log in" notice.
      Waiting changes are sent after the next login (logout warns about them first). A login that ended (401)
      does the same instead of jumping to /login. Login page: "Open the data on this device without logging in".
- [x] Settings: Login card with Log out / Log in (phones had none); people list says it needs the server.
- [x] Checked (390 px, production build): 15 pages offline in ~0.3 s each, offline daily entry waits and survives a
      reload, logout offline keeps data + waiting change, logged-out browsing offline and online, log in again →
      the change reached the database once.

Still online only (they need the server by nature): logging in, the people list, restore, the Excel/zip downloads
made by the server (the device's own copy can always be downloaded from Settings).
