/*
 * Section 7.2's "Offline": "storekeepers can create GRNs and issues
 * offline; they sync when a connection returns. The server rejects
 * any synced document that would cause negative stock and tells the
 * user."
 *
 * Any <form class="offline-form"> on the page is handled here instead
 * of submitting normally. Its `action` must point at one of the JSON
 * `*_offline_sync` endpoints in store/views.py (they accept the same
 * fields as the ordinary create forms, plus a client-generated
 * `client_ref` this script adds itself, and create-and-post a single
 * document in one request rather than the multi-step create/add-line/
 * post flow the online screens use — a disclosed simplification: an
 * offline-queued document is always a single line).
 *
 * On submit:
 *   - try the network first (covers a flaky connection, not just a
 *     fully offline one);
 *   - if that succeeds, show the server's response and reset the form;
 *   - if it fails (offline, or the request simply times out), save
 *     the submission in IndexedDB and tell the user it's queued.
 * Queued submissions are flushed on the browser's `online` event and
 * once when the page first loads (in case it loads already online
 * with something left over from a previous offline session) — this
 * app does not use the Background Sync API (see service-worker.js's
 * own docstring for why).
 */

const OFFLINE_DB_NAME = "boq-store-offline";
const OFFLINE_DB_VERSION = 1;
const QUEUE_STORE = "queue";

function openOfflineDb() {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open(OFFLINE_DB_NAME, OFFLINE_DB_VERSION);
        request.onupgradeneeded = () => {
            const db = request.result;
            if (!db.objectStoreNames.contains(QUEUE_STORE)) {
                db.createObjectStore(QUEUE_STORE, { keyPath: "id", autoIncrement: true });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function queueSubmission(url, formEntries, label) {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(QUEUE_STORE, "readwrite");
        tx.objectStore(QUEUE_STORE).add({
            url,
            body: formEntries,
            label,
            clientRef: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
            queuedAt: new Date().toISOString(),
        });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

async function listQueued() {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(QUEUE_STORE, "readonly");
        const request = tx.objectStore(QUEUE_STORE).getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
    });
}

async function removeQueued(id) {
    const db = await openOfflineDb();
    return new Promise((resolve, reject) => {
        const tx = db.transaction(QUEUE_STORE, "readwrite");
        tx.objectStore(QUEUE_STORE).delete(id);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
    });
}

function formEntriesToBody(entries) {
    const body = new URLSearchParams();
    entries.forEach(([key, value]) => body.append(key, value));
    return body;
}

function setStatus(form, message, isError) {
    let box = form.querySelector(".offline-status");
    if (!box) {
        box = document.createElement("p");
        box.className = "offline-status";
        form.appendChild(box);
    }
    box.textContent = message;
    box.style.color = isError ? "#842029" : "#0f5132";
}

async function submitToServer(url, body) {
    const response = await fetch(url, {
        method: "POST",
        body,
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
    });
    const data = await response.json();
    return { response, data };
}

async function handleOfflineForm(event) {
    const form = event.target;
    event.preventDefault();

    const formData = new FormData(form);
    const entries = Array.from(formData.entries());
    const body = formEntriesToBody(entries);
    const url = form.action;
    const label = form.dataset.offlineLabel || "Document";

    try {
        const { data } = await submitToServer(url, body);
        if (data.ok) {
            setStatus(form, data.message || `${label} saved.`, false);
            form.reset();
        } else {
            setStatus(form, (data.errors || ["The server rejected this document."]).join(" "), true);
        }
    } catch (networkError) {
        await queueSubmission(url, entries, label);
        setStatus(form, `${label} saved offline — it will sync automatically once you're back online.`, false);
        form.reset();
    }
}

async function flushQueue() {
    let queued;
    try {
        queued = await listQueued();
    } catch (e) {
        return; // IndexedDB unavailable (e.g. private browsing) -- nothing to flush
    }
    for (const item of queued) {
        const body = formEntriesToBody(item.body);
        body.set("client_ref", item.clientRef);
        try {
            const { data } = await submitToServer(item.url, body);
            await removeQueued(item.id);
            const banner = document.getElementById("offline-sync-banner");
            if (banner) {
                banner.textContent = data.ok
                    ? `Synced an offline ${item.label.toLowerCase()}.`
                    : `An offline ${item.label.toLowerCase()} was rejected: ${(data.errors || []).join(" ")}`;
                banner.hidden = false;
            }
        } catch (e) {
            break; // still offline (or the server is down) -- try again on the next 'online' event
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("form.offline-form").forEach((form) => {
        form.addEventListener("submit", handleOfflineForm);
    });

    if (navigator.onLine) {
        flushQueue();
    }
    window.addEventListener("online", flushQueue);

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    }
});
