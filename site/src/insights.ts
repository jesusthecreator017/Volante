// Insights: turns one session's swings into stats and plain-language tips. Runs in the browser only; nothing is uploaded.
// Input is the Volante app's swings.csv export (app/src/core/export.ts SWING_CSV_COLUMNS), or the example session below.

import type { IconName } from "./icons";

export interface SwingRow {
  /** seconds from the session start */
  t: number;
  /** estimated racquet head speed (rotation only), km/h */
  kmh: number;
  impact: boolean;
  timing: "early" | "on peak" | "late" | null;
  side: "forehand" | "backhand" | null;
  /** the gyro saturated during the swing: the speed is a lower bound */
  clipped: boolean;
}

export interface Insight {
  icon: IconName;
  title: string;
  text: string;
  tone: "good" | "tip" | "info";
}

export interface Report {
  swings: SwingRow[];
  /** the swings the stats use: hits when the file marks any, else every swing */
  scored: SwingRow[];
  minutes: number;
  meanKmh: number;
  topKmh: number;
  topIndex: number;
  /** coefficient of variation over the harder half of the swings */
  cv: number;
  /** last third vs first third mean speed, percent (null under 9 swings) */
  trendPct: number | null;
  /** median seconds between swings (null under 2 swings) */
  gapS: number | null;
  onPeakPct: number | null;
  backhandPct: number | null;
  clipped: boolean;
  insights: Insight[];
}

// ---------------------------------------------------------------- CSV

/** RFC 4180-ish: quoted cells with "" escapes, commas and newlines inside quotes. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') (cell += '"'), i++;
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") row.push(cell), (cell = "");
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell), rows.push(row), (row = []), (cell = "");
    } else cell += c;
  }
  if (cell !== "" || row.length) row.push(cell), rows.push(row);
  return rows.filter((r) => r.some((x) => x.trim() !== ""));
}

/** swings.csv -> rows. Throws an Error with a message for people (wrong file, no swings). */
export function parseSwingsCsv(text: string): SwingRow[] {
  const [head, ...body] = parseCsv(text);
  if (!head) throw new Error("That file is empty.");
  const col = (name: string) => head.indexOf(name);
  const iT = col("start_s");
  const iK = col("head_speed_km_h_rotation_only");
  if (iT < 0 || iK < 0) throw new Error("That isn't a swings.csv from the Volante app (it has no start_s or head speed column).");
  const iImpact = col("impact");
  const iTiming = col("timing");
  const iSide = col("side_estimate");
  const iSat = col("saturated_gyro_samples");
  const rows = body
    .map((r) => {
      const timing = r[iTiming];
      const side = r[iSide];
      return {
        t: Number(r[iT]),
        kmh: Number(r[iK]),
        impact: iImpact < 0 ? true : r[iImpact] === "true",
        timing: timing === "early" || timing === "on peak" || timing === "late" ? timing : null,
        side: side === "forehand" || side === "backhand" ? side : null,
        clipped: iSat >= 0 && Number(r[iSat]) > 0,
      } satisfies SwingRow;
    })
    .filter((r) => Number.isFinite(r.t) && Number.isFinite(r.kmh) && r.kmh > 0);
  if (!rows.length) throw new Error("No swings found in that file.");
  return rows.sort((a, b) => a.t - b.t);
}

// ---------------------------------------------------------------- analysis

const mean = (xs: number[]) => xs.reduce((a, b) => a + b, 0) / xs.length;
function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
const pct = (n: number, d: number) => Math.round((100 * n) / d);
const r0 = (x: number) => Math.round(x);
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function analyze(swings: SwingRow[]): Report {
  const hits = swings.filter((s) => s.impact);
  const scored = hits.length ? hits : swings;
  const v = scored.map((s) => s.kmh);
  const meanKmh = mean(v);
  const topKmh = Math.max(...v);
  const topIndex = swings.indexOf(scored[v.indexOf(topKmh)]);
  const hard = [...v].sort((a, b) => b - a).slice(0, Math.max(1, Math.ceil(v.length / 2)));
  const hm = mean(hard);
  const cv = hard.length > 1 ? Math.sqrt(mean(hard.map((x) => (x - hm) ** 2))) / hm : 0;
  const third = Math.floor(v.length / 3);
  const trendPct = v.length >= 9 ? (100 * (mean(v.slice(-third)) - mean(v.slice(0, third)))) / mean(v.slice(0, third)) : null;
  const gaps = swings.slice(1).map((s, i) => s.t - swings[i].t).filter((g) => g > 0);
  const gapS = gaps.length ? median(gaps) : null;
  const timed = scored.filter((s) => s.timing);
  const onPeakPct = timed.length >= 5 ? pct(timed.filter((s) => s.timing === "on peak").length, timed.length) : null;
  const sided = swings.filter((s) => s.side);
  const backhandPct = sided.length >= 5 ? pct(sided.filter((s) => s.side === "backhand").length, sided.length) : null;
  const clipped = scored.some((s) => s.clipped);
  const minutes = swings.length > 1 ? (swings[swings.length - 1].t - swings[0].t) / 60 : 0;
  const rep: Report = { swings, scored, minutes, meanKmh, topKmh, topIndex, cv, trendPct, gapS, onPeakPct, backhandPct, clipped, insights: [] };
  rep.insights = insightsFor(rep);
  return rep;
}

