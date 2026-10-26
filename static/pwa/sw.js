/* BeitDesk Service Worker — PWA + Web Push */
const CACHE  = 'beitdesk-v3';
const OFFLINE = '/static/pwa/offline.html';
const ASSETS  = [OFFLINE, '/static/img/beitbridge_logo.jpg'];

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

// ── Activate ──────────────────────────────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch (offline fallback) ──────────────────────────────────────────────────
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/') || e.request.url.includes('/accounts/')) return;
  e.respondWith(
    fetch(e.request).catch(() =>
      caches.match(e.request).then(r => r || caches.match(OFFLINE))
    )
  );
});

// ── Push Notifications ────────────────────────────────────────────────────────
self.addEventListener('push', e => {
  let data = { title: 'BeitDesk', body: 'You have a new notification', url: '/dashboard/' };
  try {
    if (e.data) data = { ...data, ...e.data.json() };
  } catch (_) {}

  // WhatsApp-style: one OS notification per ticket (shared `tag`) that updates
  // and re-alerts as new messages arrive, and no OS popup while the app is
  // already open and focused (the in-page toast covers that case).
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      if (list.some(c => c.focused && c.visibilityState === 'visible')) return;
      return self.registration.showNotification(data.title, {
        body:      data.body,
        icon:      data.icon  || '/static/pwa/icon-192.png',
        badge:     data.badge || '/static/pwa/icon-72.png',
        tag:       data.tag   || 'beitdesk',
        renotify:  !!data.renotify,
        timestamp: Date.now(),
        data:      { url: data.url || '/dashboard/' },
        vibrate:   [100, 50, 100],
        requireInteraction: false,
      });
    })
  );
});

// ── Notification click — open/focus tab ───────────────────────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/dashboard/';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.origin) && 'focus' in c) {
          c.navigate(url);
          return c.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
