// Swing detection (state machine on bias-removed |ω|) and per-swing metrics.
// Thresholds are the PROMPT.md contract: start |ω| > 300 dps, end when < 120 dps for 80 ms, discard < 80 ms.

import type { Gap, Sample } from "./packet";
import type { Track } from "./fusion";
import { trackAt } from "./fusion";
import { G, DEG, type Quat, rotate } from "./quat";
import type { Spike } from "./spikes";
import { lowerBound } from "./spikes";

export interface SwingThresholds {
  startDps: number;
  endDps: number;
  endHoldMs: number;
  minDurationMs: number;
}
export const DEFAULT_THRESHOLDS: SwingThresholds = { startDps: 300, endDps: 120, endHoldMs: 80, minDurationMs: 80 };

export interface SwingWindow {
  /** first sample index above startDps */
  i0: number;
  /** exclusive: first sample of the quiet run that ended the swing */
  i1: number;
  tStart: number;
  tEnd: number;
}

/**
 * Streaming swing state machine. push() one sample at a time (in seq order); returns a window when a swing ends.
 * Offline and live use the same class.
 */
export class SwingDetector {
  private inSwing = false;
  private i0 = 0;
  private t0 = 0;
  private quietStart: { i: number; t: number } | null = null;
  constructor(public th: SwingThresholds = DEFAULT_THRESHOLDS) {}

  get active() {
    return this.inSwing;
  }
  get activeSince() {
    return this.inSwing ? this.t0 : null;
  }

  push(i: number, t: number, wDps: number): SwingWindow | null {
    const th = this.th;
    if (!this.inSwing) {
      if (wDps > th.startDps) {
        this.inSwing = true;
        this.i0 = i;
        this.t0 = t;
        this.quietStart = null;
      }
      return null;
    }
    if (wDps < th.endDps) {
      if (!this.quietStart) this.quietStart = { i, t };
      if (t - this.quietStart.t >= th.endHoldMs / 1000) return this.close();
    } else this.quietStart = null;
    return null;
  }

  /** End of data: close an open swing at the start of its quiet run (or at the last sample). */
  flush(lastI: number, lastT: number): SwingWindow | null {
    if (!this.inSwing) return null;
    if (!this.quietStart) this.quietStart = { i: lastI + 1, t: lastT };
    return this.close();
  }

  private close(): SwingWindow | null {
    const q = this.quietStart!;
    this.inSwing = false;
    this.quietStart = null;
    const w = { i0: this.i0, i1: q.i, tStart: this.t0, tEnd: q.t };
    return (w.tEnd - w.tStart) * 1000 >= this.th.minDurationMs ? w : null;
  }
}

export function detectSwings(t: ArrayLike<number>, wDps: ArrayLike<number>, th = DEFAULT_THRESHOLDS): SwingWindow[] {
  const d = new SwingDetector(th);
  const out: SwingWindow[] = [];
  for (let i = 0; i < t.length; i++) {
    const w = d.push(i, t[i], wDps[i]);
    if (w) out.push(w);
  }
  const last = d.flush(t.length - 1, t[t.length - 1]);
  if (last) out.push(last);
  return out;
}

export type StrokeType = "smash" | "clear" | "drop" | "drive" | "lift" | "net" | "no contact";

export interface RacquetGeometry {
  lengthM: number;
  headCenterM: number;
}
export const DEFAULT_GEOMETRY: RacquetGeometry = { lengthM: 0.675, headCenterM: 0.53 };

export interface SwingContext {
  samples: Sample[];
  /** bias-removed |ω| in dps per sample */
  wDps: Float64Array;
  track: Track;
  /** impact-candidate spikes (> IMPACT_G), claps already removed */
  impacts: Spike[];
  gaps: Gap[];
  geometry: RacquetGeometry;
  handedness: "right" | "left";
  /** session start (device time) for relative times */
  t0: number;
}

