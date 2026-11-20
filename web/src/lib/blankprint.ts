// "Numbers only" printing: prints just the handwriting-style entries, without the form picture, at the
// same places, so they land in the boxes of the shop's already printed blank forms.
// Prints full-page A4 with no margin (named page `blankform` in billsheet.css). Printers differ, so the
// shift (mm) and size (%) can be adjusted; they are remembered on this device.
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

export async function printNumbersOnly() {
  const c = load();
  const v = await dialog('Print numbers only', `
    <p class="text-sm text-muted">Prints only the entries, without the form design, in the same places, so you can print onto the shop's printed blank bills. Put the blank forms in the printer the right way up. In the print window keep paper A4, margins “None” and scale 100%.</p>
    <p class="text-sm text-muted">If the numbers land a little off the boxes, move them here and print again (this device remembers it).</p>
    <div class="grid grid-cols-3 gap-3">
      <div><label class="label">Move right (mm)</label><input name="dx" class="input" type="number" step="0.5" value="${c.dx}" /></div>
      <div><label class="label">Move down (mm)</label><input name="dy" class="input" type="number" step="0.5" value="${c.dy}" /></div>
      <div><label class="label">Size (%)</label><input name="scale" class="input" type="number" step="0.5" min="80" max="120" value="${c.scale}" /></div>
    </div>
    <p class="text-xs text-muted">Negative numbers move left / up.</p>`, 'Print');
  if (!v) return;
  const cal: Cal = { dx: Number(v.dx) || 0, dy: Number(v.dy) || 0, scale: Number(v.scale) || 100 };
  store(cal);
  const root = document.documentElement;
  root.style.setProperty('--blank-dx', `${cal.dx}mm`);
  root.style.setProperty('--blank-dy', `${cal.dy}mm`);
  root.style.setProperty('--blank-scale', String(cal.scale / 100));
  document.body.classList.add('print-blank');
  const done = () => { document.body.classList.remove('print-blank'); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
}
