// App controller: device connection, live/upload sessions, saved sessions, analysis cache.

import type { ImuDevice, ConnState } from "./ble/device";
import { hostNowMs } from "./ble/device";
import { CMD, type DeviceStatus } from "./ble/profile";
import { MockDevice, DEFAULT_MOCK, type MockOptions } from "./ble/mock";
import { CapacitorBleDevice } from "./ble/capacitorBle";
import { LiveSession } from "./core/live";
import { analyzeSession, type SessionAnalysis } from "./core/analysis";
import { parse, PACKET_LEN } from "./core/packet";
import { loadSettings, saveSettings, analysisSettings, type Settings } from "./settings";
import { getSession, listSessions, newId, putSession, type StoredSession } from "./store";

export interface LogEntry {
  hostMs: number;
  bytes: number;
  packets: number;
  hex: string;
  first: string;
}

const BASE = import.meta.env.BASE_URL;

class App {
  settings: Settings = loadSettings();
  device: ImuDevice | null = null;
  conn: ConnState = "disconnected";
  status: DeviceStatus | null = null;
  statusAtMs: number | null = null;
  rssi: number | null = null;
  battery: number | null = null;
  live: LiveSession | null = null;
  upload: LiveSession | null = null;
  uploadLastDataMs = 0;
  /** notification inter-arrival times (ms), last 200 */
  interArrival: number[] = [];
  private lastArrivalMs = 0;
  log: LogEntry[] = [];
  errors: string[] = [];
  private cache = new Map<string, SessionAnalysis>();
  private polls: ReturnType<typeof setInterval>[] = [];
  mockOpts: MockOptions = { ...DEFAULT_MOCK };
  private listeners = new Set<() => void>();

  onChange(f: () => void) {
    this.listeners.add(f);
    return () => this.listeners.delete(f);
  }
  changed() {
    for (const f of this.listeners) f();
  }

  error(e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    this.errors.unshift(`${new Date().toLocaleTimeString()} ${msg}`);
    this.errors.length = Math.min(this.errors.length, 50);
    console.error(e);
    this.changed();
  }

  updateSettings(patch: Partial<Settings>) {
    this.settings = { ...this.settings, ...patch };
    saveSettings(this.settings);
    this.mockOpts.lossProb = this.settings.mockLossPct / 100;
    this.mockOpts.jitterMs = this.settings.mockJitterMs;
    this.cache.clear(); // thresholds etc. changed: re-analyse on demand
    this.changed();
  }

  // ---------------------------------------------------------------- connection

  async connectMock() {
    this.mockOpts.lossProb = this.settings.mockLossPct / 100;
    this.mockOpts.jitterMs = this.settings.mockJitterMs;
    const dev = new MockDevice(async () => new Uint8Array(await (await fetch(`${BASE}sample/imu.bin`)).arrayBuffer()), this.mockOpts);
    await this.attach(dev);
  }

  /** Must run inside a click handler on the web (Web Bluetooth user-gesture rule). */
  async connectBle() {
    const dev = await CapacitorBleDevice.request();
    await this.attach(dev);
  }

  private async attach(dev: ImuDevice) {
    this.conn = "connecting";
    this.changed();
    dev.on("data", (dv, host) => this.onData(dv, host));
    dev.on("status", (s) => {
      this.status = s;
      this.statusAtMs = hostNowMs();
      this.changed();
    });
    dev.on("disconnect", () => this.onDisconnect());
    try {
      await dev.connect();
    } catch (e) {
      this.conn = "disconnected";
      this.error(e);
      return;
    }
    this.device = dev;
    this.conn = "connected";
    try {
      this.status = await dev.readStatus();
      this.statusAtMs = hostNowMs();
    } catch (e) {
      this.error(e);
    }
    this.polls.push(
      setInterval(async () => {
        this.rssi = await dev.readRssi().catch(() => null);
        this.changed();
      }, 2000),
      setInterval(async () => {
        this.battery = await dev.readBattery().catch(() => null);
      }, 30000),
    );
    this.rssi = await dev.readRssi().catch(() => null);
    this.battery = await dev.readBattery().catch(() => null);
    this.changed();
  }

  async disconnect() {
    if (this.live) await this.stopSession();
    await this.device?.disconnect().catch((e) => this.error(e));
    this.onDisconnect();
  }

