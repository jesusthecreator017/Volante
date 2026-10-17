// Accel spike (impact / clap) detection and clap clock fit. Port of context/imu-reference/sync.rs.

import type { Sample } from "./packet";
import { G, DEG } from "./quat";

export const CLAP_G = 6.0;
export const IMPACT_G = 5.0;
/** Narrow-spike width in samples: 3 at 500 Hz (6 ms), never fewer (Apple Watch sessions are 200 Hz). */
const maxWidth = (rateHz: number) => Math.max(3, Math.round(0.006 * rateHz));
const HALF = 4;
export const MAX_DRIFT = 200e-6;

/** Per-sample deviation of accel components from the 9-sample median, in g; 0 next to a lost-packet gap. */
export function spikeStrength(samples: Sample[], rateHz = 500): Float64Array {
  const maxSpan = 9.5 / rateHz; // 19 ms at 500 Hz: the 9-sample window must not straddle a hole
  const n = samples.length;
  const out = new Float64Array(n);
  if (n < 2 * HALF + 1) return out;
  const v = new Float32Array(2 * HALF + 1);
  for (let i = HALF; i < n - HALF; i++) {
    if (samples[i + HALF].t - samples[i - HALF].t > maxSpan) continue;
    let d2 = 0;
    for (let a = 0; a < 3; a++) {
      for (let k = 0; k < 2 * HALF + 1; k++) v[k] = samples[i - HALF + k].acc[a];
      v.sort();
      const dev = (samples[i].acc[a] - v[HALF]) / G;
      d2 += dev * dev;
    }
    out[i] = Math.sqrt(d2);
  }
  return out;
}

export interface Spike {
  /** Centroid device time, s */
  t: number;
  /** Peak strength, g */
  peakG: number;
  /** Index of the strongest sample */
  index: number;
}

/** Narrow spikes (<= 3 samples) above thrG at or after sample `from`. sync.rs `spikes`, plus peak strength. */
export function spikes(samples: Sample[], strength: Float64Array, from: number, thrG: number, rateHz = 500): Spike[] {
  const out: Spike[] = [];
  let i = from;
  while (i < samples.length) {
    if (strength[i] <= thrG) {
      i++;
      continue;
    }
    const s = i;
    while (i < samples.length && strength[i] > thrG) i++;
    if (i - s <= maxWidth(rateHz)) {
      const lo = Math.max(0, s - 1);
      const hi = Math.min(i + 1, samples.length);
      let num = 0;
      let den = 0;
      let peak = 0;
      let idx = s;
      for (let k = lo; k < hi; k++) {
        num += samples[k].t * strength[k];
        den += strength[k];
        if (strength[k] > peak) {
          peak = strength[k];
          idx = k;
        }
      }
      out.push({ t: num / den, peakG: peak, index: idx });
    }
  }
  return out;
}

export const findSpikes = (samples: Sample[], from: number, thrG: number) =>
  spikes(samples, spikeStrength(samples), from, thrG);

/** Mean raw |gyro| (rad/s) over device times [t0, t1). */
function meanGyroNorm(samples: Sample[], t0: number, t1: number): number | null {
  const lo = lowerBound(samples, t0);
  const hi = lowerBound(samples, t1);
  if (hi <= lo) return null;
  let s = 0;
  for (let k = lo; k < hi; k++) s += Math.hypot(...samples[k].gyro);
  return s / (hi - lo);
}

export function lowerBound(samples: Sample[], t: number): number {
  let lo = 0;
  let hi = samples.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (samples[m].t < t) lo = m + 1;
    else hi = m;
  }
  return lo;
}

/**
 * Sync events (claps) = spikes above CLAP_G with the racquet still in the 0.3 s before (mean |gyro| < 30 dps), the
 * stillness rule sync.rs `find_end_clap` applies to the end clap. Applied to every candidate here because the app
 * doesn't have video annotations to predict where the claps are.
 */
export function findClaps(samples: Sample[], strength: Float64Array, from = 0, rateHz = 500): Spike[] {
  return spikes(samples, strength, from, CLAP_G, rateHz).filter((sp) => stillBefore(samples, sp.t));
}

/** Racquet still in the 0.3 s before t (mean raw |gyro| < 30 dps; the bias is up to ~5 dps). */
export function stillBefore(samples: Sample[], t: number): boolean {
  const m = meanGyroNorm(samples, t - 0.3, t - 0.02);
  return m !== null && m < 30 * DEG;
}

/** Exact line through first and last clap (t_dev, t_ref). Returns [a, b, driftKnown]. sync.rs `fit_claps`. */
export function fitClaps(claps: [number, number][]): [number, number, boolean] {
  const first = claps[0];
  const b0 = first[1] - first[0];
  if (claps.length >= 2) {
    const last = claps[claps.length - 1];
    const slope = (last[1] - first[1]) / (last[0] - first[0]);
    if (Number.isFinite(slope) && Math.abs(slope - 1) <= MAX_DRIFT) return [slope - 1, first[1] - first[0] * slope, true];
  }
  return [0, b0, false];
}
