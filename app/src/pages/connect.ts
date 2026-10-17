// Connect / device page: connection & device info, session + on-device recording controls, sync events, sensor health.

import { app } from "../app";
import { bleSupport } from "../ble/capacitorBle";
import { CMD, packetsPerNotification } from "../ble/profile";
import { fitClock } from "../core/analysis";
import { DEG } from "../core/quat";
import { bytes, date, esc, kv, na, v } from "../ui/fmt";

export function renderConnect(root: HTMLElement) {
  const ble = bleSupport();
  root.innerHTML = `
  <h1>Device</h1>
  <section class="row">
    <button id="ble" class="primary" ${ble.ok ? "" : "disabled"}>Connect sensor (Bluetooth)</button>
    <button id="mock">Connect mock device</button>
    <button id="disc">Disconnect</button>
  </section>
  ${ble.ok ? "" : `<p class="limit">${esc(ble.reason!)}</p>`}
  <p id="mockoff" class="note"></p>

  <h2>Session</h2>
  <div class="row">
    <button id="start" class="primary">Start session (stream 0x01)</button>
    <button id="stop">Stop &amp; save session</button>
    <button id="discard">Stop &amp; discard</button>
  </div>

  <h2>Sync events (video replay needs one at the start AND the end)</h2>
  <ol class="note">
    <li>Hold the racquet still for ~2 s, then <b>clap</b>: tap the racquet sharply where the cameras see it (or press the button below).</li>
    <li>Play.</li>
    <li>At the end: hold still ~2 s again and clap again (or press the button), then stop the session.</li>
  </ol>
  <p class="note">The pod also detects the tap itself (gyro quiet 1 s, then a &gt; 5 g spike) and reports it as a marker.</p>
  <div class="row"><button id="mark">Mark sync event now (control 0x06)</button></div>
  <div id="sync"></div>

  <h2>On-device recording (record now, upload later)</h2>
  <p class="note">Background Bluetooth is restricted on phones: the pod records into its flash (about 12.5 min) and uploads later.
  It starts recording by itself at power-on unless it still holds a session that was not uploaded. A complete upload
  is confirmed to the pod (0x07), which frees it for the next recording.</p>
  <div class="row">
    <button id="recstart">Start recording (0x03)</button>
    <button id="recstop">Stop recording (0x04)</button>
    <button id="upload">Upload stored session (0x05)</button>
    <button id="upfinish">Finish upload now</button>
  </div>

  <h2>Power (the pod has no switch)</h2>
  <p class="note">Sleep stops recording and parks the pod (~70 h on a 200 mAh cell). To wake it, connect again: while asleep it
  advertises 2 s every 30 s, so keep the picker open up to ~30 s. The app sends wake (0x09); the pod restarts (~7 s), then connect once more.</p>
  <div class="row"><button id="sleep">Put pod to sleep (0x08)</button></div>

  <h2>Import</h2>
  <div class="row">
    <label>imu.bin <input type="file" id="file" accept=".bin,application/octet-stream"></label>
    <button id="sample">Load sample session</button>
  </div>

  <div class="grid">
    <div><h2>Connection &amp; device</h2><div id="dev"></div></div>
    <div><h2>Sensor health / calibration (live)</h2><div id="health"></div>
      <div class="row"><button id="flat">Mounting check: hold racquet flat, face up, then press</button></div>
      <div id="flatres"></div></div>
  </div>
  <p id="notice" class="note"></p>
  <p id="msg" class="error"></p>`;

  const $ = <T extends HTMLElement = HTMLButtonElement>(id: string) => root.querySelector<T>("#" + id)!;
  const msg = (e: unknown) => {
    $("msg").textContent = e ? String(e instanceof Error ? e.message : e) : "";
  };
  const act = (id: string, f: () => Promise<unknown>) =>
    $(id).addEventListener("click", async () => {
      msg(null);
      try {
        await f();
      } catch (e) {
        msg(e);
        app.error(e);
      }
      refresh();
    });

  act("ble", () => app.connectBle());
  act("mock", () => app.connectMock());
  act("disc", () => app.disconnect());
  act("start", () => app.startSession());
  act("stop", async () => {
    const id = await app.stopSession(true);
    if (id) location.hash = `#/session/${id}`;
  });
  act("discard", () => app.stopSession(false));
  act("mark", () => app.command(CMD.SYNC_MARK));
  act("recstart", () => app.startRecording());
  act("recstop", () => app.command(CMD.STOP_RECORDING));
  act("upload", () => app.startUpload());
  act("sleep", () => app.sleep());
  act("upfinish", async () => {
    const id = await app.finishUpload("finished early, not marked uploaded");
    if (id) location.hash = `#/session/${id}`;
  });
  act("sample", async () => {
    location.hash = `#/session/${await app.loadSample()}`;
  });
  act("flat", async () => {
    if (!app.live) throw new Error("start a session first (the check reads the live accelerometer)");
    app.live.startFlatCheck();
  });
  $<HTMLInputElement>("file").addEventListener("change", async (ev) => {
    const f = (ev.target as HTMLInputElement).files?.[0];
    if (!f) return;
    try {
      const id = await app.importImuBin(new Uint8Array(await f.arrayBuffer()), f.name);
      location.hash = `#/session/${id}`;
    } catch (e) {
      msg(`Import failed: ${e instanceof Error ? e.message : e}`);
    }
  });

  function refresh() {
    const connected = app.conn === "connected";
    $("mock").hidden = !app.settings.mockEnabled;
    $("mockoff").textContent = app.settings.mockEnabled ? "" : "Mock device is off (Settings).";
    $("ble").disabled = !ble.ok || connected;
    $("mock").disabled = connected;
    $("disc").disabled = !connected;
    $("start").disabled = !connected || !!app.live || !!app.upload;
    $("stop").disabled = !app.live;
    $("discard").disabled = !app.live;
    const st = app.status;
    const awake = connected && !st?.sleeping;
    $("start").disabled ||= !awake;
    $("mark").disabled = !awake || !!app.upload;
    for (const id of ["recstart", "recstop", "upload", "sleep"]) $(id).disabled = !awake || !!app.upload;
    $("recstart").disabled ||= !!st?.recording || !!st?.erasing;
    $("recstop").disabled ||= !st?.recording && !st?.erasing;
    $("upload").disabled ||= !!st?.erasing;
    $("sleep").disabled ||= !!st?.erasing;
    $("notice").textContent = app.notice ?? "";
    $("upfinish").disabled = !app.upload;
    $("dev").innerHTML = deviceTable();
    $("health").innerHTML = healthTable();
    $("sync").innerHTML = syncList();
    const f = app.live?.flatCheck;
    $("flatres").innerHTML = !f
      ? ""
      : !f.result
        ? "<p>measuring… keep it still</p>"
        : kv([
            ["Handle tilt (pod +Y vs horizontal)", v(f.result.handleDeg, "°", 1), "≈ 0° when the handle is level"],
            ["Face tilt (pod +Z vs vertical)", v(90 - f.result.faceDeg, "°", 1), "≈ 0° when the face points straight up"],
            ["Held still", f.result.still ? `<span class="ok">yes</span>` : `<span class="bad">no — repeat</span>`],
          ]);
  }

  const t = setInterval(refresh, 250);
  const off = app.onChange(refresh);
  refresh();
  return () => {
    clearInterval(t);
    off();
  };
}

