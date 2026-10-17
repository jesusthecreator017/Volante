// Mock BLE device: replays an imu.bin (sample-data by default) at real-time rate in notification-sized chunks,
// implementing the full control/status contract of docs/ble-profile.md.

import { Emitter, hostNowMs, type ImuDevice } from "./device";
import { CMD, REQUESTED_MTU, packetsPerNotification, type DeviceStatus } from "./profile";
import { encodePacket, parseHeader, rawPackets, MARKER_V, PACKET_LEN, type RawPacket } from "../core/packet";

export interface MockOptions {
  /** probability of dropping each packet in transit (0..1) */
  lossProb: number;
  /** probability that a dropped packet starts a burst of 5-20 drops */
  burstProb: number;
  /** max random delivery delay per notification, ms (link latency jitter) */
  jitterMs: number;
  /** replay the file forever (seq/t continue across loops) */
  loop: boolean;
  /** upload speed multiple of real time */
  uploadSpeed: number;
  mtu: number;
}

export const DEFAULT_MOCK: MockOptions = { lossProb: 0, burstProb: 0.2, jitterMs: 8, loop: true, uploadSpeed: 8, mtu: REQUESTED_MTU };

/**
 * Timer-free core of the mock (unit-testable). The "device" produces packets in file arrival order on its own clock;
 * advance(ms) returns the notifications due.
 */
export class MockEngine {
  readonly rateHz: number;
  private packets: RawPacket[];
  private pos = 0;
  private loopSeq = 0;
  private loopT = 0;
  private spanSeq: number;
  private spanT: number;
  private carry = 0;
  /** device clock since the start of the current loop, µs */
  private clockUs = 0;
  /** per packet: device time relative to the file's first packet (unwrapped), µs */
  private relT: number[];
  private burst = 0;
  /** device micros of the most recent produced packet */
  private lastT32 = 0;
  private lastSeq16 = 0;
  streaming = false;
  recording = false;
  readonly recorded: RawPacket[] = [];
  private uploadQueue: RawPacket[] = [];
  private uploadTotal = 0;
  private uploadSent = 0;
  battery = 87;

  constructor(
    file: Uint8Array,
    public opts: MockOptions = DEFAULT_MOCK,
    private rand: () => number = Math.random,
  ) {
    this.rateHz = parseHeader(file).rateHz;
    this.packets = rawPackets(file).filter((p) => !p.v.every((x, i) => x === MARKER_V[i]));
    const seqs = this.packets.map((p) => p.seq16);
    // unwrapped span of the file (seq wraps inside the sample session)
    let s = seqs[0];
    let lo = s;
    let hi = s;
    for (let i = 1; i < seqs.length; i++) {
      s += ((((seqs[i] - seqs[i - 1]) & 0xffff) << 16) >> 16);
      lo = Math.min(lo, s);
      hi = Math.max(hi, s);
    }
    this.spanSeq = hi - lo + 1;
    this.relT = [0];
    for (let i = 1; i < this.packets.length; i++)
      this.relT.push(this.relT[i - 1] + ((this.packets[i].t32 - this.packets[i - 1].t32) | 0));
    const t0 = Math.min(...this.relT);
    this.relT = this.relT.map((x) => x - t0);
    this.spanT = Math.round(Math.max(...this.relT) + 1e6 / this.rateHz);
  }

  get perNotification() {
    return packetsPerNotification(this.opts.mtu);
  }

  status(): DeviceStatus {
    return {
      v: 1,
      fw: "mock-0.1.0",
      rate_hz: this.rateHz,
      battery_pct: this.battery,
      streaming: this.streaming,
      recording: this.recording,
      buffered_bytes: this.recorded.length * PACKET_LEN,
      upload_total_bytes: this.uploadTotal,
      upload_sent_bytes: this.uploadSent,
      acc_range_g: 16,
      gyro_range_dps: 2000,
    };
  }

  /** Next packet due by the device clock (file arrival order, paced by each packet's own timestamp so lost packets
   * leave real time holes, shifted per loop). null when none is due yet or at the end without loop. */
  private produce(): RawPacket | null {
    if (this.pos >= this.packets.length) {
      if (!this.opts.loop) return null;
      this.pos = 0;
      this.loopSeq += this.spanSeq;
      this.loopT += this.spanT;
      this.clockUs -= this.spanT;
    }
    if (this.relT[this.pos] > this.clockUs) return null;
    const p = this.packets[this.pos++];
    const out: RawPacket = { seq16: (p.seq16 + this.loopSeq) & 0xffff, t32: (p.t32 + this.loopT) >>> 0, v: p.v };
    this.lastT32 = out.t32;
    this.lastSeq16 = out.seq16;
    return out;
  }

