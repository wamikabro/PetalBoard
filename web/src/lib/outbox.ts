// Every save in the app goes through here. With a connection it goes straight to the server. Without one
// (or while older saves are still waiting) it is kept on this device in an "outbox" (IndexedDB, offline.ts),
// shown at once (local.ts applyPending works it out the way the server will), and sent in order when the
// connection comes back: when the app opens, when the phone goes online, every minute, and before any read.
//
//   - Each save has its own id (X-Change-Id): sent twice because an answer was lost, it is applied once.
//   - Things made offline (a supplier, a bill, an investor...) get a temporary negative id. When the server
//     answers with the real id, the saves still waiting and the open pages switch to it (api.ts rememberId).
//   - A sent save stays drawn on top of the device copy until the next update of the copy has it.
//   - Two devices changing the same day: the change made later wins (daily rows carry made_at); the other one
//     is listed in Settings to check. A save the server refuses later (e.g. the month was closed on another
//     phone) is listed there too.
import { api, ApiError, realId, realIds, rememberId } from './api';
import { toast } from './ui';
import { LocalError, createdIds, createdTable, remapItem, type PendingItem } from './local';
import { deviceView, outboxDelete, outboxItems, outboxPut, problemAdd, problemsAll, syncNow } from './offline';

export type DailyRow = {
  supplier_id: number;
  weight?: number | null; rate?: number | null; note?: string | null;
  add_kharcha?: number | null; kharcha_tag?: string | null; kharcha_note?: string | null; kharcha_client_id?: string | null;
  made_at?: string; base?: { weight: number | null; rate: number | null } | null;
};
export type Change = PendingItem & { label: string };
type Queued = Change & { seq: number; change_id: string; tries?: number; sent?: boolean; sent_at?: number };

export const newId = () =>
  (crypto as any).randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
/** A temporary id for something made on this device: negative, so it can never be a real one. */
const tempId = () => -(1_000_000_000 + Math.floor(Math.random() * 1_000_000_000));

/** No server right now: a dropped connection, or the server's app is down behind the proxy. */
const noServer = (e: unknown) => e instanceof ApiError && (e.status === 0 || [502, 503, 504].includes(e.status));

const waitingOnly = (items: Queued[]) => items.filter((x) => !x.sent);
export type OutboxStatus = { waiting: number; problems: number; sending: boolean };
async function announce(sending = false) {
  const detail: OutboxStatus = { waiting: waitingOnly(await outboxItems()).length, problems: (await problemsAll()).length, sending };
  window.dispatchEvent(new CustomEvent('petalboard:outbox', { detail }));
  return detail;
}
export const outboxStatus = () => announce(flushing);

async function send(c: Queued): Promise<any> {
  const headers = { 'X-Change-Id': c.change_id };
  if (c.kind === 'daily') {
    const res = await api(`/daily/${c.date}`, { method: 'PUT', body: { rows: [c.row] }, headers });
    for (const x of res?.conflicts || []) {
      await problemAdd({ label: c.label, what: x.what, yours: x.yours, theirs: x.theirs, date: c.date, supplier_id: c.row.supplier_id });
    }
    if (res?.skipped_closed?.length) {
      await problemAdd({ label: c.label, what: 'closed', date: c.date, supplier_id: c.row.supplier_id,
        message: 'This month was closed before the change arrived, so it was not saved.' });
    }
    return res;
  }
  if (c.kind === 'kharcha_del') {
    const path = c.id != null && c.id > 0 ? `/kharcha/${c.id}` : `/kharcha/by-client/${encodeURIComponent(c.client_id || '')}`;
    try {
      return await api(path, { method: 'DELETE', headers });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null; // already gone
      throw e;
    }
  }
  return api(c.path!, { method: c.method, body: c.body, headers });
}

/** The server made rows for a save: swap the temporary ids for the real ones everywhere. */
async function adoptIds(sent: Queued, res: any): Promise<Queued> {
  const pairs = createdIds(sent, res);
  if (!pairs.length) return sent;
  let self: any = { ...sent };
  for (const [, temp, real] of pairs) {
    rememberId(temp, real);
    if (self.temp_id === temp) self.temp_id = real;
    if (self.temp_bill === temp) self.temp_bill = real;
    if (self.temp_bills) self.temp_bills = Object.fromEntries(Object.entries(self.temp_bills).map(([k, v]) => [k, v === temp ? real : v]));
  }
  for (const it of (await outboxItems()) as Queued[]) {
    if (it.seq === sent.seq) continue;
    let next: any = it;
    for (const [table, temp, real] of pairs) next = remapItem(next, table, temp, real);
    if (next !== it) await outboxPut(next);
  }
  return self;
}

/** Fill in what a new save needs before it can wait on this device: its id, the time, temporary ids. */
async function prepare(c: Change): Promise<Queued> {
  const q: any = { ...c, change_id: newId() };
  // Made from a page opened with a temporary id whose real id is known by now: use the real one.
  const real = (o: any) => o && Object.fromEntries(Object.entries(o).map(([k, v]) => [k, /(^|_)id$/.test(k) ? realId(v) : v]));
  if (q.path) q.path = realIds(q.path);
  if (q.body && typeof q.body === 'object') q.body = real(q.body);
  if (q.row) q.row = real(q.row);
  if (q.id != null) q.id = realId(q.id);
  if (q.kind === 'daily') {
    q.row = { ...q.row, made_at: q.row.made_at || new Date().toISOString() };
    const view = await deviceView();
    const month = `${q.date.slice(0, 7)}-01`;
    if (view && !view.bills?.some((b: any) => b.supplier_id === q.row.supplier_id && b.month === month)) q.temp_bill = tempId();
  }
  if (q.kind === 'request') {
    if (createdTable(q.method, q.path) && q.temp_id == null) q.temp_id = tempId();
    if (q.method === 'POST' && q.path === '/bills/start-month') {
      const view = await deviceView();
      const month = `${String(q.body?.month).slice(0, 7)}-01`;
      const have = new Set((view?.bills || []).filter((b: any) => b.month === month).map((b: any) => b.supplier_id));
      q.temp_bills = Object.fromEntries((view?.suppliers || []).filter((s: any) => s.active && !have.has(s.id)).map((s: any) => [String(s.id), tempId()]));
    }
  }
  return q;
}