export interface Swing {
  index: number;
  /** device time, s */
  tStart: number;
  tEnd: number;
  /** relative to session start, s */
  start: number;
  durationMs: number;
  /** null when not computable (see phaseNote) */
  backswingMs: number | null;
  forwardMs: number | null;
  followMs: number | null;
  phaseNote: string;
  peakDps: number;
  peakRad: number;
  tPeak: number;
  /** forward-swing peak: largest |ω| in the 150 ms before the impact (global peak without an impact) */
  fwdPeakDps: number;
  tFwdPeak: number;
  /** rotation-only head speed at the forward-swing peak... see headSpeedNote; m/s */
  headSpeed: number;
  peakAccG: number;
  impact: boolean;
  impactG: number | null;
  tImpact: number | null;
  impactVsPeakMs: number | null;
  timing: "early" | "on peak" | "late" | null;
  /** handle elevation above horizontal at impact (or forward peak), degrees; + = tip above butt */
  handleTiltDeg: number | null;
  /** face normal elevation, degrees: 0 = face vertical, +90 = face pointing up */
  faceAngleDeg: number | null;
  overhead: boolean | null;
  side: "forehand" | "backhand" | null;
  /** ω about the face normal at the forward peak (dps), the input to forehand/backhand */
  faceNormalRateDps: number;
  stroke: StrokeType;
  saturatedSamples: number;
  accSaturatedSamples: number;
  lostPackets: number;
  confidence: number;
  confidenceReasons: string[];
  /** orientation used for tilt/face */
  qAt: Quat | null;
}

const ms = (s: number) => s * 1000;

