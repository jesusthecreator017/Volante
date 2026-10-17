// Gyro bias calibration and 6-axis Madgwick orientation. Port of context/imu-reference/fusion.rs.
// Madgwick runs in a z-up world internally; output is rotated into the court frame (+Y up).
// Yaw is unobservable from gravity alone: heading starts arbitrary and drifts.

import type { Sample } from "./packet";
import { G, type Quat, type Vec3, fromAxisAngle, fromTwoVectors, mul, normalize, rotate, slerp, norm } from "./quat";

export const DEFAULT_BETA = 0.04;
export const MAX_DT = 0.05;
const LOW_CONF = 0.5;
const ACC_GATE_G = 0.1;
const LOST_LOW_DT_FACTOR = 4.0;
const LOW_CONF_WINDOW_S = 0.5;
const BLEND_TAU_S = 1.0;
const FAR_S = 30.0;

export const BLOCK = 50;
const MIN_BLOCKS = 10;
const STILL_GYRO_STD_RAD = (2 * Math.PI) / 180;
const STILL_GYRO_MEAN_RAD = (6 * Math.PI) / 180;
const STILL_ACC_G = 0.05;

export interface Window {
  /** Sample indices, end exclusive */
  start: number;
  end: number;
}

/** First run of >= 1 s held still. The final block of the run is dropped (may contain the first motion). */
export function findStationary(samples: Sample[]): Window | null {
  let runStart: number | null = null;
  const nBlocks = Math.floor(samples.length / BLOCK);
  for (let b = 0; b <= nBlocks; b++) {
    const still = b < nBlocks && blockIsStill(samples, b * BLOCK, (b + 1) * BLOCK);
    if (still && runStart === null) runStart = b;
    else if (!still && runStart !== null) {
      if (b - runStart >= MIN_BLOCKS) return { start: runStart * BLOCK, end: (b - 1) * BLOCK };
      runStart = null;
    }
  }
  return null;
}

/** fusion.rs block_is_still over samples[lo, hi). */
export function blockIsStill(s: Sample[], lo: number, hi: number): boolean {
  if (hi - lo < 2) return false;
  if (s[hi - 1].t - s[lo].t > 0.12) return false;
  const n = hi - lo;
  for (let axis = 0; axis < 3; axis++) {
    let gm = 0;
    let am = 0;
    for (let i = lo; i < hi; i++) {
      gm += s[i].gyro[axis];
      am += s[i].acc[axis] / G;
    }
    gm /= n;
    am /= n;
    let gv = 0;
    let av = 0;
    for (let i = lo; i < hi; i++) {
      gv += (s[i].gyro[axis] - gm) ** 2;
      av += (s[i].acc[axis] / G - am) ** 2;
    }
    if (Math.sqrt(gv / n) > STILL_GYRO_STD_RAD || Math.abs(gm) > STILL_GYRO_MEAN_RAD || Math.sqrt(av / n) > STILL_ACC_G) {
      return false;
    }
  }
  return Math.abs(norm(meanAcc(s, lo, hi)) / G - 1) < STILL_ACC_G;
}

export function meanAcc(s: Sample[], lo: number, hi: number): Vec3 {
  const m: Vec3 = [0, 0, 0];
  for (let i = lo; i < hi; i++) for (let a = 0; a < 3; a++) m[a] += s[i].acc[a];
  const n = hi - lo;
  return [m[0] / n, m[1] / n, m[2] / n];
}

/** Mean gyro over the window: the constant bias, rad/s. */
export function gyroBias(samples: Sample[], w: Window): Vec3 {
  const b: Vec3 = [0, 0, 0];
  for (let i = w.start; i < w.end; i++) for (let a = 0; a < 3; a++) b[a] += samples[i].gyro[a];
  const n = Math.max(1, w.end - w.start);
  return [b[0] / n, b[1] / n, b[2] / n];
}

export interface Params {
  beta: number;
  twoPass: boolean;
  nominalDt: number;
}
export const defaultParams = (): Params => ({ beta: DEFAULT_BETA, twoPass: true, nominalDt: 0.002 });

type MQ = [number, number, number, number]; // (w, x, y, z)

