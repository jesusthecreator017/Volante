// Live view: current values, 5 s rolling charts, orientation (quaternion, Euler, widget), swing indicator, last swing.

import { app } from "../app";
import { DEG, G, eulerYPR } from "../core/quat";
import { lineChart, COLORS } from "../ui/chart";
import { drawOrientation } from "../ui/orient";
import { kv, na, v } from "../ui/fmt";
import { swingCard } from "./swing";

export function renderLive(root: HTMLElement) {
  root.innerHTML = `
  <h1>Live</h1>
  <div id="none" class="note"></div>
  <div class="row"><span id="swing" class="pill">no swing</span> <span id="still" class="pill">—</span> <span id="conf" class="note"></span></div>
  <div class="grid">
    <div><h2>Accelerometer</h2><div id="acc"></div><canvas class="chart" id="cacc"></canvas></div>
    <div><h2>Gyroscope (bias removed)</h2><div id="gyr"></div><canvas class="chart" id="cgyr"></canvas>
      <p class="limit">Gyro saturates at 2000 dps; hard smashes exceed it at the handle.</p></div>
  </div>
  <div class="grid">
    <div><h2>Orientation</h2><div id="ori"></div>
      <p class="limit">Yaw drifts with a 6-axis IMU (no magnetometer, no video here); only tilt (pitch/roll vs gravity) is absolute.</p></div>
    <div><h2>Orientation widget</h2><canvas class="orient" id="wid"></canvas>
      <p class="note">Green = handle (+Y), blue = face normal (+Z), red = across the head (+X).</p></div>
  </div>
  <h2>Last swing</h2><div id="last"></div>`;
  const $ = (id: string) => root.querySelector<HTMLElement>("#" + id)!;
  const cacc = $("cacc") as HTMLCanvasElement;
  const cgyr = $("cgyr") as HTMLCanvasElement;
  const wid = $("wid") as HTMLCanvasElement;
  let raf = 0;
  let lastDraw = 0;
  let lastSwingCount = -1;

  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    if (now - lastDraw < 33) return; // ~30 fps
    lastDraw = now;
    const L = app.live;
    $("none").innerHTML = L
      ? ""
      : `No streaming session. Connect a device (or the mock) and press <a href="#/device">Start session</a>.`;
    if (!L || !L.samples.length) {
      drawOrientation(wid, null);
      return;
    }
    const n = L.samples.length;
    const s = L.samples[n - 1];
    const b = L.bias ?? [0, 0, 0];
    const gd = [0, 1, 2].map((k) => (s.gyro[k] - b[k]) / DEG);
    const ag = s.acc.map((x) => x / G);
    $("acc").innerHTML = kv([
      ["x / y / z", ag.map((x) => x.toFixed(2)).join(" / ") + " g"],
      ["|a|", v(Math.hypot(...ag), "g", 2) + (s.accSaturated ? ` <span class="warn">saturated (±16 g)</span>` : "")],
    ]);
    $("gyr").innerHTML = kv([
      ["x / y / z", gd.map((x) => x.toFixed(0)).join(" / ") + " dps"],
      [
        "|ω|",
        v(Math.hypot(...gd), "dps", 0) + (L.bias ? "" : ` <small class="note">(bias not removed yet)</small>`) + (s.saturated ? ` <span class="warn">saturated</span>` : ""),
      ],
    ]);
    // Rolling 5 s window
    const tEnd = s.t;
    let i0 = n - 1;
    while (i0 > 0 && L.samples[i0 - 1].t > tEnd - 5) i0--;
    const win = L.samples.slice(i0);
    const t = win.map((x) => x.t - tEnd);
    const ax = (k: number) => win.map((x) => x.acc[k] / G);
    const gx = (k: number) => win.map((x) => (x.gyro[k] - b[k]) / DEG);
    lineChart(
      cacc,
      [
        { name: "x", color: COLORS.x, x: t, y: ax(0) },
        { name: "y", color: COLORS.y, x: t, y: ax(1) },
        { name: "z", color: COLORS.z, x: t, y: ax(2) },
        { name: "|a|", color: COLORS.mag, x: t, y: win.map((x) => Math.hypot(...x.acc) / G) },
      ],
      { xLabel: "s", yLabel: "g", xMin: -5, xMax: 0 },
    );
    lineChart(
      cgyr,
      [
        { name: "x", color: COLORS.x, x: t, y: gx(0) },
        { name: "y", color: COLORS.y, x: t, y: gx(1) },
        { name: "z", color: COLORS.z, x: t, y: gx(2) },
        { name: "|ω|", color: COLORS.mag, x: t, y: L.wDps.slice(i0) },
      ],
      { xLabel: "s", yLabel: "dps", xMin: -5, xMax: 0 },
    );
    const q = L.q;
    if (q) {
      const e = eulerYPR(q);
      $("ori").innerHTML = kv([
        ["Quaternion x, y, z, w", q.map((c) => c.toFixed(3)).join(", "), "sensor → court frame (+Y up)"],
        ["Yaw (heading of handle)", v(e.yaw, "°", 1), "drifts"],
        ["Pitch (handle above horizontal)", v(e.pitch, "°", 1)],
        ["Roll (face about the handle)", v(e.roll, "°", 1)],
      ]);
    } else $("ori").innerHTML = na("calibrating: hold the racquet still ≥ 1 s");
    drawOrientation(wid, q, L.qConf < 1 ? "low confidence (saturation / lost packets)" : "");
    const sw = $("swing");
    sw.textContent = L.inSwing ? `SWING ${((tEnd - (L.swingSince ?? tEnd)) * 1000).toFixed(0)} ms` : "no swing";
    sw.classList.toggle("on", L.inSwing);
    $("still").textContent = L.stationary ? "still" : "moving";
    $("conf").textContent = L.bias ? "" : "calibrating…";
    if (L.swings.length !== lastSwingCount) {
      lastSwingCount = L.swings.length;
      const last = L.swings[L.swings.length - 1];
      $("last").innerHTML = last
        ? `<p class="note">Swing ${last.index} of this session (live, forward-only orientation; the saved session is re-analysed with forward + backward fusion).</p>${swingCard(last, app.settings)}`
        : na("no swing yet");
    }
  };
  raf = requestAnimationFrame(frame);
  return () => cancelAnimationFrame(raf);
}
