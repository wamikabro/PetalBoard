// Compares the phone's arithmetic (src/lib/local.ts) with the server's, on every bill and month.
//
//   node scripts/check-local.mjs [API url] [username] [password]
//   (defaults: http://127.0.0.1:8000 owner petalboard-dev — the local dev database)
//
// Fetches the full table copy (/sync, as a phone does), works out each answer locally and
// compares it with the server's JSON. Prints every difference; exits 1 if there is any.
import { localAnswer } from '../src/lib/local.ts';

const [API = 'http://127.0.0.1:8000', USER = 'owner', PASS = 'petalboard-dev'] = process.argv.slice(2);

const login = await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }),
}).then((r) => r.json());
if (!login.token) { console.error('Login failed', login); process.exit(2); }
const H = { Authorization: `Bearer ${login.token}` };
const get = async (path) => {
  const r = await fetch(API + path, { headers: H });
  return { status: r.status, body: await r.json().catch(() => null) };
};

const sync = (await get('/sync?since=0')).body;
const tables = sync.tables;

/** Differences between two JSON values; numbers may differ by `tol` (rounding of averages). */
function diff(a, b, path, out, tol) {
  if (typeof a === 'number' && typeof b === 'number') {
    if (Math.abs(a - b) > tol(path)) out.push(`${path}: server ${a}, local ${b}`);
    return;
  }
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') {
    if (a !== b) out.push(`${path}: server ${JSON.stringify(a)}, local ${JSON.stringify(b)}`);
    return;
  }
  if (Array.isArray(a) !== Array.isArray(b)) { out.push(`${path}: array vs object`); return; }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (!(k in a)) out.push(`${path}.${k}: only local`);
    else if (!(k in b)) out.push(`${path}.${k}: only on server`);
    else diff(a[k], b[k], `${path}.${k}`, out, tol);
  }
}
// avg_rate is a rounded float: allow one step of the last digit; everything else must match exactly.
const tol = (p) => (/avg_rate$/.test(p) ? 0.1001 : /weight$/.test(p) ? 0.0005 : 0);

const paths = ['/bills/months', '/bills', '/reports/overview', '/reports/balances', '/flowers', '/settings',
  '/suppliers', '/suppliers?include_inactive=true', '/investors'];
for (const s of tables.suppliers) paths.push(`/suppliers/${s.id}`);
for (const i of tables.investors) paths.push(`/investors/${i.id}`);
const months = (await get('/bills/months')).body;
for (const m of months) {
  paths.push(`/bills?month=${m}`, `/reports/month?month=${m}`, `/reports/entries?date_from=${m}-01&date_to=${m}-28`);
  for (const day of ['01', '15', '28']) paths.push(`/daily/${m}-${day}`);
}
paths.push(`/daily/${new Date().toISOString().slice(0, 10)}`);
paths.push(`/reports/entries?date_from=2025-01-01&date_to=2026-12-31`);
for (const s of tables.suppliers.slice(0, 5)) paths.push(`/bills?supplier_id=${s.id}`, `/reports/entries?date_from=2025-01-01&date_to=2026-12-31&supplier_id=${s.id}`);
for (const f of tables.flowers) paths.push(`/reports/entries?date_from=2026-01-01&date_to=2026-12-31&flower_id=${f.id}`);
for (const b of tables.bills) paths.push(`/bills/${b.id}`, `/bills/${b.id}/neighbours`);

let bad = 0;
for (const p of paths) {
  const server = await get(p);
  if (server.status !== 200) { console.log(`SKIP ${p}: server ${server.status}`); continue; }
  const out = [];
  try {
    diff(server.body, localAnswer(p, tables), '', out, tol);
  } catch (e) {
    out.push(`local error: ${e.message}`);
  }
  if (out.length) {
    bad++;
    console.log(`DIFF ${p}\n  ${out.slice(0, 8).join('\n  ')}${out.length > 8 ? `\n  … ${out.length - 8} more` : ''}`);
  }
}
console.log(`${paths.length} answers compared, ${bad} differ.`);
process.exit(bad ? 1 : 0);
