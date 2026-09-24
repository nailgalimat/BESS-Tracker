/**
 * sw.js — Service Worker for BESS Field Log PWA
 * Strategy: Cache-first for app shell, network-first for API calls.
 *
 * Every PWA change: bump V here AND the ?v= on the css/js tags in index.html,
 * to the same number. The shell is cached under the exact URLs index.html
 * requests. It used to cache /app/js/app.js while the page asked for
 * app.js?v=11: after an update the old cache was deleted, and the first start
 * without network got HTML (the /app/ fallback) instead of the script — the
 * app did not open on site.
 */

const V      = '20';
const CACHE  = 'bess-v' + V;
const SHELL  = [
  '/app/',
  '/app/css/app.css?v=' + V,
  '/app/js/db.js?v=' + V,
  '/app/js/api.js?v=' + V,
  '/app/js/app.js?v=' + V,
  '/app/manifest.json',
  '/app/icons/icon-192.png',
];

// ── Install: cache app shell ─────────────────────────────────────────────────
// addAll is all-or-nothing: if any shell file fails to download, this version
// does not install and the previous one (with its cache) stays in charge.
self.addEventListener('install', e => {
  // No skipWaiting() here on purpose: a new version must not swap itself in
  // under a half-written record. It waits, the page shows "New version
  // available", and the reload happens when the engineer taps it (the page
  // then posts SKIP_WAITING, below).
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
});

// The page asks for the new version once the draft is saved.
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

// ── Activate: delete old caches (only once the new shell is fully cached) ────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── Fetch ────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls → network-only (no cache)
  const isApi = ['/auth/', '/sync/', '/worklogs/', '/projects', '/stock', '/events',
                 '/checklists', '/action-items', '/health']
                .some(p => url.pathname.startsWith(p));
  if (isApi) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({ detail: 'Offline' }), {
          status:  503,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    );
    return;
  }

  // App shell → cache-first, network fallback
  e.respondWith(
    caches.match(e.request)
      // A shell file under another ?v= (a page cached before the update) is
      // still the right file: this cache holds exactly one version of each.
      .then(cached => cached || (url.origin === self.location.origin
                                 && url.pathname.startsWith('/app/')
                                 ? caches.match(e.request, { ignoreSearch: true })
                                 : undefined))
      .then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(res => {
          // Cache only successful GET responses
          if (res.ok && e.request.method === 'GET') {
            const clone = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        }).catch(() =>
          // Offline and not cached: a page navigation gets the app; a script or
          // stylesheet must not be answered with HTML.
          e.request.mode === 'navigate'
            ? caches.match('/app/')
            : new Response('', { status: 504, statusText: 'Offline' })
        );
      })
  );
});
