// Works out the app's answers from the copy of the tables kept on this device, so pages that were
// never opened online (another month, any report range) still show offline.
//
// This is a copy of the server's arithmetic in api/billing.py, api/routes_bills.py and
// api/routes_reports.py, giving the same JSON. scripts/check-local.mjs compares the two on every bill
// and month; run it after changing either side.
//
// No imports and no browser APIs, so Node can run it directly for that check.

export type Tables = Record<string, any[]>;

/** Tables needed for the answers below. */
export const LOCAL_TABLES = ['shop_settings', 'users', 'flowers', 'suppliers', 'bills', 'bill_days', 'kharcha', 'advances',
  'khaatas', 'investors', 'khaata_shares', 'investor_entries'];

export class LocalError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

// ---------- arithmetic (api/billing.py) ----------

/** Decimal string or number -> exact integer at the given number of decimals. */
function scaled(v: number | string, places: number): number {
  const s = typeof v === 'number' ? v.toFixed(places + 2) : String(v);
  const neg = s.startsWith('-');
  const [i, f = ''] = (neg ? s.slice(1) : s).split('.');
  const n = Number(i) * 10 ** places + Number((f + '0'.repeat(places)).slice(0, places) || 0);
  return neg ? -n : n;
}

/** floor(weight × rate), exactly (232.5 × 55 = 12787). */
export function dayAmount(weight: any, rate: any): number {
  if (weight == null || rate == null) return 0;
  const p = BigInt(scaled(weight, 3)) * BigInt(scaled(rate, 2)); // ×100000
  const q = p / 100000n;
  return Number(p < 0n && q * 100000n !== p ? q - 1n : q);
}

export function commissionOf(total: number, pct: any): number {
  const p = BigInt(total) * BigInt(scaled(pct, 2)); // ×100 ×100
  const q = p / 10000n;
  return Number(p < 0n && q * 10000n !== p ? q - 1n : q);
}

export function totals(amounts: number[], kharcha: number[], pct: any, openingDue = 0) {
  const total = sum(amounts);
  const commission = commissionOf(total, pct);
  const net = total - commission;
  const kh = sum(kharcha);
  return { total, commission, net, kharcha: kh, opening_due: openingDue, final: net - kh - openingDue };
}

const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
/** Like Python's as_num: 8.0 -> 8, numeric strings -> numbers. */
const num = (v: any): number | null => (v == null ? null : Number(v));
/** Python's round(x, n) on a float, close enough for display (half-even on exact ties). */
function round(x: number, n: number): number {
  const m = 10 ** n, y = x * m, r = Math.round(y);
  return (Math.abs(y % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r) / m;
}
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const fmt = (n: number) => n.toLocaleString('en-US');
const ym = (month: string) => String(month).slice(0, 7);
function daysIn(month: string): number {
  const [y, m] = month.split('-').map(Number);
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}
function monthStart(value: string): string {
  const m = /^(\d{4})-(\d{1,2})/.exec(value || '');
  if (!m) throw new LocalError(400, 'Month must look like 2026-10');
  return `${m[1]}-${m[2].padStart(2, '0')}-01`;
}
function prevMonth(month: string): string {
  const [y, m] = month.split('-').map(Number);
  return m === 1 ? `${y - 1}-12-01` : `${y}-${String(m - 1).padStart(2, '0')}-01`;
}
function dateOf(month: string, day: number): string {
  const [y, m] = month.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1, day));
  return d.toISOString().slice(0, 10);
}
const byName = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

// ---------- the data, indexed ----------

/** Kharcha in the order they were added; ones still waiting on this device (negative ids) come last. */
const khOrder = (k: any) => (k._pending ? 1e15 - k.id : k.id);

class Data {
  suppliers = new Map<number, any>();
  flowers = new Map<number, any>();
  bills: any[];
  days = new Map<number, any[]>();
  kharcha = new Map<number, any[]>();
  t: Tables;
  constructor(t: Tables) {
    this.t = t;
    for (const s of t.suppliers || []) this.suppliers.set(s.id, s);
    for (const f of t.flowers || []) this.flowers.set(f.id, f);
    this.bills = t.bills || [];
    for (const d of t.bill_days || []) push(this.days, d.bill_id, d);
    for (const k of t.kharcha || []) push(this.kharcha, k.bill_id, k);
    for (const v of this.days.values()) v.sort((a, b) => a.day - b.day);
    for (const v of this.kharcha.values()) v.sort((a, b) => a.day - b.day || khOrder(a) - khOrder(b));
  }
  supplierName(b: any) { return this.suppliers.get(b.supplier_id)?.name ?? ''; }
  /** Bills sorted like "ORDER BY b.month DESC, s.name". */
  sorted(bills: any[]) {
    return [...bills].sort((a, b) => byName(b.month, a.month) || byName(this.supplierName(a), this.supplierName(b)));
  }
}
function push<K, V>(m: Map<K, V[]>, k: K, v: V) {
  const a = m.get(k);
  if (a) a.push(v); else m.set(k, [v]);
}

// ---------- bills ----------

/** A kharcha as the server sends it; `pending` = still waiting on this device to be sent. */
function khItem(k: any) {
  const out: any = { id: k.id, client_id: k.client_id ?? null, amount: Number(k.amount), tag: k.tag, note: k.note };
  if (k._pending) out.pending = true;
  return out;
}


function advanceBalance(d: Data, supplierId: number, upto?: string): number {
  return sum((d.t.advances || []).filter((a) => a.supplier_id === supplierId && (!upto || a.on_date <= upto))
    .map((a) => Number(a.amount)));
}

function loadBill(d: Data, id: number) {
  const bill = d.bills.find((b) => b.id === id);
  if (!bill) throw new LocalError(404, 'Bill not found');
  const s = d.suppliers.get(bill.supplier_id) || {};
  const f = bill.flower_id != null ? d.flowers.get(bill.flower_id) : null;
  const days = d.days.get(id) || [];
  const kh = d.kharcha.get(id) || [];
  const n = daysIn(bill.month);
  const byDay = new Map(days.map((x) => [x.day, x]));
  const khByDay = new Map<number, any[]>();
  for (const k of kh) push(khByDay, k.day, khItem(k));

  const rows: any[] = [], warnings: string[] = [];
  for (let day = 1; day <= n; day++) {
    const x = byDay.get(day);
    const items = khByDay.get(day) || [];
    const row: any = {
      day, weight: x ? num(x.weight) : null, rate: x ? num(x.rate) : null, amount: x ? Number(x.amount) : null,
      note: x ? x.note : null, kharcha: items, kharcha_total: sum(items.map((i) => i.amount)),
    };
    if (x && x.weight != null && x.rate != null) {
      const expected = dayAmount(x.weight, x.rate);
      if (expected !== Number(x.amount)) {
        row.mismatch = expected;
        warnings.push(`Day ${day}: ${row.weight} × ${row.rate} = ${fmt(expected)} but the bill says ${fmt(Number(x.amount))}`);
      }
    }
    rows.push(row);
  }
  // Rows on a day the month does not have (the workbook has a 31 June): counted, but not on the sheet.
  const [yy, mm] = bill.month.split('-').map(Number);
  const monthName = `${MONTHS[mm - 1]} ${yy}`;
  for (const x of days) if (x.day > n) {
    warnings.push(`Day ${x.day} is not in ${monthName}, but this bill has ${num(x.weight)} × ${num(x.rate)} = ${fmt(Number(x.amount))} on it (counted in the total)`);
  }
  for (const k of kh) if (k.day > n) {
    warnings.push(`Day ${k.day} is not in ${monthName}, but this bill has kharcha ${fmt(Number(k.amount))} on it (counted in the total)`);
  }
  const t: any = totals(days.map((x) => Number(x.amount)), kh.map((k) => Number(k.amount)), bill.commission_pct, Number(bill.opening_due));
  const weights = days.filter((x) => x.weight != null).map((x) => Number(x.weight));
  const w = round(sum(weights), 3);
  t.weight = weights.length ? w : 0;
  t.days_supplied = days.filter((x) => Number(x.weight)).length;
  t.avg_rate = weights.length && w ? round(t.total / w, 1) : null;

  const paper = bill.paper_totals;
  if (paper) {
    for (const [key, label] of [['total', 'Total amount'], ['commission', 'Commission'], ['net', 'Net amount'],
      ['kharcha', 'Kharcha'], ['final', 'Final total']]) {
      const pv = paper[key];
      let mine = key !== 'kharcha' ? t[key] : t.kharcha + t.opening_due;
      if (key === 'final' && paper.advance_cut) mine -= paper.advance_cut;
      if (pv != null && pv !== mine) warnings.push(`Paper bill wrote ${label} ${fmt(pv)}; the rows give ${fmt(mine)}`);
    }
  }
  return {
    id: bill.id,
    supplier: { id: bill.supplier_id, name: s.name, name_ur: s.name_ur, phone: s.phone },
    month: ym(bill.month),
    flower: bill.flower_id != null ? { id: bill.flower_id, name: f?.name ?? null, name_ur: f?.name_ur ?? null } : null,
    commission_pct: num(bill.commission_pct),
    opening_due: Number(bill.opening_due),
    note: bill.note,
    paper_totals: paper,
    status: bill.status,
    settlement: { paid_cash: num(bill.paid_cash), advance_cut: num(bill.advance_cut), carried: num(bill.carried), closed_at: bill.closed_at ?? null },
    advance_balance: advanceBalance(d, bill.supplier_id, dateOf(bill.month, n)),
    days: rows,
    totals: t,
    warnings,
  };
}

