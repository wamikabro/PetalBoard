// Renders a bill exactly like the printed paper sheet of Tauqeer Ahmed Flower House.
// Used by the bill page and the "print all bills" page. Styles: src/styles/billsheet.css.
import { esc, kg, num } from './format';

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

const WA = `<svg viewBox="0 0 24 24" class="bs-wa" aria-hidden="true"><circle cx="12" cy="12" r="11" fill="#25d366"/><path fill="#fff" d="M16.7 13.9c-.2-.1-1.5-.7-1.7-.8-.2-.1-.4-.1-.6.1l-.8 1c-.1.2-.3.2-.5.1-.7-.3-1.4-.7-2-1.3-.5-.5-1-1.1-1.3-1.7-.1-.2 0-.4.1-.5l.4-.5.2-.4c.1-.2 0-.3 0-.4l-.8-1.9c-.2-.5-.4-.4-.6-.4h-.5c-.2 0-.4.1-.6.3-.6.6-.9 1.4-.9 2.2.1 1 .5 2 1.1 2.8 1.2 1.7 2.7 3 4.6 3.7.5.2 1 .3 1.5.4.6.1 1.2 0 1.7-.3.4-.2.7-.6.8-1 .1-.3.1-.6 0-.9-.1-.1-.3-.2-.5-.3z"/></svg>`;

// Decorative roses drawn in SVG (the paper has a rose bouquet print here).
const ROSES = `<svg viewBox="0 0 200 200" class="bs-roses" aria-hidden="true">
  <g fill="none" stroke="#9db58a" stroke-width="2"><path d="M60 170c10-30 25-50 45-70"/><path d="M140 175c-5-30-15-55-35-75"/><path d="M100 185V110"/></g>
  <g fill="#b9cfa5"><ellipse cx="70" cy="140" rx="12" ry="5" transform="rotate(-35 70 140)"/><ellipse cx="128" cy="145" rx="12" ry="5" transform="rotate(30 128 145)"/><ellipse cx="96" cy="160" rx="10" ry="4" transform="rotate(-70 96 160)"/></g>
  ${[[100, 78, 34], [58, 105, 24], [146, 100, 26], [80, 45, 18], [130, 52, 16], [104, 124, 20]].map(([x, y, r]) => `
  <g transform="translate(${x} ${y})">
    <circle r="${r}" fill="#f6c9cf"/><circle r="${r * 0.72}" fill="#f2b3bc"/><circle r="${r * 0.46}" fill="#ec9ca8"/>
    <path d="M${-r * 0.3} 0a${r * 0.3} ${r * 0.3} 0 1 1 ${r * 0.6} 0" stroke="#e2879a" stroke-width="2" fill="none"/>
  </g>`).join('')}
</svg>`;

// Header pictures: garland car, note garland, bouquet, rose wreaths (drawn, not photos).
const HEADER_ART = `
<div class="bs-pic bs-car" aria-hidden="true"><svg viewBox="0 0 120 80"><rect width="120" height="80" fill="#eef1f6"/>
  <path d="M10 58c2-12 12-20 30-22l14-12h30l16 14c10 2 14 8 14 20z" fill="#fafafa" stroke="#9aa3b5"/>
  <path d="M40 36c12-10 30-14 46 0" stroke="#c0263a" stroke-width="7" fill="none" stroke-linecap="round" stroke-dasharray="2 5"/>
  <circle cx="34" cy="62" r="9" fill="#333"/><circle cx="92" cy="62" r="9" fill="#333"/><rect x="50" y="50" width="22" height="6" fill="#ddd" stroke="#999"/></svg></div>
<div class="bs-pic" aria-hidden="true"><svg viewBox="0 0 60 70"><rect width="60" height="70" fill="#d8c8e8"/>
  <path d="M30 8c-14 14-18 36-2 56M30 8c14 14 18 36 2 56" stroke="#7b5ea7" stroke-width="7" fill="none" stroke-dasharray="3 3"/>
  <circle cx="30" cy="62" r="6" fill="#c0263a"/></svg></div>
<div class="bs-pic" aria-hidden="true"><svg viewBox="0 0 60 70"><rect width="60" height="70" fill="#2c3b2a"/>
  ${[[20, 25], [38, 22], [28, 38], [44, 40], [16, 44], [32, 52]].map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="7" fill="${['#f4d6dc', '#e86a7c', '#f9f2e6', '#f3b5c0', '#ffffff', '#e86a7c'][i]}"/>`).join('')}</svg></div>
