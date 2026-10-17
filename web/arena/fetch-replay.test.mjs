// node --test web/arena/  (a fake fetch and an injectable sleep: no network, no timers)
import test from "node:test";
import assert from "node:assert/strict";
import { errorText, fetchReplay, retryDelay, sleep } from "./fetch-replay.js";

const U = "http://x.test/m.bin";
const bytes = (...n) => new Uint8Array(n);

// A response whose body arrives in the given chunks
function streamed(chunks, init = {}) {
  let i = 0;
  const body = new ReadableStream({
    pull(c) { if (i < chunks.length) c.enqueue(chunks[i++]); else c.close(); },
  });
  return new Response(body, init);
}
// Plays a list of responses (or Errors to throw), one per fetch call
function fakeFetch(...script) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    const next = script.shift();
    if (next instanceof Error) throw next;
    return typeof next === "function" ? next(opts) : next;
  };
  fn.calls = calls;
  return fn;
}
// A sleep that records the wait and returns at once (or reports an abort)
function fakeSleep(result = true) {
  const waits = [];
  const fn = async (ms) => { waits.push(ms); return result; };
  fn.waits = waits;
  return fn;
}
const run = (fetchFn, extra = {}) => {
  const ac = new AbortController();
  const events = [];
  const p = fetchReplay(U, {
    signal: ac.signal, fetchFn, sleepFn: fakeSleep(),
    onPreparing: () => events.push("preparing"),
    onDownloading: (d) => events.push(["downloading", d.received, d.total]),
    onProgress: (d) => events.push(["progress", d.received, d.total]),
    ...extra,
  });
  return { ac, events, p };
};

test("200 returns the concatenated bytes, passes the signal and same-origin credentials", async () => {
  const f = fakeFetch(streamed([bytes(1, 2), bytes(3), bytes(4, 5, 6)], { status: 200 }));
  const { ac, p } = run(f);
  assert.deepEqual([...(await p)], [1, 2, 3, 4, 5, 6]);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, U);
  assert.equal(f.calls[0].opts.signal, ac.signal);
  assert.equal(f.calls[0].opts.credentials, "same-origin");
});

test("progress: total comes from Content-Length when there is no Content-Encoding", async () => {
  const f = fakeFetch(streamed([bytes(1, 2), bytes(3, 4, 5)], { status: 200, headers: { "content-length": "5" } }));
  const { events, p } = run(f);
  await p;
  assert.deepEqual(events, [["downloading", 0, 5], ["progress", 2, 5], ["progress", 5, 5]]);
});

test("progress: identity encoding keeps the total, gzip or a missing/zero length gives null", async () => {
  for (const [headers, total] of [
    [{ "content-length": "3", "content-encoding": "identity" }, 3],
    [{ "content-length": "3", "content-encoding": " Identity " }, 3],
    [{ "content-length": "3", "content-encoding": "gzip" }, null],
    [{ "content-length": "3", "content-encoding": "br" }, null],
    [{}, null],
    [{ "content-length": "0" }, null],
    [{ "content-length": "abc" }, null],
  ]) {
    const { events, p } = run(fakeFetch(streamed([bytes(1, 2, 3)], { status: 200, headers })));
    await p;
    assert.deepEqual(events, [["downloading", 0, total], ["progress", 3, total]], JSON.stringify(headers));
  }
});

test("an empty 200 body is an empty Uint8Array", async () => {
  const { p } = run(fakeFetch(streamed([], { status: 200 })));
  const out = await p;
  assert.ok(out instanceof Uint8Array);
  assert.equal(out.length, 0);
});

test("202 retries after Retry-After seconds, then downloads", async () => {
  const sleepFn = fakeSleep();
  const f = fakeFetch(
    new Response(null, { status: 202, headers: { "retry-after": "7" } }),
    new Response(null, { status: 202, headers: { "retry-after": "2" } }),
    streamed([bytes(9)], { status: 200 }),
  );
  const { events, p } = run(f, { sleepFn });
  assert.deepEqual([...(await p)], [9]);
  assert.deepEqual(sleepFn.waits, [7000, 2000]);
  assert.equal(f.calls.length, 3);
  assert.deepEqual(events, ["preparing", "preparing", ["downloading", 0, null], ["progress", 1, null]]);
});

test("Retry-After is clamped to 1-30 s and defaults to 3 s", () => {
  const ms = (v) => retryDelay(new Response(null, { status: 202, headers: v === undefined ? {} : { "retry-after": v } }));
  assert.equal(ms(undefined), 3000);
  assert.equal(ms("0"), 1000);
  assert.equal(ms("1"), 1000);
  assert.equal(ms("30"), 30000);
  assert.equal(ms("31"), 30000);
  assert.equal(ms("99999"), 30000);
  assert.equal(ms(" 5 "), 5000);
  // Not an integer: the default (an HTTP date or a decimal is not honoured)
  assert.equal(ms("2.5"), 3000);
  assert.equal(ms("-4"), 3000);
  assert.equal(ms("Wed, 21 Oct 2026 07:28:00 GMT"), 3000);
  assert.equal(ms(""), 3000);
});