function summaries(d: Data, bills: any[]) {
  return d.sorted(bills).map((b) => {
    const days = d.days.get(b.id) || [];
    const kh = d.kharcha.get(b.id) || [];
    const t = totals([sum(days.map((x) => Number(x.amount)))], [sum(kh.map((k) => Number(k.amount)))], b.commission_pct, Number(b.opening_due));
    const f = b.flower_id != null ? d.flowers.get(b.flower_id) : null;
    return {
      id: b.id, supplier_id: b.supplier_id, supplier_name: d.supplierName(b), month: ym(b.month), status: b.status,
      flower_id: b.flower_id, flower_name: f?.name ?? null, commission_pct: num(b.commission_pct),
      weight: round(sum(days.map((x) => Number(x.weight || 0))), 3), days_supplied: days.filter((x) => Number(x.weight) > 0).length,
      imported: b.paper_totals != null, carried: num(b.carried), paid_cash: num(b.paid_cash), advance_cut: num(b.advance_cut),
      ...t,
    };
  });
}

function neighbours(d: Data, id: number) {
  const b = d.bills.find((x) => x.id === id);
  if (!b) throw new LocalError(404, 'Bill not found');
  const ids = d.sorted(d.bills.filter((x) => x.month === b.month)).map((x) => x.id);
  const i = ids.indexOf(id);
  return { prev: i > 0 ? ids[i - 1] : null, next: i + 1 < ids.length ? ids[i + 1] : null, position: i + 1, count: ids.length };
}

function daily(d: Data, onDate: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(onDate)) throw new LocalError(422, 'Not a date');
  const month = onDate.slice(0, 8) + '01', day = Number(onDate.slice(8));
  const bills = new Map<number, any>();
  for (const b of d.bills) if (b.month === month) bills.set(b.supplier_id, b);
  const sups = [...d.suppliers.values()].filter((s) => s.active || bills.has(s.id)).sort((a, b) => byName(a.name, b.name));
  // Latest rate before this date, per supplier.
  const last = new Map<number, { at: string; rate: number }>();
  for (const b of d.bills) {
    for (const x of d.days.get(b.id) || []) {
      if (x.rate == null) continue;
      const at = dateOf(b.month, x.day);
      if (at >= onDate) continue;
      const cur = last.get(b.supplier_id);
      if (!cur || at > cur.at) last.set(b.supplier_id, { at, rate: Number(x.rate) });
    }
  }
  return {
    date: onDate,
    rows: sups.map((s) => {
      const b = bills.get(s.id);
      const x = b ? (d.days.get(b.id) || []).find((r) => r.day === day) : null;
      return {
        supplier_id: s.id, supplier_name: s.name, flower_name: s.flower_id != null ? d.flowers.get(s.flower_id)?.name ?? null : null,
        bill_id: b ? b.id : null, bill_status: b ? b.status : null,
        weight: x ? num(x.weight) : null, rate: x ? num(x.rate) : null, amount: x ? Number(x.amount) : null, note: x ? x.note : null,
        kharcha: b ? (d.kharcha.get(b.id) || []).filter((k) => k.day === day).sort((p, q) => khOrder(p) - khOrder(q))
          .map(khItem) : [],
        last_rate: last.get(s.id)?.rate ?? null,
        ...(x?._pending ? { pending: true } : {}),
      };
    }),
  };
}

// ---------- reports (api/routes_reports.py) ----------

function kpis(bills: any[]) {
  const total = sum(bills.map((b) => b.total));
  const weight = sum(bills.map((b) => Number(b.weight || 0)));
  return {
    suppliers: bills.filter((b) => b.total || b.kharcha).length,
    bills: bills.length,
    weight: round(weight, 3),
    total,
    commission: sum(bills.map((b) => b.commission)),
    net: sum(bills.map((b) => b.net)),
    kharcha: sum(bills.map((b) => b.kharcha)),
    opening_due: sum(bills.map((b) => b.opening_due)),
    payable: sum(bills.filter((b) => b.final > 0).map((b) => b.final)),
    receivable: -sum(bills.filter((b) => b.final < 0).map((b) => b.final)) || 0,
    avg_rate: weight ? round(total / weight, 1) : null,
    open_bills: bills.filter((b) => b.status === 'open').length,
  };
}

function monthReport(d: Data, monthArg: string) {
  const m = monthStart(monthArg);
  const bills = summaries(d, d.bills.filter((b) => b.month === m));
  const prev = summaries(d, d.bills.filter((b) => b.month === prevMonth(m)));
  const n = daysIn(m);
  const groups = new Map<string, { day: number; flower: string; weight: number; amount: number; min: number | null; max: number | null; suppliers: number }>();
  const khDay = new Map<number, number>();
  for (const b of d.bills) {
    if (b.month !== m) continue;
    const flower = (b.flower_id != null && d.flowers.get(b.flower_id)?.name) || 'Other';
    for (const x of d.days.get(b.id) || []) {
      const k = `${x.day}\u0000${flower}`;
      let g = groups.get(k);
      if (!g) groups.set(k, g = { day: x.day, flower, weight: 0, amount: 0, min: null, max: null, suppliers: 0 });
      g.weight += Number(x.weight || 0);
      g.amount += Number(x.amount || 0);
      if (Number(x.weight) > 0) g.suppliers++;
      if (x.rate != null) {
        const r = Number(x.rate);
        g.min = g.min == null ? r : Math.min(g.min, r);
        g.max = g.max == null ? r : Math.max(g.max, r);
      }
    }
    for (const k of d.kharcha.get(b.id) || []) khDay.set(k.day, (khDay.get(k.day) || 0) + Number(k.amount));
  }
  const dayRows = [...groups.values()].sort((a, b) => a.day - b.day || byName(a.flower, b.flower));
  const flowers = [...new Set(dayRows.map((r) => r.flower))].sort(byName);
  const dailyRows = Array.from({ length: n }, (_, i) => ({ day: i + 1, weight: 0, amount: 0, kharcha: 0, suppliers: 0, by_flower: {} as Record<string, any> }));
  const ft = new Map<string, any>();
  for (const r of dayRows) {
    const row = dailyRows[r.day - 1];   // none for e.g. 31 June: counts in the totals only
    const w = r.weight, a = r.amount;
    if (row) {
      row.weight = round(row.weight + w, 3);
      row.amount += a;
      row.suppliers += r.suppliers;
      row.by_flower[r.flower] = { weight: round(w, 3), amount: a, avg_rate: w ? round(a / w, 1) : null, min_rate: r.min, max_rate: r.max };
    }
    let f = ft.get(r.flower);
    if (!f) ft.set(r.flower, f = { flower: r.flower, weight: 0, amount: 0, min_rate: null, max_rate: null });
    f.weight += w;
    f.amount += a;
    if (r.min != null) f.min_rate = f.min_rate == null ? r.min : Math.min(f.min_rate, r.min);
    if (r.max != null) f.max_rate = f.max_rate == null ? r.max : Math.max(f.max_rate, r.max);
  }
  for (const [day, amount] of khDay) if (dailyRows[day - 1]) dailyRows[day - 1].kharcha = amount;
  for (const f of ft.values()) {
    f.weight = round(f.weight, 3);
    f.avg_rate = f.weight ? round(f.amount / f.weight, 1) : null;
    f.suppliers = bills.filter((b) => (b.flower_name || 'Other') === f.flower && b.total).length;
  }
  return {
    month: m.slice(0, 7),
    kpis: kpis(bills),
    previous: prev.length ? kpis(prev) : null,
    flowers,
    daily: dailyRows,
    by_flower: [...ft.values()].sort((a, b) => b.amount - a.amount),
    by_supplier: [...bills].sort((a, b) => b.total - a.total),
  };
}

