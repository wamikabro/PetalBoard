// Renders a bill on the shop's own printed form (web/public/bill/empty-bill.jpg), writing the entries
// into its boxes the way they were written by hand. Used by the bill page and "print all bills".
// Styles: src/styles/billsheet.css. Box positions are measured on the 1132 x 1600 px form image.
import { esc, kg, num } from './format';

export const FORM_IMAGE = '/bill/empty-bill.jpg';
const W = 1132;
const H = 1600;

/** Day cells are written plain on paper: 1105, not 1,105. */
const plain = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(v));

export type Settings = {
  name_ur: string; name_en: string; tagline_ur: string; address_ur: string; proprietor_label_ur: string;
  proprietor1_name_ur: string; proprietor1_phone: string; proprietor2_name_ur: string; proprietor2_phone: string;
};

export type Kharcha = { id: number; amount: number; tag: string | null; note: string | null };
export type Day = {
  day: number; weight: number | null; rate: number | null; amount: number | null; note: string | null;
  kharcha: Kharcha[]; kharcha_total: number; mismatch?: number;
};
export type Bill = {
  id: number; month: string; status: 'open' | 'closed'; commission_pct: number; opening_due: number; note: string | null;
  supplier: { id: number; name: string; name_ur: string | null; phone: string | null };
  flower: { id: number; name: string; name_ur: string | null } | null;
  days: Day[];
  totals: { total: number; commission: number; net: number; kharcha: number; opening_due: number; final: number;
            weight: number; days_supplied: number; avg_rate: number | null };
  settlement: { paid_cash: number | null; advance_cut: number | null; carried: number | null; closed_at: string | null };
  advance_balance: number;
  warnings: string[];
  paper_totals: Record<string, number> | null;
};

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// ---- measured on the form image (pixels) ----
// Day rows: row k runs from ROW_LINES[k-1] to ROW_LINES[k].
const ROW_LINES = [389, 426, 462, 497, 532, 568, 603, 638, 673, 708, 742, 776, 811, 846, 882, 916, 951, 986, 1020, 1055,
  1090, 1124, 1158, 1192, 1226, 1259, 1292, 1326, 1359, 1392, 1427, 1464];
// Columns left to right: kharcha | amount | rate | weight | (printed day number)
const COLS = { kharcha: [382, 514], amount: [514, 652], rate: [652, 798], weight: [798, 936] } as const;
const BOX = {
  name: [420, 236, 1010, 282],        // on the line after نام
  month: [60, 236, 310, 282],         // on the line after تاریخ
  advance: [46, 724, 262, 778],       // ایڈوانس box
  total: [42, 790, 262, 854],         // ٹوٹل رقم
  commission: [42, 854, 262, 908],    // کمیشن
  net: [42, 908, 262, 960],           // رقم
  kharcha: [42, 960, 262, 1016],      // خرچہ
  final: [50, 1392, 262, 1460],       // ٹوٹل box
  settle: [40, 1468, 365, 1500],      // under the ٹوٹل box (closed bills)
} as const;

const pct = (v: number, of: number) => `${((v / of) * 100).toFixed(3)}%`;
function at([x0, y0, x1, y1]: readonly number[], html: string, cls = '') {
  return `<div class="bs-at ${cls}" style="left:${pct(x0, W)};top:${pct(y0, H)};width:${pct(x1 - x0, W)};height:${pct(y1 - y0, H)}">${html}</div>`;
}
const ink = (s: string, cls = '') => (s ? `<span class="bs-ink ${cls}">${s}</span>` : '');

function kharchaText(d: Day, opening: number): string {
  const parts: string[] = [];
  if (d.day === 1 && opening > 0) parts.push(plain(opening));
  for (const k of d.kharcha) parts.push(plain(k.amount) + (k.tag ? `<sup>${esc(k.tag)}</sup>` : ''));
  return parts.join('+');
}

/** Final total written the way the shop writes it: "2641/=" or "-23931". */
const finalText = (v: number) => (v < 0 ? `-${num(-v)}` : `${num(v)}/=`);

function settlementNote(bill: Bill): string {
  const st = bill.settlement;
  const bits: string[] = [];
  if (st.paid_cash) bits.push(st.paid_cash > 0 ? `Paid ${num(st.paid_cash)}` : `Received ${num(-st.paid_cash)}`);
  if (st.advance_cut) bits.push(st.advance_cut > 0 ? `Advance cut ${num(st.advance_cut)}` : `To advance ${num(-st.advance_cut)}`);
  if (st.carried) bits.push(`Carried ${st.carried < 0 ? '-' : ''}${num(Math.abs(st.carried))}`);
  return bits.join(' · ') || 'Settled';
}

export function billSheetHtml(bill: Bill, _s?: Settings): string {
  const [y, m] = bill.month.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const t = bill.totals;
  const parts: string[] = [];

  // "Ajaz (Genda)" is written on paper as "Ajaz" with the flower beside it.
  const paperName = bill.flower?.name_ur ? bill.supplier.name.replace(/\s*\([^)]*\)\s*$/, '') : bill.supplier.name;
  parts.push(at(BOX.name, `${ink(esc(paperName))}${bill.flower?.name_ur ? ink(esc(bill.flower.name_ur), 'bs-ur') : ''}`, 'bs-line'));
  parts.push(at(BOX.month, ink(`${MONTH_SHORT[m - 1]} ${y}`), 'bs-line'));

  for (let day = 1; day <= 31; day++) {
    const top = ROW_LINES[day - 1], bottom = ROW_LINES[day];
    if (day > daysInMonth) {
      parts.push(at([COLS.kharcha[0], top, COLS.weight[1], bottom], '', 'bs-off'));
      continue;
    }
    const d = bill.days[day - 1];
    if (!d) continue;
    const supplied = d.weight !== null || d.amount !== null;
    if (supplied) {
      parts.push(at([COLS.weight[0], top, COLS.weight[1], bottom], ink(kg(d.weight))));
      parts.push(at([COLS.rate[0], top, COLS.rate[1], bottom], ink(plain(d.rate))));
      parts.push(at([COLS.amount[0], top, COLS.amount[1], bottom], ink(plain(d.amount)), d.mismatch !== undefined ? 'bs-flag' : ''));
    }
    const kh = kharchaText(d, bill.opening_due);
    if (kh) parts.push(at([COLS.kharcha[0], top, COLS.kharcha[1], bottom], ink(kh, kh.replace(/<[^>]+>/g, '').length > 9 ? 'bs-small' : '')));
  }

  const commission = bill.commission_pct > 0;
  if (bill.advance_balance) parts.push(at(BOX.advance, ink(num(bill.advance_balance))));
  parts.push(at(BOX.total, ink(num(t.total))));
  if (commission) {
    parts.push(at(BOX.commission, ink(num(t.commission))));
    parts.push(at(BOX.net, ink(num(t.net))));
  }
  const kharchaAll = t.kharcha + Math.max(t.opening_due, 0);
  if (kharchaAll) parts.push(at(BOX.kharcha, ink(num(kharchaAll))));
  parts.push(at(BOX.final, ink(finalText(t.final), 'bs-big')));
  if (bill.status === 'closed') parts.push(at(BOX.settle, ink(settlementNote(bill), 'bs-small')));

  return `
<article class="bs-sheet" aria-label="Bill for ${esc(bill.supplier.name)}, ${MONTH_SHORT[m - 1]} ${y}">
  <img class="bs-form" src="${FORM_IMAGE}" alt="" />
  ${parts.join('\n  ')}
</article>`;
}
