// Live ingest: notification bytes -> packets -> accounting -> small reorder buffer -> in-order samples ->
// live calibration, forward-only Madgwick, swing state machine, per-swing metrics.
// Everything is processed in batches per notification; the UI reads the state at ~30 fps.

import {
  PACKET_LEN,
  Unwrapper,
  decodePacket,
  encode,
  isMarker,
  toSample,
  type Marker,
  type RawPacket,
  type Sample,
} from "./packet";
import { BLOCK, LiveFusion, blockIsStill, meanAcc, type Track, type Params } from "./fusion";
import { DEG, G, type Quat, type Vec3 } from "./quat";
import { spikeStrength, spikes, stillBefore, IMPACT_G, CLAP_G } from "./spikes";
import { SwingDetector, analyzeSwing, type Swing } from "./swings";
import { DEFAULT_ANALYSIS, tiltFromAcc, type AnalysisSettings } from "./analysis";

/** Hold back this many packets (by seq) to put reordered ones back in place. ~16 ms at 500 Hz. */
const REORDER_DEPTH = 8;
const MIN_STILL_BLOCKS = 10;

export interface LiveStats {
  notifications: number;
  badNotifications: number;
  arrivals: number;
  unique: number;
  duplicate: number;
  reordered: number;
  /** missing seqs between first and highest seen so far (late arrivals fill them back) */
  lost: number;
  gaps: number;
  lastGapPackets: number;
  /** reordered packets that arrived after their slot was already processed live (kept for the saved session) */
  tooLate: number;
  saturatedGyro: number;
  saturatedAcc: number;
  markers: number;
}

export class LiveSession {
  readonly startedHostMs: number;
  /** arrival-order packets (markers excluded): exactly what imu.bin stores */
  private raw: RawPacket[] = [];
  readonly markers: Marker[] = [];
  /** [t_dev of newest sample in a notification, host time s] */
  readonly arrivals: [number, number][] = [];
  readonly stats: LiveStats = {
    notifications: 0,
    badNotifications: 0,
    arrivals: 0,
    unique: 0,
    duplicate: 0,
    reordered: 0,
    lost: 0,
    gaps: 0,
    lastGapPackets: 0,
    tooLate: 0,
    saturatedGyro: 0,
    saturatedAcc: 0,
    markers: 0,
  };
  private un = new Unwrapper();
  private seen = new Set<number>();
  private topSeq = -Infinity;
  private firstSeq: number | null = null;
  private pending = new Map<number, Sample>();
  private nextSeq: number | null = null;
  /** in-order samples (live processing) */
  readonly samples: Sample[] = [];
  /** bias-removed |ω| (dps) per in-order sample, 0 before calibration */
  readonly wDps: number[] = [];
  readonly track: Track = { t: [], q: [], w: [], conf: [] };
  /** index into samples where the live track starts */
  trackStart = 0;
  bias: Vec3 | null = null;
  biasWindowT: [number, number] | null = null;
  biasAtHostMs: number | null = null;
  restAccG: number | null = null;
  restGyroStdDps: Vec3 | null = null;
  restAccStdG: Vec3 | null = null;
  private stillBlocks = 0;
  private processedBlocks = 0;
  private fusion: LiveFusion | null = null;
  private detector: SwingDetector;
  readonly swings: Swing[] = [];
  lastHostMs = 0;
  /** current orientation (court frame) and confidence */
  q: Quat | null = null;
  qConf = 1;
  stationary = false;
  /** host time of the newest arrival vs the device clock fit (for "current gap") */
  lastPacketHostMs = 0;
  flatCheck: { until: number; acc: Vec3[]; result: { handleDeg: number; faceDeg: number; still: boolean } | null } | null =
    null;
  onSwing: ((s: Swing) => void) | null = null;
  /** sync claps detected live (accel spike > 6 g with the racquet still before it), device time s */
  readonly claps: { t: number; peakG: number }[] = [];
  private clapCheckedT = -Infinity;

  constructor(
    readonly rateHz: number,
    readonly settings: AnalysisSettings = DEFAULT_ANALYSIS,
    readonly mode: "stream" | "upload" = "stream",
  ) {
    this.startedHostMs = performance.timeOrigin + performance.now();
    this.detector = new SwingDetector(settings.thresholds);
  }

