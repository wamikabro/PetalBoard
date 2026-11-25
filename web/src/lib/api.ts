// Talks to the FastAPI backend. The login token lives in localStorage.
// The live API on the Oracle server. PUBLIC_API_URL (web/.env) overrides it, e.g. http://127.0.0.1:8000 for local dev.
const PRODUCTION_API = 'https://api.129.213.60.139.sslip.io';
export const API_URL: string = (import.meta.env.PUBLIC_API_URL || PRODUCTION_API).replace(/\/$/, '');

import { deviceAnswer, saveResponse } from './offline';
import { flush, hasWaiting } from './outbox';

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
export function clearLogin() {
  try { store()?.removeItem(TOKEN_KEY); store()?.removeItem(USER_KEY); } catch { /* ignore */ }
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
  const send = () => fetch(url, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  const keep = method === 'GET' && !path.startsWith('/sync') && !path.startsWith('/export') && !path.startsWith('/auth');
  /** No server: show what was saved on this device the last time, if anything. */
  const fromDevice = async (why: string) => {
    let saved: { data: any; at: number } | null = null;
    try {
      saved = keep ? await deviceAnswer(url) : null;
    } catch (e: any) {
      throw new ApiError(e?.status || 0, e?.message || why);
    }
    if (!saved) throw new ApiError(0, why);
    window.dispatchEvent(new CustomEvent('petalboard:offline', { detail: { at: saved.at } }));
    return saved.data as T;
  };
  // Changes made on this device still waiting: send them first, so the answer includes them.
  // If they cannot go yet, answer from this device (it shows them on top of the saved data).
  if (keep && (await hasWaiting())) {
    const st = await flush();
    if (st.waiting) return fromDevice('Changes are waiting to be sent.');
  }
  let res: Response;
  try {
    res = await send();
  } catch {
    // A dropped connection: try reads once more. Saves are not repeated, so nothing is entered twice.
    if (method !== 'GET') throw new ApiError(0, 'Cannot reach the server. Check the internet connection and try again.');
    try {
      await new Promise((r) => setTimeout(r, 800));
      res = await send();
    } catch {
      return fromDevice('Cannot reach the server. Check the internet connection.');
    }
  }
  // The server is there but its app is down (proxy error): same as no server, for reading.
  if (method === 'GET' && [502, 503, 504].includes(res.status)) return fromDevice(`The server is not answering (${res.status}).`);
  if (res.status === 401 && !path.startsWith('/auth/login')) {
    clearLogin();
    location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
    throw new ApiError(401, 'Please log in');
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