test("202 has no retry cap", async () => {
  const sleepFn = fakeSleep();
  const script = Array.from({ length: 50 }, () => new Response(null, { status: 202 }));
  script.push(streamed([bytes(1)], { status: 200 }));
  const { p } = run(fakeFetch(...script), { sleepFn });
  assert.deepEqual([...(await p)], [1]);
  assert.equal(sleepFn.waits.length, 50);
  assert.ok(sleepFn.waits.every((w) => w === 3000));
});

test("error text: JSON detail, then error, then HTTP n", async () => {
  const text = (body, status = 500) => errorText(new Response(body, { status }));
  assert.equal(await text(JSON.stringify({ detail: "match not found", error: "e" }), 404), "match not found");
  assert.equal(await text(JSON.stringify({ error: "boom" })), "boom");
  assert.equal(await text(JSON.stringify({ detail: "", error: "boom" })), "boom");
  assert.equal(await text(JSON.stringify({ detail: 5, error: 6 }), 502), "HTTP 502");
  assert.equal(await text(JSON.stringify({ status: "x" }), 503), "HTTP 503");
  assert.equal(await text("<html>bad gateway</html>", 502), "HTTP 502");
  assert.equal(await text("null", 500), "HTTP 500");
  assert.equal(await text("", 410), "HTTP 410");
});

test("a non-2xx response throws its error text and does not retry", async () => {
  const f = fakeFetch(new Response(JSON.stringify({ detail: "no such match" }), { status: 404 }), streamed([bytes(1)]));
  const { p } = run(f);
  await assert.rejects(p, { message: "no such match" });
  assert.equal(f.calls.length, 1);
  await assert.rejects(run(fakeFetch(new Response("oops", { status: 500 }))).p, { message: "HTTP 500" });
});

test("a network error from fetch is 'Network error'", async () => {
  await assert.rejects(run(fakeFetch(new TypeError("failed to fetch"))).p, { message: "Network error" });
});

test("a body that fails mid-download is 'Network error'", async () => {
  let n = 0;
  const body = new ReadableStream({
    pull(c) { if (n++ === 0) c.enqueue(bytes(1)); else c.error(new TypeError("reset")); },
  });
  const { events, p } = run(fakeFetch(new Response(body, { status: 200 })));
  await assert.rejects(p, { message: "Network error" });
  assert.deepEqual(events.at(-1), ["progress", 1, null]);
});

test("abort during the 202 wait returns null and does not fetch again", async () => {
  const f = fakeFetch(new Response(null, { status: 202 }), streamed([bytes(1)]));
  const ac = new AbortController();
  // The real sleep: the abort cuts the wait short
  const p = fetchReplay(U, { signal: ac.signal, fetchFn: f });
  setTimeout(() => ac.abort(), 10);
  assert.equal(await p, null);
  assert.equal(f.calls.length, 1);
});

test("abort while a fake sleep is pending, and an already-aborted signal", async () => {
  assert.equal(await run(fakeFetch(new Response(null, { status: 202 })), { sleepFn: fakeSleep(false) }).p, null);
  const ac = new AbortController();
  ac.abort();
  assert.equal(await sleep(60000, ac.signal), false);
});

test("sleep resolves true once the time has passed and false on abort", async () => {
  const ac = new AbortController();
  assert.equal(await sleep(5, ac.signal), true);
  const p = sleep(60000, ac.signal);
  ac.abort();
  assert.equal(await p, false);
});

test("abort during the download returns null (not a network error)", async () => {
  const ac = new AbortController();
  let sent;
  const fetchFn = async (_u, { signal }) => {
    const body = new ReadableStream({
      start(c) {
        signal.addEventListener("abort", () => c.error(new DOMException("aborted", "AbortError")), { once: true });
      },
      pull(c) { if (!sent) { sent = true; c.enqueue(bytes(1, 2)); } else return new Promise(() => {}); },
    });
    return new Response(body, { status: 200 });
  };
  const events = [];
  const p = fetchReplay(U, { signal: ac.signal, fetchFn, onProgress: (d) => { events.push(d.received); ac.abort(); } });
  assert.equal(await p, null);
  assert.deepEqual(events, [2]);
});

test("a fetch that rejects because the signal aborted returns null", async () => {
  const ac = new AbortController();
  const fetchFn = async () => { ac.abort(); throw new DOMException("aborted", "AbortError"); };
  assert.equal(await fetchReplay(U, { signal: ac.signal, fetchFn }), null);
});

test("callbacks are optional", async () => {
  const ac = new AbortController();
  const out = await fetchReplay(U, { signal: ac.signal, fetchFn: fakeFetch(new Response(null, { status: 202 }), streamed([bytes(7)])), sleepFn: fakeSleep() });
  assert.deepEqual([...out], [7]);
});
