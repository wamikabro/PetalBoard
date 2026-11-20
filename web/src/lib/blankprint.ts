// Printing bills. The default is "numbers only": just the handwriting-style entries, without the form
// picture, at the same places, so they land in the boxes of the shop's already printed blank forms.
// The screen still shows the form. "With design" prints the full form (for a PDF to send).
// Numbers-only pages are full A4 with no margin (named page `blankform` in billsheet.css). Printers differ,
// so the shift (mm) and size (%) can be adjusted; they are remembered on this device.
import { dialog } from './ui';

const KEY = 'petalboard.blankPrint';
type Cal = { dx: number; dy: number; scale: number };
const DEFAULT: Cal = { dx: 0, dy: 0, scale: 100 };

function load(): Cal {
  try { return { ...DEFAULT, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { ...DEFAULT }; }
}
function store(c: Cal) {
  try { localStorage.setItem(KEY, JSON.stringify(c)); } catch { /* private window: just don't remember */ }
}

/** Put the saved shift and size into the page (also used by Ctrl+P). Call once on load. */
export function applyPrintCalibration(c: Cal = load()) {
  const root = document.documentElement;
  root.style.setProperty('--blank-dx', `${c.dx}mm`);
  root.style.setProperty('--blank-dy', `${c.dy}mm`);
  root.style.setProperty('--blank-scale', String(c.scale / 100));
}

/** Print the bill(s): numbers only (default), or the full form with its design. */
export function printBills(withDesign = false) {
  applyPrintCalibration();
  if (!withDesign) { window.print(); return; }
  document.body.classList.add('print-full');
  const done = () => { document.body.classList.remove('print-full'); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
}

/** Note shown above the sheet on screen (never printed). */
export const PRINT_NOTE = `<p class="no-print text-xs text-muted mb-2">🖨 <b>Print</b> sends <b>only the numbers</b> (not the form design) so they land on your printed blank bills. In the print window choose margins “None” and scale 100%.</p>`;

/** Ask for the shift and size of the numbers on the printed forms. */
export async function adjustPrintPosition() {
  const c = load();
  const v = await dialog('Print position', `
    <p class="text-sm text-muted">If the printed numbers land a little off the boxes of your blank forms, move them here. This device remembers it.</p>
    <div class="grid grid-cols-3 gap-3">
      <div><label class="label">Move right (mm)</label><input name="dx" class="input" type="number" step="0.5" value="${c.dx}" /></div>
      <div><label class="label">Move down (mm)</label><input name="dy" class="input" type="number" step="0.5" value="${c.dy}" /></div>
      <div><label class="label">Size (%)</label><input name="scale" class="input" type="number" step="0.5" min="80" max="120" value="${c.scale}" /></div>
    </div>
    <p class="text-xs text-muted">Negative numbers move left / up. Put the blank forms in the printer the right way up.</p>`, 'Save and print');
  if (!v) return;
  const cal: Cal = { dx: Number(v.dx) || 0, dy: Number(v.dy) || 0, scale: Number(v.scale) || 100 };
  store(cal);
  applyPrintCalibration(cal);
  printBills();
}