function deviceTable(): string {
  const d = app.device;
  const st = app.status;
  const live = app.live ?? app.upload;
  const s = live?.stats;
  const ia = app.interArrival;
  let jitter: string = na("needs a streaming session");
  if (ia.length > 5) {
    const m = ia.reduce((a, b) => a + b, 0) / ia.length;
    const sd = Math.sqrt(ia.reduce((a, b) => a + (b - m) ** 2, 0) / ia.length);
    jitter = `${sd.toFixed(1)} ms std of notification intervals (mean ${m.toFixed(1)} ms)`;
  }
  let measuredRate: string = na("needs a streaming session");
  if (live && live.samples.length > 50) {
    const first = live.samples[0].t;
    const last = live.samples[live.samples.length - 1].t;
    if (last > first) measuredRate = v((s!.unique + s!.lost - 1) / (last - first), "Hz", 1);
  }
  const lostPct = s && s.unique + s.lost ? (100 * s.lost) / (s.unique + s.lost) : null;
  const mtu = d?.mtu ?? null;
  const sinceLast = app.live && app.live.lastPacketHostMs ? performance.timeOrigin + performance.now() - app.live.lastPacketHostMs : null;
  const upTotal = app.uploadExpected;
  const upGot = app.upload?.stats.arrivals ?? 0;
  return kv([
    ["Device", d ? `${esc(d.name)} <small class="note">${esc(d.id)} (${d.kind})</small>` : na("not connected")],
    ["Connection state", app.conn],
    ["RSSI", v(app.rssi, "dBm", 0, d ? "Web Bluetooth can't read RSSI of a connected device; native apps can" : "not connected")],
    [
      "MTU",
      mtu ? `${mtu} B → ${packetsPerNotification(mtu)} packets/notification` : na(d ? "not exposed by Web Bluetooth (requested 247)" : "not connected"),
    ],
    ["Firmware version", st ? `${st.fwMajor}.${st.fwMinor}` : na(d ? "not reported" : "not connected")],
    ["Pod state", st ? podState() : na("not connected")],
    [
      "Battery",
      app.battery !== null && app.battery !== 255
        ? v(app.battery, "%", 0)
        : st?.batteryPct != null
          ? `${st.batteryPct} % <small class="note">(status)</small>`
          : na(d ? "unknown (the firmware reports 255 until the battery divider is fitted)" : "not connected"),
    ],
    [
      "Sensor ranges",
      st?.accelG ? `±${st.accelG} g, ±${st.gyroDps} dps` : na(d ? "not reported" : "not connected"),
    ],
    ["Sample rate (configured)", st?.rateHz ? `${st.rateHz} Hz` : na(d ? "not reported" : "not connected")],
    ["IMU", st ? (st.imuOk ? `<span class="ok">ok</span>${st.imuIs6500 ? " (MPU-6500)" : ""}` : `<span class="bad">not responding</span>`) : na("not connected")],
    ["IMU FIFO overflows", st ? `${st.fifoOverflows}${st.fifoOverflows ? ` <small class="note">(real gaps in the data)</small>` : ""}` : na("not connected")],
    ["Sample rate (measured)", measuredRate],
    ["Packets received", s ? `${s.arrivals} (${s.unique} unique, ${s.notifications} notifications)` : na("no session")],
    ["Lost", s && lostPct !== null ? `${s.lost} (${lostPct.toFixed(2)} %)` : na("no packets yet")],
    ["Duplicated", s ? String(s.duplicate) : na("no session")],
    ["Reordered", s ? `${s.reordered} <small class="note">(${s.tooLate} too late for live processing; kept in the saved file)</small>` : na("no session")],
    [
      "Current gap",
      sinceLast !== null ? `${sinceLast.toFixed(0)} ms since last notification; last hole ${s!.lastGapPackets} packets (${s!.gaps} holes)` : na("not streaming"),
    ],
    ["Link latency (arrival jitter)", jitter],
    ["Stored on device", st?.sleeping ? na("sleeping: the stored session is kept, wake the pod to see it") : st ? `${st.bufferedPackets} packets (${bytes(st.bufferedPackets * 18)}, ${(st.bufferedPackets / (st.rateHz || 500)).toFixed(0)} s)` : na("not connected")],
    [
      "Upload progress",
      app.upload
        ? `${upGot} / ${upTotal} packets${upTotal ? ` (${Math.min(100, (100 * upGot) / upTotal).toFixed(0)} %)` : ""}`
        : na("no upload"),
    ],
    ["Status updated", app.statusAtMs ? date(app.statusAtMs) : na("never")],
  ]);
}