function entries(d: Data, p: URLSearchParams) {
  const from = p.get('date_from') || '', to = p.get('date_to') || '';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from) || !/^\d{4}-\d{2}-\d{2}$/.test(to)) throw new LocalError(422, 'Pick a start and an end date');
  if (to < from) throw new LocalError(400, 'The end date is before the start date');
  if ((Date.parse(to) - Date.parse(from)) / 864e5 > 800) throw new LocalError(400, 'Pick a range of two years or less');
  const sid = Number(p.get('supplier_id')) || 0, fid = Number(p.get('flower_id')) || 0;
  const out: any[] = [];
  for (const b of d.bills) {
    if (sid && b.supplier_id !== sid) continue;
    if (fid && b.flower_id !== fid) continue;
    const days = d.days.get(b.id) || [], kh = d.kharcha.get(b.id) || [];
    const dayNums = new Set<number>([...days.map((x) => x.day), ...kh.map((k) => k.day)]);
    for (const day of dayNums) {
      const at = dateOf(b.month, day);
      if (at < from || at > to) continue;
      const x = days.find((r) => r.day === day);
      out.push({
        date: at, bill_id: b.id, supplier_id: b.supplier_id, supplier: d.supplierName(b),
        flower: (b.flower_id != null && d.flowers.get(b.flower_id)?.name) || '',
        weight: x ? num(x.weight) : null, rate: x ? num(x.rate) : null, amount: x ? Number(x.amount) : 0,
        kharcha: sum(kh.filter((k) => k.day === day).map((k) => Number(k.amount))),
        _month: b.month, _day: day,
      });
    }
  }
  out.sort((a, b) => byName(a.date, b.date) || byName(a.supplier, b.supplier) || byName(a._month, b._month) || a._day - b._day);
  for (const r of out) { delete r._month; delete r._day; }
  const w = sum(out.map((r) => Number(r.weight || 0)));
  const a = sum(out.map((r) => r.amount));
  return {
    rows: out,
    totals: { weight: round(w, 3), amount: a, kharcha: sum(out.map((r) => r.kharcha)), avg_rate: w ? round(a / w, 1) : null,
      days: new Set(out.map((r) => r.date)).size, suppliers: new Set(out.map((r) => r.supplier_id)).size },
  };
}

function overview(d: Data) {
  const months = new Map<string, any[]>();
  for (const b of summaries(d, d.bills)) push(months, b.month, b);
  return [...months.keys()].sort(byName).map((k) => ({ month: k, ...kpis(months.get(k)!) }));
}

function balances(d: Data) {
  const bills = summaries(d, d.bills);
  const adv = new Map<number, number>();
  for (const a of d.t.advances || []) adv.set(a.supplier_id, (adv.get(a.supplier_id) || 0) + Number(a.amount));
  const latest = new Map<number, any>();
  for (const b of bills) if (!latest.has(b.supplier_id)) latest.set(b.supplier_id, b);
  const rows = [...d.suppliers.values()].sort((a, b) => byName(a.name, b.name)).map((s) => {
    const last = latest.get(s.id);
    const bal = !last ? 0 : last.status === 'closed' ? (last.carried || 0) : last.final;
    return { supplier_id: s.id, name: s.name, active: s.active, balance: bal, advance: adv.get(s.id) || 0,
      last_month: last ? last.month : null, last_status: last ? last.status : null };
  });
  return {
    rows,
    payable: sum(rows.filter((r) => r.balance > 0).map((r) => r.balance)),
    receivable: -sum(rows.filter((r) => r.balance < 0).map((r) => r.balance)) || 0,
    advances: sum(rows.map((r) => r.advance)),
  };
}

// ---------- routing ----------

// ---------- settings, suppliers, investors (api/routes_suppliers.py, api/routes_investors.py) ----------

const strip = ({ _key, _pending, ...row }: any) => row;
const userName = (d: Data, id: any) => (id == null ? null : (d.t.users || []).find((u) => u.id === id)?.display_name ?? null);
const FAR = '9999-12-01';

function settings(d: Data) {
  const s = (d.t.shop_settings || []).find((x) => x.id === 1) || (d.t.shop_settings || [])[0];
  if (!s) throw new LocalError(404, 'No shop settings');
  return { ...strip(s), default_commission_pct: num(s.default_commission_pct) };
}

/** Latest bill per supplier (bill summaries are newest month first). */
function latestBills(d: Data): Map<number, any> {
  const latest = new Map<number, any>();
  for (const b of summaries(d, d.bills)) if (!latest.has(b.supplier_id)) latest.set(b.supplier_id, b);
  return latest;
}
const balanceOf = (last: any) => (last == null ? 0 : last.status === 'closed' ? (last.carried || 0) : last.final);

function supplierList(d: Data, includeInactive: boolean) {
  const latest = latestBills(d);
  const count = new Map<number, number>();
  for (const b of d.bills) count.set(b.supplier_id, (count.get(b.supplier_id) || 0) + 1);
  return [...d.suppliers.values()]
    .filter((s) => includeInactive || s.active)
    .sort((a, b) => Number(b.active) - Number(a.active) || byName(a.name, b.name))
    .map((s) => {
      const last = latest.get(s.id) ?? null;
      return {
        ...strip(s), flower_name: s.flower_id != null ? d.flowers.get(s.flower_id)?.name ?? null : null,
        bill_count: count.get(s.id) || 0, commission_pct: num(s.commission_pct),
        last_bill: last, balance: balanceOf(last), advance: advanceBalance(d, s.id),
      };
    });
}

function shareOf(commission: number, pct: any): number {
  const p = BigInt(commission) * BigInt(scaled(pct, 2)); // ×10000
  const q = p / 10000n;
  return Number(p < 0n && q * 10000n !== p ? q - 1n : q);
}

