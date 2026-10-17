// History: sessions list, personal bests over time, comparison of two sessions.

import { app } from "../app";
import { speed } from "../settings";
import { listSessions } from "../store";
import { lineChart, COLORS } from "../ui/chart";
import { date, dur, esc, na, v } from "../ui/fmt";

export async function renderHistory(root: HTMLElement) {
  const all = await listSessions();
  const u = app.settings.speedUnit;
  if (!all.length) {
    root.innerHTML = `<h1>History</h1><p>No sessions yet. <button id="sample">Load the sample session</button></p>`;
    root.querySelector("#sample")!.addEventListener("click", async () => {
      location.hash = `#/session/${await app.loadSample()}`;
    });
    return;
  }
  const chrono = [...all].reverse();
  // Personal best progression: running max of session max speed
  let best = -Infinity;
  const pb: { s: (typeof all)[0]; max: number; isPb: boolean }[] = [];
  for (const s of chrono) {
    const m = s.headline?.maxSpeed ?? null;
    if (m === null) continue;
    const isPb = m > best;
    best = Math.max(best, m);
    pb.push({ s, max: m, isPb });
  }
  const top = <K extends "maxSpeed" | "hits" | "durationS" | "peakDps">(k: K) =>
    all.reduce<(typeof all)[0] | null>((acc, s) => ((s.headline?.[k] ?? -1) > (acc?.headline?.[k] ?? -1) ? s : acc), null);
  const bSpeed = top("maxSpeed");
  const bHits = top("hits");
  const bDur = top("durationS");
  const bDps = top("peakDps");
  const opts = all.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("");
  root.innerHTML = `
  <h1>History</h1>
  <div class="scroll"><table>
    <tr><th>date</th><th>name</th><th>duration</th><th>swings</th><th>hits</th><th>max speed</th></tr>
    ${all
      .map(
        (s) => `<tr><td>${date(s.createdAt)}</td><td><a href="#/session/${s.id}">${esc(s.name)}</a></td><td>${dur(s.headline?.durationS)}</td>
        <td>${s.headline?.swings ?? "—"}</td><td>${s.headline?.hits ?? "—"}</td>
        <td>${s.headline?.maxSpeed != null ? speed(s.headline.maxSpeed, u).toFixed(1) + " " + u : "—"}</td></tr>`,
      )
      .join("")}
  </table></div>
  <p class="note">Speeds are rotation-only estimates (lower bounds on saturated swings).</p>

  <h2>Personal bests</h2>
  <table>
    <tr><th>Fastest head speed</th><td>${bSpeed?.headline?.maxSpeed != null ? `${v(speed(bSpeed.headline.maxSpeed, u), u, 1)} · <a href="#/session/${bSpeed.id}">${esc(bSpeed.name)}</a>` : na("no swings")}</td></tr>
    <tr><th>Highest peak |ω|</th><td>${bDps?.headline?.peakDps != null ? `${v(bDps.headline.peakDps, "dps", 0)} · <a href="#/session/${bDps.id}">${esc(bDps.name)}</a>` : na("no swings")}</td></tr>
    <tr><th>Most hits</th><td>${bHits?.headline ? `${bHits.headline.hits} · <a href="#/session/${bHits.id}">${esc(bHits.name)}</a>` : na("no sessions")}</td></tr>
    <tr><th>Longest session</th><td>${bDur?.headline ? `${dur(bDur.headline.durationS)} · <a href="#/session/${bDur.id}">${esc(bDur.name)}</a>` : na("no sessions")}</td></tr>
  </table>
  <h2>Max head speed over time</h2>
  <canvas class="chart" id="pb"></canvas>
  <p class="note">x = session number (oldest first); points = each session's max; line = personal best so far.</p>

  <h2>Compare two sessions</h2>
  <div class="row"><select id="ca">${opts}</select> vs <select id="cb">${opts}</select></div>
  <div id="cmp" class="scroll"></div>`;

  const xs = pb.map((_, i) => i + 1);
  let run = -Infinity;
  lineChart(
    root.querySelector("#pb")!,
    [
      { name: "session max", color: COLORS.b, x: xs, y: pb.map((p) => speed(p.max, u)) },
      { name: "personal best", color: COLORS.y, x: xs, y: pb.map((p) => speed((run = Math.max(run, p.max)), u)) },
    ],
    { points: true, yLabel: u },
  );

  const ca = root.querySelector<HTMLSelectElement>("#ca")!;
  const cb = root.querySelector<HTMLSelectElement>("#cb")!;
  if (all.length > 1) cb.selectedIndex = 1;
  const compare = async () => {
    const [A, B] = await Promise.all([app.session(ca.value), app.session(cb.value)]);
    const rows: [string, (x: typeof A) => number | null, string, number][] = [
      ["Duration", (x) => x.a.summary.durationS, "s", 1],
      ["Swings", (x) => x.a.summary.swingCount, "", 0],
      ["Hits", (x) => x.a.summary.hitCount, "", 0],
      ["Hits / min", (x) => x.a.summary.hitsPerMin, "", 1],
      ["Rallies", (x) => x.a.summary.rallyCount, "", 0],
      ["Avg head speed", (x) => (x.a.summary.speed ? speed(x.a.summary.speed.avg, u) : null), u, 1],
      ["p90 head speed", (x) => (x.a.summary.speed ? speed(x.a.summary.speed.p90, u) : null), u, 1],
      ["Max head speed", (x) => (x.a.summary.speed ? speed(x.a.summary.speed.max, u) : null), u, 1],
      ["Peak |ω|", (x) => x.a.summary.peakDps, "dps", 0],
      ["Peak g", (x) => x.a.summary.peakG, "g", 1],
      ["Overhead %", (x) => (x.a.summary.overheadRatio === null ? null : x.a.summary.overheadRatio * 100), "%", 0],
      ["Timing std", (x) => x.a.summary.timingStdMs, "ms", 1],
      ["Packet loss", (x) => x.a.summary.quality.lossPct, "%", 2],
    ];
    root.querySelector("#cmp")!.innerHTML = `<table><tr><th></th><th>${esc(A.s.name)}</th><th>${esc(B.s.name)}</th><th>Δ (B − A)</th></tr>${rows
      .map(([k, f, unit, d]) => {
        const x = f(A);
        const y = f(B);
        return `<tr><th>${k}</th><td>${v(x, unit, d)}</td><td>${v(y, unit, d)}</td><td>${x !== null && y !== null ? (y - x >= 0 ? "+" : "") + (y - x).toFixed(d) : "—"}</td></tr>`;
      })
      .join("")}</table>`;
  };
  ca.addEventListener("change", compare);
  cb.addEventListener("change", compare);
  await compare();
}