/** One Madgwick step. q = (w,x,y,z) sensor -> z-up world. acc: any scale. Mutates q. */
export function madgwickStep(q: MQ, gyro: Vec3, acc: Vec3 | null, dt: number, beta: number) {
  const [q0, q1, q2, q3] = q;
  const qd = [
    0.5 * (-q1 * gyro[0] - q2 * gyro[1] - q3 * gyro[2]),
    0.5 * (q0 * gyro[0] + q2 * gyro[2] - q3 * gyro[1]),
    0.5 * (q0 * gyro[1] - q1 * gyro[2] + q3 * gyro[0]),
    0.5 * (q0 * gyro[2] + q1 * gyro[1] - q2 * gyro[0]),
  ];
  if (acc) {
    const n = norm(acc);
    if (n > 1e-9) {
      const ax = acc[0] / n;
      const ay = acc[1] / n;
      const az = acc[2] / n;
      const f1 = 2 * (q1 * q3 - q0 * q2) - ax;
      const f2 = 2 * (q0 * q1 + q2 * q3) - ay;
      const f3 = 2 * (0.5 - q1 * q1 - q2 * q2) - az;
      const g = [
        -2 * q2 * f1 + 2 * q1 * f2,
        2 * q3 * f1 + 2 * q0 * f2 - 4 * q1 * f3,
        -2 * q0 * f1 + 2 * q3 * f2 - 4 * q2 * f3,
        2 * q1 * f1 + 2 * q2 * f2,
      ];
      const gn = Math.hypot(g[0], g[1], g[2], g[3]);
      if (gn > 1e-12) for (let i = 0; i < 4; i++) qd[i] -= (beta * g[i]) / gn;
    }
  }
  const r = [q0 + qd[0] * dt, q1 + qd[1] * dt, q2 + qd[2] * dt, q3 + qd[3] * dt];
  const n = Math.hypot(r[0], r[1], r[2], r[3]);
  for (let i = 0; i < 4; i++) q[i] = r[i] / n;
}

const toM = ([x, y, z, w]: Quat): MQ => [w, x, y, z];
const fromM = ([w, x, y, z]: MQ): Quat => [x, y, z, w];
/** Z-up Madgwick world -> court (+Y up): -90 deg about X. */
export const WORLD_TO_COURT: Quat = fromAxisAngle([1, 0, 0], -Math.PI / 2);
const UP: Vec3 = [0, 0, 1];

export interface Track {
  t: number[];
  /** Sensor -> court (+Y up), [x,y,z,w], arbitrary heading */
  q: Quat[];
  /** Bias-corrected rate, sensor frame, rad/s */
  w: Vec3[];
  /** 1 trusted, 0.5 low */
  conf: number[];
}

/** Track state at device time t (slerp between samples); null outside or inside a gap > MAX_DT. */
export function trackAt(tr: Track, t: number): { q: Quat; w: Vec3; conf: number } | null {
  // partition_point(x <= t)
  let lo = 0;
  let hi = tr.t.length;
  while (lo < hi) {
    const m = (lo + hi) >> 1;
    if (tr.t[m] <= t) lo = m + 1;
    else hi = m;
  }
  const i = lo;
  if (i === 0) return null;
  if (i === tr.t.length) return t === tr.t[i - 1] ? { q: tr.q[i - 1], w: tr.w[i - 1], conf: tr.conf[i - 1] } : null;
  const t0 = tr.t[i - 1];
  const t1 = tr.t[i];
  if (t1 - t0 > MAX_DT) return null;
  const f = (t - t0) / (t1 - t0);
  const a = tr.w[i - 1];
  const b = tr.w[i];
  return {
    q: slerp(tr.q[i - 1], tr.q[i], f),
    w: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f],
    conf: Math.min(tr.conf[i - 1], tr.conf[i]),
  };
}

/** Lightweight view of a sample for a pass (the backward pass negates times). */
interface PS {
  t: number;
  acc: Vec3;
  gyro: Vec3;
}

