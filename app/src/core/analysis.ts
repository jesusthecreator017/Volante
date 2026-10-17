// Offline session analysis: capture -> bias -> two-pass fusion -> spikes/claps -> swings -> summary + health.

import { type Capture, type Marker, type Sample } from "./packet";
import { calibrateAndRun, type Params, type Track, type Window, findStationary, gyroBias, meanAcc } from "./fusion";
import { G, DEG, type Vec3 } from "./quat";
import { spikeStrength, spikes, findClaps, IMPACT_G, type Spike, fitClaps } from "./spikes";
import {
  analyzeSwing,
  detectSwings,
  type Swing,
  type SwingThresholds,
  type RacquetGeometry,
  type StrokeType,
} from "./swings";

export interface AnalysisSettings {
  beta: number;
  thresholds: SwingThresholds;
  geometry: RacquetGeometry;
  handedness: "right" | "left";
  /** Hits closer than this form a rally, s */
  rallyGapS: number;
}

export interface Calibration {
  window: Window | null;
  /** rad/s */
  bias: Vec3 | null;
  windowT: [number, number] | null;
  accRestG: number | null;
  gyroNoiseDps: Vec3 | null;
  accNoiseG: Vec3 | null;
  /** tilt of the pod during the stationary window: handle elevation and face roll (deg) */
  restTilt: { handleDeg: number; faceDeg: number } | null;
  note: string | null;
}

export interface ClockFit {
  /** host_time_s = t_dev*(1+a) + b; from the lower envelope of notification arrival times */
  offsetS: number;
  driftPpm: number;
  /** std of arrival latency around the envelope fit, ms */
  jitterMs: number;
  n: number;
}

export interface SyncEvent {
  kind: "clap" | "marker";
  t: number;
  /** relative to session start */
  rel: number;
  peakG: number | null;
}

export interface Summary {
  durationS: number;
  activeS: number;
  idleS: number;
  swingCount: number;
  hitCount: number;
  hitsPerMin: number;
  rallies: { start: number; end: number; hits: number }[];
  rallyCount: number;
  avgRallyHits: number | null;
  longestRallyHits: number | null;
  rests: { start: number; durS: number }[];
  avgRestS: number | null;
  longestRestS: number | null;
  speed: { avg: number; max: number; p90: number } | null;
  speedHist: { lo: number; hi: number; n: number }[];
  peakDps: number | null;
  peakG: number | null;
  strokes: Record<StrokeType, number>;
  forehand: number;
  backhand: number;
  overheadRatio: number | null;
  timingStdMs: number | null;
  fastestIndex: number | null;
  /** per swing: [rel time, head speed] */
  fatigue: [number, number][];
  /** least-squares slope of head speed vs time, m/s per minute */
  fatigueSlope: number | null;
  /** per minute bucket: total rotation sum(|ω|dt) in rad */
  load: { minute: number; rad: number }[];
  quality: { lossPct: number; gaps: number; maxGapMs: number; saturatedGyro: number; saturatedAcc: number };
}

export interface SessionAnalysis {
  rateHz: number;
  t0: number;
  t1: number;
  samples: Sample[];
  capture: Capture;
  markers: Marker[];
  calibration: Calibration;
  track: Track | null;
  wDps: Float64Array;
  spikes: Spike[];
  syncEvents: SyncEvent[];
  /** sync fit between the first and last sync event (device -> session-relative check only) */
  swings: Swing[];
  summary: Summary;
  clock: ClockFit | null;
  measuredRateHz: number;
}

export const DEFAULT_ANALYSIS: AnalysisSettings = {
  beta: 0.04,
  thresholds: { startDps: 300, endDps: 120, endHoldMs: 80, minDurationMs: 80 },
  geometry: { lengthM: 0.675, headCenterM: 0.53 },
  handedness: "right",
  rallyGapS: 4,
};

