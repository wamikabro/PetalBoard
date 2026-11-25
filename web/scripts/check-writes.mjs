// Checks that the device works out saves the way the server does (offline saves, src/lib/local.ts applyPending).
//
//   node scripts/check-writes.mjs [API url] [username] [password]
//   (defaults: http://127.0.0.1:8002 owner petalboard-dev)
//
// IT CHANGES THE DATA: run it against a throwaway copy, e.g.
//   psql -p 55432 -U postgres -c "CREATE DATABASE petalboard_check TEMPLATE petalboard_dev"
//   (api/) DATABASE_URL=postgresql://postgres@127.0.0.1:55432/petalboard_check python -m uvicorn main:app --port 8002
//
// Each scenario is a list of saves as the outbox keeps them. They are applied to the device copy (/sync) and
// sent to the server in order (temporary ids swapped for the real ones as the answers come back); then the
// pages they touch are compared. Exits 1 on any difference.
import { applyPending, createdIds, createdTable, localAnswer, remapItem } from '../src/lib/local.ts';

const [API = 'http://127.0.0.1:8002', USER = 'owner', PASS = 'petalboard-dev'] = process.argv.slice(2);
const login = await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: USER, password: PASS }),
}).then((r) => r.json());
if (!login.token) { console.error('Login failed', login); process.exit(2); }
const H = { Authorization: `Bearer ${login.token}`, 'Content-Type': 'application/json' };
const call = async (method, path, body, extra = {}) => {
  const r = await fetch(API + path, { method, headers: { ...H, ...extra }, body: body !== undefined ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json().catch(() => null) };
};
const ctx = { userId: login.user.id, now: new Date().toISOString() };
let tempN = -5_000_000;
const temp = () => tempN--;
const madeAt = () => new Date().toISOString();

function diff(a, b, path, out) {
  const key = path.split('.').pop();
  if (/_at$|^rev$/.test(key)) return;
  if (typeof b === 'number' && b < 0 && (key === 'id' || key.endsWith('_id') || /^(prev|next)$/.test(key))) return; // temporary id here
  if (typeof a === 'number' && typeof b === 'number') {
    const tol = /avg_rate$/.test(path) ? 0.1001 : /weight$/.test(path) ? 0.0005 : 0;
    if (Math.abs(a - b) > tol) out.push(`${path}: server ${a}, local ${b}`);
    return;
  }
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') {
    if (a !== b) out.push(`${path}: server ${JSON.stringify(a)}, local ${JSON.stringify(b)}`);
    return;
  }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (/_at$|^rev$|^_?pending$/.test(k)) continue; // times, change numbers and the device's own "waiting" mark
    if (!(k in a)) out.push(`${path}.${k}: only local`);
    else if (!(k in b)) out.push(`${path}.${k}: only on server`);
    else diff(a[k], b[k], `${path}.${k}`, out);
  }
}

function send(it) {
  const h = { 'X-Change-Id': `check-${Math.random().toString(36).slice(2)}` };
  if (it.kind === 'daily') return call('PUT', `/daily/${it.date}`, { rows: [it.row] }, h);
  if (it.kind === 'kharcha_del') return call('DELETE', it.id ? `/kharcha/${it.id}` : `/kharcha/by-client/${it.client_id}`, undefined, h);
  return call(it.method, it.path, it.body, h);
}

