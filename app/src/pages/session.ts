// Session page: sanity numbers, summary, charts, swings table, calibration/health, sync events.

import { app } from "../app";
import { DEG } from "../core/quat";
import { confidenceLabel } from "../core/swings";
import { speed } from "../settings";
import { deleteSession } from "../store";
import { barChart, lineChart, COLORS } from "../ui/chart";
import { fmtClock } from "./connect";
import { date, dur, esc, est, kv, lb, na, pct, v } from "../ui/fmt";

export async function renderSession(root: HTMLElement, params: string[]) {
  const id = params[0] ?? (await app.latestSessionId());
  if (!id) {
    root.innerHTML = `<h1>Session</h1><p>No sessions yet. Record one on the <a href="#/device">Device</a> page, import an imu.bin, or
      <button id="sample">load the sample session</button>.</p>`;
    root.querySelector("#sample")!.addEventListener("click", async () => {
      location.hash = `#/session/${await app.loadSample()}`;
    });
    return;
  }
  root.innerHTML = `<p>Analysing…</p>`;
  await new Promise((r) => setTimeout(r, 0));
  const { s, a } = await app.session(id);
  const S = a.summary;
  const u = app.settings.speedUnit;
  const sp = (x: number | null | undefined) => v(x === null || x === undefined ? null : speed(x, u), u, 1, "no swings");
  const anySat = a.swings.some((x) => x.saturatedSamples > 0);
  const c = a.calibration;
  const cal = c.bias;
  const labeled = Object.keys(s.labels).length;

  root.innerHTML = `
  <h1>${esc(s.name)}</h1>
  <p class="note">${date(s.createdAt)} · ${s.source}${s.deviceName ? " · " + esc(s.deviceName) : ""} · <a href="#/export/${id}">export</a>
   ${s.replay ? ` · <a href="#/replay/${id}">replay</a>` : ""} · <button id="del">Delete</button></p>

  <div class="card" id="sanity"><b>Sanity numbers</b>: swings <b>${S.swingCount}</b> · impacts <b>${S.hitCount}</b> ·
    max |ω| <b>${S.peakDps === null ? "—" : S.peakDps.toFixed(1) + " dps"}</b> · saturated gyro samples <b>${S.quality.saturatedGyro}</b>
    ${s.source === "sample" ? `<br><small class="note">Expected for sample-data/imu.bin: 6547 packets read, 6534 unique, 66 lost, 13 duplicate, 99 reordered, 44 gaps, 12 saturated; got ${a.capture.stats.arrivals}/${a.capture.stats.unique}/${a.capture.stats.lost}/${a.capture.stats.duplicate}/${a.capture.stats.reordered}, ${a.capture.gaps.length} gaps.</small>` : ""}
  </div>

  <div class="grid">
  <div><h2>Session summary</h2>${kv([
    ["Duration", dur(S.durationS)],
    ["Active / idle", `${dur(S.activeS)} / ${dur(S.idleS)}`, "active = swings ± 0.5 s"],
    ["Swings", String(S.swingCount)],
    ["Hits (impacts)", String(S.hitCount)],
    ["Hits per minute", v(S.hitsPerMin, "/min", 1)],
    [
      "Rallies",
      S.rallyCount ? `${S.rallyCount} · avg ${S.avgRallyHits!.toFixed(1)} hits · longest ${S.longestRallyHits} hits` : na(`no run of ≥ 2 hits < ${app.settings.rallyGapS} s apart`),
      `hits < ${app.settings.rallyGapS} s apart form a rally`,
    ],
    [
      "Rest periods",
      S.rests.length ? `${S.rests.length} · avg ${S.avgRestS!.toFixed(1)} s · longest ${S.longestRestS!.toFixed(1)} s` : na("no gaps ≥ rally gap between hits"),
    ],
    [
      "Head speed avg / p90 / max",
      S.speed ? `${sp(S.speed.avg)} / ${sp(S.speed.p90)} / ${sp(S.speed.max)} ${est("rotation-only")}${anySat ? " " + lb("max is a lower bound: a swing saturated") : ""}` : na("no swings"),
      "rotation-only estimate, excludes arm translation, underestimates",
    ],
    ["Peak |ω|", v(S.peakDps, "dps", 1, "no swings") + (anySat ? " " + lb() : "")],
    ["Peak acceleration", v(S.peakG, "g", 1, "no swings")],
    [
      "Stroke types",
      S.swingCount ? Object.entries(S.strokes).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ") + " " + est("heuristic, classifier comes later") : na("no swings"),
    ],
    [
      "Forehand / backhand",
      S.forehand + S.backhand ? `${S.forehand} / ${S.backhand} (${((100 * S.forehand) / (S.forehand + S.backhand)).toFixed(0)} % forehand) ${est()}` : na("no classified swings"),
    ],
    ["Overhead ratio", S.overheadRatio === null ? na("no hits with orientation") : pct(S.overheadRatio, 0)],
    ["Timing consistency (std of impact − peak)", v(S.timingStdMs, "ms", 1, "needs ≥ 2 hits") + " " + est()],
    ["Fastest swing", S.fastestIndex ? `<a href="#/swing/${id}/${S.fastestIndex}">swing ${S.fastestIndex}</a>` : na("no swings")],
    ["Fatigue trend", v(S.fatigueSlope === null ? null : speed(S.fatigueSlope, u), `${u} per min`, 2, "needs ≥ 3 swings"), "slope of head speed over time; noisy for short sessions"],
    ["Labeled swings", `${labeled} / ${S.swingCount}`],
  ])}</div>
  <div>
    <h2>Head-speed histogram (m/s)</h2><canvas class="chart" id="hist"></canvas>
    <h2>Fatigue: head speed per swing</h2><canvas class="chart" id="fat"></canvas>
    <h2>Load: rotation per minute (Σ|ω|·dt)</h2><canvas class="chart" id="load"></canvas>
  </div>
  </div>

  <h2>Swings</h2>
  <div class="scroll"><table>
   <tr><th>#</th><th>start s</th><th>dur ms</th><th>peak dps</th><th>speed ${u}</th><th>impact g</th><th>vs peak ms</th><th>over</th><th class="hide-sm">side</th><th>stroke</th><th class="hide-sm">sat</th><th>conf</th><th class="hide-sm">label</th></tr>
   ${a.swings
     .map(
       (x) => `<tr><td><a href="#/swing/${id}/${x.index}">${x.index}</a></td><td>${x.start.toFixed(2)}</td><td>${x.durationMs.toFixed(0)}</td>
       <td>${x.peakDps.toFixed(0)}${x.saturatedSamples ? "+" : ""}</td><td>${speed(x.headSpeed, u).toFixed(1)}${x.saturatedSamples ? "+" : ""}</td>
       <td>${x.impactG?.toFixed(1) ?? "—"}</td><td>${x.impactVsPeakMs?.toFixed(0) ?? "—"}</td><td>${x.overhead === null ? "—" : x.overhead ? "yes" : "no"}</td>
       <td class="hide-sm">${x.side ?? "—"}</td><td>${x.stroke}</td><td class="hide-sm">${x.saturatedSamples || ""}</td>
       <td title="${esc(x.confidenceReasons.join("; "))}">${confidenceLabel(x.confidence)}</td><td class="hide-sm">${esc(s.labels[x.index] ?? "")}</td></tr>`,
     )
     .join("")}
  </table></div>
  <p class="note">"+" = lower bound (gyro saturated). Speeds are rotation-only estimates. Stroke, side and timing are heuristics.</p>

  <div class="grid">
  <div><h2>Sensor health / calibration</h2>${kv([
    ["Gyro bias x/y/z", cal ? cal.map((b) => (b / DEG).toFixed(3)).join(" / ") + " dps" : na(c.note ?? "no stationary window")],
    ["Estimated from", c.windowT ? `device ${c.windowT[0].toFixed(2)}–${c.windowT[1].toFixed(2)} s (first stationary window)` : na("no stationary window")],
    ["|a| at rest", v(c.accRestG, "g", 4, "no stationary window"), "should be ≈ 1 g"],
    ["Noise at rest: gyro std", c.gyroNoiseDps ? c.gyroNoiseDps.map((x) => x.toFixed(3)).join(" / ") + " dps" : na("no stationary window")],
    ["Noise at rest: accel std", c.accNoiseG ? c.accNoiseG.map((x) => x.toFixed(4)).join(" / ") + " g" : na("no stationary window")],
    ["Pod tilt at rest", c.restTilt ? `handle ${c.restTilt.handleDeg.toFixed(1)}°, face normal ${c.restTilt.faceDeg.toFixed(1)}° above horizontal` : na("no stationary window")],
    ["Saturated samples gyro / accel", `${S.quality.saturatedGyro} / ${S.quality.saturatedAcc}`],
    [
      "Clock offset / drift vs phone",
      a.clock ? `device t = 0 at ${fmtClock(a.clock.offsetS)} / ${a.clock.driftPpm.toFixed(1)} ppm (jitter ${a.clock.jitterMs.toFixed(1)} ms)` : na(s.arrivals.length ? "too few notifications" : "needs a live-streamed session (imports and uploads have no arrival times)"),
    ],
    ["Sample rate configured / measured", `${a.rateHz} Hz / ${a.measuredRateHz.toFixed(2)} Hz`],
  ])}</div>
  <div><h2>Data quality</h2>${kv([
    ["Packets read / unique", `${a.capture.stats.arrivals} / ${a.capture.stats.unique}`],
    ["Lost", `${a.capture.stats.lost} (${S.quality.lossPct.toFixed(2)} %)`],
    ["Duplicated / reordered", `${a.capture.stats.duplicate} / ${a.capture.stats.reordered}`],
    ["Gaps / longest", `${S.quality.gaps} / ${S.quality.maxGapMs.toFixed(0)} ms`],
    ["Saturations (gyro / accel)", `${S.quality.saturatedGyro} / ${S.quality.saturatedAcc}`],
  ])}
  <p class="limit">Packet loss during impacts is common on wireless links; swings overlapping gaps get lower confidence.</p></div>
  </div>

  <h2>Sync events</h2>
  ${
    a.syncEvents.length
      ? `<table><tr><th>#</th><th>type</th><th>session time</th><th>device time</th><th>strength</th><th>role</th></tr>${a.syncEvents
          .map(
            (e, i) =>
              `<tr><td>${i + 1}</td><td>${e.kind}</td><td>${e.rel.toFixed(3)} s</td><td>${e.t.toFixed(4)} s</td><td>${e.peakG ? e.peakG.toFixed(1) + " g" : "—"}</td>
              <td>${i === 0 ? "start" : i === a.syncEvents.length - 1 ? "end" : "extra"}</td></tr>`,
          )
          .join("")}</table>`
      : `<p class="warn">No sync events detected. The video pipeline needs a clap (or button press) at the start and at the end.</p>`
  }
  ${a.syncEvents.length === 1 ? `<p class="warn">Only one sync event: clock drift can't be fixed for the video replay. Add an end clap next time.</p>` : ""}
  <p class="note">A clap = an accel spike > 6 g (≤ 6 ms wide) with the racquet still in the 0.3 s before it. The video pipeline uses the first and last.</p>
  `;

  barChart(root.querySelector("#hist")!, S.speedHist.map((b) => String(b.lo)), S.speedHist.map((b) => b.n), { yLabel: "swings" });
  lineChart(
    root.querySelector("#fat")!,
    [{ name: `head speed (${u})`, color: COLORS.b, x: S.fatigue.map((f) => f[0]), y: S.fatigue.map((f) => speed(f[1], u)) }],
    { xLabel: "s", points: true },
  );
  barChart(root.querySelector("#load")!, S.load.map((l) => `${l.minute}`), S.load.map((l) => l.rad), { yLabel: "rad / min (x = minute)", color: COLORS.a });
  root.querySelector("#del")!.addEventListener("click", async () => {
    if (!confirm(`Delete “${s.name}”?`)) return;
    await deleteSession(id);
    location.hash = "#/history";
  });
}
