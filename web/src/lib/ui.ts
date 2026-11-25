// Small UI helpers: toasts, query params, modal dialogs.
import { esc } from './format';

export function toast(message: string, kind: 'ok' | 'error' = 'ok') {
  const box = document.getElementById('toasts');
  if (!box) return alert(message);
  const el = document.createElement('div');
  el.setAttribute('role', 'status');
  el.className = `pointer-events-auto rounded-lg px-4 py-2.5 text-sm shadow-lg border ${
    kind === 'ok' ? 'bg-panel border-line text-ink' : 'bg-bad text-white border-bad'}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), kind === 'ok' ? 2600 : 6000);
}

export const qp = (name: string) => new URLSearchParams(location.search).get(name);

export function setQuery(params: Record<string, string | null>) {
  const u = new URL(location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v === null) u.searchParams.delete(k);
    else u.searchParams.set(k, v);
  }
  history.replaceState(null, '', u);
}

/** A modal built from HTML. Resolves with the form's values, or null when cancelled. */
export function dialog(
  title: string,
  bodyHtml: string,
  okLabel = 'Save',
  danger = false,
): Promise<Record<string, string> | null> {
  return new Promise((resolve) => {
    const wrap = document.createElement('div');
    wrap.className = 'fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 sm:p-4';
    wrap.innerHTML = `
      <form class="card w-full sm:max-w-md rounded-b-none sm:rounded-xl p-5 space-y-4 shadow-2xl max-h-[90vh] overflow-auto">
        <h2 class="text-lg font-semibold">${esc(title)}</h2>
        <div class="space-y-3">${bodyHtml}</div>
        <div class="flex justify-end gap-2 pt-1">
          <button type="button" data-cancel class="btn-ghost">Cancel</button>
          <button type="submit" class="${danger ? 'btn-danger' : 'btn-primary'}">${esc(okLabel)}</button>
        </div>
      </form>`;
    document.body.appendChild(wrap);
    const form = wrap.querySelector('form')!;
    const close = (v: Record<string, string> | null) => { wrap.remove(); resolve(v); };
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(null); });
    wrap.querySelector('[data-cancel]')!.addEventListener('click', () => close(null));
    wrap.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(null); });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      close(Object.fromEntries(new FormData(form).entries()) as Record<string, string>);
    });
    (form.querySelector('input,select,textarea') as HTMLElement | null)?.focus();
  });
}

/** Wrap an async action: disables the button and shows errors as toasts. */
export async function busy<T>(btn: HTMLButtonElement | null, fn: () => Promise<T>): Promise<T | undefined> {
  if (btn) btn.disabled = true;
  try {
    return await fn();
  } catch (e: any) {
    toast(e?.message || 'Something went wrong', 'error');
    return undefined;
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** Download a file from the API (sends the login token), e.g. download('/export/backup'). */
export async function download(path: string, btn?: HTMLButtonElement | null) {
  const { API_URL, getToken } = await import('./api');
  const label = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.textContent = 'Preparing…'; }
  try {
    const res = await fetch(API_URL + path, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new Error(d?.detail || `Download failed (${res.status})`);
    }
    const name = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '')?.[1] || 'download';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await res.blob());
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    toast(`Downloaded ${name}`);
  } catch (e: any) {
    toast(e?.message || 'Download failed', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = label!; }
  }
}