<div class="bs-wreaths" aria-hidden="true"><svg viewBox="0 0 130 60">${[22, 65, 108].map((x) => `
  <circle cx="${x}" cy="30" r="19" fill="none" stroke="#fdfdf6" stroke-width="12" stroke-dasharray="5 2"/>
  <circle cx="${x}" cy="30" r="19" fill="none" stroke="#f2c230" stroke-width="3" stroke-dasharray="3 9"/>`).join('')}</svg></div>`;

function cell(v: string, cls = '') {
  return `<td class="${cls}">${v ? `<span class="bs-ink">${v}</span>` : ''}</td>`;
}

function kharchaCell(d: Day, opening: number): string {
  const parts: string[] = [];
  if (d.day === 1 && opening > 0) parts.push(num(opening));
  for (const k of d.kharcha) {
    parts.push(num(k.amount) + (k.tag ? `<sup>${esc(k.tag)}</sup>` : ''));
  }
  return parts.join('+');
}

/** Final total written the way the shop writes it: "2641/=" or "-23931". */
function finalText(v: number): string {
  return v < 0 ? `-${num(-v)}` : `${num(v)}/=`;
}

export function billSheetHtml(bill: Bill, s: Settings): string {
  const [y, m] = bill.month.split('-').map(Number);
  const daysInMonth = new Date(y, m, 0).getDate();
  const t = bill.totals;
  const supplierName = bill.supplier.name;
  const flowerUr = bill.flower?.name_ur ? `<span class="bs-flower urdu">${esc(bill.flower.name_ur)}</span>` : '';

  const rows = [];
  for (let day = 1; day <= 31; day++) {
    const d = bill.days[day - 1];
    if (day > daysInMonth || !d) {
      rows.push(`<tr class="bs-off"><td class="bs-date">${day}</td><td></td><td></td><td></td><td></td></tr>`);
      continue;
    }
    const supplied = d.weight !== null || d.amount !== null;
    rows.push(`<tr>
      <td class="bs-date">${day}</td>
      ${cell(supplied ? kg(d.weight) : '')}
      ${cell(supplied ? num(d.rate) : '')}
      ${cell(supplied ? num(d.amount) : '', d.mismatch !== undefined ? 'bs-flag' : '')}
      ${cell(kharchaCell(d, bill.opening_due), 'bs-kh')}
    </tr>`);
  }

  const showCommission = bill.commission_pct > 0;
  return `
<article class="bs-sheet" dir="rtl" lang="ur">
  <header class="bs-head">
    <div class="bs-head-text">
      <h1 class="bs-shop urdu">${esc(s.name_ur)}</h1>
      <p class="bs-tagline urdu">${esc(s.tagline_ur)}</p>
      <div class="bs-head-pics">${HEADER_ART}</div>
      <p class="bs-address urdu">${esc(s.address_ur)}</p>
    </div>
  </header>

  <div class="bs-fields">
    <div class="bs-field bs-name"><span class="urdu">نام</span><div class="bs-box"><span class="bs-ink" dir="ltr">${esc(supplierName)}</span>${flowerUr}</div></div>
    <div class="bs-field bs-month"><span class="urdu">تاریخ</span><div class="bs-box"><span class="bs-ink" dir="ltr">${MONTH_SHORT[m - 1]} ${y}</span></div></div>
  </div>

  <div class="bs-body">
    <table class="bs-table">
      <thead><tr>
        <th class="urdu">تاریخ</th><th class="urdu">وزن</th><th class="urdu">ریٹ</th><th class="urdu">ٹوٹل رقم</th><th class="urdu">خرچہ</th>
      </tr></thead>
      <tbody>${rows.join('')}</tbody>
    </table>

    <aside class="bs-side">
      <div class="bs-owners">
        <p><span class="bs-prop urdu">${esc(s.proprietor_label_ur)}</span> <span class="urdu bs-owner">${esc(s.proprietor1_name_ur)}</span></p>
        <p class="bs-phone" dir="ltr">${WA}${esc(s.proprietor1_phone)}</p>
        <p class="urdu bs-owner">${esc(s.proprietor2_name_ur)}</p>
        <p class="bs-phone" dir="ltr">${WA}${esc(s.proprietor2_phone)}</p>
      </div>

      <div class="bs-adv"><span class="urdu">ایڈوانس</span><div class="bs-box">${bill.advance_balance ? `<span class="bs-ink" dir="ltr">${num(bill.advance_balance)}</span>` : ''}</div></div>

      <table class="bs-sum">
        <tr><th class="urdu">ٹوٹل رقم</th><td><span class="bs-ink">${num(t.total)}</span></td></tr>
        <tr><th class="urdu">کمیشن</th><td>${showCommission ? `<span class="bs-ink">${num(t.commission)}</span>` : ''}</td></tr>
        <tr><th class="urdu">رقم</th><td>${showCommission ? `<span class="bs-ink">${num(t.net)}</span>` : ''}</td></tr>
        <tr><th class="urdu">خرچہ</th><td>${t.kharcha + t.opening_due ? `<span class="bs-ink">${num(t.kharcha + Math.max(t.opening_due, 0))}</span>` : ''}</td></tr>
      </table>

      <div class="bs-deco">${ROSES}</div>

      <div class="bs-final"><span class="urdu">ٹوٹل</span><span class="bs-ink" dir="ltr">${finalText(t.final)}</span></div>
      ${bill.status === 'closed' && bill.settlement ? settlementNote(bill) : ''}
      <div class="bs-sign"><span class="urdu">دستخط</span><span class="bs-line"></span></div>
    </aside>
  </div>
</article>`;
}

function settlementNote(bill: Bill): string {
  const st = bill.settlement;
  const bits: string[] = [];
  if (st.paid_cash) bits.push(st.paid_cash > 0 ? `Paid ${num(st.paid_cash)}` : `Received ${num(-st.paid_cash)}`);
  if (st.advance_cut) bits.push(st.advance_cut > 0 ? `Advance cut ${num(st.advance_cut)}` : `Added to advance ${num(-st.advance_cut)}`);
  if (st.carried) bits.push(`Carried ${st.carried < 0 ? '-' : ''}${num(Math.abs(st.carried))}`);
  if (!bits.length) bits.push('Settled');
  return `<p class="bs-settle" dir="ltr"><span class="bs-ink">${bits.join(' · ')}</span></p>`;
}
