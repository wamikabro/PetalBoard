// Talks to the FastAPI backend. The login token lives in localStorage.
export const API_URL: string = (import.meta.env.PUBLIC_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

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
  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method || 'GET',
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check the internet connection.');
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