/** One row per (investor, bill) inside a share period, like routes_investors.earnings. */
function earnings(d: Data, investorId?: number, supplierId?: number) {
  const investors = new Map((d.t.investors || []).map((i) => [i.id, i]));
  const shares = (d.t.khaata_shares || [])
    .filter((s) => (!investorId || s.investor_id === investorId) && (!supplierId || s.supplier_id === supplierId))
    .sort((a, b) => a.id - b.id);
  if (!shares.length) return [];
  const sups = new Set(shares.map((s) => s.supplier_id));
  const bills = summaries(d, d.bills.filter((b) => sups.has(b.supplier_id)));
  const out: any[] = [];
  for (const s of shares) {
    const lo = s.from_month, hi = s.to_month || FAR;
    for (const b of bills) {
      const m = `${b.month}-01`;
      if (b.supplier_id === s.supplier_id && lo <= m && m <= hi) {
        out.push({
          investor_id: s.investor_id, investor_name: investors.get(s.investor_id)?.name ?? null,
          supplier_id: b.supplier_id, supplier_name: b.supplier_name, month: b.month,
          bill_id: b.id, total: b.total, commission: b.commission,
          pct: num(s.pct), earned: shareOf(b.commission, s.pct), status: b.status,
        });
      }
    }
  }
  return out.sort((a, b) => byName(a.month, b.month) || byName(a.supplier_name, b.supplier_name));
}

function khaataInfo(d: Data, supplierId: number) {
  const rows = (d.t.khaatas || []).filter((k) => k.supplier_id === supplierId)
    .sort((a, b) => byName(a.opened_on, b.opened_on) || a.id - b.id);
  let history = rows.map((r) => ({
    id: r.id, opened_on: r.opened_on, terms: r.terms,
    commission_pct: r.commission_pct != null ? num(r.commission_pct) : null,
    closed_on: r.closed_on ?? null, close_reason: r.close_reason, written_off: r.written_off ?? null,
    closed_by: userName(d, r.closed_by), opened_by: userName(d, r.created_by),
  }));
  if (!history.length) {
    const dates = [
      ...d.bills.filter((b) => b.supplier_id === supplierId).map((b) => b.month),
      ...(d.t.advances || []).filter((a) => a.supplier_id === supplierId).map((a) => a.on_date),
    ].sort();
    history = [{ id: null, opened_on: dates[0] ?? null, terms: null, commission_pct: null, closed_on: null,
      close_reason: null, written_off: null, closed_by: null, opened_by: null }];
  }
  const current = history[history.length - 1];
  return { status: current.closed_on ? 'closed' : 'open', current, history, outstanding: advanceBalance(d, supplierId) };
}

function supplierPage(d: Data, id: number) {
  const s = d.suppliers.get(id);
  if (!s) throw new LocalError(404, 'Supplier not found');
  const mine = d.bills.filter((b) => b.supplier_id === id);
  const bills = summaries(d, mine);
  const billMonth = new Map(d.bills.map((b) => [b.id, b.month]));
  const days: any[] = [], kharcha: any[] = [];
  for (const b of mine) {
    for (const x of d.days.get(b.id) || []) if (x.weight != null) days.push({ date: dateOf(b.month, x.day), weight: num(x.weight), rate: num(x.rate), amount: Number(x.amount) });
    for (const k of d.kharcha.get(b.id) || []) kharcha.push({ date: dateOf(b.month, k.day), amount: Number(k.amount), tag: k.tag, note: k.note, bill_id: b.id, _id: khOrder(k) });
  }
  days.sort((a, b) => byName(a.date, b.date));
  kharcha.sort((a, b) => byName(b.date, a.date) || b._id - a._id);
  // The market rate on the same days: every bill of this supplier's flower, in the months this supplier has bills.
  const months = new Set(mine.map((b) => b.month));
  const mk = new Map<string, { amount: number; weight: number }>();
  for (const b of d.bills) {
    if (!months.has(b.month) || (b.flower_id ?? null) !== (s.flower_id ?? null)) continue;
    for (const x of d.days.get(b.id) || []) {
      if (!(Number(x.weight) > 0)) continue;
      const at = dateOf(b.month, x.day), cur = mk.get(at) || { amount: 0, weight: 0 };
      cur.amount += Number(x.amount); cur.weight += Number(x.weight);
      mk.set(at, cur);
    }
  }
  const market = [...mk.keys()].sort().map((at) => {
    const v = mk.get(at)!;
    return { date: at, avg_rate: v.weight && v.amount ? round(v.amount / v.weight, 1) : null };
  });
  let running = 0;
  const advances = (d.t.advances || []).filter((a) => a.supplier_id === id)
    .sort((a, b) => byName(a.on_date, b.on_date) || a.id - b.id)
    .map((a) => {
      running += Number(a.amount);
      const bm = a.bill_id != null ? billMonth.get(a.bill_id) : null;
      return { id: a.id, on_date: a.on_date, amount: Number(a.amount), kind: a.kind, bill_id: a.bill_id ?? null, note: a.note,
        bill_month: bm ? ym(bm) : null, by_name: userName(d, a.created_by), running };
    });
  const investors = new Map((d.t.investors || []).map((i) => [i.id, i]));
  const shares = (d.t.khaata_shares || []).filter((x) => x.supplier_id === id)
    .sort((a, b) => byName(a.from_month, b.from_month) || a.id - b.id)
    .map((x) => ({ id: x.id, investor_id: x.investor_id, investor_name: investors.get(x.investor_id)?.name ?? null,
      pct: num(x.pct), from_month: ym(x.from_month), to_month: x.to_month ? ym(x.to_month) : null, note: x.note }));
  const last = bills[0] ?? null;
  return {
    supplier: { ...strip(s), flower_name: s.flower_id != null ? d.flowers.get(s.flower_id)?.name ?? null : null, commission_pct: num(s.commission_pct) },
    position: { balance: balanceOf(last), advance: advanceBalance(d, id), last_bill: last },
    bills,
    days,
    market,
    kharcha: kharcha.map(({ _id, ...k }) => k),
    advances,
    khaata: khaataInfo(d, id),
    shares,
    investor_earnings: earnings(d, undefined, id),
  };
}

function entryTotals(d: Data, investorId: number) {
  const m = { invested: 0, withdrawn: 0, paid: 0 };
  let any = false;
  for (const e of d.t.investor_entries || []) {
    if (e.investor_id !== investorId) continue;
    any = true;
    if (e.kind === 'invested') m.invested += Number(e.amount);
    if (e.kind === 'withdrawn') m.withdrawn += Number(e.amount);
    if (e.kind === 'payout') m.paid += Number(e.amount);
  }
  return any ? m : null;
}

function investorSummary(inv: any, rows: any[], money: any) {
  const earned = sum(rows.map((r) => r.earned));
  const m = money || { invested: 0, withdrawn: 0, paid: 0 };
  return { ...strip(inv), earned, paid: m.paid, payable: earned - m.paid, invested: m.invested, withdrawn: m.withdrawn,
    capital: m.invested - m.withdrawn, khaatas: new Set(rows.map((r) => r.supplier_id)).size };
}

function investorList(d: Data) {
  const rows = earnings(d);
  return [...(d.t.investors || [])]
    .sort((a, b) => Number(b.active) - Number(a.active) || byName(a.name, b.name))
    .map((inv) => {
      const mine = rows.filter((r) => r.investor_id === inv.id);
      const s: any = investorSummary(inv, mine, entryTotals(d, inv.id));
      s.active_khaatas = (d.t.khaata_shares || []).filter((x) => x.investor_id === inv.id && x.to_month == null).length;
      const months = new Map<string, number>();
      for (const r of mine) months.set(r.month, (months.get(r.month) || 0) + r.earned);
      s.by_month = [...months.keys()].sort().map((k) => ({ month: k, earned: months.get(k) }));
      return s;
    });
}

