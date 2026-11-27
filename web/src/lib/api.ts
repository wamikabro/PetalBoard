// Talks to the FastAPI backend. The login token lives in localStorage.
// The live API on the Oracle server. PUBLIC_API_URL (web/.env) overrides it, e.g. http://127.0.0.1:8000 for local dev.
const PRODUCTION_API = 'https://api.129.213.60.139.sslip.io';
export const API_URL: string = (import.meta.env.PUBLIC_API_URL || PRODUCTION_API).replace(/\/$/, '');

import { deviceAnswer, saveResponse } from './offline';
import { flush, flushSoon, hasWaiting } from './outbox';

const TOKEN_KEY = 'petalboard_token';
const USER_KEY = 'petalboard_user';

export type Role = 'owner' | 'manager' | 'clerk' | 'viewer';
export type User = { id: number; username: string; display_name: string; role: Role };

function store(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}
export function getToken(): string | null {
  try { return store()?.getItem(TOKEN_KEY) ?? null; } catch { return null; }
}
export function getUser(): User | null {
  try { return JSON.parse(store()?.getItem(USER_KEY) || 'null'); } catch { return null; }
}
export function saveLogin(token: string, user: User) {
  try { store()?.setItem(TOKEN_KEY, token); store()?.setItem(USER_KEY, JSON.stringify(user)); } catch { /* private mode */ }
}
/** Log out on this device. Only the login goes: the shop's data saved on the device (offline.ts) and any
 *  changes still waiting to be sent stay, so the data can always be seen here, logged in or not. */
export function clearLogin() {
  try { store()?.removeItem(TOKEN_KEY); store()?.removeItem(USER_KEY); } catch { /* ignore */ }
}
/** True once this device holds a copy of the shop's data (set by offline.ts after the first full update). */
export const COPY_KEY = 'petalboard_device_copy';
export function hasDeviceCopy(): boolean {
  try { return store()?.getItem(COPY_KEY) === '1'; } catch { return false; }
}

// ---------- temporary ids (things made offline) and the real ids the server gave them ----------
const IDMAP_KEY = 'petalboard_idmap';
function idMap(): Record<string, number> {
  try { return JSON.parse(store()?.getItem(IDMAP_KEY) || '{}'); } catch { return {}; }
}
export function rememberId(temp: number, real: number) {
  try { const m = idMap(); m[String(temp)] = real; store()?.setItem(IDMAP_KEY, JSON.stringify(m)); } catch { /* ignore */ }
}
export function realId<T>(v: T): T | number {
  const n = Number(v);
  return Number.isInteger(n) && n < 0 ? idMap()[String(n)] ?? v : v;
}
export const realIds = (path: string) => path.replace(/\/(-\d+)(?=\/|$|[?])/g, (s, n) => `/${realId(Number(n))}`)
  .replace(/([?&][a-z_]*id=)(-\d+)/g, (s, k, n) => `${k}${realId(Number(n))}`);

const RANK: Record<Role, number> = { viewer: 0, clerk: 1, manager: 2, owner: 3 };
export function can(role: Role): boolean {
  const u = getUser();
  return !!u && RANK[u.role] >= RANK[role];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// The server failed a moment ago: for a short while reads come straight from this device instead of
// waiting for the network again on every page (the background update keeps checking for the server).
let downUntil = 0;
const DOWN_FOR = 20e3;
/** True while the server is known to be unreachable (saves then wait on this device at once). */
export const serverDown = () => !navigator.onLine || Date.now() < downUntil;
if (typeof window !== 'undefined') window.addEventListener('online', () => { downUntil = 0; });

/** fetch, giving up after `ms` (a phone on a weak signal can otherwise wait for minutes). */
function fetchWithin(url: string, init: RequestInit, ms: number): Promise<Response> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  return fetch(url, { ...init, signal: ctl.signal }).finally(() => clearTimeout(timer));
}

