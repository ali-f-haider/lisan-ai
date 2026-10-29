// Lisan AI computer app: the smallest service worker that makes the site installable. It keeps NO copies of pages or files
// (every page and every update always comes from the server, as in a browser tab). Only when a page cannot be reached at all
// (no internet) it shows a short note instead of the browser's error page. Uploads, downloads, videos and API calls are
// never touched.
const OFFLINE_PAGE = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<title>Lisan AI</title><style>body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;' +
    'min-height:100vh;margin:0;background:#f5f6fb;color:#1a237e;text-align:center}div{padding:24px}p{margin:8px 0}</style></head>' +
    '<body><div><p><b>You are offline.</b></p>' +
    '<p>Lisan AI needs an internet connection. Please connect and try again.</p>' +
    '<p dir="rtl" lang="ar"><b>أنت غير متصل بالإنترنت.</b></p><p dir="rtl" lang="ar">يحتاج Lisan AI إلى اتصال بالإنترنت. اتصل وحاول مرة أخرى.</p>' +
    '</div></body></html>';

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
    if (event.request.mode !== "navigate") return;          // everything except opening a page goes straight to the server
    event.respondWith(fetch(event.request).catch(() =>
        new Response(OFFLINE_PAGE, { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } })));
});