  private onDisconnect() {
    this.polls.forEach(clearInterval);
    this.polls = [];
    this.conn = "disconnected";
    this.device = null;
    this.changed();
  }

  async command(cmd: number) {
    if (!this.device) throw new Error("not connected");
    await this.device.command(cmd);
    this.status = (await this.device.readStatus().catch(() => null)) ?? this.status;
    this.changed();
  }

  private onData(dv: DataView, hostMs: number) {
    if (this.lastArrivalMs) {
      this.interArrival.push(hostMs - this.lastArrivalMs);
      if (this.interArrival.length > 200) this.interArrival.shift();
    }
    this.lastArrivalMs = hostMs;
    const u8 = new Uint8Array(dv.buffer, dv.byteOffset, dv.byteLength);
    const hex = Array.from(u8.subarray(0, PACKET_LEN), (b) => b.toString(16).padStart(2, "0")).join(" ");
    let first = "";
    if (dv.byteLength >= PACKET_LEN) {
      const vals = [0, 1, 2, 3, 4, 5].map((k) => dv.getInt16(6 + 2 * k, true));
      first = `seq=${dv.getUint16(0, true)} t_us=${dv.getUint32(2, true)} a=[${vals.slice(0, 3)}] g=[${vals.slice(3)}]`;
    }
    this.log.unshift({ hostMs, bytes: dv.byteLength, packets: dv.byteLength / PACKET_LEN, hex, first });
    if (this.log.length > 100) this.log.length = 100;
    if (this.upload) {
      this.upload.ingest(dv, hostMs);
      this.uploadLastDataMs = hostMs;
    } else this.live?.ingest(dv, hostMs);
  }

  // ---------------------------------------------------------------- sessions

  async startSession() {
    if (!this.device) throw new Error("connect a device first");
    this.live = new LiveSession(this.status?.rate_hz ?? 500, analysisSettings(this.settings), "stream");
    this.interArrival = [];
    this.lastArrivalMs = 0;
    await this.command(CMD.START_STREAM);
  }

  async stopSession(save = true): Promise<string | null> {
    const live = this.live;
    if (!live) return null;
    await this.device?.command(CMD.STOP_STREAM).catch((e) => this.error(e));
    // Let in-flight notifications land
    await new Promise((r) => setTimeout(r, 150));
    live.finish();
    this.live = null;
    this.changed();
    if (!save || live.rawCount === 0) return null;
    return this.save({
      source: this.device?.kind === "mock" ? "mock" : "ble",
      deviceName: this.device?.name ?? null,
      imuBin: live.toImuBin(),
      markers: live.markers,
      arrivals: live.arrivals,
    });
  }

  async startUpload() {
    if (!this.device) throw new Error("connect a device first");
    if (this.live) await this.stopSession();
    this.upload = new LiveSession(this.status?.rate_hz ?? 500, analysisSettings(this.settings), "upload");
    this.uploadLastDataMs = hostNowMs();
    await this.command(CMD.UPLOAD);
    // Done when the device reports everything sent and the stream has been quiet for 500 ms
    const poll = setInterval(async () => {
      const s = (await this.device?.readStatus().catch(() => null)) ?? this.status;
      if (s) this.status = s;
      this.changed();
      const done = s && s.upload_total_bytes && (s.upload_sent_bytes ?? 0) >= s.upload_total_bytes;
      if (done && hostNowMs() - this.uploadLastDataMs > 500) {
        clearInterval(poll);
        await this.finishUpload();
      }
    }, 250);
  }

  async finishUpload(): Promise<string | null> {
    const up = this.upload;
    if (!up) return null;
    up.finish();
    this.upload = null;
    this.changed();
    if (!up.rawCount) return null;
    return this.save({
      source: "device-upload",
      deviceName: this.device?.name ?? null,
      imuBin: up.toImuBin(),
      markers: up.markers,
      arrivals: [],
    });
  }

  async importImuBin(bytes: Uint8Array, name: string, source: StoredSession["source"] = "import", replay: Uint8Array | null = null) {
    const cap = parse(bytes); // validates, throws on malformed files
    return this.save({ source, deviceName: null, imuBin: bytes, markers: cap.markers, arrivals: [], name, replay });
  }