export async function api<T = any>(
  path: string,
  opts: { method?: string; body?: unknown; params?: Record<string, any>; headers?: Record<string, string> } = {},
): Promise<T> {
  // A page opened with the temporary id of something made offline: use the real id once the server gave one.
  path = realIds(path);
  if (opts.params) opts = { ...opts, params: Object.fromEntries(Object.entries(opts.params).map(([k, v]) => [k, /(^|_)id$/.test(k) ? realId(v) : v])) };
  let url = API_URL + path;
  if (opts.params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
    const s = q.toString();
    if (s) url += '?' + s;
  }
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const method = opts.method || 'GET';
  const keep = method === 'GET' && !path.startsWith('/sync') && !path.startsWith('/export') && !path.startsWith('/auth');
  const copy = keep && hasDeviceCopy();
  /** No server: show what this device has. */
  const fromDevice = async (why: string, loggedOut = false, quiet = false) => {
    let saved: { data: any; at: number } | null = null;
    try {
      saved = keep ? await deviceAnswer(url) : null;
    } catch (e: any) {
      throw new ApiError(e?.status || 0, e?.message || why);
    }
    if (!saved) throw new ApiError(loggedOut ? 401 : 0, why);
    if (!quiet) window.dispatchEvent(new CustomEvent(loggedOut ? 'petalboard:loggedout' : 'petalboard:offline', { detail: { at: saved.at } }));
    return saved.data as T;
  };
  // Logged out: the data on this device can still be seen; changes need a login.
  if (!token && !path.startsWith('/auth/login')) {
    if (keep) return fromDevice('Log in to see this page.', true);
    throw new ApiError(401, 'You are logged out. Log in to make changes.');
  }
  // No connection at all, or the server failed a moment ago: answer from this device at once.
  if (copy && (!navigator.onLine || Date.now() < downUntil)) return fromDevice('No connection to the server.');
  // Changes made on this device still waiting: send them first, so the answer includes them.
  // If they cannot go yet, answer from this device (it shows them on top of the saved data).
  if (keep && (await hasWaiting())) {
    // With a copy here, do not make the page wait for the sending: show it from this device now.
    if (copy) { flushSoon(0); return fromDevice('Changes are waiting to be sent.', false, navigator.onLine); }
    const st = await flush();
    if (st.waiting) return fromDevice('Changes are waiting to be sent.');
  }
  const init: RequestInit = { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined };
  // Reads with a copy here wait a few seconds at most; the rest get longer (a save, a download, the first visit).
  const send = () => fetchWithin(url, init, copy ? 6000 : method === 'GET' ? 30000 : 20000);
  let res: Response;
  try {
    res = await send();
  } catch {
    // Saves are not repeated here: outbox.ts keeps them and sends them again with the same change id.
    if (method !== 'GET') throw new ApiError(0, 'Cannot reach the server. Check the internet connection and try again.');
    try {
      if (copy) throw new Error('use the copy');
      await new Promise((r) => setTimeout(r, 800));
      res = await send();
    } catch {
      downUntil = Date.now() + DOWN_FOR;
      return fromDevice('Cannot reach the server. Check the internet connection.');
    }
  }
  // The server is there but its app is down (proxy error): same as no server, for reading.
  if (method === 'GET' && [502, 503, 504].includes(res.status)) {
    downUntil = Date.now() + DOWN_FOR;
    return fromDevice(`The server is not answering (${res.status}).`);
  }
  downUntil = 0;
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    // The login ended (logged out elsewhere, or not used for a year). The data on this device stays and
    // can still be seen; log in again to save.
    clearLogin();
    if (keep) return fromDevice('Your login has ended. Please log in again.', true);
    window.dispatchEvent(new CustomEvent('petalboard:loggedout', { detail: {} }));
    throw new ApiError(401, 'Your login has ended. Log in again to save.');
  }
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const d = data?.detail;
    const msg = typeof d === 'string' ? d : Array.isArray(d) ? d.map((x: any) => x.msg).join(', ') : `Error ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  if (keep) saveResponse(url, data);
  window.dispatchEvent(new CustomEvent('petalboard:online'));
  return data as T;
}