  private dropped(): boolean {
    if (this.burst > 0) {
      this.burst--;
      return true;
    }
    if (this.rand() < this.opts.lossProb) {
      if (this.rand() < this.opts.burstProb) this.burst = 4 + Math.floor(this.rand() * 16);
      return true;
    }
    return false;
  }

  private chunk(ps: RawPacket[]): DataView[] {
    const out: DataView[] = [];
    const n = this.perNotification;
    for (let i = 0; i < ps.length; i += n) {
      const part = ps.slice(i, i + n);
      const buf = new DataView(new ArrayBuffer(part.length * PACKET_LEN));
      part.forEach((p, k) => encodePacket(p, buf, k * PACKET_LEN));
      out.push(buf);
    }
    return out;
  }

  /** Advance the device clock by ms; returns notifications to deliver. */
  advance(ms: number): DataView[] {
    this.clockUs += ms * 1000;
    this.carry += (ms / 1000) * this.rateHz;
    const n = Math.floor(this.carry);
    this.carry -= n;
    const live: RawPacket[] = [];
    for (;;) {
      const p = this.produce();
      if (!p) break;
      if (this.recording) this.recorded.push(p);
      if (this.streaming && !this.dropped()) live.push(p);
    }
    const out = this.chunk(live);
    if (this.uploadQueue.length) {
      const k = Math.min(this.uploadQueue.length, Math.ceil(n * this.opts.uploadSpeed));
      const part = this.uploadQueue.splice(0, k);
      this.uploadSent += part.length * PACKET_LEN;
      out.push(...this.chunk(part)); // upload is reliable (acknowledged by the firmware design), no loss injected
    }
    return out;
  }

  get uploading() {
    return this.uploadQueue.length > 0;
  }

  command(cmd: number): DataView[] {
    switch (cmd) {
      case CMD.START_STREAM:
        this.streaming = true;
        break;
      case CMD.STOP_STREAM:
        this.streaming = false;
        break;
      case CMD.START_RECORDING:
        this.recorded.length = 0;
        this.recording = true;
        break;
      case CMD.STOP_RECORDING:
        this.recording = false;
        break;
      case CMD.UPLOAD:
        // Contract: streaming stops while an upload runs (both share imu_stream)
        this.streaming = false;
        this.uploadQueue = [...this.recorded];
        this.uploadTotal = this.uploadQueue.length * PACKET_LEN;
        this.uploadSent = 0;
        break;
      case CMD.SYNC_MARK: {
        const m: RawPacket = { seq16: this.lastSeq16, t32: this.lastT32, v: [...MARKER_V] };
        if (this.recording) this.recorded.push(m);
        return this.streaming ? this.chunk([m]) : [];
      }
    }
    return [];
  }
}

export class MockDevice extends Emitter implements ImuDevice {
  readonly kind = "mock" as const;
  readonly id = "mock-0";
  readonly name = "oso mock (sample-data/imu.bin)";
  readonly hasBatteryService = true;
  private timer: ReturnType<typeof setInterval> | null = null;
  private last = 0;
  private statusTick = 0;
  private nextAt = 0;
  engine: MockEngine | null = null;

  constructor(
    private loadFile: () => Promise<Uint8Array>,
    public opts: MockOptions = { ...DEFAULT_MOCK },
  ) {
    super();
  }

  get mtu() {
    return this.opts.mtu;
  }

  async connect() {
    this.engine = new MockEngine(await this.loadFile(), this.opts);
    this.last = performance.now();
    // Nominal notification interval: 13 packets at 500 Hz = 26 ms
    this.timer = setInterval(() => this.tick(), 20);
  }

  private tick() {
    const e = this.engine!;
    const now = performance.now();
    const out = e.advance(now - this.last);
    this.last = now;
    this.deliver(out);
    if (++this.statusTick % 25 === 0) this.emit("status", e.status());
  }

  private deliver(out: DataView[]) {
    for (const dv of out) {
      // Random latency, but notifications on one characteristic keep their order
      const now = performance.now();
      this.nextAt = Math.max(this.nextAt, now + Math.random() * this.opts.jitterMs);
      setTimeout(() => this.emit("data", dv, hostNowMs()), this.nextAt - now);
    }
  }

  async disconnect() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.emit("disconnect");
  }

  async command(cmd: number) {
    const e = this.engine;
    if (!e) throw new Error("not connected");
    this.deliver(e.command(cmd));
    this.emit("status", e.status());
  }

  async readStatus() {
    return this.engine?.status() ?? null;
  }

  async readRssi() {
    return Math.round(-58 + (Math.random() - 0.5) * 8);
  }

  async readBattery() {
    return this.engine?.battery ?? null;
  }
}