function investorPage(d: Data, id: number) {
  const inv = (d.t.investors || []).find((i) => i.id === id);
  if (!inv) throw new LocalError(404, 'Not found in investors');
  const rows = earnings(d, id);
  const sname = (sid: any) => (sid == null ? null : d.suppliers.get(sid)?.name ?? null);
  const shares = (d.t.khaata_shares || []).filter((x) => x.investor_id === id)
    .sort((a, b) => byName(sname(a.supplier_id) || '', sname(b.supplier_id) || '') || byName(a.from_month, b.from_month))
    .map((x) => ({ ...strip(x), supplier_name: sname(x.supplier_id), pct: num(x.pct), from_month: ym(x.from_month), to_month: x.to_month ? ym(x.to_month) : null }));
  const entries = (d.t.investor_entries || []).filter((e) => e.investor_id === id)
    .sort((a, b) => byName(a.on_date, b.on_date) || a.id - b.id)
    .map((e) => ({ ...strip(e), amount: Number(e.amount), supplier_name: sname(e.supplier_id) }));
  const withShare = new Set((d.t.khaata_shares || []).filter((x) => x.investor_id === id).map((x) => x.supplier_id));
  const khaatas = new Map<number, any>();
  const k = (sid: number, name: string | null) => {
    if (!khaatas.has(sid)) khaatas.set(sid, { supplier_id: sid, supplier_name: name, commission: 0, earned: 0, invested: 0, months: 0 });
    return khaatas.get(sid);
  };
  for (const r of rows) { const x = k(r.supplier_id, r.supplier_name); x.commission += r.commission; x.earned += r.earned; x.months += 1; }
  for (const e of entries) if (e.supplier_id) {
    k(e.supplier_id, e.supplier_name).invested += e.kind === 'invested' ? e.amount : e.kind === 'withdrawn' ? -e.amount : 0;
  }
  for (const x of khaatas.values()) x.advance = withShare.has(x.supplier_id) ? advanceBalance(d, x.supplier_id) : 0;
  const months = new Map<string, any>();
  for (const r of rows) {
    const m = months.get(r.month) || { month: r.month, commission: 0, earned: 0, khaatas: 0 };
    m.commission += r.commission; m.earned += r.earned; m.khaatas += 1;
    months.set(r.month, m);
  }
  const paid = new Map<string, number>();
  for (const e of entries) if (e.kind === 'payout') paid.set(ym(e.on_date), (paid.get(ym(e.on_date)) || 0) + e.amount);
  for (const key of paid.keys()) if (!months.has(key)) months.set(key, { month: key, commission: 0, earned: 0, khaatas: 0 });
  let running = 0;
  const monthRows = [...months.keys()].sort().map((key) => {
    const m = months.get(key);
    m.paid = paid.get(key) || 0;
    running += m.earned - m.paid;
    m.payable_after = running;
    return m;
  });
  return {
    investor: investorSummary(inv, rows, entryTotals(d, id)),
    shares,
    khaatas: [...khaatas.values()].sort((a, b) => b.earned - a.earned),
    months: monthRows,
    rows,
    entries,
  };
}

/** The answer to GET `path` (with its query string) from the tables, or undefined if it cannot be worked out here. */
export function localAnswer(path: string, t: Tables): any {
  const [p, q = ''] = path.split('?');
  const params = new URLSearchParams(q);
  let m: RegExpExecArray | null;
  const d = () => new Data(t);
  if (p === '/bills/months') return [...new Set(t.bills.map((b) => ym(b.month)))].sort().reverse();
  if (p === '/bills') {
    const month = params.get('month'), sid = Number(params.get('supplier_id')) || 0;
    const ms = month ? monthStart(month) : null;
    return summaries(d(), t.bills.filter((b) => (!ms || b.month === ms) && (!sid || b.supplier_id === sid)));
  }
  if ((m = /^\/bills\/(-?\d+)$/.exec(p))) return loadBill(d(), Number(m[1]));
  if ((m = /^\/bills\/(-?\d+)\/neighbours$/.exec(p))) return neighbours(d(), Number(m[1]));
  if ((m = /^\/daily\/([\d-]+)$/.exec(p))) return daily(d(), m[1]);
  if (p === '/reports/month') return monthReport(d(), params.get('month') || '');
  if (p === '/reports/entries') return entries(d(), params);
  if (p === '/reports/overview') return overview(d());
  if (p === '/reports/balances') return balances(d());
  if (p === '/settings') return settings(d());
  if (p === '/suppliers') return supplierList(d(), params.get('include_inactive') === 'true');
  if ((m = /^\/suppliers\/(-?\d+)$/.exec(p))) return supplierPage(d(), Number(m[1]));
  if (p === '/investors') return investorList(d());
  if ((m = /^\/investors\/(-?\d+)$/.exec(p))) return investorPage(d(), Number(m[1]));
  if (p === '/flowers') return [...t.flowers].sort((a, b) => Number(b.active) - Number(a.active) || byName(a.name, b.name)).map(strip);
  return undefined;
}

// ---------- changes still waiting on this device (outbox) ----------
//
// Each waiting save is applied to a copy of the tables the way the server will apply it (the routes in
// api/routes_*.py), so every page shows it at once. Rows made here get the save's temporary negative id
// and are marked `_pending`. A save the server would refuse throws LocalError; `strict` decides whether
// that is passed on (checking a new save before it is queued) or the save is just left out (older ones).

export type Ctx = { userId: number | null; now: string };
export type PendingItem = {
  kind: 'daily' | 'kharcha_del' | 'request';
  date?: string; row?: any; temp_bill?: number | null;
  id?: number | null; client_id?: string | null;
  method?: string; path?: string; body?: any; temp_id?: number | null; temp_bills?: Record<string, number>;
};

/** Columns of tables that can be empty on the device (api/schema.sql + migrations), for rows made here. */
const COLUMNS: Record<string, string[]> = {
  advances: ['id', 'supplier_id', 'on_date', 'amount', 'kind', 'bill_id', 'note', 'created_at', 'created_by', 'rev'],
  khaatas: ['id', 'supplier_id', 'opened_on', 'terms', 'commission_pct', 'closed_on', 'close_reason', 'written_off',
    'ended_shares', 'created_at', 'created_by', 'closed_by', 'rev'],
  investors: ['id', 'name', 'phone', 'notes', 'active', 'created_at', 'rev'],
  khaata_shares: ['id', 'supplier_id', 'investor_id', 'pct', 'from_month', 'to_month', 'note', 'created_at', 'rev'],
  investor_entries: ['id', 'investor_id', 'supplier_id', 'on_date', 'kind', 'amount', 'note', 'created_at', 'created_by', 'rev'],
  flowers: ['id', 'name', 'name_ur', 'active', 'rev'],
};
const nonNull = (o: any) => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== null && v !== undefined));
const pick = (o: any, keys: string[]) => Object.fromEntries(Object.entries(o).filter(([k]) => keys.includes(k)));
const lower = (s: any) => String(s ?? '').trim().toLowerCase();
const refuse = (msg: string): never => { throw new LocalError(400, msg); };
const MONTH_NAME = (month: string) => `${MONTHS[Number(month.slice(5, 7)) - 1]} ${month.slice(0, 4)}`;

class Writer {
  t: Tables;
  ctx: Ctx;
  tmp = -1;
  constructor(t: Tables, ctx: Ctx) {
    this.t = { ...t };
    for (const k of Object.keys(this.t)) this.t[k] = [...(this.t[k] || [])];
    this.ctx = ctx;
  }
  rows(table: string) { return (this.t[table] ||= []); }
  find(table: string, id: any, what = table) {
    const r = this.rows(table).find((x) => x.id === Number(id));
    if (!r) throw new LocalError(404, `Not found in ${what}`);
    return r;
  }
  /** A new row with every column the table has (null), like the server's RETURNING *. */
  insert(table: string, fields: any) {
    const tpl = this.rows(table)[0];
    const cols = tpl ? Object.keys(tpl).filter((k) => !k.startsWith('_')) : COLUMNS[table] || [];
    const row: any = { ...Object.fromEntries(cols.map((k) => [k, null])), ...fields, _key: String(fields.id), _pending: true };
    if (cols.includes('created_at') && row.created_at == null) row.created_at = this.ctx.now;
    this.rows(table).push(row);
    return row;
  }
  /** Change a row in place (as a new object, so the device copy's own row is never touched). */
  update(table: string, row: any, fields: any) {
    const next = { ...row, ...fields, _pending: true };
    const list = this.rows(table);
    list[list.indexOf(row)] = next;
    return next;
  }
  remove(table: string, pred: (r: any) => boolean) { this.t[table] = this.rows(table).filter((r) => !pred(r)); }
  newId(given?: number | null) { return given ?? this.tmp--; }
  data() { return new Data(this.t); }
  settings() { return this.rows('shop_settings').find((s) => s.id === 1) || this.rows('shop_settings')[0] || {}; }

