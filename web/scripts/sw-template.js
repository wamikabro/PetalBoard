// PetalBoard service worker (made by scripts/build-sw.mjs; do not edit dist/sw.js).
// Keeps every page, script, style, font and the blank bill form on the device, so the app opens
// with no internet. Data from the API is not handled here.
const VERSION = '__VERSION__';
const PRECACHE = __PRECACHE__;
const APP = `petalboard-app-${VERSION}`;
const FONTS = 'petalboard-fonts';
// Some servers add "Vary: Origin"; a saved file must still be found when the request comes from a script.
const MATCH = { ignoreSearch: true, ignoreVary: true };

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(APP);
    // One by one, so a single failed file does not stop the rest from being saved.
    await Promise.all(PRECACHE.map((url) => cache.add(new Request(url, { cache: 'reload' })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('petalboard-app-') && key !== APP) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

/** Pages: the saved copy at once (the app opens instantly, with or without internet), refreshed from the
 *  network in the background for next time. With no saved copy yet, the network (then the dashboard). */
async function page(event) {
  const { request } = event;
  const url = new URL(request.url);
  const key = url.pathname.replace(/\.html$/, '').replace(/\/$/, '') || '/';
  const cache = await caches.open(APP);
  const hit = await cache.match(key, MATCH);
  const fresh = fetch(request).then((res) => {
    // Only a normal page goes in (not a 404 page, a redirect or an error).
    if (res.ok && !res.redirected && (res.headers.get('content-type') || '').includes('text/html')) cache.put(key, res.clone());
    return res;
  });
  if (hit) {
    event.waitUntil(fresh.catch(() => {}));
    return hit;
  }
  try {
    return await fresh;
  } catch {
    return (await cache.match('/', MATCH)) || Response.error();
  }
}

/** Saved copy first (files have a hash in their name, so they never change). */
async function asset(request) {
  const cache = await caches.open(APP);
  const hit = await cache.match(request, MATCH);
  if (hit) return hit;
  const res = await fetch(request);
  if (res.ok) cache.put(request, res.clone());
  return res;
}

/** Google Fonts: saved copy at once, refreshed in the background. */
async function font(request) {
  const cache = await caches.open(FONTS);
  const hit = await cache.match(request, { ignoreVary: true });
  const fresh = fetch(request).then((res) => {
    if (res.ok || res.type === 'opaque') cache.put(request, res.clone());
    return res;
  }).catch(() => hit);
  return hit || fresh;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (request.mode === 'navigate') event.respondWith(page(event));
    else event.respondWith(asset(request));
  } else if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(font(request));
  }
  // Anything else (the API) goes straight to the network.
});
