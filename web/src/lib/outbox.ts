// Changes made on this device go to the server through here. With no connection they wait in an
// "outbox" (IndexedDB, see offline.ts), show on screen at once (local.ts applyPending), and are sent in
// order when the connection comes back: when the app opens, when the phone goes online, every minute.
//
// Each change carries its own ids and the time it was made, so:
//   - a change sent twice (the reply was lost) is saved once (kharcha client_id; a day is just set again);
//   - two devices changing the same day: the change made later wins. A change that lost, or that replaced
//     someone else's newer-looking value, is listed under "Sync problems" in Settings to check.
// Closing months, khaata close and advances stay online-only (money decisions that need fresh balances).
import { api, ApiError } from './api';
import { outboxDelete, outboxItems, outboxPut, problemAdd, problemsAll, syncNow } from './offline';

export type DailyRow = {
  supplier_id: number;
  weight?: number | null; rate?: number | null; note?: string | null;
  add_kharcha?: number | null; kharcha_tag?: string | null; kharcha_note?: string | null; kharcha_client_id?: string | null;
  made_at?: string; base?: { weight: number | null; rate: number | null } | null;
};
export type Change =
  | { kind: 'daily'; date: string; row: DailyRow; label: string }
  | { kind: 'kharcha_del'; id?: number | null; client_id?: string | null; label: string };
type Queued = Change & { seq: number; tries?: number };

export const newId = () =>
  (crypto as any).randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

/** No server right now: a dropped connection, or the server's app is down behind the proxy. */
const noServer = (e: unknown) => e instanceof ApiError && (e.status === 0 || [502, 503, 504].includes(e.status));

export type OutboxStatus = { waiting: number; problems: number; sending: boolean };
async function announce(sending = false) {
  const detail: OutboxStatus = { waiting: (await outboxItems()).length, problems: (await problemsAll()).length, sending };
  window.dispatchEvent(new CustomEvent('petalboard:outbox', { detail }));
  return detail;
}
export const outboxStatus = () => announce(flushing);

async function send(c: Change): Promise<any> {
  if (c.kind === 'daily') {
    const res = await api(`/daily/${c.date}`, { method: 'PUT', body: { rows: [c.row] } });
    for (const x of res?.conflicts || []) {
      await problemAdd({ label: c.label, what: x.what, yours: x.yours, theirs: x.theirs, date: c.date, supplier_id: c.row.supplier_id });
    }
    if (res?.skipped_closed?.length) {
      await problemAdd({ label: c.label, what: 'closed', date: c.date, supplier_id: c.row.supplier_id,
        message: 'This month was closed before the change arrived, so it was not saved.' });
    }
    return res;
  }
  const path = c.id != null && c.id > 0 ? `/kharcha/${c.id}` : `/kharcha/by-client/${encodeURIComponent(c.client_id || '')}`;
  try {
    return await api(path, { method: 'DELETE' });
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null; // already gone
    throw e;
  }
}

/** Save a change: straight to the server when it can, else into the outbox.
 *  Returns queued = true when it is waiting on this device. Throws only when the server refused it. */
export async function submit(c: Change): Promise<{ queued: boolean; result?: any }> {
  if (c.kind === 'daily' && !c.row.made_at) c.row.made_at = new Date().toISOString();
  // A kharcha that never left this device: removing it just takes it out of the outbox.
  if (c.kind === 'kharcha_del' && c.client_id && (await dropQueuedKharcha(c.client_id))) {
    announce();
    return { queued: false };
  }
  // Anything already waiting goes first, so changes reach the server in the order they were made.
  if ((await outboxItems()).length || !navigator.onLine) {
    await outboxPut({ ...c });
    announce();
    flushSoon();
    return { queued: true };
  }
  try {
    return { queued: false, result: await send(c) };
  } catch (e) {
    if (!noServer(e)) throw e;
    await outboxPut({ ...c });
    announce();
    flushSoon(5000);
    return { queued: true };
  }
}

async function dropQueuedKharcha(clientId: string): Promise<boolean> {
  for (const it of (await outboxItems()) as Queued[]) {
    if (it.kind !== 'daily' || it.row.kharcha_client_id !== clientId) continue;
    const { add_kharcha, kharcha_tag, kharcha_note, kharcha_client_id, ...rest } = it.row;
    if ('weight' in rest || 'rate' in rest || 'note' in rest) await outboxPut({ ...it, row: rest, label: it.label.replace(/, kharcha [^,]*$/, '') });
    else await outboxDelete(it.seq);
    return true;
  }
  return false;
}

let flushing = false;
let timer: ReturnType<typeof setTimeout> | null = null;
export function flushSoon(ms = 300) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; flush(); }, ms);
}

/** Send what is waiting, oldest first. Stops at the first change that cannot reach the server. */
export async function flush(): Promise<OutboxStatus> {
  if (flushing) return announce(true);
  flushing = true;
  let sent = 0;
  try {
    const items = ((await outboxItems()) as Queued[]).sort((a, b) => a.seq - b.seq);
    if (items.length) announce(true);
    for (const it of items) {
      try {
        await send(it);
        await outboxDelete(it.seq);
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
    syncNow();
  }
  return st;
}

/** True when something is waiting: then reads come from this device, so the waiting changes show. */
export async function hasWaiting(): Promise<boolean> {
  return (await outboxItems()).length > 0;
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