  openBill(id: any) {
    const b = this.find('bills', id, 'bills');
    if (b.status !== 'open') refuse('This bill is closed. Reopen it to make changes.');
    return b;
  }
  checkDay(bill: any, day: number) {
    if (!(day >= 1 && day <= daysIn(bill.month))) refuse(`Day must be between 1 and ${daysIn(bill.month)}`);
  }
  ensureBill(supplierId: number, month: string, id?: number | null) {
    const have = this.rows('bills').find((b) => b.supplier_id === supplierId && b.month === month);
    if (have) return have;
    const s = this.find('suppliers', supplierId, 'suppliers');
    const prev = this.rows('bills').filter((b) => b.supplier_id === supplierId && b.month < month)
      .sort((a, b) => byName(b.month, a.month))[0];
    return this.insert('bills', { id: this.newId(id), supplier_id: supplierId, month, flower_id: s.flower_id,
      commission_pct: s.commission_pct, opening_due: prev?.status === 'closed' && prev.carried ? -Number(prev.carried) : 0,
      note: null, paper_totals: null, status: 'open' });
  }
  setDay(bill: any, day: number, weight: any, rate: any, note: any) {
    this.remove('bill_days', (x) => x.bill_id === bill.id && x.day === day);
    if (weight == null && rate == null && !note) return;
    this.rows('bill_days').push({ _key: `${bill.id}:${day}`, bill_id: bill.id, day, weight: weight ?? null, rate: rate ?? null,
      amount: dayAmount(weight, rate), note: note ?? null, updated_by: this.ctx.userId, _pending: true });
  }
  addKharcha(bill: any, day: number, amount: number, tag: any, note: any, clientId: any) {
    if (clientId && this.rows('kharcha').some((k) => k.client_id === clientId)) return;
    this.insert('kharcha', { id: this.tmp--, client_id: clientId ?? null, bill_id: bill.id, day, amount, tag: tag ?? null,
      note: note ?? null, created_by: this.ctx.userId });
  }
  syncNextOpening(bill: any, carried: number | null) {
    const nxt = this.rows('bills').filter((b) => b.supplier_id === bill.supplier_id && b.month > bill.month)
      .sort((a, b) => byName(a.month, b.month))[0];
    if (nxt && nxt.status === 'open') this.update('bills', nxt, { opening_due: -(carried || 0) });
  }
  uniqueName(table: string, name: string, skipId: number | null, msg: string) {
    if (this.rows(table).some((r) => lower(r.name) === lower(name) && r.id !== skipId)) refuse(msg);
  }

  // PUT /daily/{date}, one row
  daily(it: PendingItem) {
    const r = it.row, date = it.date!, month = monthStart(date), day = Number(date.slice(8));
    const touchesDay = 'weight' in r || 'rate' in r || 'note' in r;
    const existing = this.rows('bills').find((b) => b.supplier_id === r.supplier_id && b.month === month);
    if (!existing && r.weight == null && r.rate == null && !r.add_kharcha) return;
    const bill = existing || this.ensureBill(r.supplier_id, month, it.temp_bill);
    if (bill.status !== 'open') refuse('This month is closed for this supplier.');
    if (touchesDay) this.setDay(bill, day, r.weight, r.rate, r.note);
    if (r.add_kharcha) this.addKharcha(bill, day, r.add_kharcha, r.kharcha_tag, r.kharcha_note, r.kharcha_client_id);
  }

  kharchaDel(it: PendingItem) {
    const k = this.rows('kharcha').find((x) => (it.id != null && x.id === it.id) || (it.client_id && x.client_id === it.client_id));
    if (!k) return;
    this.openBill(k.bill_id);
    this.remove('kharcha', (x) => x === k);
  }

