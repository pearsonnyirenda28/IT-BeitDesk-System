/**
 * BeitDesk Service Worker
 * Municipality of Beitbridge IT Help Desk
 *
 * Strategy:
 * - Static assets (CSS, JS, images, fonts): Cache-first, then network
 * - Dashboard pages: Network-first, fallback to cache
 * - Offline fallback page shown when network unavailable and no cache
 */

const CACHE_NAME      = 'beitdesk-v1';
const OFFLINE_URL     = '/static/pwa/offline.html';

// Assets to pre-cache on install
const PRECACHE_ASSETS = [
  '/static/pwa/offline.html',
  '/static/pwa/manifest.json',
  '/static/pwa/icon-192.png',
  '/static/pwa/icon-512.png',
  '/static/img/beitbridge_logo.jpg',
  '/static/img/beitbridge_bg.png',
  '/static/img/favicon.svg',
];

// Pages to cache for offline access
const CACHE_PAGES = [
  '/dashboard/',
  '/dashboard/tickets/',
  '/dashboard/glossary/',
];

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(PRECACHE_ASSETS).catch(err => {
        console.warn('[BeitDesk SW] Pre-cache partial failure:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── Activate ──────────────────────────────────────────────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin requests
  if (url.origin !== location.origin) return;

  // Skip non-GET and API/admin requests (must always be fresh)
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/dashboard/api/') ||
      url.pathname.startsWith('/admin/')) return;

  // Static assets — Cache first, then network, update cache in background
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        const networkFetch = fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(request, clone));
          }
          return response;
        }).catch(() => cached);
        return cached || networkFetch;
      })
    );
    return;
  }

  // Fonts from Google — Cache first
  if (url.hostname.includes('fonts.')) {
    event.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(c => c.put(request, clone));
        return response;
      }))
    );
    return;
  }

  // HTML pages — Network first, fallback to cache, then offline page
  event.respondWith(
    fetch(request)
      .then(response => {
        // Cache successful page responses
        if (response.ok && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(c => c.put(request, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(request).then(cached => {
          if (cached) return cached;
          // Show offline fallback
          return caches.match(OFFLINE_URL);
        })
      )
  );
});

// ── Background sync for offline ticket creation (future) ─────────────────────
self.addEventListener('sync', event => {
  if (event.tag === 'sync-tickets') {
    event.waitUntil(syncOfflineTickets());
  }
});

async function syncOfflineTickets() {
  // Placeholder for future offline ticket queue sync
  console.log('[BeitDesk SW] Syncing offline tickets...');
}

// ── Push notifications (future) ───────────────────────────────────────────────
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'BeitDesk', {
      body:    data.body || 'You have a new notification',
      icon:    '/static/pwa/icon-192.png',
      badge:   '/static/pwa/icon-72.png',
      tag:     data.tag || 'beitdesk',
      data:    { url: data.url || '/dashboard/' },
      actions: [
        { action: 'open',    title: 'Open BeitDesk' },
        { action: 'dismiss', title: 'Dismiss' },
      ],
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.action !== 'dismiss') {
    const url = event.notification.data?.url || '/dashboard/';
    event.waitUntil(clients.openWindow(url));
  }
});
