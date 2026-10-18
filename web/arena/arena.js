// Arena entry point: window.oso_* API for the same-origin parent, replay download / drop handling, wasm start.
// Runs under a strict CSP: no inline script or style, text goes in via textContent only.
import init, * as oso from "./pkg/oso.js";
import { fetchReplay } from "./fetch-replay.js";
import { setupTimeline } from "./timeline.js";

const STATUS_FIELDS = ["loaded", "time", "duration", "playing", "speed", "fps", "frame", "frame_count",
  "skeleton", "big_shuttle", "has_shuttle", "cameras", "preset", "stroke_count"];
const STATUS_DEFAULTS = { loaded: false, time: 0, duration: 0, playing: false, speed: 1, fps: 60, frame: 0,
  frame_count: 0, skeleton: false, big_shuttle: false, has_shuttle: false, cameras: 0, preset: 1, stroke_count: 0 };
const COMMANDS = ["oso_seek", "oso_set_playing", "oso_toggle_play", "oso_step", "oso_set_speed", "oso_preset",
  "oso_toggle_skeleton", "oso_toggle_big_shuttle", "oso_next_stroke", "oso_prev_stroke"];

// Theme: the site passes its light/dark choice (?theme=) and its toggle calls window.oso_theme; "system" follows the OS.
const setTheme = (t) => {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
};
setTheme(new URLSearchParams(location.search).get("theme"));
window.oso_theme = setTheme;

// The phone layout puts the player names just above the timeline panel, whose height depends on how its rows wrap.
const timelineEl = document.getElementById("timeline");
if (timelineEl && "ResizeObserver" in window) {
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--timeline-h", `${Math.ceil(timelineEl.getBoundingClientRect().height)}px`);
  }).observe(timelineEl);
}

const state = { phase: "waiting", error: "", received: 0, total: null };
let wasmReady = false;
const queue = [];

// 1. window API, synchronously
for (const name of COMMANDS) {
  window[name] = (...args) => {
    if (wasmReady) oso[name](...args);
    else queue.push([name, args]);
  };
}
window.oso_score = (t) => (wasmReady ? oso.oso_score(t) : new Uint32Array(0));
// The match's names and builds ({a: {name, build: {power, speed, reach, accuracy}}, b: {...}}), null without a match header or before ready
window.oso_players = () => {
  if (!wasmReady) return null;
  try {
    const text = oso.oso_players();
    return text ? JSON.parse(text) : null;
  } catch (_) {
    return null;
  }
};
window.oso_replay_error = () => (wasmReady ? oso.oso_replay_error() : state.error);
window.oso_status = () => {
  const out = { ...STATUS_DEFAULTS };
  if (wasmReady) {
    const s = oso.oso_status();
    for (const k of STATUS_FIELDS) out[k] = s[k];
    s.free();
  }
  out.phase = state.phase;
  out.error = state.error;
  out.received = state.received;
  out.total = state.total;
  return out;
};
let readyResolve, readyReject;
window.oso_ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });
window.oso_ready.catch(() => {});

const $ = (id) => document.getElementById(id);
const el = { status: $("ov-status"), progress: $("ov-progress"), error: $("ov-error"), drop: $("ov-drop"),
  overlay: $("overlay"), canvas: $("canvas"), corner: $("status") };

// Overlay helpers
function show(statusText, { progress = false, value = null, drop = false } = {}) {
  if (state.phase === "error") return; // error is sticky for the life of the page
  el.overlay.hidden = false;
  el.status.textContent = statusText;
  el.progress.hidden = !progress;
  if (value === null) el.progress.removeAttribute("value");
  else el.progress.value = value;
  el.drop.hidden = !drop;
}
function setPhase(phase) {
  if (state.phase === "ready" || state.phase === "error") return;
  state.phase = phase;
  if (phase === "waiting") show("", { drop: true });
  else if (phase === "preparing") show("Preparing the replay, up to a minute the first time...", { progress: true });
  else if (phase === "downloading") showProgress();
  else if (phase === "loading") show("Loading replay...", { progress: true });
  else if (phase === "ready") { el.overlay.hidden = true; readyResolve(); }
}
function fail(message) {
  if (state.phase === "ready" || state.phase === "error") return;
  state.phase = "error";
  state.error = message;
  abort.abort(); // stop any download or 202 wait
  el.overlay.hidden = false;
  el.status.textContent = "";
  el.progress.hidden = true;
  el.drop.hidden = true;
  el.error.textContent = message;
  readyReject(message);
}
function showProgress() {
  const mb = (state.received / 1048576).toFixed(1);
  if (state.total) {
    const pct = Math.min(100, Math.floor((state.received / state.total) * 100));
    show(`Downloading ${mb} MB (${pct}%)`, { progress: true, value: state.received / state.total });
  } else {
    show(`Downloading ${mb} MB`, { progress: true });
  }
}