  async loadSample() {
    const [imu, rep] = await Promise.all([
      fetch(`${BASE}sample/imu.bin`).then((r) => r.arrayBuffer()),
      fetch(`${BASE}sample/replay.bin`).then((r) => r.arrayBuffer()),
    ]);
    return this.importImuBin(new Uint8Array(imu), "Sample session (synthetic, sample-data/)", "sample", new Uint8Array(rep));
  }

  private async save(p: {
    source: StoredSession["source"];
    deviceName: string | null;
    imuBin: Uint8Array;
    markers: { t: number }[];
    arrivals: [number, number][];
    name?: string;
    replay?: Uint8Array | null;
  }): Promise<string> {
    const id = newId();
    const s: StoredSession = {
      id,
      createdAt: Date.now(),
      name: p.name ?? `${p.source === "device-upload" ? "Uploaded" : "Session"} ${new Date().toLocaleString()}`,
      source: p.source,
      deviceName: p.deviceName,
      imuBin: p.imuBin,
      markers: p.markers,
      arrivals: p.arrivals,
      labels: {},
      replay: p.replay ?? null,
      headline: null,
    };
    const a = this.analyze(s);
    s.headline = {
      durationS: a.summary.durationS,
      swings: a.summary.swingCount,
      hits: a.summary.hitCount,
      maxSpeed: a.summary.speed?.max ?? null,
      peakDps: a.summary.peakDps,
    };
    await putSession(s);
    this.changed();
    return id;
  }

  analyze(s: StoredSession): SessionAnalysis {
    const hit = this.cache.get(s.id);
    if (hit) return hit;
    const cap = parse(s.imuBin);
    const a = analyzeSession({ ...cap, markers: [...cap.markers, ...s.markers] }, analysisSettings(this.settings), s.arrivals);
    this.cache.set(s.id, a);
    return a;
  }

  async session(id: string) {
    const s = await getSession(id);
    if (!s) throw new Error(`session ${id} not found`);
    return { s, a: this.analyze(s) };
  }

  async latestSessionId(): Promise<string | null> {
    return (await listSessions())[0]?.id ?? null;
  }

  // ---------------------------------------------------------------- Pi server (optional)

  /** true when the page itself is served by the oso Pi server (answers /api/health) */
  sameOriginServer = false;

  async probeServer() {
    try {
      const r = await fetch(`${BASE}api/health`, { cache: "no-store" });
      this.sameOriginServer = r.ok && (await r.json()).app === "oso";
    } catch {
      this.sameOriginServer = false;
    }
    this.changed();
  }

  serverBase(): string | null {
    if (this.settings.serverUrl) return this.settings.serverUrl.replace(/\/$/, "");
    return this.sameOriginServer ? BASE.replace(/\/$/, "") : null;
  }

  async serverUpload(s: StoredSession) {
    const base = this.serverBase();
    if (base === null) throw new Error("no server configured (Settings → Pi server URL)");
    const a = this.analyze(s);
    const meta = { id: s.id, name: s.name, createdAt: s.createdAt, source: s.source, headline: s.headline, markers: s.markers };
    let r = await fetch(`${base}/api/sessions/${s.id}/imu.bin`, { method: "PUT", body: s.imuBin as BlobPart });
    if (!r.ok) throw new Error(`upload imu.bin: HTTP ${r.status}`);
    r = await fetch(`${base}/api/sessions/${s.id}/meta.json`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...meta, summary: a.summary }),
    });
    if (!r.ok) throw new Error(`upload meta: HTTP ${r.status}`);
    s.uploadedAt = Date.now();
    await putSession(s);
    this.changed();
  }

  /** Fetches replay.bin for a session from the server (written there by the video pipeline). */
  async serverFetchReplay(s: StoredSession): Promise<boolean> {
    const base = this.serverBase();
    if (base === null) throw new Error("no server configured (Settings → Pi server URL)");
    const r = await fetch(`${base}/api/sessions/${s.id}/replay.bin`);
    if (r.status === 404) return false;
    if (!r.ok) throw new Error(`replay: HTTP ${r.status}`);
    s.replay = new Uint8Array(await r.arrayBuffer());
    await putSession(s);
    this.changed();
    return true;
  }
}

export const app = new App();
