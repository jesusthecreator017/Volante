// Swing detail page (#/swing/<session>/<index>) and the shared swing summary card.

import { app } from "../app";
import type { Swing } from "../core/swings";
import { confidenceLabel } from "../core/swings";
import { G } from "../core/quat";
import { speed, type Settings } from "../settings";
import { putSession } from "../store";
import { lineChart, COLORS } from "../ui/chart";
import { drawOrientation } from "../ui/orient";
import { esc, est, kv, lb, na, v } from "../ui/fmt";

export const LABELS = ["smash", "clear", "drop", "drive", "lift", "net", "serve", "other", "not a stroke"];

export function swingCard(s: Swing, st: Settings): string {
  const sat = s.saturatedSamples > 0;
  const u = st.speedUnit;
  return kv([
    ["Start", `${s.start.toFixed(3)} s <small class="note">(device ${s.tStart.toFixed(3)} s)</small>`],
    ["Duration", v(s.durationMs, "ms", 0)],
    ["Backswing", v(s.backswingMs, "ms", 0, "see note"), s.phaseNote],
    ["Forward swing", v(s.forwardMs, "ms", 0)],
    ["Follow-through", v(s.followMs, "ms", 0)],
    ["Peak |ω|", `${v(s.peakDps, "dps", 0)} · ${v(s.peakRad, "rad/s", 2)}${sat ? " " + lb() : ""}`],
    [
      "Racquet-head speed",
      `${v(speed(s.headSpeed, u), u, 1)} <small class="note">(${s.headSpeed.toFixed(1)} m/s, ${(s.headSpeed * 3.6).toFixed(0)} km/h)</small> ${est()}${sat ? " " + lb() : ""}`,
      `rotation-only estimate = |ω| × ${st.headCenterM} m; excludes arm translation, underestimates`,
    ],
    ["Peak acceleration", v(s.peakAccG, "g", 1) + (s.accSaturatedSamples ? ` <span class="warn" title="an axis hit ±16 g">accel saturated</span>` : "")],
    ["Impact detected", s.impact ? "yes" : "no" + (s.lostPackets ? ` <small class="note">(${s.lostPackets} packets lost in this swing — a contact may be missed)</small>` : "")],
    ["Impact strength", v(s.impactG, "g", 1, "no impact")],
    [
      "Impact vs forward-swing |ω| peak",
      s.impactVsPeakMs !== null ? `${s.impactVsPeakMs.toFixed(1)} ms (${s.timing}) ${est("timing heuristic: early < -10 ms < on peak < +10 ms < late")}` : na("no impact"),
      "negative = contact before the peak",
    ],
    ["Handle tilt vs horizontal", v(s.handleTiltDeg, "°", 1, "no orientation"), s.impact ? "at impact" : "at forward peak"],
    ["Face angle (normal vs horizontal)", v(s.faceAngleDeg, "°", 1, "no orientation"), "0° = face vertical, 90° = face up"],
    ["Overhead / underarm", s.overhead === null ? na("no orientation") : s.overhead ? "overhead" : "underarm", "handle above/below horizontal"],
    [
      "Forehand / backhand",
      s.side ? `${s.side} ${est("sign of rotation about the face normal; convention set from synthetic data, check on real recordings")}` : na("rotation about the face normal too small (< 50 dps)"),
    ],
    ["Stroke type", `${esc(s.stroke)} ${est("heuristic, classifier comes later")}`],
    ["Saturated", sat ? `<span class="warn">yes — ${s.saturatedSamples} gyro samples</span>` : "no"],
    ["Confidence", `${s.confidence.toFixed(2)} (${confidenceLabel(s.confidence)})`, s.confidenceReasons.join("; ") || "no gaps or saturation"],
  ]);
}

export async function renderSwing(root: HTMLElement, params: string[]) {
  const [sid, idxStr] = params;
  const { s: sess, a } = await app.session(sid);
  const idx = Number(idxStr);
  const sw = a.swings.find((x) => x.index === idx);
  if (!sw) throw new Error(`swing ${idx} not found`);
  const prev = a.swings.find((x) => x.index === idx - 1);
  const next = a.swings.find((x) => x.index === idx + 1);
  root.innerHTML = `
    <p><a href="#/session/${sid}">← ${esc(sess.name)}</a></p>
    <h1>Swing ${idx} <small class="note">of ${a.swings.length}</small></h1>
    <div class="row">${prev ? `<a href="#/swing/${sid}/${idx - 1}">← swing ${idx - 1}</a>` : ""} ${next ? `<a href="#/swing/${sid}/${idx + 1}">swing ${idx + 1} →</a>` : ""}</div>
    ${a.summary.fastestIndex === idx ? `<p class="ok">Fastest swing of the session</p>` : ""}
    <div class="grid">
      <div>${swingCard(sw, app.settings)}</div>
      <div>
        <h2>Label (training data for a future classifier)</h2>
        <div class="row"><select id="label"><option value="">— unlabeled —</option>${LABELS.map((l) => `<option ${sess.labels[idx] === l ? "selected" : ""}>${l}</option>`).join("")}</select> <span id="saved" class="note"></span></div>
        <h2>Orientation ${sw.impact ? "at impact" : "at forward peak"}</h2>
        <canvas class="orient" id="wid"></canvas>
        <p class="limit">Yaw is arbitrary (drifts); tilt and face angle are absolute.</p>
      </div>
    </div>
    <h2>|ω| and acceleration around the swing</h2>
    <canvas class="chart" id="cw"></canvas>
    <canvas class="chart" id="ca"></canvas>`;
  const lo = sw.tStart - 0.3;
  const hi = sw.tEnd + 0.3;
  const idxs: number[] = [];
  a.samples.forEach((x, i) => {
    if (x.t >= lo && x.t <= hi) idxs.push(i);
  });
  const t = idxs.map((i) => a.samples[i].t - sw.tStart);
  const markers = [
    { x: 0, label: "start", color: "#888" },
    { x: sw.tPeak - sw.tStart, label: "peak", color: COLORS.b },
    { x: sw.tFwdPeak - sw.tStart, label: "fwd peak", color: COLORS.a },
    ...(sw.tImpact ? [{ x: sw.tImpact - sw.tStart, label: "impact", color: "#d62728" }] : []),
    { x: sw.tEnd - sw.tStart, label: "end", color: "#888" },
  ];
  lineChart(root.querySelector("#cw")!, [{ name: "|ω| dps", color: COLORS.mag, x: t, y: idxs.map((i) => a.wDps[i]) }], { xLabel: "s", markers });
  lineChart(
    root.querySelector("#ca")!,
    [{ name: "|a| g", color: COLORS.z, x: t, y: idxs.map((i) => Math.hypot(...a.samples[i].acc) / G) }],
    { xLabel: "s", markers },
  );
  drawOrientation(root.querySelector("#wid")!, sw.qAt, sw.qAt ? `ω_z at fwd peak ${(sw.faceNormalRateDps).toFixed(0)} dps` : "");
  root.querySelector<HTMLSelectElement>("#label")!.addEventListener("change", async (e) => {
    const val = (e.target as HTMLSelectElement).value;
    if (val) sess.labels[idx] = val;
    else delete sess.labels[idx];
    await putSession(sess);
    root.querySelector("#saved")!.textContent = "saved";
  });
}
