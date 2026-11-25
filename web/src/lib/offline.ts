// Keeps the shop's data on this device, so it stays safe and can be seen without the server.
//
// Two things are saved in the browser's IndexedDB:
//   1. A full copy of every table (GET /sync). This is the safe copy: if the server is ever lost,
//      the data is still here and can be downloaded from Settings.
//   2. The last answer to each page's data request, so pages open offline with what was last seen.
//
// Safety rules (owner, 2026-11-25: "a server error must never override the real data"):
//   - Only a complete, valid reply from the server changes anything here. Errors, timeouts,
//     empty or strange replies change nothing.
//   - Rows are only added or updated; a row is removed only when the server lists that exact row
//     as deleted, or during a full check after the counts below have passed.
//   - A reply from a different database (another db_id) is refused.
//   - If a reply would make the copy much smaller (e.g. 300 bills here, 20 on the server), it is
//     refused and a warning is shown. Someone must choose to accept it in Settings.
import { API_URL, getToken } from './api';

export const SYNC_TABLES = ['shop_settings', 'users', 'flowers', 'suppliers', 'investors', 'bills', 'bill_days', 'kharcha',
  'advances', 'khaata_shares', 'investor_entries', 'khaatas'] as const;
/** Tables watched for a sudden drop. */
const GUARDED = ['suppliers', 'bills', 'bill_days', 'kharcha', 'advances', 'investors', 'investor_entries'];
const NAMES: Record<string, string> = { suppliers: 'suppliers', bills: 'bills', bill_days: 'bill days', kharcha: 'kharcha entries',
  advances: 'advance entries', investors: 'investors', investor_entries: 'investor entries' };
const DB_NAME = 'petalboard-offline';
const OVERLAP = 500;              // ask again for a few recent changes, in case one was still being saved
const FULL_EVERY = 24 * 3600e3;   // a full check once a day
const SYNC_EVERY = 5 * 60e3;

export type SyncState = {
  dbId?: string;
  rev: number;
  lastSync?: number;
  lastFull?: number;
  lastTry?: number;
  counts: Record<string, number>;
  status: 'never' | 'ok' | 'offline' | 'blocked' | 'error';
  warning?: string;
  serverCounts?: Record<string, number>;
  prefetchedRev?: number;
};

// ---------- IndexedDB basics ----------