let toldAt = 0;
function tellQueued() {
  if (Date.now() - toldAt < 30e3) return; // once in a while, not on every row typed
  toldAt = Date.now();
  toast('No connection: saved on this device. It is sent when the connection is back.');
}

async function queue(q: Queued, delay = 300) {
  // Refused here already (closed month, too big a cut, a name that exists...): say so now, keep nothing.
  try {
    await deviceView(q);
  } catch (e) {
    if (e instanceof LocalError) throw new ApiError(e.status, e.message);
    throw e;
  }
  const { seq, ...item } = q as any;
  await outboxPut(item);
  announce();
  tellQueued();
  flushSoon(delay);
}

/** Save a change: straight to the server when it can, else into the outbox.
 *  Returns queued = true when it is waiting on this device, and the id of anything it made (temporary
 *  while waiting). Throws when the server (or, while waiting, this device) refuses it. */
export async function submit(c: Change): Promise<{ queued: boolean; result?: any; id?: number | null }> {
  // A kharcha that never left this device: removing it just takes it out of the outbox.
  if (c.kind === 'kharcha_del' && c.client_id && (await dropQueuedKharcha(c.client_id))) {
    announce();
    return { queued: false };
  }
  const q = await prepare(c);
  // Anything already waiting goes first, so saves reach the server in the order they were made.
  if (waitingOnly(await outboxItems()).length || !navigator.onLine) {
    await queue(q);
    return { queued: true, id: q.temp_id ?? null };
  }
  try {
    const result = await send(q);
    syncSoon();
    return { queued: false, result, id: result?.id ?? null };
  } catch (e) {
    if (!noServer(e)) throw e;
    await queue(q, 5000);
    return { queued: true, id: q.temp_id ?? null };
  }
}

/** A plain save (POST/PUT/PATCH/DELETE `path`), the same as api() but able to wait offline. */
export function save(method: string, path: string, body: unknown, label: string) {
  return submit({ kind: 'request', method, path, body, label });
}

async function dropQueuedKharcha(clientId: string): Promise<boolean> {
  for (const it of waitingOnly((await outboxItems()) as Queued[])) {
    if (it.kind === 'daily' && it.row.kharcha_client_id === clientId) {
      const { add_kharcha, kharcha_tag, kharcha_note, kharcha_client_id, ...rest } = it.row;
      if ('weight' in rest || 'rate' in rest || 'note' in rest) await outboxPut({ ...it, row: rest, label: it.label.replace(/, kharcha [^,]*$/, '') });
      else await outboxDelete(it.seq);
      return true;
    }
    if (it.kind === 'request' && it.method === 'POST' && /^\/bills\/-?\d+\/kharcha$/.test(it.path || '') && it.body?.client_id === clientId) {
      await outboxDelete(it.seq);
      return true;
    }
  }
  return false;
}

let syncTimer: ReturnType<typeof setTimeout> | null = null;
/** Saves went straight to the server: bring the device copy up to date a little later (once for many saves). */
function syncSoon() {
  if (syncTimer) clearTimeout(syncTimer);
  syncTimer = setTimeout(() => { syncTimer = null; syncNow(); }, 4000);
}

let flushing = false;
let timer: ReturnType<typeof setTimeout> | null = null;
export function flushSoon(ms = 300) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; flush(); }, ms);
}

/** Send what is waiting, oldest first. Stops at the first save that cannot reach the server. */
export async function flush(): Promise<OutboxStatus> {
  if (flushing) return announce(true);
  flushing = true;
  let sent = 0;
  try {
    for (;;) {
      // Read again each time: sending one can change the ones after it (real ids).
      const it = waitingOnly((await outboxItems()) as Queued[]).sort((a, b) => a.seq - b.seq)[0];
      if (!it) break;
      announce(true);
      try {
        const res = await send(it);
        const done = await adoptIds(it, res);
        await outboxPut({ ...done, sent: true, sent_at: Date.now() });
        sent++;
      } catch (e) {
        if (noServer(e) || (e instanceof ApiError && e.status === 401)) break;
        const tries = (it.tries || 0) + 1;
        // The server refused it (closed month, removed supplier...), or kept failing: put it aside to check.
        if ((e instanceof ApiError && e.status >= 400 && e.status < 500) || tries >= 5) {
          await problemAdd({ label: it.label, what: 'refused', message: (e as Error).message, change: it });
          await outboxDelete(it.seq);
        } else {
          await outboxPut({ ...it, tries });
          break;
        }
      }
    }
  } finally {
    flushing = false;
  }
  const st = await announce(false);
  if (sent) {
    window.dispatchEvent(new CustomEvent('petalboard:outbox-sent', { detail: { sent } }));
    await syncNow();
  }
  return st;
}

/** True when saves are waiting to be sent: then reads come from this device, so they show. */
export async function hasWaiting(): Promise<boolean> {
  return waitingOnly(await outboxItems()).length > 0;
}

let started = false;
export function startOutbox() {
  if (started) return;
  started = true;
  flushSoon(1000);
  window.addEventListener('online', () => flushSoon(500));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') flushSoon(500); });
  setInterval(() => { if (navigator.onLine) flush(); }, 60e3);
}