  /** One imu_stream notification (N whole 18-byte packets). */
  ingest(dv: DataView, hostMs: number) {
    this.stats.notifications++;
    this.lastHostMs = hostMs;
    if (dv.byteLength === 0 || dv.byteLength % PACKET_LEN !== 0) {
      this.stats.badNotifications++;
      return;
    }
    let newest = -Infinity;
    for (let off = 0; off < dv.byteLength; off += PACKET_LEN) {
      const p = decodePacket(dv, off);
      if (isMarker(p)) {
        const t = this.un.peekTime(p.t32);
        if (t !== null) {
          this.markers.push({ t: t * 1e-6 });
          this.stats.markers++;
        }
        continue;
      }
      this.raw.push(p);
      const { seq, tUs } = this.un.next(p);
      this.stats.arrivals++;
      if (this.firstSeq === null) this.firstSeq = seq;
      if (this.seen.has(seq)) {
        this.stats.duplicate++;
        continue;
      }
      this.seen.add(seq);
      this.stats.unique++;
      if (seq < this.topSeq) this.stats.reordered++;
      this.topSeq = Math.max(this.topSeq, seq);
      const s = toSample(tUs, p.v);
      if (s.saturated) this.stats.saturatedGyro++;
      if (s.accSaturated) this.stats.saturatedAcc++;
      newest = Math.max(newest, s.t);
      if (this.nextSeq !== null && seq < this.nextSeq) {
        this.stats.tooLate++;
        continue;
      }
      this.pending.set(seq, s);
    }
    this.stats.lost = this.firstSeq === null ? 0 : this.topSeq - this.firstSeq + 1 - this.stats.unique;
    if (this.mode === "stream" && Number.isFinite(newest)) {
      this.arrivals.push([newest, hostMs / 1000]);
      this.lastPacketHostMs = hostMs;
    }
    this.drain(false);
  }

  /** Emit pending samples in seq order, holding back the newest REORDER_DEPTH unless flushing. */
  private drain(flush: boolean) {
    if (!this.pending.size) return;
    const keys = [...this.pending.keys()].sort((a, b) => a - b);
    const limit = flush ? Infinity : this.topSeq - REORDER_DEPTH;
    for (const k of keys) {
      if (k > limit) break;
      if (this.nextSeq !== null && k > this.nextSeq) {
        this.stats.gaps++;
        this.stats.lastGapPackets = k - this.nextSeq;
      }
      const s = this.pending.get(k)!;
      this.pending.delete(k);
      this.nextSeq = k + 1;
      this.process(s);
    }
  }

  finish() {
    this.drain(true);
    const w = this.detector.flush(this.samples.length - 1, this.samples[this.samples.length - 1]?.t ?? 0);
    if (w) this.addSwing(w);
  }

  private process(s: Sample) {
    const i = this.samples.length;
    this.samples.push(s);
    if (this.flatCheck) {
      if (s.t <= this.flatCheck.until) this.flatCheck.acc.push([...s.acc] as Vec3);
    }
    // Stationary indicator + live calibration, per 0.1 s block
    if ((i + 1) % BLOCK === 0) {
      const lo = i + 1 - BLOCK;
      const still = blockIsStill(this.samples, lo, i + 1);
      this.stationary = still;
      this.processedBlocks++;
      if (!this.bias) {
        // Same rule as fusion.rs find_stationary: >= 10 consecutive still blocks; the block that broke the run is not used.
        if (still) this.stillBlocks++;
        else this.stillBlocks = 0;
        if (this.stillBlocks >= MIN_STILL_BLOCKS + 1) this.calibrate(i + 1 - this.stillBlocks * BLOCK, i + 1 - BLOCK);
      }
      this.finishFlatCheck();
      this.detectClaps(i);
    }
    if (!this.fusion) {
      this.wDps.push(0);
      return;
    }
    const b = this.bias!;
    const w: Vec3 = [s.gyro[0] - b[0], s.gyro[1] - b[1], s.gyro[2] - b[2]];
    const wd = Math.hypot(w[0], w[1], w[2]) / DEG;
    this.wDps.push(wd);
    const r = this.fusion.push(s);
    this.q = r.q;
    this.qConf = r.conf;
    this.track.t.push(s.t);
    this.track.q.push(r.q);
    this.track.w.push(w);
    this.track.conf.push(r.conf);
    const sw = this.detector.push(i, s.t, wd);
    if (sw) this.addSwing(sw);
  }

  private detectClaps(i: number) {
    const lo = Math.max(0, i + 1 - BLOCK - 16);
    const sub = this.samples.slice(lo, i + 1);
    if (sub.length < 12) return;
    const limit = sub[sub.length - 5].t; // spike strength needs 4 samples after
    for (const sp of spikes(sub, spikeStrength(sub), 0, CLAP_G)) {
      if (sp.t <= this.clapCheckedT || sp.t > limit) continue;
      if (stillBefore(this.samples, sp.t)) this.claps.push({ t: sp.t, peakG: sp.peakG });
    }
    this.clapCheckedT = Math.max(this.clapCheckedT, limit);
  }