/** One Madgwick pass in list order; returns Madgwick-frame (w,x,y,z) after each sample. */
function pass(samples: PS[], bias: Vec3, q0: MQ, sign: number, p: Params): MQ[] {
  const q: MQ = [...q0];
  const out: MQ[] = new Array(samples.length);
  const gyroAt = (i: number): Vec3 => {
    const g = samples[i].gyro;
    return [sign * (g[0] - bias[0]), sign * (g[1] - bias[1]), sign * (g[2] - bias[2])];
  };
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    const gyro = gyroAt(i);
    const dt = i > 0 ? s.t - samples[i - 1].t : 0;
    const acc: Vec3 = [s.acc[0], s.acc[1], s.acc[2]];
    if (dt > MAX_DT) {
      const qm = fromM(q);
      const tilt = fromTwoVectors(rotate(qm, acc), UP);
      const r = toM(normalize(mul(tilt, qm)));
      for (let k = 0; k < 4; k++) q[k] = r[k];
    } else if (dt > 0) {
      const gated = Math.abs(norm(acc) / G - 1) <= ACC_GATE_G;
      const w0 = gyroAt(i - 1);
      const w1 = gyro;
      if (dt <= 1.5 * p.nominalDt) {
        const rate: Vec3 = [0.5 * (w0[0] + w1[0]), 0.5 * (w0[1] + w1[1]), 0.5 * (w0[2] + w1[2])];
        madgwickStep(q, rate, gated ? acc : null, dt, p.beta);
      } else {
        const slope = (a: number, b: number): Vec3 => {
          const h = samples[b].t - samples[a].t;
          if (h > 0 && h <= 1.5 * p.nominalDt) {
            const wa = gyroAt(a);
            const wb = gyroAt(b);
            return [(wb[0] - wa[0]) / h, (wb[1] - wa[1]) / h, (wb[2] - wa[2]) / h];
          }
          return [0, 0, 0];
        };
        const m0 = i >= 2 ? slope(i - 2, i - 1) : ([0, 0, 0] as Vec3);
        const m1 = i + 1 < samples.length ? slope(i, i + 1) : ([0, 0, 0] as Vec3);
        const n = Math.max(1, Math.round(dt / p.nominalDt));
        for (let k = 0; k < n; k++) {
          const u = (k + 0.5) / n;
          const u2 = u * u;
          const u3 = u2 * u;
          const h00 = 2 * u3 - 3 * u2 + 1;
          const h10 = u3 - 2 * u2 + u;
          const h01 = 3 * u2 - 2 * u3;
          const h11 = u3 - u2;
          const rate: Vec3 = [0, 1, 2].map((a) => h00 * w0[a] + h10 * dt * m0[a] + h01 * w1[a] + h11 * dt * m1[a]) as Vec3;
          const last = k + 1 === n;
          madgwickStep(q, rate, gated && last ? acc : null, dt / n, p.beta);
        }
      }
    }
    out[i] = [q[0], q[1], q[2], q[3]];
  }
  return out;
}

/** Disturbance intervals: saturated gyro sample, or a hole of 4+ packets. */
export function disturbances(samples: Sample[], nominalDt: number): [number, number][] {
  const d: [number, number][] = [];
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    if (i > 0 && s.t - samples[i - 1].t >= LOST_LOW_DT_FACTOR * nominalDt) d.push([samples[i - 1].t, s.t]);
    if (s.saturated) d.push([s.t, s.t]);
  }
  d.sort((a, b) => a[0] - b[0]);
  return d;
}

/** (time since end of last disturbance <= t, time until start of next >= t), capped at FAR_S. */
function distances(dist: [number, number][], t: number): [number, number] {
  let since = FAR_S;
  let until = FAR_S;
  for (const [a, b] of dist) {
    if (b <= t) since = Math.min(since, t - b);
    if (a >= t) {
      until = Math.min(until, a - t);
      break;
    }
    if (a < t && t < b) return [0, 0];
  }
  return [since, until];
}

/** Offline fusion (forward + optional backward pass blended by disturbance distance). fusion.rs `run`. */
export function run(samples: Sample[], bias: Vec3, initAcc: Vec3, p: Params): Track {
  const track: Track = { t: [], q: [], w: [], conf: [] };
  if (!samples.length) return track;
  const q0 = toM(fromTwoVectors(initAcc, UP));
  const fwd = pass(samples, bias, q0, 1, p);
  const dist = disturbances(samples, p.nominalDt);
  let q: Quat[];
  if (p.twoPass) {
    const last = samples.length - 1;
    const acc: Vec3 = [samples[last].acc[0], samples[last].acc[1], samples[last].acc[2]];
    const qf = fromM(fwd[last]);
    const qb0 =
      Math.abs(norm(acc) / G - 1) <= ACC_GATE_G ? toM(normalize(mul(fromTwoVectors(rotate(qf, acc), UP), qf))) : fwd[last];
    const rev: PS[] = [];
    for (let i = last; i >= 0; i--) rev.push({ t: -samples[i].t, acc: samples[i].acc, gyro: samples[i].gyro });
    const bwd = pass(rev, bias, qb0, -1, p).reverse();
    // Walk the disturbance list once for all samples is possible, but distances() breaks early; keep it simple.
    q = samples.map((s, i) => {
      const [since, until] = distances(dist, s.t);
      const wFwd = 1 / (1 + Math.exp((-2 * (since - until)) / BLEND_TAU_S));
      return slerp(fromM(fwd[i]), fromM(bwd[i]), 1 - wFwd);
    });
  } else q = fwd.map(fromM);
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    const [since, until] = distances(dist, s.t);
    const low = p.twoPass ? since < LOW_CONF_WINDOW_S && until < LOW_CONF_WINDOW_S : since < LOW_CONF_WINDOW_S;
    track.t.push(s.t);
    track.q.push(normalize(mul(WORLD_TO_COURT, q[i])));
    track.w.push([s.gyro[0] - bias[0], s.gyro[1] - bias[1], s.gyro[2] - bias[2]]);
    track.conf.push(low ? LOW_CONF : 1);
  }
  return track;
}

