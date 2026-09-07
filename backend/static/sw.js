/**
 * sw.js — Service Worker for BESS Field Log PWA
 * Strategy: Cache-first for app shell, network-first for API calls.
 */

const CACHE  = 'bess-v8';
const SHELL  = [
  '/app/',
  '/app/css/app.css',
  '/app/js/db.js',
  '/app/js/api.js',
  '/app/js/app.js',
  '/app/manifest.json',
  '/app/icons/icon-192.png',
];

// ── Install: cache app shell ─────────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

// ── Activate: delete old caches ──────────────────────────────────────────────
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
  const isApi = ['/auth/', '/sync/', '/worklogs/', '/projects', '/stock', '/events', '/health'].some(p => url.pathname.startsWith(p));
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
    caches.match(e.request).then(cached => {
      if (cached) return cached;
      return fetch(e.request).then(res => {
        // Cache only successful GET responses
        if (res.ok && e.request.method === 'GET') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() =>
        // Offline and not in cache → serve index as fallback
        caches.match('/app/')
      );
    })
  );
});