let bad = 0, compared = 0;
/** build(T, ids) returns { items, paths: (real) => [path, ...] , refuse?: true } */
async function scenario(name, build) {
  const T = (await call('GET', '/sync?since=0')).body.tables;
  const { items, paths, expectRefused = [] } = build(T);
  for (const it of items) if (it.kind === 'request' && createdTable(it.method, it.path) && it.temp_id == null) it.temp_id = temp();
  const problems = [];
  // On the device: each save checked as it would be when queued.
  const localRefused = new Set();
  for (let i = 0; i < items.length; i++) {
    try { applyPending(T, items.slice(0, i + 1), ctx, true); } catch (e) { localRefused.add(i); }
  }
  const after = applyPending(T, items, ctx);
  // On the server, in order.
  const queue = items.map((x) => structuredClone(x));
  const map = new Map();
  for (let i = 0; i < queue.length; i++) {
    const res = await send(queue[i]);
    // A daily row on a closed month is skipped by the server (200) and refused straight away on the device.
    const refused = res.status >= 400 || (queue[i].kind === 'daily' && res.body?.skipped_closed?.length > 0);
    if (refused !== localRefused.has(i)) problems.push(`save ${i} (${queue[i].path || queue[i].kind}): server ${res.status} ${JSON.stringify(res.body?.detail ?? '')}, device ${localRefused.has(i) ? 'refused' : 'took it'}`);
    if (refused !== expectRefused.includes(i)) problems.push(`save ${i}: expected ${expectRefused.includes(i) ? 'refused' : 'taken'}, server ${res.status} ${JSON.stringify(res.body?.detail ?? '')}`);
    for (const [t, tmp, real] of createdIds(queue[i], res.body)) {
      map.set(tmp, real);
      for (let j = i + 1; j < queue.length; j++) queue[j] = remapItem(queue[j], t, tmp, real);
    }
  }
  const real = (p) => p.replace(/-\d{5,}/g, (s) => String(map.get(Number(s)) ?? s));
  for (const p of paths) {
    compared++;
    const server = await call('GET', real(p));
    if (server.status !== 200) { problems.push(`${p}: server ${server.status}`); continue; }
    const out = [];
    try { diff(server.body, localAnswer(p, after), '', out); } catch (e) { out.push(`local error: ${e.message}`); }
    if (out.length) problems.push(`${p}\n    ${out.slice(0, 10).join('\n    ')}${out.length > 10 ? `\n    … ${out.length - 10} more` : ''}`);
  }
  if (problems.length) bad++;
  console.log(`${problems.length ? 'DIFF' : 'ok  '} ${name}${problems.length ? '\n  ' + problems.join('\n  ') : ''}`);
  return map;
}

// ---------- the data to work on ----------
const T0 = (await call('GET', '/sync?since=0')).body.tables;
const months = [...new Set(T0.bills.map((b) => b.month))].sort();
const lastMonth = months[months.length - 1];
const nextOf = (m) => { const [y, mo] = m.split('-').map(Number); return mo === 12 ? `${y + 1}-01` : `${y}-${String(mo + 1).padStart(2, '0')}`; };
const NEXT = nextOf(lastMonth.slice(0, 7)), AFTER = nextOf(NEXT);
const adv = (sid) => T0.advances.filter((a) => a.supplier_id === sid).reduce((s, a) => s + Number(a.amount), 0);
const lastBills = localAnswer(`/bills?month=${lastMonth.slice(0, 7)}`, T0);
const X = lastBills.find((b) => b.status === 'open' && b.final > 5000 && adv(b.supplier_id) > 0) || lastBills.find((b) => b.status === 'open' && b.final > 5000);
const Y = lastBills.find((b) => b.status === 'open' && b.id !== X.id && b.final > 0);
const flower = T0.flowers[0];
console.log(`Working on: last month ${lastMonth.slice(0, 7)}, bill ${X.id} (${X.supplier_name}, final ${X.final}, advance ${adv(X.supplier_id)}), bill ${Y.id} (${Y.supplier_name})`);

const sTemp = temp(), bTemp = temp(), advTemp = temp(), invTemp = temp(), shareTemp = temp(), payTemp = temp(), fTemp = temp();
let ids = new Map();

ids = new Map([...ids, ...await scenario('shop details', () => ({
  items: [{ kind: 'request', method: 'PATCH', path: '/settings', body: { proprietor1_phone: '0300-0000000' } }],
  paths: ['/settings'],
}))]);

ids = new Map([...ids, ...await scenario('flowers: add and rename', () => ({
  items: [
    { kind: 'request', method: 'POST', path: '/flowers', body: { name: 'Check Flower', name_ur: 'ٹیسٹ' }, temp_id: fTemp },
    { kind: 'request', method: 'PATCH', path: `/flowers/${fTemp}`, body: { name: 'Check Flower', name_ur: 'ٹیسٹ ۲' } },
    { kind: 'request', method: 'PATCH', path: `/flowers/${fTemp}`, body: { name_ur: 'no name' } },
    { kind: 'request', method: 'POST', path: '/flowers', body: { name: 'check flower' } },
    { kind: 'request', method: 'PATCH', path: `/flowers/${fTemp}`, body: { name: 'Check Flower', active: false } },
  ],
  expectRefused: [2, 3],
  paths: ['/flowers'],
}))]);

