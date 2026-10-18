// Mock BLE device: replays an imu.bin (sample-data by default) at real-time rate in notification-sized chunks,
// implementing the full control/status contract of docs/ble-profile.md.

import { Emitter, hostNowMs, type ImuDevice } from "./device";
import {
  CMD,
  ERR,
  EVENT,
  REQUESTED_MTU,
  STATE,
  STATE_EXT,
  encodeStatus,
  packetsPerNotification,
  parseStatus,
  type DeviceStatus,
} from "./profile";
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
  /** flash erase before a recording starts, ms of device time (the real pod takes ~5-20 s) */
  eraseMs: number;
  /** firmware boot rule: power-on records by itself unless an un-uploaded session is stored */
  autoRecord: boolean;
}

export const DEFAULT_MOCK: MockOptions = {
  lossProb: 0,
  burstProb: 0.2,
  jitterMs: 8,
  loop: true,
  uploadSpeed: 8,
  mtu: REQUESTED_MTU,
  eraseMs: 1500,
  autoRecord: true,
};

/** Firmware limits (firmware/src/store.rs) */
const MAX_MARKERS = 32;
const CONFIRM_MS = 5000;

/**
 * Timer-free core of the mock (unit-testable), following the firmware's control/status contract
 * (docs/ble-profile.md): binary status, markers and upload_done as status events, record confirm, mark uploaded,
 * sleep / wake. The "device" produces packets in file arrival order on its own clock; advance(ms) returns the
 * notifications due, and events() the status events raised since the last call.
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
  /** device clock since boot, ms (timeouts) */
  private nowMs = 0;
  /** per packet: device time relative to the file's first packet (unwrapped), µs */
  private relT: number[];
  private burst = 0;
  /** device micros of the most recent produced packet */
  private lastT32 = 0;
  streaming = false;
  recording = false;
  /** device time when the running flash erase ends (then recording starts), null when not erasing */
  private eraseUntil: number | null = null;
  readonly recorded: RawPacket[] = [];
  private storedMarkers: { t32: number; source: "tap" | "command" }[] = [];
  /** a session with data exists in "flash" */
  private started = false;
  private uploaded = false;
  /** last upload delivered everything: 0x07 is allowed */
  private uploadOk = false;
  private confirmUntil = -1;
  private uploadQueue: RawPacket[] = [];
  private uploadTotal = 0;
  sleeping = false;
  private pendingEvents: [number, number][] = [];
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
    this.boot();
  }

  /** Normal boot: record unless the stored session has data that was not uploaded. */
  private boot() {
    this.sleeping = false;
    this.streaming = false;
    if (this.opts.autoRecord && !this.needsConfirm) this.eraseAndStart();
  }

  private get needsConfirm() {
    return this.started && !this.uploaded && this.recorded.length > 0;
  }

  private eraseAndStart() {
    this.recorded.length = 0;
    this.storedMarkers = [];
    this.started = true;
    this.uploaded = false;
    this.uploadOk = false;
    if (this.opts.eraseMs > 0) this.eraseUntil = this.nowMs + this.opts.eraseMs;
    else this.recording = true;
  }

  get perNotification() {
    return packetsPerNotification(this.opts.mtu);
  }

  get uploading() {
    return this.uploadQueue.length > 0;
  }

  /** The 24-byte status characteristic value, as the firmware encodes it. */
  statusBytes(event: number = EVENT.NONE, eventValue = 0): DataView {
    if (this.sleeping) {
      // Sleeping cycle: minimal boot, the session fields read 0
      return encodeStatus({ fwMajor: 0, fwMinor: 1, rateHz: this.rateHz, batteryPct: this.battery, stateBits: 0, accelG: 16,
        gyroDps: 2000, bufferedPackets: 0, fifoOverflows: 0, event, eventValue, stateExt: STATE_EXT.SLEEPING });
    }
    const bits =
      (this.streaming ? STATE.STREAMING : 0) |
      (this.recording ? STATE.RECORDING : 0) |
      (this.uploading ? STATE.UPLOADING : 0) |
      (!this.recording && this.needsConfirm ? STATE.PENDING_UPLOAD : 0) |
      STATE.IMU_OK |
      (this.eraseUntil !== null ? STATE.ERASING : 0);
    return encodeStatus({ fwMajor: 0, fwMinor: 1, rateHz: this.rateHz, batteryPct: this.battery, stateBits: bits, accelG: 16,
      gyroDps: 2000, bufferedPackets: this.recorded.length, fifoOverflows: 0, event, eventValue, stateExt: 0 });
  }

  status(): DeviceStatus {
    return parseStatus(this.statusBytes())!;
  }

  /** Status events raised since the last call, as notification values. */
  events(): DataView[] {
    return this.pendingEvents.splice(0).map(([e, v]) => this.statusBytes(e, v));
  }

  private event(e: number, v: number) {
    this.pendingEvents.push([e, v >>> 0]);
  }

  private error(code: number) {
    this.event(EVENT.ERROR, code);
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

  /** Advance the device clock by ms; returns imu_stream notifications to deliver. */
  advance(ms: number): DataView[] {
    this.nowMs += ms;
    if (this.sleeping) return [];
    if (this.eraseUntil !== null && this.nowMs >= this.eraseUntil) {
      this.eraseUntil = null;
      this.recording = true;
    }
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
      out.push(...this.chunk(this.uploadQueue.splice(0, k))); // flow-controlled: no loss on upload
      if (!this.uploadQueue.length) {
        // The stored markers follow the data, then upload_done with the packet count
        for (const m of this.storedMarkers) this.event(m.source === "tap" ? EVENT.MARKER_TAP : EVENT.MARKER_CMD, m.t32);
        this.event(EVENT.UPLOAD_DONE, this.uploadTotal);
        this.uploadOk = true;
      }
    }
    return out;
  }

  private stopRecording() {
    this.recording = false;
    this.eraseUntil = null;
  }

  command(cmd: number): DataView[] {
    if (this.sleeping) {
      if (cmd === CMD.WAKE) this.boot();
      else this.error(ERR.SLEEPING);
      return [];
    }
    switch (cmd) {
      case CMD.START_STREAM:
        this.streaming = true;
        break;
      case CMD.STOP_STREAM:
        this.streaming = false;
        break;
      case CMD.START_RECORDING:
        if (this.recording || this.uploading || this.eraseUntil !== null) break; // already recording / busy
        if (this.needsConfirm && this.nowMs >= this.confirmUntil) {
          this.confirmUntil = this.nowMs + CONFIRM_MS;
          this.error(ERR.NEED_CONFIRM);
          break;
        }
        this.confirmUntil = -1;
        this.eraseAndStart();
        break;
      case CMD.STOP_RECORDING:
        this.stopRecording();
        break;
      case CMD.UPLOAD:
        if (this.eraseUntil !== null || this.uploading) {
          this.error(ERR.BUSY);
          break;
        }
        this.stopRecording();
        this.streaming = false; // both share imu_stream
        if (!this.started) {
          this.error(ERR.NO_SESSION);
          break;
        }
        this.uploadOk = false;
        this.pendingEvents = []; // the firmware clears queued events before an upload
        this.uploadQueue = [...this.recorded];
        this.uploadTotal = this.recorded.length;
        if (!this.uploadTotal) {
          this.event(EVENT.UPLOAD_DONE, 0);
          this.uploadOk = true;
        }
        break;
      case CMD.SYNC_MARK:
        if (!this.uploading) this.event(EVENT.MARKER_CMD, this.lastT32);
        if (this.recording && this.storedMarkers.length < MAX_MARKERS) this.storedMarkers.push({ t32: this.lastT32, source: "command" });
        break;
      case CMD.MARK_UPLOADED:
        if (this.recording || !this.started || this.uploaded || !this.uploadOk) this.error(ERR.NO_SESSION);
        else this.uploaded = true;
        this.uploadOk = false;
        break;
      case CMD.SLEEP:
        if (this.eraseUntil !== null || this.uploading) {
          this.error(ERR.BUSY);
          break;
        }
        this.stopRecording();
        this.streaming = false;
        this.sleeping = true;
        break;
      case CMD.WAKE:
        break; // only honored while sleeping
      default:
        this.error(ERR.BAD_OPCODE);
    }
    return [];
  }
}

