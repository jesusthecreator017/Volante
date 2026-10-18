import "./style.css";
import { app } from "./app";
import { renderConnect } from "./pages/connect";
import { renderLive } from "./pages/live";
import { renderSession } from "./pages/session";
import { renderSwing } from "./pages/swing";
import { renderHistory } from "./pages/history";
import { renderReplay } from "./pages/replay";
import { renderExport } from "./pages/export";
import { renderSettings } from "./pages/settings";
import { renderDebug } from "./pages/debug";

export type Page = (root: HTMLElement, params: string[]) => void | (() => void) | Promise<void | (() => void)>;

const routes: Record<string, [string, Page, boolean]> = {
  device: ["Device", renderConnect, true],
  live: ["Live", renderLive, true],
  session: ["Session", renderSession, true],
  swing: ["Swing", renderSwing, false],
  history: ["History", renderHistory, true],
  replay: ["Replay", renderReplay, true],
  export: ["Export", renderExport, true],
  settings: ["Settings", renderSettings, true],
  debug: ["Debug", renderDebug, true],
};

const nav = document.getElementById("nav")!;
const main = document.getElementById("main")!;
const conn = document.getElementById("conn")!;
let cleanup: (() => void) | void = undefined;

nav.innerHTML = Object.entries(routes)
  .filter(([, r]) => r[2])
  .map(([k, [label]]) => `<a href="#/${k}" data-route="${k}">${label}</a>`)
  .join("");

async function route() {
  const [name = "device", ...params] = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const r = routes[name] ?? routes.device;
  for (const a of nav.querySelectorAll("a")) a.toggleAttribute("aria-current", a.dataset.route === name);
  if (typeof cleanup === "function") cleanup();
  cleanup = undefined;
  main.innerHTML = "";
  document.title = `Volante · ${r[0]}`;
  try {
    cleanup = await r[1](main, params);
  } catch (e) {
    main.innerHTML = `<p class="error">${String(e instanceof Error ? e.message : e)}</p>`;
    app.error(e);
  }
  window.scrollTo(0, 0);
}

function updateConn() {
  const s = app.conn;
  const rec = app.live ? " · streaming session" : app.upload ? " · uploading" : "";
  conn.textContent = `${s === "connected" ? "● " + (app.device?.name ?? "") : s === "connecting" ? "◌ connecting…" : "○ not connected"}${rec}`;
  conn.className = s;
}
app.onChange(updateConn);
updateConn();
window.addEventListener("hashchange", route);
app.probeServer();
route();
