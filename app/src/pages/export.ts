// Export page: imu.bin (exact device format), swings CSV, summary JSON, labeled swings for training, Pi upload.

import { app } from "../app";
import { labeledWindowsJson, summaryJson, swingsCsv } from "../core/export";
import { listSessions } from "../store";
import { date, esc } from "../ui/fmt";
import { saveFile } from "../ui/save";

export async function renderExport(root: HTMLElement, params: string[]) {
  const all = await listSessions();
  if (!all.length) {
    root.innerHTML = `<h1>Export</h1><p>No sessions yet.</p>`;
    return;
  }
  const id = params[0] ?? all[0].id;
  const { s, a } = await app.session(id);
  const base = s.name.replace(/[^\w.-]+/g, "_").slice(0, 60) || s.id;
  const nLabels = Object.keys(s.labels).length;
  const server = app.serverBase();
  root.innerHTML = `
  <h1>Export</h1>
  <label>Session <select id="sel">${all.map((x) => `<option value="${x.id}" ${x.id === id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
  <h2>Files</h2>
  <table>
    <tr><td><button id="imu">imu.bin</button></td><td>Raw session in the device format (data-format.md): 16-byte header + 18-byte packets in arrival order. Readable by the Rust/Python pipeline (oso-imu). ${a.capture.stats.arrivals} packets.</td></tr>
    <tr><td><button id="csv">swings.csv</button></td><td>One row per swing (${a.swings.length}), every per-swing metric + your label.</td></tr>
    <tr><td><button id="json">summary.json</button></td><td>Session summary, calibration, clock, sync events and swings.</td></tr>
    <tr><td><button id="lab" ${nLabels ? "" : "disabled"}>labeled-swings.json</button></td><td>Training data for a future stroke classifier: raw 6-axis window (−300…+200 ms around the impact) + features for each labeled swing (${nLabels} labeled; label swings on their detail page).</td></tr>
    ${s.replay ? `<tr><td><button id="rep">replay.bin</button></td><td>The attached replay from the video pipeline.</td></tr>` : ""}
  </table>
  <h2>Pi server</h2>
  <p>${
    server === null
      ? `No server configured. Set the Pi URL in <a href="#/settings">Settings</a> (web pages served by the Pi use it automatically).`
      : `Server: <code>${esc(server || location.origin)}</code>. ${s.uploadedAt ? `Uploaded ${date(s.uploadedAt)}.` : "Not uploaded yet."}`
  }</p>
  <button id="up" ${server === null ? "disabled" : ""}>Upload imu.bin + summary to the Pi (for the video pipeline)</button>
  <p id="msg" class="note"></p>`;
  const msg = (t: string) => (root.querySelector("#msg")!.textContent = t);
  const on = (sel: string, f: () => Promise<void>) =>
    root.querySelector(sel)?.addEventListener("click", () => f().catch((e) => msg(String(e))));
  root.querySelector<HTMLSelectElement>("#sel")!.addEventListener("change", (e) => {
    location.hash = `#/export/${(e.target as HTMLSelectElement).value}`;
  });
  on("#imu", () => saveFile(`${base}.imu.bin`, s.imuBin, "application/octet-stream"));
  on("#csv", () => saveFile(`${base}.swings.csv`, swingsCsv(a.swings, s.labels), "text/csv"));
  on("#json", () => saveFile(`${base}.summary.json`, summaryJson(a, { id: s.id, name: s.name, created_at: new Date(s.createdAt).toISOString(), source: s.source }), "application/json"));
  on("#lab", () => saveFile(`${base}.labeled-swings.json`, labeledWindowsJson(a, s.labels, s.id), "application/json"));
  on("#rep", () => saveFile(`${base}.replay.bin`, s.replay!, "application/octet-stream"));
  on("#up", async () => {
    msg("uploading…");
    await app.serverUpload(s);
    msg("uploaded");
  });
}