export function analyzeSwing(ctx: SwingContext, w: SwingWindow, index: number): Swing {
  const { samples, wDps } = ctx;
  let iPeak = w.i0;
  let peakAcc = 0;
  let sat = 0;
  let accSat = 0;
  for (let i = w.i0; i < w.i1; i++) {
    if (wDps[i] > wDps[iPeak]) iPeak = i;
    const s = samples[i];
    peakAcc = Math.max(peakAcc, Math.hypot(s.acc[0], s.acc[1], s.acc[2]) / G);
    if (s.saturated) sat++;
    if (s.accSaturated) accSat++;
  }
  const peakDps = wDps[iPeak];
  const tPeak = samples[iPeak].t;

  // Impact: strongest impact spike inside the swing window (+20 ms: contact can land right at the quiet edge)
  const inside = ctx.impacts.filter((sp) => sp.t >= w.tStart && sp.t <= w.tEnd + 0.02);
  const imp = inside.length ? inside.reduce((a, b) => (b.peakG > a.peakG ? b : a)) : null;

  // Forward-swing peak
  let iFwd = iPeak;
  if (imp) {
    const lo = Math.max(w.i0, lowerBound(samples, imp.t - 0.15));
    const hi = Math.min(w.i1, lowerBound(samples, imp.t + 0.005));
    if (hi > lo) {
      iFwd = lo;
      for (let i = lo; i < hi; i++) if (wDps[i] > wDps[iFwd]) iFwd = i;
    }
  }
  const tFwd = samples[iFwd].t;
  // Valley between backswing and forward swing: minimum |ω| between the swing start and the forward peak, searched only
  // after the first local max so a monotone rise doesn't put the valley at the start.
  let iValley: number | null = null;
  {
    // Skip the forward lobe's own rising flank (values >= 60 % of the forward peak), then take the largest |ω| before it
    let j = iFwd;
    while (j > w.i0 && wDps[j - 1] >= 0.6 * wDps[iFwd]) j--;
    let iMax = w.i0;
    for (let i = w.i0; i < j; i++) if (wDps[i] > wDps[iMax]) iMax = i;
    if (j > w.i0 && iMax < iFwd) {
      let iv = iMax;
      for (let i = iMax; i < iFwd; i++) if (wDps[i] < wDps[iv]) iv = i;
      // A real reversal dips well below both peaks
      if (wDps[iv] < 0.6 * Math.min(wDps[iMax], wDps[iFwd])) iValley = iv;
    }
  }
  const tSplit2 = imp ? imp.t : tFwd;
  let backswingMs: number | null = null;
  let forwardMs: number | null = null;
  let phaseNote: string;
  if (iValley !== null) {
    backswingMs = ms(samples[iValley].t - w.tStart);
    forwardMs = ms(tSplit2 - samples[iValley].t);
    phaseNote = imp ? "split at the |ω| valley and the impact" : "split at the |ω| valley and the forward peak (no impact)";
  } else {
    forwardMs = ms(tSplit2 - w.tStart);
    phaseNote = "no backswing/forward reversal found: whole start→" + (imp ? "impact" : "peak") + " counted as forward swing";
  }
  const followMs = ms(w.tEnd - tSplit2);

  // Orientation at impact (or forward peak)
  const tq = imp ? imp.t : tFwd;
  const at = trackAt(ctx.track, tq);
  const q = at ? at.q : nearestQ(ctx.track, tq);
  let handleTiltDeg: number | null = null;
  let faceAngleDeg: number | null = null;
  let overhead: boolean | null = null;
  if (q) {
    const handle = rotate(q, [0, 1, 0]);
    const normal = rotate(q, [0, 0, 1]);
    handleTiltDeg = Math.asin(clamp(handle[1])) / DEG;
    faceAngleDeg = Math.asin(clamp(normal[1])) / DEG;
    overhead = handleTiltDeg > 0;
  }
  // Forehand/backhand: sign of the rate about the face normal (sensor/racquet +Z) at the forward peak.
  // Convention (right hand): positive = forehand. Set from the synthetic data (forehand overhead strokes); verify on
  // real recordings. Left-handed flips it.
  const wz = ctx.track.w[iFwd][2] / DEG;
  const sideSign = ctx.handedness === "right" ? 1 : -1;
  const side: Swing["side"] = Math.abs(wz) < 50 ? null : wz * sideSign > 0 ? "forehand" : "backhand";

  const headSpeed = peakDps * DEG * ctx.geometry.headCenterM;
  let impactVsPeakMs: number | null = null;
  let timing: Swing["timing"] = null;
  if (imp) {
    impactVsPeakMs = ms(imp.t - tFwd);
    timing = impactVsPeakMs < -10 ? "early" : impactVsPeakMs > 10 ? "late" : "on peak";
  }

  // Confidence
  const reasons: string[] = [];
  let conf = 1;
  let lost = 0;
  let bigNearKey = false;
  for (const g of ctx.gaps) {
    if (g.tEnd < w.tStart - 0.02 || g.tStart > w.tEnd) continue;
    lost += g.missing;
    const key = [tFwd, imp?.t].filter((x): x is number => x != null);
    if (g.missing >= 4 && key.some((k) => k >= g.tStart - 0.03 && k <= g.tEnd + 0.03)) bigNearKey = true;
  }
  if (lost) {
    conf -= Math.min(0.4, lost * 0.02);
    reasons.push(`${lost} packets lost inside the swing`);
  }
  if (bigNearKey) {
    conf -= 0.2;
    reasons.push("a gap of 4+ packets near the peak/impact (impact may be missed)");
  }
  if (sat) {
    conf -= 0.2;
    reasons.push(`gyro saturated on ${sat} samples (peak |ω| and speed are lower bounds)`);
  }
  if (at && at.conf < 1) {
    conf -= 0.1;
    reasons.push("orientation low-confidence at impact (fusion near a disturbance)");
  }
  conf = Math.max(0.1, Math.min(1, conf));

  return {
    index,
    tStart: w.tStart,
    tEnd: w.tEnd,
    start: w.tStart - ctx.t0,
    durationMs: ms(w.tEnd - w.tStart),
    backswingMs,
    forwardMs,
    followMs,
    phaseNote,
    peakDps,
    peakRad: peakDps * DEG,
    tPeak,
    fwdPeakDps: wDps[iFwd],
    tFwdPeak: tFwd,
    headSpeed,
    peakAccG: peakAcc,
    impact: !!imp,
    impactG: imp ? imp.peakG : null,
    tImpact: imp ? imp.t : null,
    impactVsPeakMs,
    timing,
    handleTiltDeg,
    faceAngleDeg,
    overhead,
    side,
    faceNormalRateDps: wz,
    stroke: classifyStroke(headSpeed, overhead, handleTiltDeg, !!imp, sat > 0),
    saturatedSamples: sat,
    accSaturatedSamples: accSat,
    lostPackets: lost,
    confidence: conf,
    confidenceReasons: reasons,
    qAt: q,
  };
}

const clamp = (x: number) => Math.max(-1, Math.min(1, x));

function nearestQ(tr: Track, t: number): Quat | null {
  if (!tr.t.length) return null;
  let lo = 0;
  let hi = tr.t.length - 1;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (tr.t[m] < t) lo = m + 1;
    else hi = m;
  }
  return tr.q[lo];
}

/**
 * Stroke type HEURISTIC (a trained classifier comes later). Speeds are rotation-only head speeds, which top out near
 * 18.5 m/s at gyro saturation (2000 dps x 0.53 m), so thresholds are on that scale.
 */
export function classifyStroke(
  headSpeed: number,
  overhead: boolean | null,
  tiltDeg: number | null,
  impact: boolean,
  saturated: boolean,
): StrokeType {
  if (!impact) return "no contact";
  if (overhead) {
    if (saturated || headSpeed >= 15) return "smash";
    if (headSpeed >= 10) return "clear";
    return "drop";
  }
  const t = tiltDeg ?? 0;
  if (t > -25) return headSpeed >= 9 ? "drive" : "net";
  return headSpeed >= 8 ? "lift" : "net";
}

export const confidenceLabel = (c: number) => (c >= 0.8 ? "high" : c >= 0.5 ? "medium" : "low");
