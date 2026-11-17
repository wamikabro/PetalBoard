// Talks to the FastAPI backend. The login token lives in localStorage.
// The live API on the Oracle server. PUBLIC_API_URL (web/.env) overrides it, e.g. http://127.0.0.1:8000 for local dev.
const PRODUCTION_API = 'https://api.129.213.60.139.sslip.io';
export const API_URL: string = (import.meta.env.PUBLIC_API_URL || PRODUCTION_API).replace(/\/$/, '');

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
  opts: { method?: string; body?: unknown; params?: Record<string, any> } = {},
): Promise<T> {
  let url = API_URL + path;
  if (opts.params) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
    const s = q.toString();
    if (s) url += '?' + s;
  }
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  const method = opts.method || 'GET';
  const send = () => fetch(url, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
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
      throw new ApiError(0, 'Cannot reach the server. Check the internet connection.');
    }
  }
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
  return data as T;
}
