// Replay download for the Arena page, with no DOM: the 202 "Preparing match" retry loop, error text, progress and abort.
// arena.js passes the callbacks that touch the page; the tests (fetch-replay.test.mjs) pass a fake fetch and sleep.

/** Waits `ms`; resolves true when the time passed, false when `signal` aborted first. */
export function sleep(ms, signal) {
  return new Promise((res) => {
    if (signal.aborted) return res(false);
    const done = () => { clearTimeout(timer); res(false); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", done); res(true); }, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** Milliseconds to wait after a 202: `Retry-After` as integer seconds (default 3), clamped to 1-30. */
export function retryDelay(resp) {
  const raw = (resp.headers.get("retry-after") || "").trim();
  const n = /^\d+$/.test(raw) ? parseInt(raw, 10) : 3;
  return Math.min(30, Math.max(1, n)) * 1000;
}

/** The message for a non-2xx response: JSON `detail`, else `error`, else `HTTP <status>`. */
export async function errorText(resp) {
  try {
    const j = JSON.parse(await resp.text());
    if (j && typeof j.detail === "string" && j.detail) return j.detail;
    if (j && typeof j.error === "string" && j.error) return j.error;
  } catch (_) { /* not JSON */ }
  return `HTTP ${resp.status}`;
}

/**
 * Returns the replay bytes (Uint8Array), or null when `signal` aborted (a drop won). Throws Error(message) on a fatal problem.
 * Options: signal (AbortSignal, required), fetchFn (default fetch), sleepFn (default sleep; resolves false on abort),
 * onPreparing() on each 202, onDownloading({received: 0, total}) when a 200 starts, onProgress({received, total}) after each chunk.
 * `total` is the Content-Length only when Content-Encoding is absent or identity (else null).
 */
export async function fetchReplay(url, { signal, fetchFn = globalThis.fetch, sleepFn = sleep, onPreparing, onDownloading, onProgress }) {
  for (;;) {
    let resp;
    try {
      resp = await fetchFn(url, { signal, credentials: "same-origin" });
    } catch (_) {
      if (signal.aborted) return null;
      throw new Error("Network error");
    }
    if (resp.status === 202) {
      onPreparing?.();
      Promise.resolve(resp.body?.cancel?.()).catch(() => {});
      if (!(await sleepFn(retryDelay(resp), signal))) return null;
      continue;
    }
    if (!resp.ok) throw new Error(await errorText(resp));

    const enc = (resp.headers.get("content-encoding") || "").trim().toLowerCase();
    const len = parseInt(resp.headers.get("content-length") || "", 10);
    const total = (!enc || enc === "identity") && len > 0 ? len : null;
    let received = 0;
    onDownloading?.({ received, total });
    const chunks = [];
    try {
      const reader = resp.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        onProgress?.({ received, total });
      }
    } catch (_) {
      if (signal.aborted) return null;
      throw new Error("Network error");
    }
    const bytes = new Uint8Array(received);
    let at = 0;
    for (const c of chunks) { bytes.set(c, at); at += c.length; }
    return bytes;
  }
}