export function calibrationFrom(samples: Sample[], window: Window | null, bias: Vec3 | null): Calibration {
  if (!window || !bias) {
    return {
      window: null,
      bias: null,
      windowT: null,
      accRestG: null,
      gyroNoiseDps: null,
      accNoiseG: null,
      restTilt: null,
      note: "no stationary window (≥ 1 s held still) found — hold the racquet still at the start",
    };
  }
  const { start, end } = window;
  const acc = meanAcc(samples, start, end);
  const n = end - start;
  const gStd: Vec3 = [0, 0, 0];
  const aStd: Vec3 = [0, 0, 0];
  for (let a = 0; a < 3; a++) {
    let gs = 0;
    let as = 0;
    for (let i = start; i < end; i++) {
      gs += (samples[i].gyro[a] - bias[a]) ** 2;
      as += ((samples[i].acc[a] - acc[a]) / G) ** 2;
    }
    gStd[a] = Math.sqrt(gs / n) / DEG;
    aStd[a] = Math.sqrt(as / n);
  }
  return {
    window,
    bias,
    windowT: [samples[start].t, samples[end - 1].t],
    accRestG: Math.hypot(...acc) / G,
    gyroNoiseDps: gStd,
    accNoiseG: aStd,
    restTilt: tiltFromAcc(acc),
    note: null,
  };
}

/** Pod tilt from a resting accelerometer vector (specific force points up): handle (+Y) elevation and face (+Z) elevation. */
export function tiltFromAcc(acc: Vec3): { handleDeg: number; faceDeg: number } {
  const n = Math.hypot(...acc);
  return { handleDeg: Math.asin(acc[1] / n) / DEG, faceDeg: Math.asin(acc[2] / n) / DEG };
}

/**
 * Device clock vs host clock from notification arrivals. arrivals[k] = [t_dev of the newest sample in notification k,
 * host time s]. Lower envelope: per 1 s bucket keep the minimum latency, then least squares on those points.
 */
export function fitClock(arrivals: [number, number][]): ClockFit | null {
  if (arrivals.length < 10) return null;
  const buckets = new Map<number, [number, number]>();
  for (const [td, th] of arrivals) {
    const k = Math.floor(td);
    const cur = buckets.get(k);
    if (!cur || th - td < cur[1] - cur[0]) buckets.set(k, [td, th]);
  }
  const pts = [...buckets.values()];
  let a = 0;
  let b: number;
  if (pts.length >= 3) {
    const mx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
    const my = pts.reduce((s, p) => s + (p[1] - p[0]), 0) / pts.length;
    let sxy = 0;
    let sxx = 0;
    for (const [x, y] of pts) {
      sxy += (x - mx) * (y - x - my);
      sxx += (x - mx) ** 2;
    }
    a = sxx > 0 ? sxy / sxx : 0;
    b = my - a * mx;
  } else b = Math.min(...arrivals.map(([x, y]) => y - x));
  const res = arrivals.map(([x, y]) => y - (x * (1 + a) + b));
  const m = res.reduce((s, r) => s + r, 0) / res.length;
  const sd = Math.sqrt(res.reduce((s, r) => s + (r - m) ** 2, 0) / res.length);
  return { offsetS: b, driftPpm: a * 1e6, jitterMs: sd * 1000, n: arrivals.length };
}

