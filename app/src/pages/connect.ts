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
  <div class="row"><button id="mark">Mark sync event now (control 0x06)</button></div>
  <div id="sync"></div>

  <h2>On-device recording (record now, upload later)</h2>
  <p class="note">Background Bluetooth is restricted on phones: the device can record into its own buffer and upload later.</p>
  <div class="row">
    <button id="recstart">Start recording (0x03)</button>
    <button id="recstop">Stop recording (0x04)</button>
    <button id="upload">Upload buffered session (0x05)</button>
    <button id="upfinish">Finish upload now</button>
  </div>

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
  act("recstart", () => app.command(CMD.START_RECORDING));
  act("recstop", () => app.command(CMD.STOP_RECORDING));
  act("upload", () => app.startUpload());
  act("upfinish", async () => {
    const id = await app.finishUpload();
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
    $("mark").disabled = !connected;
    for (const id of ["recstart", "recstop", "upload"]) $(id).disabled = !connected || !!app.upload;
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
  const upTotal = st?.upload_total_bytes ?? 0;
  return kv([
    ["Device", d ? `${esc(d.name)} <small class="note">${esc(d.id)} (${d.kind})</small>` : na("not connected")],
    ["Connection state", app.conn],
    ["RSSI", v(app.rssi, "dBm", 0, d ? "Web Bluetooth can't read RSSI of a connected device; native apps can" : "not connected")],
    [
      "MTU",
      mtu ? `${mtu} B → ${packetsPerNotification(mtu)} packets/notification` : na(d ? "not exposed by Web Bluetooth (requested 247)" : "not connected"),
    ],
    ["Firmware version", st?.fw ? esc(st.fw) : na(d ? "not reported" : "not connected")],
    [
      "Battery",
      app.battery !== null ? v(app.battery, "%", 0) : st?.battery_pct !== undefined ? `${st.battery_pct} % <small class="note">(status)</small>` : na(d ? "no Battery Service" : "not connected"),
    ],
    [
      "Sensor ranges",
      st?.acc_range_g ? `±${st.acc_range_g} g, ±${st.gyro_range_dps} dps` : na(d ? "not reported" : "not connected"),
    ],
    ["Sample rate (configured)", st?.rate_hz ? `${st.rate_hz} Hz` : na(d ? "not reported" : "not connected")],
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
    ["Recording on device", st ? (st.recording ? `<span class="pill on">recording</span>` : "no") : na("not connected")],
    ["Buffered on device", st ? bytes(st.buffered_bytes) : na("not connected")],
    [
      "Upload progress",
      app.upload || upTotal
        ? `${bytes(st?.upload_sent_bytes ?? 0)} / ${bytes(upTotal)}${upTotal ? ` (${(100 * (st!.upload_sent_bytes ?? 0) / upTotal).toFixed(0)} %)` : ""}${app.upload ? ` · ${app.upload.stats.arrivals} packets received` : ""}`
        : na("no upload"),
    ],
    ["Status updated", app.statusAtMs ? date(app.statusAtMs) : na("never")],
  ]);
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
    ...L.markers.map((m) => ({ kind: "button (0x06)", t: m.t, g: null as number | null })),
  ].sort((a, b) => a.t - b.t);
  if (!ev.length) return `<p class="warn">No sync event yet — clap or press the button.</p>`;
  return `<table><tr><th>#</th><th>type</th><th>time</th><th>strength</th></tr>${ev
    .map((e, i) => `<tr><td>${i + 1}</td><td>${e.kind}</td><td>${(e.t - t0).toFixed(3)} s</td><td>${e.g ? e.g.toFixed(1) + " g" : "—"}</td></tr>`)
    .join("")}</table>`;
}
