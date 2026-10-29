/* OpenGrab service worker: versioned cache (B17).
   The server replaces __OPENGRAB_VERSION__ with the app version when it serves
   this file, so every release gets a fresh cache and old caches are deleted.
   HTML, JS and CSS are network-first (cache only as offline fallback);
   other static assets are cache-first. API calls are never cached. */
const VERSION = "__OPENGRAB_VERSION__";
const CACHE = "opengrab-ui-" + VERSION;
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
const NETWORK_FIRST = /(\/|\.html|\.js|\.css)$/;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("opengrab-ui-") && k !== CACHE)
          .map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function store(req, res) {
  if (res.ok && new URL(req.url).origin === location.origin) {
    const clone = res.clone();
    caches.open(CACHE).then((c) => c.put(req, clone));
  }
  return res;
}

function networkFirst(req) {
  return fetch(req)
    .then((res) => store(req, res))
    .catch(() => caches.match(req).then((hit) => hit || caches.match(new URL("index.html", SCOPE).href)));
}

function cacheFirst(req) {
  return caches.match(req).then((hit) => hit || fetch(req).then((res) => store(req, res)));
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith(API_PREFIX)) return; // never cache API
  if (e.request.mode === "navigate" || NETWORK_FIRST.test(url.pathname)) {
    e.respondWith(networkFirst(e.request));
  } else {
    e.respondWith(cacheFirst(e.request));
  }
});