let dbPromise: Promise<IDBDatabase> | null = null;
function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('meta')) db.createObjectStore('meta');
      if (!db.objectStoreNames.contains('responses')) db.createObjectStore('responses');
      for (const t of SYNC_TABLES) if (!db.objectStoreNames.contains(t)) db.createObjectStore(t, { keyPath: '_key' });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => { dbPromise = null; reject(req.error); };
  });
  return dbPromise;
}
const done = <T>(req: IDBRequest<T>) => new Promise<T>((res, rej) => { req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
const finished = (tx: IDBTransaction) => new Promise<void>((res, rej) => { tx.oncomplete = () => res(); tx.onerror = () => rej(tx.error); tx.onabort = () => rej(tx.error); });

export async function getState(): Promise<SyncState> {
  try {
    const db = await openDb();
    const s = await done(db.transaction('meta').objectStore('meta').get('state'));
    return s || { rev: 0, counts: {}, status: 'never' };
  } catch {
    return { rev: 0, counts: {}, status: 'error', warning: 'This browser cannot save data on the device.' };
  }
}
async function setState(s: SyncState) {
  const db = await openDb();
  const tx = db.transaction('meta', 'readwrite');
  tx.objectStore('meta').put(s, 'state');
  await finished(tx);
  window.dispatchEvent(new CustomEvent('petalboard:sync', { detail: s }));
}

// ---------- saved page data ----------

const keyOf = (url: string) => url.startsWith(API_URL) ? url.slice(API_URL.length) : url;

export async function saveResponse(url: string, data: unknown) {
  try {
    const db = await openDb();
    const tx = db.transaction('responses', 'readwrite');
    tx.objectStore('responses').put({ data, at: Date.now() }, keyOf(url));
    await finished(tx);
  } catch { /* saving is a bonus, never an error */ }
}

export async function savedResponse(url: string): Promise<{ data: any; at: number } | null> {
  try {
    const db = await openDb();
    return (await done(db.transaction('responses').objectStore('responses').get(keyOf(url)))) || null;
  } catch {
    return null;
  }
}

// ---------- the full copy ----------

type Reply = {
  db_id: string; rev: number; since: number;
  tables: Record<string, any[]>; deletions: { table: string; key: string }[]; counts: Record<string, number>;
};

function validReply(d: any): d is Reply {
  return d && typeof d.db_id === 'string' && d.db_id.length >= 16 && Number.isInteger(d.rev) && d.rev >= 0
    && d.tables && typeof d.tables === 'object' && Array.isArray(d.deletions) && d.counts && typeof d.counts === 'object'
    && SYNC_TABLES.every((t) => Array.isArray(d.tables[t]) && Number.isInteger(d.counts[t])
      && d.tables[t].every((r: any) => r && typeof r._key === 'string'));
}

let running: Promise<SyncState> | null = null;

/** Bring the copy on this device up to date. Never throws; returns the new state.
 *  `accept` = someone chose in Settings to accept a reply that was refused (smaller or another database). */
export function syncNow(opts: { force?: boolean; accept?: boolean } = {}): Promise<SyncState> {
  if (!running) running = doSync(opts).finally(() => { running = null; });
  return running;
}

async function doSync({ force = false, accept = false }): Promise<SyncState> {
  const s = await getState();
  if (s.status === 'error' && s.warning?.includes('cannot save')) return s;
  const token = getToken();
  if (!token) return s;
  const full = force || accept || !s.rev || !s.lastFull || Date.now() - s.lastFull > FULL_EVERY;
  const since = full ? 0 : Math.max(0, s.rev - OVERLAP);
  let d: any;
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 60e3);
    const res = await fetch(`${API_URL}/sync?since=${since}`, { headers: { Authorization: `Bearer ${token}` }, signal: ctl.signal });
    clearTimeout(timer);
    if (!res.ok) {
      const next = { ...s, lastTry: Date.now(), status: s.status === 'blocked' ? s.status : 'offline' as const };
      await setState(next);
      return next;
    }
    d = await res.json();
  } catch {
    const next = { ...s, lastTry: Date.now(), status: s.status === 'blocked' ? s.status : 'offline' as const };
    await setState(next);
    return next;
  }
  if (!validReply(d)) {
    const next = { ...s, lastTry: Date.now(), status: s.status === 'blocked' ? s.status : 'error' as const,
      warning: s.status === 'blocked' ? s.warning : 'The server sent something unexpected. Nothing was changed on this device.' };
    await setState(next);
    return next;
  }
  // Another database (e.g. the server was reset or replaced): keep what is here.
  if (s.dbId && s.dbId !== d.db_id && !accept) {
    const next: SyncState = { ...s, lastTry: Date.now(), status: 'blocked', serverCounts: d.counts,
      warning: 'The server now has a different database from the one saved on this device. Nothing was changed here.' };
    await setState(next);
    return next;
  }
  // A big drop: refuse unless someone accepts it.
  if (!accept) {
    for (const t of GUARDED) {
      const here = s.counts[t] || 0, there = d.counts[t];
      if (here >= 20 && there < here * 0.75) {
        const next: SyncState = { ...s, lastTry: Date.now(), status: 'blocked', serverCounts: d.counts,
          warning: `The server now has ${there.toLocaleString()} ${NAMES[t] || t} but this device has ${here.toLocaleString()}. Nothing was changed here.` };
        await setState(next);
        return next;
      }
    }
  }
  const changedBills = new Set<number>(), changedSuppliers = new Set<number>();
  const db = await openDb();
  const tx = db.transaction([...SYNC_TABLES], 'readwrite');
  const replacing = !!(accept && s.dbId && s.dbId !== d.db_id);
  for (const t of SYNC_TABLES) {
    const store = tx.objectStore(t);
    if (replacing) store.clear();
    const keep = full ? new Set<string>() : null;
    for (const row of d.tables[t]) {
      store.put(row);
      keep?.add(row._key);
      if (t === 'bills') { changedBills.add(row.id); changedSuppliers.add(row.supplier_id); }
      if (t === 'bill_days' || t === 'kharcha') changedBills.add(row.bill_id);
      if (t === 'advances' || t === 'khaatas' || t === 'khaata_shares') changedSuppliers.add(row.supplier_id);
    }
    // A full check: rows that are here but no longer on the server (counts already checked above).
    if (keep && !replacing) {
      const keys = await done(store.getAllKeys());
      for (const k of keys) if (!keep.has(String(k))) store.delete(k);
    }
  }
  for (const del of d.deletions) {
    if ((SYNC_TABLES as readonly string[]).includes(del.table)) tx.objectStore(del.table).delete(del.key);
    if (del.table === 'bill_days' || del.table === 'kharcha' || del.table === 'bills') changedBills.add(-1); // something changed
  }
  await finished(tx);
  const counts: Record<string, number> = {};
  const ctx = db.transaction([...SYNC_TABLES]);
  for (const t of SYNC_TABLES) counts[t] = await done(ctx.objectStore(t).count());
  const next: SyncState = {
    dbId: d.db_id, rev: d.rev, lastSync: Date.now(), lastTry: Date.now(), lastFull: full ? Date.now() : s.lastFull,
    counts, status: 'ok', prefetchedRev: s.prefetchedRev,
  };
  await setState(next);
  if (next.prefetchedRev !== d.rev) prefetch(next, full || !s.prefetchedRev, changedBills, changedSuppliers);
  return next;
}