/** Stationary window -> bias -> fusion. null when no stationary window exists. */
export function calibrateAndRun(samples: Sample[], p: Params): { track: Track; bias: Vec3; window: Window } | null {
  const w = findStationary(samples);
  if (!w) return null;
  const bias = gyroBias(samples, w);
  const acc = meanAcc(samples, w.start, w.end);
  return { track: run(samples, bias, acc, p), bias, window: w };
}

/**
 * Live (forward-only) fusion, one sample at a time. Same step rules as `pass` with sign +1, except the Hermite slope
 * after a hole uses only past samples (the next one hasn't arrived): m1 = 0. Confidence = forward-only rule.
 */
export class LiveFusion {
  private q: MQ | null = null;
  private prev: Sample | null = null;
  private prevW: Vec3 = [0, 0, 0];
  private prevSlope: Vec3 = [0, 0, 0];
  private lastDisturbEnd = -Infinity;
  constructor(public bias: Vec3, public p: Params) {}

  reset(initAcc: Vec3) {
    this.q = toM(fromTwoVectors(initAcc, UP));
    this.prev = null;
  }

  /** Returns court-frame quaternion and confidence after this sample. */
  push(s: Sample): { q: Quat; conf: number } {
    const acc: Vec3 = [s.acc[0], s.acc[1], s.acc[2]];
    if (!this.q) this.q = toM(fromTwoVectors(acc, UP));
    const q = this.q;
    const w1: Vec3 = [s.gyro[0] - this.bias[0], s.gyro[1] - this.bias[1], s.gyro[2] - this.bias[2]];
    const p = this.p;
    if (this.prev) {
      const dt = s.t - this.prev.t;
      if (dt >= LOST_LOW_DT_FACTOR * p.nominalDt) this.lastDisturbEnd = s.t;
      if (dt > MAX_DT) {
        const qm = fromM(q);
        const r = toM(normalize(mul(fromTwoVectors(rotate(qm, acc), UP), qm)));
        for (let k = 0; k < 4; k++) q[k] = r[k];
      } else if (dt > 0) {
        const gated = Math.abs(norm(acc) / G - 1) <= ACC_GATE_G;
        const w0 = this.prevW;
        if (dt <= 1.5 * p.nominalDt) {
          madgwickStep(q, [0.5 * (w0[0] + w1[0]), 0.5 * (w0[1] + w1[1]), 0.5 * (w0[2] + w1[2])], gated ? acc : null, dt, p.beta);
        } else {
          const m0 = this.prevSlope;
          const n = Math.max(1, Math.round(dt / p.nominalDt));
          for (let k = 0; k < n; k++) {
            const u = (k + 0.5) / n;
            const u2 = u * u;
            const u3 = u2 * u;
            const h00 = 2 * u3 - 3 * u2 + 1;
            const h10 = u3 - 2 * u2 + u;
            const h01 = 3 * u2 - 2 * u3;
            const rate = [0, 1, 2].map((a) => h00 * w0[a] + h10 * dt * m0[a] + h01 * w1[a]) as Vec3;
            madgwickStep(q, rate, gated && k + 1 === n ? acc : null, dt / n, p.beta);
          }
        }
      }
      this.prevSlope =
        dt > 0 && dt <= 1.5 * p.nominalDt
          ? [(w1[0] - this.prevW[0]) / dt, (w1[1] - this.prevW[1]) / dt, (w1[2] - this.prevW[2]) / dt]
          : [0, 0, 0];
    }
    if (s.saturated) this.lastDisturbEnd = s.t;
    this.prev = s;
    this.prevW = w1;
    return {
      q: normalize(mul(WORLD_TO_COURT, fromM(q))),
      conf: s.t - this.lastDisturbEnd < LOW_CONF_WINDOW_S ? LOW_CONF : 1,
    };
  }
}