// 3. replay promise, handed to wasm via globalThis.oso_replay before init()
const abort = new AbortController();
let settled = false;
let resolveReplay, rejectReplay;
const replay = new Promise((res, rej) => { resolveReplay = res; rejectReplay = rej; });
replay.catch(() => {});
globalThis.oso_replay = replay;

function deliver(bytes) {
  if (settled) return;
  settled = true;
  setPhase("loading");
  resolveReplay(bytes);
}
function reject(message) {
  if (settled) return;
  settled = true;
  fail(message);
  rejectReplay(new Error(message));
}

function openDb() {
  return new Promise((res, rej) => {
    const req = indexedDB.open("oso-viewer", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("drop");
    req.onsuccess = () => res(req.result);
    req.onerror = () => rej(req.error);
    req.onblocked = () => rej(new Error("IndexedDB blocked"));
  });
}
async function takeStash() {
  const db = await openDb();
  try {
    return await new Promise((res, rej) => {
      const tx = db.transaction("drop", "readwrite");
      const store = tx.objectStore("drop");
      const get = store.get("replay");
      store.delete("replay");
      tx.oncomplete = () => res(get.result || null);
      tx.onerror = () => rej(tx.error);
      tx.onabort = () => rej(tx.error);
    });
  } finally {
    db.close();
  }
}
async function putStash(file) {
  const db = await openDb();
  try {
    await new Promise((res, rej) => {
      const tx = db.transaction("drop", "readwrite");
      tx.objectStore("drop").put(file, "replay");
      tx.oncomplete = () => res();
      tx.onerror = () => rej(tx.error);
      tx.onabort = () => rej(tx.error);
    });
  } finally {
    db.close();
  }
}

// Returns the bytes, or null when aborted by a drop. Throws Error(message) on a fatal problem. (fetch-replay.js has the logic.)
function fetchMatch(url, signal) {
  const track = ({ received, total }) => { state.received = received; state.total = total; };
  return fetchReplay(url, {
    signal,
    onPreparing: () => setPhase("preparing"),
    onDownloading: (p) => { track(p); setPhase("downloading"); },
    onProgress: (p) => { track(p); showProgress(); },
  });
}

async function acquire() {
  let stashed = null;
  try { stashed = await takeStash(); } catch (_) { /* no IndexedDB: carry on */ }
  if (settled) return;
  if (stashed) {
    deliver(new Uint8Array(await stashed.arrayBuffer()));
    return;
  }
  const param = new URLSearchParams(location.search).get("match");
  if (!param) { setPhase("waiting"); return; }
  let url;
  try {
    url = new URL(param, location.href);
  } catch (_) {
    return reject("Invalid match URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return reject("Invalid match URL");
  try {
    const bytes = await fetchMatch(url.href, abort.signal);
    if (bytes) deliver(bytes);
  } catch (e) {
    reject(e.message);
  }
}
setPhase("waiting");
acquire();

// 5. drag and drop (registered before init so an early drop works)
let dragDepth = 0;
const hasFiles = (e) => !!e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
document.addEventListener("dragenter", (e) => {
  e.preventDefault();
  if (!hasFiles(e)) return;
  dragDepth++;
  document.body.classList.add("dragging");
});
document.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (dragDepth === 0) document.body.classList.remove("dragging");
});
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", async (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  if (!settled) {
    abort.abort();
    try {
      deliver(new Uint8Array(await file.arrayBuffer()));
    } catch (_) {
      reject("Could not read the dropped file");
    }
    return;
  }
  try {
    await putStash(file);
  } catch (_) {
    el.overlay.hidden = false;
    el.progress.hidden = true;
    el.drop.hidden = true;
    el.error.textContent = "Reload the page and drop the file again";
    return;
  }
  location.reload();
});

// 4. start wasm, then poll for the loaded / error state
function poll() {
  if (state.phase === "ready" || state.phase === "error") return;
  const s = window.oso_status();
  const wasmError = oso.oso_replay_error();
  const cornerText = el.corner.textContent;
  if (s.loaded) setPhase("ready");
  else if (wasmError) fail("Not a valid replay: " + wasmError);
  else if (cornerText.startsWith("error:")) fail(cornerText);
  if (state.phase !== "ready" && state.phase !== "error") requestAnimationFrame(poll);
}
init().then(() => {
  wasmReady = true;
  for (const [name, args] of queue.splice(0)) oso[name](...args);
  setupTimeline(oso);
  requestAnimationFrame(poll);
}).catch((e) => {
  console.error(e);
  fail(String(e));
});