ids = new Map([...ids, ...await scenario('new supplier, daily entry, advance, kharcha, bill settings', (T) => ({
  items: [
    { kind: 'request', method: 'POST', path: '/suppliers', body: { name: 'Check Supplier', flower_id: flower.id, phone: null }, temp_id: sTemp },
    { kind: 'request', method: 'PATCH', path: `/suppliers/${sTemp}`, body: { phone: '0311-1111111', place: 'Hala' } },
    { kind: 'daily', date: `${NEXT}-03`, temp_bill: bTemp, row: { supplier_id: sTemp, weight: 12.5, rate: 110, note: null, add_kharcha: 300, kharcha_tag: 'B', kharcha_client_id: 'chk-k1', made_at: madeAt(), base: { weight: null, rate: null } } },
    { kind: 'daily', date: `${NEXT}-04`, row: { supplier_id: sTemp, weight: 7.25, rate: 120, note: null, made_at: madeAt(), base: { weight: null, rate: null } } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sTemp}/advances`, body: { on_date: `${NEXT}-01`, amount: 5000, kind: 'given', note: 'check' }, temp_id: advTemp },
    { kind: 'request', method: 'POST', path: `/suppliers/${sTemp}/advances`, body: { on_date: `${NEXT}-02`, amount: 1000, kind: 'recovered', note: null } },
    { kind: 'request', method: 'POST', path: `/bills/${bTemp}/kharcha`, body: { day: 5, amount: 200, tag: null, note: null, client_id: 'chk-k2' } },
    { kind: 'request', method: 'POST', path: `/bills/${bTemp}/kharcha`, body: { day: 40, amount: 200, tag: null, note: null, client_id: 'chk-k3' } },
    { kind: 'request', method: 'PATCH', path: `/bills/${bTemp}`, body: { commission_pct: 8, opening_due: 0, note: 'checked' } },
    { kind: 'request', method: 'POST', path: '/suppliers', body: { name: 'check supplier' } },
  ],
  expectRefused: [7, 9],
  paths: ['/suppliers', '/suppliers?include_inactive=true', `/suppliers/${sTemp}`, `/bills/${bTemp}`, `/bills/${bTemp}/neighbours`,
    `/daily/${NEXT}-03`, `/daily/${NEXT}-04`, `/bills?month=${NEXT}`, `/bills/months`, `/reports/month?month=${NEXT}`, '/reports/balances', '/reports/overview',
    `/reports/entries?date_from=${NEXT}-01&date_to=${NEXT}-28`],
}))]);

const cut = Math.max(0, Math.floor(Math.min(adv(X.supplier_id), X.final - 1000) / 2));
ids = new Map([...ids, ...await scenario('edit and close a month with an advance cut', (T) => ({
  items: [
    { kind: 'daily', date: `${lastMonth.slice(0, 7)}-02`, row: { supplier_id: X.supplier_id, weight: 3.5, rate: 95, note: null, made_at: madeAt(), base: null } },
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/kharcha`, body: { day: 9, amount: 450, tag: 'B', note: null, client_id: 'chk-k4' } },
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/close`, body: { paid_cash: 1000, advance_cut: adv(X.supplier_id) + X.final } },
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/close`, body: { paid_cash: 1000, advance_cut: cut, note: 'closed in check' } },
    { kind: 'daily', date: `${lastMonth.slice(0, 7)}-03`, row: { supplier_id: X.supplier_id, weight: 1, rate: 1, note: null, made_at: madeAt(), base: null } },
  ],
  expectRefused: [2, 4],
  paths: [`/bills/${X.id}`, `/suppliers/${X.supplier_id}`, `/bills?month=${lastMonth.slice(0, 7)}`, '/reports/balances', '/suppliers'],
}))]);

const startTemps = {};
ids = new Map([...ids, ...await scenario('start the next month', (T) => {
  const have = new Set(T.bills.filter((b) => b.month === `${NEXT}-01`).map((b) => b.supplier_id));
  for (const s of T.suppliers) if (s.active && !have.has(s.id)) startTemps[s.id] = temp();
  return {
    items: [{ kind: 'request', method: 'POST', path: '/bills/start-month', body: { month: NEXT }, temp_bills: startTemps }],
    paths: [`/bills?month=${NEXT}`, `/bills/${startTemps[X.supplier_id]}`, `/suppliers/${X.supplier_id}`, '/reports/overview', `/daily/${NEXT}-10`],
  };
})]);
const nextX = ids.get(startTemps[X.supplier_id]);