export function analyzeSession(
  cap: Capture & { markers?: Marker[] },
  settings: AnalysisSettings = DEFAULT_ANALYSIS,
  arrivals: [number, number][] = [],
): SessionAnalysis {
  const samples = cap.samples;
  const t0 = samples.length ? samples[0].t : 0;
  const t1 = samples.length ? samples[samples.length - 1].t : 0;
  const params: Params = { beta: settings.beta, twoPass: true, nominalDt: 1 / (cap.rateHz || 500) };
  const fused = samples.length ? calibrateAndRun(samples, params) : null;
  const calibration = calibrationFrom(samples, fused?.window ?? null, fused?.bias ?? null);
  const bias = fused?.bias ?? [0, 0, 0];
  const wDps = new Float64Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const g = samples[i].gyro;
    wDps[i] = Math.hypot(g[0] - bias[0], g[1] - bias[1], g[2] - bias[2]) / DEG;
  }
  const strength = spikeStrength(samples);
  const allSpikes = spikes(samples, strength, 0, IMPACT_G);
  const claps = findClaps(samples, strength, 0);
  const markers = cap.markers ?? [];
  const syncEvents: SyncEvent[] = [
    ...claps.map((c) => ({ kind: "clap" as const, t: c.t, rel: c.t - t0, peakG: c.peakG })),
    ...markers.map((m) => ({ kind: "marker" as const, t: m.t, rel: m.t - t0, peakG: null })),
  ].sort((a, b) => a.t - b.t);
  const clapTimes = new Set(claps.map((c) => c.t));
  const impacts = allSpikes.filter((s) => !clapTimes.has(s.t));

  const swings: Swing[] = fused
    ? detectSwings(fused.track.t, wDps, settings.thresholds).map((w, k) =>
        analyzeSwing(
          {
            samples,
            wDps,
            track: fused.track,
            impacts,
            gaps: cap.gaps,
            geometry: settings.geometry,
            handedness: settings.handedness,
            t0,
          },
          w,
          k + 1,
        ),
      )
    : [];

  const measuredRateHz = samples.length > 1 ? (cap.stats.unique + cap.stats.lost - 1) / (t1 - t0) : 0;
  return {
    rateHz: cap.rateHz,
    t0,
    t1,
    samples,
    capture: cap,
    markers,
    calibration,
    track: fused?.track ?? null,
    wDps,
    spikes: allSpikes,
    syncEvents,
    swings,
    summary: summarize(samples, wDps, swings, cap, settings, t0, t1),
    clock: fitClock(arrivals),
    measuredRateHz,
  };
}

export function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return NaN;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

const std = (xs: number[]) => {
  if (xs.length < 2) return null;
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1));
};

