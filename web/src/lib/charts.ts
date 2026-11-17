// Chart.js with the app's chart rules: fixed series order, thin marks, 4px rounded bar ends,
// hairline grid, hover tooltips, one y-axis per chart. Colours come from CSS variables so
// light and dark mode each use their own validated steps.
import {
  BarController, BarElement, CategoryScale, Chart, Filler, Legend, LinearScale, LineController, LineElement,
  PointElement, Tooltip, type ChartConfiguration,
} from 'chart.js';
import { compact, num } from './format';

Chart.register(BarController, BarElement, CategoryScale, Filler, Legend, LinearScale, LineController, LineElement, PointElement, Tooltip);

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const rgb = (name: string) => `rgb(${css(name)})`;

/** Series colour by slot (1-based). Colour follows the entity, so pass a fixed slot per entity. */
export const series = (slot: number) => css(`--series-${((slot - 1) % 4) + 1}`);

function themed(): Partial<ChartConfiguration['options']> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        position: 'top',
        align: 'start',
        labels: { color: rgb('--muted'), boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2, font: { size: 12 } },
      },
      tooltip: {
        backgroundColor: rgb('--panel'),
        titleColor: rgb('--ink'),
        bodyColor: rgb('--ink'),
        borderColor: rgb('--line'),
        borderWidth: 1,
        padding: 10,
        boxPadding: 4,
        usePointStyle: true,
      },
    },
    scales: {
      x: { grid: { display: false }, border: { color: css('--grid') }, ticks: { color: css('--axis'), font: { size: 11 }, maxRotation: 0, autoSkipPadding: 8 } },
      y: {
        beginAtZero: true,
        grid: { color: css('--grid'), lineWidth: 1 },
        border: { display: false },
        ticks: { color: css('--axis'), font: { size: 11 }, callback: (v) => compact(Number(v)), maxTicksLimit: 6 },
      },
    },
  } as any;
}

export function makeChart(canvas: HTMLCanvasElement, config: ChartConfiguration, opts: { money?: boolean; unit?: string; legend?: boolean } = {}) {
  const base = themed() as any;
  const fmt = (v: number) => (opts.money ? 'Rs ' + num(v) : num(v) + (opts.unit ? ' ' + opts.unit : ''));
  base.plugins.tooltip.callbacks = { label: (c: any) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` };
  if (opts.legend === false) base.plugins.legend.display = false;
  const merged: any = { ...config, options: deepMerge(base, config.options || {}) };
  for (const ds of merged.data.datasets) {
    if ((ds.type || merged.type) === 'bar') Object.assign(ds, { borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: 'bottom', maxBarThickness: 24, ...ds });
    else Object.assign(ds, { borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, pointHoverBorderWidth: 2, pointHoverBorderColor: rgb('--panel'), tension: 0.25, ...ds });
  }
  const existing = Chart.getChart(canvas);
  existing?.destroy();
  const chart = new Chart(canvas, merged);
  return chart;
}

function deepMerge(a: any, b: any): any {
  const out = Array.isArray(a) ? [...a] : { ...a };
  for (const [k, v] of Object.entries(b)) out[k] = v && typeof v === 'object' && !Array.isArray(v) && a[k] ? deepMerge(a[k], v) : v;
  return out;
}

/** Run `draw` now and again whenever light/dark changes (colours are read when drawing). */
export function onTheme(draw: () => void) {
  draw();
  window.addEventListener('themechange', () => requestAnimationFrame(draw));
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => requestAnimationFrame(draw));
}