  request(it: PendingItem) {
    const method = (it.method || 'POST').toUpperCase(), path = (it.path || '').split('?')[0], b = it.body || {};
    let m: RegExpExecArray | null;
    const is = (meth: string, re: RegExp) => method === meth && (m = re.exec(path));
    const id = () => Number(m![1]);

    if (is('PATCH', /^\/settings$/)) {
      const s = this.settings();
      return this.update('shop_settings', s, nonNull(b));
    }
    if (is('POST', /^\/flowers$/)) {
      this.uniqueName('flowers', b.name, null, 'That flower already exists');
      return this.insert('flowers', { id: this.newId(it.temp_id), name: String(b.name).trim(), name_ur: b.name_ur ?? null, active: true });
    }
    if (is('PATCH', /^\/flowers\/(-?\d+)$/)) {
      if (!b.name) refuse('The name is needed');
      return this.update('flowers', this.find('flowers', id()), pick(nonNull(b), ['name', 'name_ur', 'active']));
    }
    if (is('POST', /^\/suppliers$/)) {
      this.uniqueName('suppliers', b.name, null, 'A supplier with that name already exists');
      const f: any = nonNull(pick(b, ['name', 'name_ur', 'phone', 'place', 'flower_id', 'commission_pct', 'notes', 'active']));
      f.name = String(f.name).trim();
      if (f.commission_pct == null) f.commission_pct = this.settings().default_commission_pct;
      return this.insert('suppliers', { active: true, ...f, id: this.newId(it.temp_id) });
    }
    if (is('PATCH', /^\/suppliers\/(-?\d+)$/)) {
      const s = this.find('suppliers', id());
      const f: any = nonNull(pick(b, ['name', 'name_ur', 'phone', 'place', 'flower_id', 'commission_pct', 'notes', 'active']));
      if (f.name) { f.name = String(f.name).trim(); this.uniqueName('suppliers', f.name, s.id, 'A supplier with that name already exists'); }
      if (!Object.keys(f).length) refuse('Nothing to change');
      return this.update('suppliers', s, f);
    }
    if (is('POST', /^\/suppliers\/(-?\d+)\/advances$/)) {
      this.find('suppliers', id());
      const signed = ['given', 'opening'].includes(b.kind) ? Number(b.amount) : -Number(b.amount);
      return this.insert('advances', { id: this.newId(it.temp_id), supplier_id: id(), on_date: b.on_date, amount: signed,
        kind: b.kind, bill_id: null, note: b.note ?? null, created_by: this.ctx.userId });
    }
    if (is('DELETE', /^\/advances\/(-?\d+)$/)) {
      const a = this.find('advances', id());
      if (a.kind === 'bill_cut') refuse('This was cut from a bill. Reopen that bill instead.');
      if (a.kind === 'written_off') refuse('This was written off when the khaata was closed. Reopen the khaata instead.');
      return this.remove('advances', (x) => x === a);
    }
    if (is('POST', /^\/suppliers\/(-?\d+)\/khaata\/close$/)) return this.khaataClose(id(), b, it);
    if (is('POST', /^\/suppliers\/(-?\d+)\/khaata\/renew$/)) return this.khaataRenew(id(), b, it);
    if (is('POST', /^\/suppliers\/(-?\d+)\/khaata\/reopen$/)) return this.khaataReopen(id());
    if (is('POST', /^\/bills$/)) return this.ensureBill(Number(b.supplier_id), monthStart(b.month), it.temp_id);
    if (is('POST', /^\/bills\/start-month$/)) {
      const month = monthStart(b.month);
      const have = new Set(this.rows('bills').filter((x) => x.month === month).map((x) => x.supplier_id));
      for (const s of this.rows('suppliers')) {
        if (s.active && !have.has(s.id) && it.temp_bills?.[String(s.id)] != null) this.ensureBill(s.id, month, it.temp_bills[String(s.id)]);
      }
      return;
    }
    if (is('PATCH', /^\/bills\/(-?\d+)$/)) {
      const bill = this.openBill(id());
      const f = pick(b, ['commission_pct', 'opening_due', 'flower_id', 'note']);
      if (!Object.keys(f).length) refuse('Nothing to change');
      return this.update('bills', bill, f);
    }
    if (is('POST', /^\/bills\/(-?\d+)\/kharcha$/)) {
      const bill = this.openBill(id());
      this.checkDay(bill, Number(b.day));
      if (!Number(b.amount)) refuse('Amount cannot be zero');
      return this.addKharcha(bill, Number(b.day), Number(b.amount), b.tag, b.note, b.client_id);
    }
    if (is('PUT', /^\/bills\/(-?\d+)\/days\/(\d+)$/)) {
      const bill = this.openBill(id());
      const day = Number(m![2]);
      this.checkDay(bill, day);
      return this.setDay(bill, day, b.weight, b.rate, b.note);
    }
    if (is('DELETE', /^\/kharcha\/(-?\d+)$/)) return this.kharchaDel({ kind: 'kharcha_del', id: id() });
    if (is('DELETE', /^\/kharcha\/by-client\/(.+)$/)) return this.kharchaDel({ kind: 'kharcha_del', client_id: decodeURIComponent(m![1]) });
    if (is('POST', /^\/bills\/(-?\d+)\/close$/)) return this.closeBill(id(), b);
    if (is('POST', /^\/bills\/(-?\d+)\/reopen$/)) return this.reopenBill(id());
    if (is('POST', /^\/investors$/)) {
      this.uniqueName('investors', b.name, null, 'An investor with that name already exists');
      return this.insert('investors', { active: true, ...nonNull(pick(b, ['name', 'phone', 'notes', 'active'])),
        name: String(b.name).trim(), id: this.newId(it.temp_id) });
    }
    if (is('PATCH', /^\/investors\/(-?\d+)$/)) {
      const f = pick(nonNull(b), ['name', 'phone', 'notes', 'active']);
      if (!Object.keys(f).length) refuse('Nothing to change');
      return this.update('investors', this.find('investors', id()), f);
    }
    if (is('POST', /^\/khaata-shares$/)) {
      const f: any = pick(b, ['supplier_id', 'investor_id', 'pct', 'from_month', 'to_month', 'note']);
      f.from_month = monthStart(f.from_month);
      f.to_month = f.to_month ? monthStart(f.to_month) : null;
      this.checkShare(f.supplier_id, f.investor_id, f.from_month, f.to_month, Number(f.pct), null);
      return this.insert('khaata_shares', { ...f, id: this.newId(it.temp_id) });
    }
    if (is('PATCH', /^\/khaata-shares\/(-?\d+)$/)) {
      const cur = this.find('khaata_shares', id());
      const f: any = pick(b, ['pct', 'from_month', 'to_month', 'note']);
      if ('from_month' in f) f.from_month = monthStart(f.from_month);
      if ('to_month' in f) f.to_month = f.to_month ? monthStart(f.to_month) : null;
      const lo = f.from_month ?? cur.from_month, hi = 'to_month' in f ? f.to_month : cur.to_month;
      this.checkShare(cur.supplier_id, cur.investor_id, lo, hi, Number(f.pct ?? cur.pct), cur.id);
      if (!Object.keys(f).length) refuse('Nothing to change');
      return this.update('khaata_shares', cur, f);
    }
    if (is('DELETE', /^\/khaata-shares\/(-?\d+)$/)) {
      const cur = this.find('khaata_shares', id());
      return this.remove('khaata_shares', (x) => x === cur);
    }
    if (is('POST', /^\/investors\/(-?\d+)\/entries$/)) {
      this.find('investors', id());
      if (b.supplier_id) this.find('suppliers', b.supplier_id);
      return this.insert('investor_entries', { kind: b.kind, amount: Number(b.amount), on_date: b.on_date,
        supplier_id: b.supplier_id ?? null, note: b.note ?? null, investor_id: id(), created_by: this.ctx.userId,
        id: this.newId(it.temp_id) });
    }
    if (is('DELETE', /^\/investor-entries\/(-?\d+)$/)) {
      const e = this.find('investor_entries', id());
      return this.remove('investor_entries', (x) => x === e);
    }
    throw new LocalError(501, `This change cannot be made without a connection (${method} ${path}).`);
  }

  checkShare(supplierId: number, investorId: number, lo: string, hi: string | null, pct: number, skipId: number | null) {
    if (hi && hi < lo) refuse('The end month is before the start month');
    const others = this.rows('khaata_shares').filter((s) => s.supplier_id === supplierId && s.id !== skipId);
    const overlaps = (s: any) => s.from_month <= (hi || FAR) && (s.to_month || FAR) >= lo;
    if (others.some((s) => s.investor_id === investorId && overlaps(s))) refuse('This investor already has a share in this khaata for some of those months');
    // The busiest month in the range (up to five years) must stay at or under 100%.
    let worst = 0;
    let at = lo;
    const end = [hi || FAR, `${Number(lo.slice(0, 4)) + 5}${lo.slice(4)}`].sort()[0];
    for (let n = 0; at <= end && n < 61; n++) {
      const tot = sum(others.filter((s) => s.from_month <= at && (s.to_month || FAR) >= at).map((s) => Number(s.pct)));
      worst = Math.max(worst, tot);
      const [y, mo] = at.split('-').map(Number);
      at = mo === 12 ? `${y + 1}-01-01` : `${y}-${String(mo + 1).padStart(2, '0')}-01`;
    }
    if (worst + pct > 100) refuse(`Shares in this khaata would add up to more than 100% (${worst + pct}%)`);
  }

  closeBill(id: number, b: any) {
    const bill = this.openBill(id);
    const loaded = loadBill(this.data(), id);
    const final = loaded.totals.final, cut = Number(b.advance_cut || 0), paid = Number(b.paid_cash || 0);
    const out = loaded.advance_balance;
    if (cut > 0 && cut > Math.max(0, Math.min(out, final))) {
      if (out <= 0) refuse('Nothing can be cut: this supplier has no advance outstanding.');
      if (final <= 0) refuse(`Nothing can be cut: this bill leaves nothing for the supplier (final Rs ${fmt(final)}).`);
      if (cut > out) refuse(`Only Rs ${fmt(out)} is outstanding, so no more than that can be cut.`);
      refuse(`This bill leaves Rs ${fmt(final)} for the supplier, so no more than that can be cut.`);
    }
    const carried = final - paid - cut;
    const closed = this.update('bills', bill, { status: 'closed', paid_cash: paid, advance_cut: cut, carried,
      closed_at: this.ctx.now, closed_by: this.ctx.userId, note: b.note ?? bill.note });
    if (cut) {
      this.insert('advances', { id: this.tmp--, supplier_id: bill.supplier_id, on_date: dateOf(bill.month, daysIn(bill.month)),
        amount: -cut, kind: 'bill_cut', bill_id: id, created_by: this.ctx.userId, note: `From the ${MONTH_NAME(bill.month)} bill` });
    }
    this.syncNextOpening(closed, carried);
  }