ids = new Map([...ids, ...await scenario('reopen the closed month (next month opening follows)', () => ({
  items: [
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/reopen` },
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/reopen` },
    { kind: 'request', method: 'POST', path: `/bills/${X.id}/close`, body: { paid_cash: 0, advance_cut: 0 } },
    { kind: 'request', method: 'POST', path: '/bills', body: { supplier_id: Y.supplier_id, month: AFTER }, temp_id: temp() },
  ],
  expectRefused: [1],
  paths: [`/bills/${X.id}`, `/bills/${nextX}`, `/suppliers/${X.supplier_id}`, `/suppliers/${Y.supplier_id}`, `/bills?month=${AFTER}`],
}))]);

const sReal = ids.get(sTemp);
ids = new Map([...ids, ...await scenario('investor, share, money in and out', () => ({
  items: [
    { kind: 'request', method: 'POST', path: '/investors', body: { name: 'Check Investor', phone: null }, temp_id: invTemp },
    { kind: 'request', method: 'PATCH', path: `/investors/${invTemp}`, body: { phone: '0322-2222222', notes: 'n' } },
    { kind: 'request', method: 'POST', path: '/khaata-shares', body: { supplier_id: sReal, investor_id: invTemp, pct: 30, from_month: `${NEXT}`, to_month: null, note: null }, temp_id: shareTemp },
    { kind: 'request', method: 'POST', path: '/khaata-shares', body: { supplier_id: sReal, investor_id: invTemp, pct: 10, from_month: `${AFTER}`, to_month: null, note: null } },
    { kind: 'request', method: 'POST', path: `/investors/${invTemp}/entries`, body: { kind: 'invested', amount: 100000, on_date: `${NEXT}-05`, supplier_id: sReal, note: null } },
    { kind: 'request', method: 'POST', path: `/investors/${invTemp}/entries`, body: { kind: 'payout', amount: 300, on_date: `${NEXT}-28`, supplier_id: null, note: null }, temp_id: payTemp },
    { kind: 'request', method: 'POST', path: `/investors/${invTemp}/entries`, body: { kind: 'payout', amount: 100, on_date: `${NEXT}-29`, supplier_id: null, note: null } },
    { kind: 'request', method: 'PATCH', path: `/khaata-shares/${shareTemp}`, body: { pct: 25 } },
    { kind: 'request', method: 'DELETE', path: `/investor-entries/${payTemp}` },
  ],
  expectRefused: [3],
  paths: ['/investors', `/investors/${invTemp}`, `/suppliers/${sReal}`],
}))]);

ids = new Map([...ids, ...await scenario('deletions', (T) => {
  const k = T.kharcha.find((x) => x.bill_id === Y.id);
  return {
    items: [
      { kind: 'request', method: 'DELETE', path: `/advances/${ids.get(advTemp)}` },
      { kind: 'kharcha_del', id: k.id },
      { kind: 'kharcha_del', client_id: 'chk-k2' },
      { kind: 'request', method: 'DELETE', path: `/khaata-shares/${ids.get(shareTemp)}` },
      { kind: 'request', method: 'DELETE', path: `/advances/${T.advances.find((a) => a.kind === 'bill_cut')?.id ?? 0}` },
    ],
    expectRefused: T.advances.some((a) => a.kind === 'bill_cut') ? [4] : [4],
    paths: [`/suppliers/${sReal}`, `/bills/${Y.id}`, `/bills/${ids.get(bTemp)}`, `/investors/${ids.get(invTemp)}`],
  };
})]);

ids = new Map([...ids, ...await scenario('khaata: close (write off), reopen, close, renew', () => ({
  items: [
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/advances`, body: { on_date: `${NEXT}-06`, amount: 2500, kind: 'given', note: null } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/close`, body: { on_date: `${NEXT}-20`, reason: 'test', write_off: false } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/close`, body: { on_date: `${NEXT}-20`, reason: 'test', write_off: true } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/reopen` },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/close`, body: { on_date: `${NEXT}-21`, reason: null, write_off: true } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/renew`, body: { on_date: `${NEXT}-20`, terms: 'x' } },
    { kind: 'request', method: 'POST', path: `/suppliers/${sReal}/khaata/renew`, body: { on_date: `${NEXT}-25`, terms: 'new terms', commission_pct: 8, advance: 3000 } },
  ],
  expectRefused: [1, 5],
  paths: [`/suppliers/${sReal}`, '/suppliers?include_inactive=true', `/investors/${ids.get(invTemp)}`, '/reports/balances'],
}))]);

console.log(`${compared} answers compared, ${bad} scenario(s) differ.`);
process.exit(bad ? 1 : 0);