  private calibrate(lo: number, hi: number) {
    const n = hi - lo;
    const b: Vec3 = [0, 0, 0];
    for (let k = lo; k < hi; k++) for (let a = 0; a < 3; a++) b[a] += this.samples[k].gyro[a];
    this.bias = [b[0] / n, b[1] / n, b[2] / n];
    const acc = meanAcc(this.samples, lo, hi);
    this.restAccG = Math.hypot(...acc) / G;
    const gs: Vec3 = [0, 0, 0];
    const as: Vec3 = [0, 0, 0];
    for (let k = lo; k < hi; k++)
      for (let a = 0; a < 3; a++) {
        gs[a] += (this.samples[k].gyro[a] - this.bias[a]) ** 2;
        as[a] += ((this.samples[k].acc[a] - acc[a]) / G) ** 2;
      }
    this.restGyroStdDps = gs.map((v) => Math.sqrt(v / n) / DEG) as Vec3;
    this.restAccStdG = as.map((v) => Math.sqrt(v / n)) as Vec3;
    this.biasWindowT = [this.samples[lo].t, this.samples[hi - 1].t];
    this.biasAtHostMs = this.lastHostMs || performance.timeOrigin + performance.now();
    const p: Params = { beta: this.settings.beta, twoPass: false, nominalDt: 1 / this.rateHz };
    this.fusion = new LiveFusion(this.bias, p);
    this.fusion.reset(acc);
    this.trackStart = this.samples.length;
    // Fill |ω| for the samples already seen
    for (let k = 0; k < this.samples.length; k++) {
      const g = this.samples[k].gyro;
      this.wDps[k] = Math.hypot(g[0] - this.bias[0], g[1] - this.bias[1], g[2] - this.bias[2]) / DEG;
    }
  }

  private addSwing(w: { i0: number; i1: number; tStart: number; tEnd: number }) {
    // Analyse on a slice with margin (spike detection needs ±4 samples)
    const a = Math.max(this.trackStart, w.i0 - 20);
    const b = Math.min(this.samples.length, w.i1 + 20);
    const sub = this.samples.slice(a, b);
    const ts = a - this.trackStart;
    const track: Track = {
      t: this.track.t.slice(ts, ts + (b - a)),
      q: this.track.q.slice(ts, ts + (b - a)),
      w: this.track.w.slice(ts, ts + (b - a)),
      conf: this.track.conf.slice(ts, ts + (b - a)),
    };
    const imp = spikes(sub, spikeStrength(sub), 0, IMPACT_G);
    const gaps = [];
    for (let k = 1; k < sub.length; k++) {
      const missing = Math.round((sub[k].t - sub[k - 1].t) * this.rateHz) - 1;
      if (missing > 0) gaps.push({ tStart: sub[k - 1].t, tEnd: sub[k].t, missing });
    }
    const swing = analyzeSwing(
      {
        samples: sub,
        wDps: Float64Array.from(this.wDps.slice(a, b)),
        track,
        impacts: imp,
        gaps,
        geometry: this.settings.geometry,
        handedness: this.settings.handedness,
        t0: this.samples[0].t,
      },
      { i0: w.i0 - a, i1: w.i1 - a, tStart: w.tStart, tEnd: w.tEnd },
      this.swings.length + 1,
    );
    this.swings.push(swing);
    this.onSwing?.(swing);
  }

  /** "Hold racquet flat" mounting check: average the accelerometer over the next second. */
  startFlatCheck() {
    const last = this.samples[this.samples.length - 1];
    this.flatCheck = { until: (last?.t ?? 0) + 1, acc: [], result: null };
  }

  private finishFlatCheck() {
    const f = this.flatCheck;
    const last = this.samples[this.samples.length - 1];
    if (!f || f.result || !last || last.t < f.until || f.acc.length < 10) return;
    const m: Vec3 = [0, 0, 0];
    for (const a of f.acc) for (let k = 0; k < 3; k++) m[k] += a[k] / f.acc.length;
    let spread = 0;
    for (const a of f.acc) spread = Math.max(spread, Math.hypot(a[0] - m[0], a[1] - m[1], a[2] - m[2]) / G);
    f.result = { ...tiltFromAcc(m), still: spread < 0.1 };
  }

  get inSwing() {
    return this.detector.active;
  }
  get swingSince() {
    return this.detector.activeSince;
  }

  /** The session as an imu.bin file (arrival order, markers excluded). */
  toImuBin(): Uint8Array {
    return encode(this.rateHz, this.raw);
  }

  get rawCount() {
    return this.raw.length;
  }
}