  reopenBill(id: number) {
    const bill = this.find('bills', id, 'bills');
    if (bill.status !== 'closed') refuse('This bill is already open');
    if (this.rows('bills').some((x) => x.supplier_id === bill.supplier_id && x.month > bill.month && x.status === 'closed')) {
      refuse('A later month is already closed for this supplier. Reopen that first.');
    }
    this.remove('advances', (a) => a.bill_id === id && a.kind === 'bill_cut');
    const open = this.update('bills', bill, { status: 'open', paid_cash: null, advance_cut: null, carried: null, closed_at: null, closed_by: null });
    this.syncNextOpening(open, 0);
  }

  khaataClose(sid: number, b: any, it: PendingItem) {
    this.find('suppliers', sid);
    const info = khaataInfo(this.data(), sid);
    if (info.status === 'closed') refuse('This khaata is already closed. Renew it to start a new one.');
    const cur: any = info.current;
    if (cur.opened_on && b.on_date < cur.opened_on) refuse(`The khaata was opened on ${cur.opened_on}; it cannot close before that.`);
    const left = info.outstanding;
    if (left && !b.write_off) refuse(`Rs ${fmt(left)} is still outstanding. Choose to write it off to close the khaata, or recover it first.`);
    if (left) {
      this.insert('advances', { id: this.tmp--, supplier_id: sid, on_date: b.on_date, amount: -left, kind: 'written_off',
        bill_id: null, note: 'Written off when the khaata was closed' + (b.reason ? `: ${b.reason}` : ''), created_by: this.ctx.userId });
    }
    const month = monthStart(b.on_date);
    const ended = this.rows('khaata_shares').filter((s) => s.supplier_id === sid && (s.to_month == null || s.to_month > month) && s.from_month <= month);
    for (const s of ended) this.update('khaata_shares', s, { to_month: month });
    const closing = { closed_on: b.on_date, close_reason: b.reason ?? null, written_off: left || null, closed_by: this.ctx.userId,
      ended_shares: ended.map((s) => ({ id: s.id, to_month: s.to_month ?? null })) };
    if (cur.id) this.update('khaatas', this.find('khaatas', cur.id), closing);
    else {
      const opened = cur.opened_on && cur.opened_on < b.on_date ? cur.opened_on : b.on_date;
      this.insert('khaatas', { id: this.newId(it.temp_id), supplier_id: sid, opened_on: opened, terms: null, commission_pct: null,
        created_by: null, ...closing });
    }
  }

  khaataRenew(sid: number, b: any, it: PendingItem) {
    this.find('suppliers', sid);
    const info = khaataInfo(this.data(), sid);
    if (info.status === 'open') refuse('The khaata is still open. Close it before starting a new one.');
    if (b.on_date < (info.current as any).closed_on) refuse(`The last khaata closed on ${(info.current as any).closed_on}; the new one cannot start before that.`);
    this.insert('khaatas', { id: this.newId(it.temp_id), supplier_id: sid, opened_on: b.on_date, terms: b.terms ?? null,
      commission_pct: b.commission_pct ?? null, created_by: this.ctx.userId, closed_on: null, close_reason: null,
      written_off: null, closed_by: null, ended_shares: null });
    if (b.commission_pct != null) this.update('suppliers', this.find('suppliers', sid), { commission_pct: b.commission_pct });
    if (b.advance) {
      this.insert('advances', { id: this.tmp--, supplier_id: sid, on_date: b.on_date, amount: Number(b.advance), kind: 'given',
        bill_id: null, note: 'Advance with the renewed khaata', created_by: this.ctx.userId });
    }
  }

  khaataReopen(sid: number) {
    this.find('suppliers', sid);
    const info = khaataInfo(this.data(), sid);
    if (info.status === 'open') refuse('The khaata is already open.');
    const k = this.find('khaatas', (info.current as any).id);
    this.remove('advances', (a) => a.supplier_id === sid && a.kind === 'written_off' && a.on_date === k.closed_on);
    for (const sh of k.ended_shares || []) {
      const s = this.rows('khaata_shares').find((x) => x.id === sh.id);
      if (s) this.update('khaata_shares', s, { to_month: sh.to_month });
    }
    this.update('khaatas', k, { closed_on: null, close_reason: null, written_off: null, closed_by: null, ended_shares: null });
  }

  apply(it: PendingItem) {
    if (it.kind === 'daily') return this.daily(it);
    if (it.kind === 'kharcha_del') return this.kharchaDel(it);
    return this.request(it);
  }
}

/** The tables with this device's waiting saves put on top. With `strict`, a refusal of the LAST save is thrown
 *  (used to check a new save before queueing it); refusals of earlier saves just leave them out. */
export function applyPending(t: Tables, items: PendingItem[], ctx: Ctx = { userId: null, now: new Date().toISOString() },
  strict = false): Tables {
  if (!items.length) return t;
  const w = new Writer(t, ctx);
  items.forEach((it, i) => {
    try {
      w.apply(it);
    } catch (e) {
      if (strict && i === items.length - 1) throw e;
    }
  });
  return w.t;
}

/** The row a waiting save made (by its temporary id), so it can be kept once the server gives the real id. */
export function pendingRow(t: Tables, items: PendingItem[], table: string, tempId: number, ctx?: Ctx): any | null {
  const after = applyPending(t, items, ctx);
  return (after[table] || []).find((r) => r.id === tempId) ?? null;
}

// ---------- temporary ids for rows made on this device ----------

/** The table a save makes a new row in (so it needs a temporary id until the server answers). */
export function createdTable(method: string, path: string): string | null {
  if (method.toUpperCase() !== 'POST') return null;
  const p = path.split('?')[0];
  if (p === '/suppliers') return 'suppliers';
  if (p === '/flowers') return 'flowers';
  if (p === '/investors') return 'investors';
  if (p === '/bills') return 'bills';
  if (p === '/khaata-shares') return 'khaata_shares';
  if (/^\/investors\/-?\d+\/entries$/.test(p)) return 'investor_entries';
  if (/^\/suppliers\/-?\d+\/advances$/.test(p)) return 'advances';
  return null;
}

/** [table, temporary id, real id] for every row a save made, from the server's answer to it. */
export function createdIds(it: PendingItem, res: any): [string, number, number][] {
  const out: [string, number, number][] = [];
  if (!res || typeof res !== 'object') return out;
  if (it.kind === 'daily' && it.temp_bill != null) {
    const real = res.bill_ids?.[String(it.row?.supplier_id)];
    if (real != null) out.push(['bills', it.temp_bill, real]);
  }
  if (it.kind === 'request') {
    const table = createdTable(it.method || '', it.path || '');
    if (table && it.temp_id != null && res.id != null) out.push([table, it.temp_id, res.id]);
    for (const b of res.bills || []) {
      const temp = it.temp_bills?.[String(b.supplier_id)];
      if (temp != null) out.push(['bills', temp, b.id]);
    }
  }
  return out;
}

const REFS: Record<string, { seg: string; fields: string[] }> = {
  suppliers: { seg: 'suppliers', fields: ['supplier_id'] },
  investors: { seg: 'investors', fields: ['investor_id'] },
  bills: { seg: 'bills', fields: ['bill_id'] },
  flowers: { seg: 'flowers', fields: ['flower_id'] },
  advances: { seg: 'advances', fields: [] },
  khaata_shares: { seg: 'khaata-shares', fields: [] },
  investor_entries: { seg: 'investor-entries', fields: [] },
};

/** A waiting save with a temporary id swapped for the real one (in its path, body and daily row). */
export function remapItem<T extends PendingItem>(it: T, table: string, temp: number, real: number): T {
  const r = REFS[table];
  if (!r) return it;
  const out: any = { ...it };
  if (out.path) out.path = out.path.replace(new RegExp(`/${r.seg}/${temp}(?=/|$|[?])`), `/${r.seg}/${real}`);
  for (const f of r.fields) {
    if (out.body && out.body[f] === temp) out.body = { ...out.body, [f]: real };
    if (out.row && out.row[f] === temp) out.row = { ...out.row, [f]: real };
  }
  return out;
}
