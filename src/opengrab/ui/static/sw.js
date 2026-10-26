/* OpenGrab service worker — cache-first for static UI assets.
   API calls always go to the network (never cached). */
const CACHE = "opengrab-ui-v2";
const SCOPE = new URL("./", self.registration.scope);
const ASSETS = [
  "",
  "index.html",
  "style.css",
  "app.js",
  "opengrab.svg",
  "manifest.webmanifest",
].map((path) => new URL(path, SCOPE).href);
const API_PREFIX = new URL("api/", SCOPE).pathname;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith(API_PREFIX)) return; // never cache API
  e.respondWith(
    caches.match(e.request).then((hit) => {
      if (hit) return hit;
      return fetch(e.request).then((res) => {
        if (res.ok && url.origin === location.origin) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
        }
        return res;
      });
    })
  );
});
