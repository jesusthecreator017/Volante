// App controller: device connection, live/upload sessions, saved sessions, analysis cache.

import type { ImuDevice, ConnState } from "./ble/device";
import { hostNowMs } from "./ble/device";
import { CMD, ERR, EVENT, errorText, type DeviceStatus } from "./ble/profile";
import { MockDevice, DEFAULT_MOCK, type MockOptions } from "./ble/mock";
import { CapacitorBleDevice } from "./ble/capacitorBle";
import { LiveSession } from "./core/live";
import { analyzeSession, type SessionAnalysis } from "./core/analysis";
import { parse, PACKET_LEN, type Marker } from "./core/packet";
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
  /** packets the pod holds when the upload started (status.bufferedPackets) */
  uploadExpected = 0;
  /** info line for the Device page (wake, upload result, ...) */
  notice: string | null = null;
  /** asked before overwriting a session the pod has not uploaded (Device page sets a dialog) */
  confirmOverwrite: () => Promise<boolean> = async () => confirm("The pod holds a session that was not uploaded. Erase it and record a new one?");
  private eventWaiters: ((s: DeviceStatus) => boolean)[] = [];
  private uploadWatch: ReturnType<typeof setInterval> | null = null;
  private mock: MockDevice | null = null;
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
    // One mock pod per page load: like the real one it keeps its stored session and sleep state across connections
    this.mock ??= new MockDevice(async () => new Uint8Array(await (await fetch(`${BASE}sample/imu.bin`)).arrayBuffer()), this.mockOpts);
    await this.attach(this.mock);
  }

  /** Must run inside a click handler on the web (Web Bluetooth user-gesture rule). */
  async connectBle() {
    const dev = await CapacitorBleDevice.request();
    await this.attach(dev);
  }

  private async attach(dev: ImuDevice) {
    this.conn = "connecting";
    this.changed();
    dev.offAll();
    dev.on("data", (dv, host) => this.onData(dv, host));
    dev.on("status", (s) => {
      this.status = s;
      this.statusAtMs = hostNowMs();
      if (s.event !== EVENT.NONE) this.onEvent(s);
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
    this.notice = null;
    if (this.status?.sleeping) {
      // Caught a sleeping pod in its 2 s advert window: it has 10 s for 0x09, then resets into the normal boot
      this.notice = "The pod was sleeping: sent wake (0x09). It restarts (~7 s); then connect again.";
      await dev.command(CMD.WAKE).catch((e) => this.error(e));
    }
    this.changed();
  }

  async disconnect() {
    if (this.live) await this.stopSession();
    await this.device?.disconnect().catch((e) => this.error(e));
    this.onDisconnect();
  }

  private onDisconnect() {
    if (this.upload) {
      this.stopUploadWatch();
      this.error("link lost during the upload: the pod keeps the session, upload again (or Finish upload now to keep what arrived)");
    }
    this.polls.forEach(clearInterval);
    this.polls = [];
    this.conn = "disconnected";
    this.device = null;
    this.changed();
  }

  async command(cmd: number) {
    if (!this.device) throw new Error("not connected");
    if (this.status?.sleeping && cmd !== CMD.WAKE) throw new Error(errorText(ERR.SLEEPING));
    await this.device.command(cmd);
    this.status = (await this.device.readStatus().catch(() => null)) ?? this.status;
    this.changed();
  }

  /** Resolves with the first status event matching `pred` within `ms`, else null. */
  private waitEvent(pred: (s: DeviceStatus) => boolean, ms: number): Promise<DeviceStatus | null> {
    return new Promise((res) => {
      const w = (s: DeviceStatus) => {
        if (!pred(s)) return false;
        clearTimeout(t);
        res(s);
        return true;
      };
      const t = setTimeout(() => {
        this.eventWaiters = this.eventWaiters.filter((x) => x !== w);
        res(null);
      }, ms);
      this.eventWaiters.push(w);
    });
  }

  /** Status events (notifications only; a read always says none). */
  private onEvent(s: DeviceStatus) {
    const before = this.eventWaiters.length;
    this.eventWaiters = this.eventWaiters.filter((w) => !w(s));
    const awaited = this.eventWaiters.length < before;
    switch (s.event) {
      case EVENT.MARKER_TAP:
      case EVENT.MARKER_CMD: {
        const target = this.upload ?? this.live;
        if (target) target.addDeviceMarker(s.eventValue, s.event === EVENT.MARKER_TAP ? "tap" : "command");
        break;
      }
      case EVENT.UPLOAD_DONE:
        if (this.upload) void this.completeUpload(s.eventValue);
        break;
      case EVENT.ERROR:
        if (!awaited) this.error(`device: ${errorText(s.eventValue)}`);
        break;
    }
  }

  /** 0x03. A stored session that was not uploaded needs a second 0x03 within 5 s (after asking the user). */
  async startRecording() {
    const needConfirm = this.waitEvent((s) => s.event === EVENT.ERROR && s.eventValue === ERR.NEED_CONFIRM, 1500);
    await this.command(CMD.START_RECORDING);
    if (!(await needConfirm)) return;
    if (!(await this.confirmOverwrite())) return;
    await this.command(CMD.START_RECORDING);
  }

  /** 0x08: stops recording, parks the pod; the link closes. Reconnect (scan up to ~30 s) to wake it. */
  async sleep() {
    if (this.live) await this.stopSession();
    await this.command(CMD.SLEEP);
    this.notice = "Pod going to sleep. To wake it, connect again: it advertises 2 s every 30 s, keep the picker open.";
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
    this.live = new LiveSession(this.status?.rateHz ?? 500, analysisSettings(this.settings), "stream");
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
    this.upload = new LiveSession(this.status?.rateHz ?? 500, analysisSettings(this.settings), "upload");
    this.uploadExpected = this.status?.bufferedPackets ?? 0;
    this.uploadLastDataMs = hostNowMs();
    this.notice = null;
    try {
      await this.command(CMD.UPLOAD);
    } catch (e) {
      this.upload = null;
      throw e;
    }
    // The firmware ends with upload_done{count}; this only catches a stalled link (oso-rec gives up after 20 s too)
    this.uploadWatch = setInterval(() => {
      if (this.upload && hostNowMs() - this.uploadLastDataMs > 20000) {
        this.stopUploadWatch();
        this.error("upload stalled for 20 s: Finish upload now keeps what arrived (the pod still holds the session)");
      }
      this.changed();
    }, 1000);
  }

  private stopUploadWatch() {
    if (this.uploadWatch) clearInterval(this.uploadWatch);
    this.uploadWatch = null;
  }

  /** upload_done: save, and only a complete upload is confirmed to the pod with 0x07 (mark uploaded). */
  private async completeUpload(count: number) {
    const up = this.upload;
    if (!up) return;
    // Packets arrive before the event, but give the last notifications a moment to land
    await new Promise((r) => setTimeout(r, 100));
    const got = up.stats.arrivals;
    const id = await this.finishUpload(got === count ? null : `incomplete: ${got} of ${count} packets`);
    if (got !== count) {
      this.error(`upload: received ${got} packets, the pod says ${count}. Not marking it uploaded; upload again.`);
      return;
    }
    try {
      if (count > 0) await this.command(CMD.MARK_UPLOADED);
      this.notice = `Uploaded ${count} packets${count ? "; the pod marked the session uploaded (its next power-on records again)" : " (empty session)"}.`;
    } catch (e) {
      this.error(e);
    }
    this.changed();
    if (id) location.hash = `#/session/${id}`;
  }

  /** Saves what arrived. Called on upload_done, or by the user ("Finish upload now"), which never marks uploaded. */
  async finishUpload(incomplete: string | null = "finished early"): Promise<string | null> {
    const up = this.upload;
    if (!up) return null;
    this.stopUploadWatch();
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
      name: `Uploaded ${new Date().toLocaleString()}${incomplete ? ` (${incomplete})` : ""}`,
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
    markers: Marker[];
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