// ---------- save the pages' data ahead of time ----------

let prefetching = false;
async function prefetch(state: SyncState, everything: boolean, bills: Set<number>, suppliers: Set<number>) {
  if (prefetching) return;
  prefetching = true;
  try {
    const { api } = await import('./api');
    const get = (path: string, params?: Record<string, any>) => api(path, { params }).catch(() => null);
    const today = new Date();
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    for (const p of ['/settings', '/flowers', '/suppliers', '/reports/overview', '/reports/balances', '/investors']) await get(p);
    await get('/suppliers', { include_inactive: true });
    await get(`/daily/${iso(today)}`);
    await get(`/daily/${iso(new Date(today.getTime() - 864e5))}`);
    const months: any[] = (await get('/bills/months')) || [];
    const recent = months.map((m: any) => (typeof m === 'string' ? m : m.month)).filter(Boolean).sort().slice(-2);
    for (const m of recent) {
      const list: any[] = (await get('/bills', { month: m })) || [];
      await get('/reports/month', { month: m });
      for (const b of list) {
        if (!everything && !bills.has(b.id) && !bills.has(-1) && (await savedResponse(`/bills/${b.id}`))) continue;
        await get(`/bills/${b.id}`);
        await get(`/bills/${b.id}/neighbours`);
      }
    }
    const sups: any[] = (await get('/suppliers')) || [];
    for (const s of sups) {
      if (!everything && !suppliers.has(s.id) && (await savedResponse(`/suppliers/${s.id}`))) continue;
      await get(`/suppliers/${s.id}`);
    }
    for (const i of ((await get('/investors')) || []) as any[]) await get(`/investors/${i.id}`);
    await setState({ ...(await getState()), prefetchedRev: state.rev });
  } finally {
    prefetching = false;
  }
}

// ---------- a copy of this device's data, as a file ----------

/** Everything saved on this device, in the same layout as the server backup's _restore folder. */
export async function deviceCopy(): Promise<Blob> {
  const db = await openDb();
  const tx = db.transaction([...SYNC_TABLES, 'meta']);
  const out: Record<string, any> = { made_at: new Date().toISOString(), from: 'this device', state: await done(tx.objectStore('meta').get('state')) };
  for (const t of SYNC_TABLES) {
    out[t] = (await done(tx.objectStore(t).getAll())).map(({ _key, ...row }: any) => row);
  }
  return new Blob([JSON.stringify(out, null, 1)], { type: 'application/json' });
}

// ---------- run in the background ----------

let started = false;
export function startSync() {
  if (started || !getToken() || !('indexedDB' in window)) return;
  started = true;
  setTimeout(() => syncNow(), 1500);
  window.addEventListener('online', () => syncNow());
  setInterval(() => { if (document.visibilityState === 'visible') syncNow(); }, SYNC_EVERY);
}
