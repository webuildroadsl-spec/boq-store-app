"""
Section 7.2's "Offline": "storekeepers can create GRNs and issues
offline; they sync when a connection returns." The two pieces that
make a Progressive Web App possible -- a manifest and a service
worker -- are served here, at the site root (not under /static/), so
the service worker's default scope covers the whole site rather than
just /static/.

The actual offline queueing/sync logic lives client-side, in
`pwa/static/pwa/offline-queue.js` (registered from `base.html`) plus
the two `*_offline_sync` JSON endpoints in `store/views.py` that it
posts queued GRNs/issues to once a connection returns.
"""

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

MANIFEST = {
    "name": "BOQ & Store",
    "short_name": "BOQ & Store",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#ffffff",
    "theme_color": "#1f2933",
    # No custom icon set has been designed yet (a disclosed
    # simplification) -- most mobile browsers will still accept
    # "Add to home screen" without one, just with a generic icon.
    "icons": [],
}


@require_GET
def manifest(request):
    return JsonResponse(MANIFEST)


@require_GET
def service_worker(request):
    """
    A minimal service worker: cache-then-network for the app shell
    (so store screens still *load* offline, not just submit offline),
    and otherwise fall through to the network untouched. It does not
    implement the Background Sync API (which needs a permission
    prompt and isn't available in every browser) -- `offline-queue.js`
    instead syncs on the browser's own `online` event and on page
    load, a disclosed simplification of "sync when a connection
    returns" that covers the common case (the phone reconnects while
    the app is open, or is re-opened after reconnecting) without that
    extra API.
    """
    js = """
const CACHE_NAME = "boq-store-shell-v1";
const SHELL_URLS = ["/", "/static/pwa/offline-queue.js"];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).catch(() => {})
    );
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
    if (event.request.method !== "GET") {
        return; // POSTs (form submissions) always go straight to the network/offline-queue.js
    }
    event.respondWith(
        fetch(event.request)
            .then((response) => {
                const copy = response.clone();
                caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy)).catch(() => {});
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
"""
    return HttpResponse(js, content_type="application/javascript")
