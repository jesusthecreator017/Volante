// Debug: raw notification/packet log, counters, errors.

import { app } from "../app";
import { esc } from "../ui/fmt";

export function renderDebug(root: HTMLElement) {
  root.innerHTML = `<h1>Debug</h1>
  <div class="row"><button id="pause">Pause log</button> <button id="clear">Clear</button></div>
  <h2>Counters</h2><pre id="cnt"></pre>
  <h2>Raw notification log (newest first, first packet decoded)</h2>
  <div class="scroll"><table id="log"></table></div>
  <h2>Errors</h2><pre id="err"></pre>
  <h2>Environment</h2><pre id="env"></pre>`;
  let paused = false;
  root.querySelector("#pause")!.addEventListener("click", (e) => {
    paused = !paused;
    (e.target as HTMLButtonElement).textContent = paused ? "Resume log" : "Pause log";
  });
  root.querySelector("#clear")!.addEventListener("click", () => {
    app.log.length = 0;
    app.errors.length = 0;
  });
  root.querySelector("#env")!.textContent = JSON.stringify(
    {
      userAgent: navigator.userAgent,
      webBluetooth: "bluetooth" in navigator,
      secureContext: window.isSecureContext,
      webgpu: "gpu" in navigator,
      server: app.serverBase(),
    },
    null,
    2,
  );
  const tick = () => {
    if (paused) return;
    const L = app.live ?? app.upload;
    root.querySelector("#cnt")!.textContent = JSON.stringify(
      {
        connection: app.conn,
        device: app.device ? { kind: app.device.kind, name: app.device.name, mtu: app.device.mtu } : null,
        status: app.status,
        session: L
          ? {
              mode: L.mode,
              ...L.stats,
              samplesProcessed: L.samples.length,
              swings: L.swings.length,
              claps: L.claps.length,
              arrivalsLogged: L.arrivals.length,
            }
          : null,
      },
      null,
      2,
    );
    root.querySelector("#log")!.innerHTML =
      `<tr><th>time</th><th>bytes</th><th>pkts</th><th>first packet</th><th class="hide-sm">hex (first 18 B)</th></tr>` +
      app.log
        .slice(0, 60)
        .map(
          (l) =>
            `<tr><td>${new Date(l.hostMs).toISOString().slice(11, 23)}</td><td>${l.bytes}</td><td>${l.packets}</td><td><code>${esc(l.first)}</code></td><td class="hide-sm"><code>${l.hex}</code></td></tr>`,
        )
        .join("");
    root.querySelector("#err")!.textContent = app.errors.join("\n") || "none";
  };
  const t = setInterval(tick, 300);
  tick();
  return () => clearInterval(t);
}
