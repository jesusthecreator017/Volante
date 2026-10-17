// Replay page: the embedded wasm renderer (iframe, one renderer per page) for sessions that have a replay.bin.

import { app } from "../app";
import { listSessions, getSession, putSession } from "../store";
import { esc } from "../ui/fmt";

const BASE = import.meta.env.BASE_URL;

export async function renderReplay(root: HTMLElement, params: string[]) {
  const all = await listSessions();
  const withReplay = all.filter((s) => s.replay);
  const id = params[0] ?? withReplay[0]?.id ?? null;
  const cur = id ? await getSession(id) : undefined;
  const server = app.serverBase() !== null;
  root.innerHTML = `
  <h1>Replay</h1>
  <p class="limit">A replay comes from the video pipeline (cameras + IMU). IMU-only sessions have no replay: use the
  <a href="#/live">Live</a> orientation widget and the Session page for them. The renderer has no touch camera yet:
  on phones use the camera preset buttons in its timeline (Baseline, Side, Top, Cam A, Cam B).</p>
  <div class="row">
    <label class="field" style="width:100%">Session <select id="sel" style="width:100%"><option value="">— choose —</option>${all
      .map((s) => `<option value="${s.id}" ${s.id === id ? "selected" : ""}>${esc(s.name)}${s.replay ? " (replay)" : ""}</option>`)
      .join("")}</select></label>
  </div>
  <div class="row">
    <label>Attach replay.bin <input type="file" id="file" accept=".bin"></label>
    <button id="srv" ${server && cur ? "" : "disabled"} title="${server ? "" : "configure the Pi server in Settings"}">Fetch replay from Pi server</button>
    <a href="${BASE}renderer/index.html${cur?.replay ? `?session=${cur.id}` : ""}" target="_blank" rel="noopener">open full screen</a>
  </div>
  <p id="msg" class="note"></p>
  ${
    cur?.replay
      ? `<iframe class="renderer" title="3D replay" src="${BASE}renderer/index.html?session=${cur.id}" allow="fullscreen"></iframe>
         <p class="note">Keyboard (desktop, click the view first): WASD/arrows move, drag to orbit, Space play/pause, 1–5 camera presets.</p>`
      : cur
        ? `<p>This session has no replay.bin yet. Attach one, or fetch it from the Pi after the video pipeline has run.</p>`
        : `<p>Choose a session. ${withReplay.length ? "" : "None has a replay yet — the sample session ships with one: "}<button id="sample">load sample session</button></p>`
  }`;
  const msg = (t: string) => (root.querySelector("#msg")!.textContent = t);
  root.querySelector<HTMLSelectElement>("#sel")!.addEventListener("change", (e) => {
    const v = (e.target as HTMLSelectElement).value;
    location.hash = v ? `#/replay/${v}` : "#/replay";
  });
  root.querySelector("#sample")?.addEventListener("click", async () => {
    location.hash = `#/replay/${await app.loadSample()}`;
  });
  root.querySelector<HTMLInputElement>("#file")!.addEventListener("change", async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (!f || !cur) return msg(cur ? "" : "choose a session first");
    const b = new Uint8Array(await f.arrayBuffer());
    if (String.fromCharCode(...b.subarray(0, 4)) !== "OSOR") return msg("not a replay.bin (magic OSOR missing)");
    cur.replay = b;
    await putSession(cur);
    location.hash = `#/replay/${cur.id}`;
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  root.querySelector("#srv")!.addEventListener("click", async () => {
    if (!cur) return;
    try {
      msg((await app.serverFetchReplay(cur)) ? "replay downloaded" : "the server has no replay for this session yet");
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    } catch (e) {
      msg(String(e));
    }
  });
}