export class MockDevice extends Emitter implements ImuDevice {
  readonly kind = "mock" as const;
  readonly id = "mock-0";
  readonly name = "Volante mock (sample-data/imu.bin)";
  readonly hasBatteryService = true;
  private timer: ReturnType<typeof setInterval> | null = null;
  private last = 0;
  private statusTick = 0;
  private nextAt = 0;
  /** the "pod": kept across connections like the real one keeps its flash, sleep state and clock */
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
    this.engine ??= new MockEngine(await this.loadFile(), this.opts);
    this.last = performance.now();
    // Nominal notification interval: 13 packets at 500 Hz = 26 ms
    this.timer = setInterval(() => this.tick(), 20);
  }

  private tick() {
    const e = this.engine!;
    const now = performance.now();
    const out = e.advance(now - this.last);
    this.last = now;
    this.deliver(out.map((dv) => ["data", dv] as const));
    this.flushEvents();
    // The firmware notifies status once per second when no event happens
    if (++this.statusTick % 50 === 0) this.deliver([["status", e.statusBytes()]]);
  }

  private flushEvents() {
    this.deliver(this.engine!.events().map((dv) => ["status", dv] as const));
  }

  /** Random latency, but notifications keep their order (status events after the data they follow). */
  private deliver(out: (readonly ["data" | "status", DataView])[]) {
    for (const [kind, dv] of out) {
      const now = performance.now();
      this.nextAt = Math.max(this.nextAt, now + Math.random() * this.opts.jitterMs);
      setTimeout(() => {
        if (!this.timer) return; // link closed
        if (kind === "data") this.emit("data", dv, hostNowMs());
        else this.emit("status", parseStatus(dv)!);
      }, this.nextAt - now);
    }
  }

  /** The link drops: host disconnect, the pod going to sleep, or the reset after a wake. */
  private drop() {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
    this.emit("disconnect");
  }

  async disconnect() {
    this.drop();
  }

  async command(cmd: number) {
    const e = this.engine;
    if (!e || !this.timer) throw new Error("not connected");
    const wasSleeping = e.sleeping;
    e.command(cmd);
    this.flushEvents();
    // Sleep: the pod parks ~300 ms after the write. Wake: software reset into the normal boot. Both close the link.
    if ((!wasSleeping && e.sleeping) || (wasSleeping && !e.sleeping)) setTimeout(() => this.drop(), 300);
  }

  async readStatus() {
    // A read always has event = none: events only travel as notifications
    return this.engine ? parseStatus(this.engine.statusBytes()) : null;
  }

  async readRssi() {
    return Math.round(-58 + (Math.random() - 0.5) * 8);
  }

  async readBattery() {
    return this.engine?.battery ?? null;
  }
}