function insightsFor(r: Report): Insight[] {
  const out: Insight[] = [];
  const top = r.swings[r.topIndex];
  out.push({
    icon: "lightning",
    title: `Top swing: ${r0(r.topKmh)} km/h`,
    text: `Swing ${r.topIndex + 1} of ${r.swings.length}, ${clock(top.t - r.swings[0].t)} into the session. That's ${r0((100 * (r.topKmh - r.meanKmh)) / r.meanKmh)}% above your average.`,
    tone: "good",
  });
  const spread = r0(r.cv * 100);
  if (r.cv < 0.1) out.push({ icon: "seal-check", title: "Consistent power", text: `Your harder swings stay within about ${spread}% of each other. A repeatable swing is the base for adding speed.`, tone: "good" });
  else if (r.cv <= 0.2) out.push({ icon: "crosshair", title: "Some swings fall short", text: `Your harder swings vary by about ${spread}%. Slow, full practice swings help make the fast ones repeatable.`, tone: "tip" });
  else out.push({ icon: "crosshair", title: "Power is hit and miss", text: `Your harder swings vary by about ${spread}%. Work on the same grip and preparation every time before chasing speed.`, tone: "tip" });
  if (r.trendPct !== null) {
    const t = r0(Math.abs(r.trendPct));
    if (r.trendPct <= -8) out.push({ icon: "trend-down", title: `Speed fell ${t}% late on`, text: "Your last third was slower than your first. Take longer breaks between sets, or keep sessions shorter while you build endurance.", tone: "tip" });
    else if (r.trendPct >= 8) out.push({ icon: "trend-up", title: `You sped up ${t}%`, text: "Your last third was faster than your first: you needed time to warm up. A few minutes of fast shadow swings before playing gets you there sooner.", tone: "info" });
    else out.push({ icon: "seal-check", title: "No drop-off", text: "You held your speed from the first swing to the last.", tone: "good" });
  }
  if (r.onPeakPct !== null) {
    out.push(
      r.onPeakPct >= 60
        ? { icon: "target", title: `${r.onPeakPct}% hit on peak`, text: "Most hits met the shuttle at your racquet's fastest point. Good timing.", tone: "good" }
        : { icon: "target", title: `${r.onPeakPct}% hit on peak`, text: "Many hits came before or after your racquet's fastest point. Let the shuttle come a little closer before you swing through.", tone: "tip" },
    );
  }
  if (r.backhandPct !== null) out.push({ icon: "racquet", title: `${r.backhandPct}% backhands`, text: r.backhandPct < 15 ? "You rarely used your backhand. Mix some backhand drills into your next session." : "A healthy mix of forehands and backhands.", tone: r.backhandPct < 15 ? "tip" : "info" });
  if (r.gapS !== null) out.push({ icon: "timer", title: `A swing every ${r.gapS.toFixed(1)} s`, text: `${r.swings.length} swings in ${Math.max(1, r0(r.minutes))} min.`, tone: "info" });
  if (r.clipped) out.push({ icon: "info", title: "Some swings maxed out the sensor", text: "Their speeds are lower bounds, so your real top speed may be higher.", tone: "info" });
  return out;
}

// ---------------------------------------------------------------- example

// Totals from the 2026-10-17 smash session analysis (estimates from the sensor); the per-swing detail is simulated around them.
export const SESSION = { smashes: 41, meanKmh: 69, maxKmh: 95 };

/** A deterministic example session with exactly the real session's count, mean and top speed, and a late-session drop. */
export function exampleSession(): SwingRow[] {
  let seed = 11;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const n = SESSION.smashes;
  const raw = Array.from({ length: n }, (_, i) => 74 - 12 * (i / (n - 1)) + (rnd() - 0.5) * 22);
  const m = mean(raw);
  const k = (SESSION.maxKmh - SESSION.meanKmh) / (Math.max(...raw) - m);
  const timings: SwingRow["timing"][] = ["on peak", "on peak", "early", "on peak", "late", "on peak", "on peak", "early"];
  let t = 4;
  return raw.map((x, i) => {
    t += 6 + rnd() * 6;
    return { t, kmh: SESSION.meanKmh + (x - m) * k, impact: true, timing: timings[i % timings.length], side: i % 6 === 5 ? "backhand" : "forehand", clipped: false };
  });
}