export function summarize(
  samples: Sample[],
  wDps: Float64Array,
  swings: Swing[],
  cap: Capture,
  settings: AnalysisSettings,
  t0: number,
  t1: number,
): Summary {
  const durationS = Math.max(0, t1 - t0);
  // Active time: union of swing windows padded by 0.5 s each side
  const iv = swings.map((s) => [Math.max(t0, s.tStart - 0.5), Math.min(t1, s.tEnd + 0.5)]).sort((a, b) => a[0] - b[0]);
  let activeS = 0;
  let cur: number[] | null = null;
  for (const x of iv) {
    if (cur && x[0] <= cur[1]) cur[1] = Math.max(cur[1], x[1]);
    else {
      if (cur) activeS += cur[1] - cur[0];
      cur = [...x];
    }
  }
  if (cur) activeS += cur[1] - cur[0];

  const hits = swings.filter((s) => s.impact);
  const rallies: Summary["rallies"] = [];
  let run: Swing[] = [];
  const flushRun = () => {
    if (run.length >= 2) rallies.push({ start: run[0].tImpact! - t0, end: run[run.length - 1].tImpact! - t0, hits: run.length });
    run = [];
  };
  for (const h of hits) {
    if (run.length && h.tImpact! - run[run.length - 1].tImpact! >= settings.rallyGapS) flushRun();
    run.push(h);
  }
  flushRun();
  const rests: Summary["rests"] = [];
  for (let k = 1; k < hits.length; k++) {
    const d = hits[k].tImpact! - hits[k - 1].tImpact!;
    if (d >= settings.rallyGapS) rests.push({ start: hits[k - 1].tImpact! - t0, durS: d });
  }

  const speeds = swings.map((s) => s.headSpeed);
  const sorted = [...speeds].sort((a, b) => a - b);
  const hist: Summary["speedHist"] = [];
  if (sorted.length) {
    const top = Math.ceil(sorted[sorted.length - 1] + 1e-9);
    for (let lo = 0; lo < top; lo++) hist.push({ lo, hi: lo + 1, n: speeds.filter((v) => v >= lo && v < lo + 1).length });
  }
  const strokes = { smash: 0, clear: 0, drop: 0, drive: 0, lift: 0, net: 0, "no contact": 0 } as Record<StrokeType, number>;
  for (const s of swings) strokes[s.stroke]++;
  const fatigue = swings.map((s) => [s.start, s.headSpeed] as [number, number]);
  let fatigueSlope: number | null = null;
  if (fatigue.length >= 3) {
    const mx = fatigue.reduce((a, p) => a + p[0], 0) / fatigue.length;
    const my = fatigue.reduce((a, p) => a + p[1], 0) / fatigue.length;
    let sxy = 0;
    let sxx = 0;
    for (const [x, y] of fatigue) {
      sxy += (x - mx) * (y - my);
      sxx += (x - mx) ** 2;
    }
    if (sxx > 0) fatigueSlope = (sxy / sxx) * 60;
  }
  const load: Summary["load"] = [];
  for (let i = 1; i < samples.length; i++) {
    const dt = samples[i].t - samples[i - 1].t;
    if (dt > 0.05) continue; // don't integrate across holes
    const m = Math.floor((samples[i].t - t0) / 60);
    while (load.length <= m) load.push({ minute: load.length, rad: 0 });
    load[m].rad += 0.5 * (wDps[i] + wDps[i - 1]) * DEG * dt;
  }
  const overheadKnown = hits.filter((s) => s.overhead !== null);
  const timings = hits.map((s) => s.impactVsPeakMs!).filter((x) => x !== null);
  let fastestIndex: number | null = null;
  for (const s of swings) if (fastestIndex === null || s.headSpeed > swings[fastestIndex - 1].headSpeed) fastestIndex = s.index;
  const expected = cap.stats.unique + cap.stats.lost;
  return {
    durationS,
    activeS,
    idleS: durationS - activeS,
    swingCount: swings.length,
    hitCount: hits.length,
    hitsPerMin: durationS > 0 ? hits.length / (durationS / 60) : 0,
    rallies,
    rallyCount: rallies.length,
    avgRallyHits: rallies.length ? rallies.reduce((a, r) => a + r.hits, 0) / rallies.length : null,
    longestRallyHits: rallies.length ? Math.max(...rallies.map((r) => r.hits)) : null,
    rests,
    avgRestS: rests.length ? rests.reduce((a, r) => a + r.durS, 0) / rests.length : null,
    longestRestS: rests.length ? Math.max(...rests.map((r) => r.durS)) : null,
    speed: sorted.length
      ? { avg: speeds.reduce((a, b) => a + b, 0) / speeds.length, max: sorted[sorted.length - 1], p90: percentile(sorted, 0.9) }
      : null,
    speedHist: hist,
    peakDps: swings.length ? Math.max(...swings.map((s) => s.peakDps)) : null,
    peakG: swings.length ? Math.max(...swings.map((s) => s.peakAccG)) : null,
    strokes,
    forehand: swings.filter((s) => s.side === "forehand").length,
    backhand: swings.filter((s) => s.side === "backhand").length,
    overheadRatio: overheadKnown.length ? overheadKnown.filter((s) => s.overhead).length / overheadKnown.length : null,
    timingStdMs: std(timings),
    fastestIndex,
    fatigue,
    fatigueSlope,
    load,
    quality: {
      lossPct: expected ? (100 * cap.stats.lost) / expected : 0,
      gaps: cap.gaps.length,
      maxGapMs: cap.gaps.length ? Math.max(...cap.gaps.map((g) => (g.tEnd - g.tStart) * 1000)) : 0,
      saturatedGyro: samples.filter((s) => s.saturated).length,
      saturatedAcc: samples.filter((s) => s.accSaturated).length,
    },
  };
}

/** Clap clock fit between first and last sync event, vs reference times (e.g. video annotation). */
export function syncFit(events: SyncEvent[], refTimes: number[]) {
  if (!events.length || !refTimes.length) return null;
  const pairs: [number, number][] = [[events[0].t, refTimes[0]]];
  if (events.length >= 2 && refTimes.length >= 2) pairs.push([events[events.length - 1].t, refTimes[refTimes.length - 1]]);
  const [a, b, driftKnown] = fitClaps(pairs);
  return { a, b, driftKnown, driftPpm: a * 1e6 };
}

export { findStationary, gyroBias };
