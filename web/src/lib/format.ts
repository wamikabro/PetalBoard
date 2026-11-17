// Number and date formatting used across the app. Money is whole Pakistani rupees.
const nf = new Intl.NumberFormat('en-PK', { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat('en-PK', { maximumFractionDigits: 3 });

export const num = (v: number | null | undefined) => (v === null || v === undefined ? '' : nf.format(v));
export const kg = (v: number | null | undefined) => (v === null || v === undefined ? '' : nf2.format(v));
export const rs = (v: number | null | undefined) => (v === null || v === undefined ? '—' : 'Rs ' + nf.format(v));

/** Signed balance: "Rs 2,641" or "−Rs 835". */
export const rsSigned = (v: number) => (v < 0 ? '−Rs ' + nf.format(-v) : 'Rs ' + nf.format(v));

/** Compact for chart axes and tiles: 12.9K, 1.2M. */
export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1_000_000) return (v / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (a >= 10_000) return Math.round(v / 1000) + 'K';
  return nf.format(v);
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const monthName = (m: string) => { const [y, mo] = m.split('-').map(Number); return `${MONTHS[mo - 1]} ${y}`; };
export const monthShort = (m: string) => { const [y, mo] = m.split('-').map(Number); return `${MONTHS[mo - 1].slice(0, 3)} ${String(y).slice(2)}`; };
const pad = (n: number) => String(n).padStart(2, '0');
export const thisMonth = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };
export const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
export const shiftMonth = (m: string, by: number) => {
  const [y, mo] = m.split('-').map(Number);
  const d = new Date(y, mo - 1 + by, 1);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
};
export const shiftDay = (iso: string, by: number) => {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + by);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};
export const prettyDate = (iso: string) =>
  new Date(iso + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

/** Balance words: who owes whom. */
export function balanceWords(v: number): string {
  if (v > 0) return 'Shop owes supplier';
  if (v < 0) return 'Supplier owes shop';
  return 'Settled';
}

const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