function podState(): string {
  const st = app.status!;
  if (st.sleeping) return `<span class="pill">sleeping</span> <small class="note">only wake (0x09) is accepted</small>`;
  const pills = [
    st.streaming && "streaming",
    st.recording && "recording",
    st.erasing && "erasing flash (recording starts after it)",
    st.uploading && "uploading",
    st.pendingUpload && "session not uploaded yet",
    st.flashFull && "flash full (recording stopped)",
  ].filter(Boolean) as string[];
  return pills.length ? pills.map((p) => `<span class="pill on">${p}</span>`).join(" ") : "idle";
}

function healthTable(): string {
  const L = app.live;
  if (!L) return `<p class="note">Start a session to see live calibration. Saved sessions show the offline values on their Session page.</p>`;
  const b = L.bias;
  const last = L.samples[L.samples.length - 1];
  const clock = fitClock(L.arrivals.slice(-20000));
  return kv([
    [
      "Gyro bias x/y/z",
      b ? b.map((x) => (x / DEG).toFixed(3)).join(" / ") + " dps" : na("hold the racquet still ≥ 1 s to calibrate"),
    ],
    [
      "Bias estimated",
      L.biasWindowT && L.biasAtHostMs
        ? `${new Date(L.biasAtHostMs).toLocaleTimeString()} (device ${L.biasWindowT[0].toFixed(2)}–${L.biasWindowT[1].toFixed(2)} s)`
        : na("not yet"),
    ],
    ["Stationary", L.stationary ? `<span class="ok">still</span>` : `<span class="warn">moving</span>`],
    ["|a| at rest", v(L.restAccG, "g", 3, "not calibrated yet"), "should be ≈ 1 g"],
    ["|a| now", last ? v(Math.hypot(...last.acc) / 9.80665, "g", 3) : na("no data")],
    ["Saturated samples (gyro / accel)", `${L.stats.saturatedGyro} / ${L.stats.saturatedAcc}`],
    ["Noise at rest: gyro std x/y/z", L.restGyroStdDps ? L.restGyroStdDps.map((x) => x.toFixed(3)).join(" / ") + " dps" : na("not calibrated yet")],
    ["Noise at rest: accel std x/y/z", L.restAccStdG ? L.restAccStdG.map((x) => x.toFixed(4)).join(" / ") + " g" : na("not calibrated yet")],
    [
      "Clock: device vs this device",
      clock ? `device t = 0 at ${fmtClock(clock.offsetS)}; drift ${clock.driftPpm.toFixed(0)} ppm` : na("needs ~10 notifications"),
      clock ? "drift needs minutes of data to be meaningful" : undefined,
    ],
  ]);
}

/** host epoch seconds -> local wall-clock time with ms */
export function fmtClock(s: number) {
  const d = new Date(s * 1000);
  return `${d.toLocaleTimeString()}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

function syncList(): string {
  const L = app.live;
  if (!L) return `<p class="note">Detected events appear here during a session (and on the Session page afterwards).</p>`;
  const t0 = L.samples[0]?.t ?? 0;
  const ev = [
    ...L.claps.map((c) => ({ kind: "clap", t: c.t, g: c.peakG })),
    ...L.markers.map((m) => ({ kind: m.source === "tap" ? "tap (pod)" : "button (0x06)", t: m.t, g: null as number | null })),
  ].sort((a, b) => a.t - b.t);
  if (!ev.length) return `<p class="warn">No sync event yet — clap or press the button.</p>`;
  return `<table><tr><th>#</th><th>type</th><th>time</th><th>strength</th></tr>${ev
    .map((e, i) => `<tr><td>${i + 1}</td><td>${e.kind}</td><td>${(e.t - t0).toFixed(3)} s</td><td>${e.g ? e.g.toFixed(1) + " g" : "—"}</td></tr>`)
    .join("")}</table>`;
}
