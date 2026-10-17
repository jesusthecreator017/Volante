// The firmware BLE contract (docs/ble-profile.md, proto/src/lib.rs): status layout and the mock pod's state machine.

import { describe, expect, test } from "vitest";
import { MockEngine, DEFAULT_MOCK, type MockOptions } from "../src/ble/mock";
import { CMD, ERR, EVENT, STATE, STATE_EXT, encodeStatus, packetsPerNotification, parseStatus, type DeviceStatus } from "../src/ble/profile";
import { LiveSession } from "../src/core/live";
import { sampleBytes } from "./helpers";

const engine = (o: Partial<MockOptions> = {}) => new MockEngine(sampleBytes(), { ...DEFAULT_MOCK, loop: true, eraseMs: 0, ...o });
const events = (e: MockEngine): DeviceStatus[] => e.events().map((d) => parseStatus(d)!);

describe("status characteristic (24 bytes)", () => {
  test("decodes the proto status_roundtrip vector byte for byte", () => {
    // proto/src/lib.rs `status_roundtrip`, encoded by hand from the documented offsets
    const b = new Uint8Array(24);
    const dv = new DataView(b.buffer);
    b[0] = 1;
    b[1] = 2;
    dv.setUint16(2, 500, true);
    b[4] = 255;
    b[5] = STATE.STREAMING | STATE.IMU_OK;
    b[6] = 16;
    dv.setUint16(7, 2000, true);
    dv.setUint32(9, 123_456, true);
    dv.setUint16(13, 7, true);
    b[15] = EVENT.UPLOAD_DONE;
    dv.setUint32(16, 0xdeadbeef, true);
    b[20] = STATE_EXT.SLEEPING;
    const s = parseStatus(dv)!;
    expect(s).toMatchObject({
      fwMajor: 1, fwMinor: 2, rateHz: 500, batteryPct: null, accelG: 16, gyroDps: 2000, bufferedPackets: 123_456,
      fifoOverflows: 7, event: EVENT.UPLOAD_DONE, eventValue: 0xdeadbeef, streaming: true, imuOk: true, recording: false,
      sleeping: true,
    });
    expect(new Uint8Array(encodeStatus(s).buffer)).toEqual(b);
  });

  test("short values are rejected, not misread", () => {
    expect(parseStatus(new DataView(new ArrayBuffer(23)))).toBeNull();
    // the old app's JSON status
    expect(parseStatus(new DataView(new TextEncoder().encode('{"v":1}').buffer))).toBeNull();
  });

  test("notifications carry at most 13 packets", () => {
    expect(packetsPerNotification(247)).toBe(13);
    expect(packetsPerNotification(517)).toBe(13);
    expect(packetsPerNotification(23)).toBe(1);
  });
});

describe("mock pod follows the firmware state machine", () => {
  test("power-on erases, then records by itself", () => {
    const e = engine({ eraseMs: 1000 });
    expect(e.status().erasing).toBe(true);
    expect(e.status().recording).toBe(false);
    e.advance(1100);
    expect(e.status()).toMatchObject({ erasing: false, recording: true });
    e.advance(1000);
    expect(e.status().bufferedPackets).toBeGreaterThan(400);
  });

  test("an un-uploaded session needs 0x03 twice within 5 s, and blocks the boot recording", () => {
    const e = engine();
    e.advance(1000);
    e.command(CMD.STOP_RECORDING);
    const n = e.status().bufferedPackets;
    expect(e.status().pendingUpload).toBe(true);
    e.command(CMD.START_RECORDING);
    expect(events(e)).toMatchObject([{ event: EVENT.ERROR, eventValue: ERR.NEED_CONFIRM }]);
    expect(e.status().bufferedPackets).toBe(n); // kept
    e.advance(6000); // confirm window over: asks again
    e.command(CMD.START_RECORDING);
    expect(events(e)).toMatchObject([{ event: EVENT.ERROR, eventValue: ERR.NEED_CONFIRM }]);
    e.advance(100);
    e.command(CMD.START_RECORDING);
    expect(events(e)).toEqual([]);
    expect(e.status()).toMatchObject({ recording: true, pendingUpload: false });
  });

  test("0x07 is honored only after a complete upload, and frees the session", () => {
    const e = engine();
    e.advance(500);
    e.command(CMD.STOP_RECORDING);
    e.command(CMD.MARK_UPLOADED);
    expect(events(e)).toMatchObject([{ event: EVENT.ERROR, eventValue: ERR.NO_SESSION }]);
    e.command(CMD.UPLOAD);
    expect(e.status().uploading).toBe(true);
    let guard = 0;
    while (e.uploading && guard++ < 500) e.advance(26);
    expect(events(e).map((s) => s.event)).toEqual([EVENT.UPLOAD_DONE]);
    e.command(CMD.MARK_UPLOADED);
    expect(events(e)).toEqual([]);
    expect(e.status().pendingUpload).toBe(false);
    e.command(CMD.START_RECORDING); // no confirm needed any more
    expect(events(e)).toEqual([]);
    expect(e.status().recording).toBe(true);
  });

  test("0x06 while streaming: a MARKER_CMD event whose t_us lands on the live stream's clock", () => {
    const e = engine({ autoRecord: false });
    const live = new LiveSession(500);
    e.command(CMD.START_STREAM);
    for (let k = 0; k < 40; k++) for (const dv of e.advance(26)) live.ingest(dv, 0);
    e.command(CMD.SYNC_MARK);
    const [m] = events(e);
    expect(m.event).toBe(EVENT.MARKER_CMD);
    live.addDeviceMarker(m.eventValue, "command");
    for (const dv of e.advance(26)) live.ingest(dv, 0);
    live.finish();
    const last = live.samples[live.samples.length - 1].t;
    expect(live.markers[0].source).toBe("command");
    expect(last - live.markers[0].t).toBeGreaterThanOrEqual(0);
    expect(last - live.markers[0].t).toBeLessThan(0.1);
  });

  test("a marker that arrives before any packet waits for the first one", () => {
    const e = engine({ autoRecord: false });
    const live = new LiveSession(500);
    e.command(CMD.START_STREAM);
    const first = e.advance(26);
    live.addDeviceMarker(new DataView(first[0].buffer).getUint32(2, true), "tap");
    expect(live.markers.length).toBe(0);
    for (const dv of first) live.ingest(dv, 0);
    expect(live.markers).toMatchObject([{ source: "tap" }]);
  });

  test("sleep: only wake is accepted; wake boots normally and keeps an un-uploaded session", () => {
    const e = engine();
    e.advance(500);
    const n = e.status().bufferedPackets;
    e.command(CMD.SLEEP);
    expect(e.status()).toMatchObject({ sleeping: true, recording: false, bufferedPackets: 0 });
    expect(e.advance(1000)).toEqual([]);
    e.command(CMD.START_STREAM);
    expect(events(e)).toMatchObject([{ event: EVENT.ERROR, eventValue: ERR.SLEEPING }]);
    e.command(CMD.WAKE);
    expect(e.status()).toMatchObject({ sleeping: false, recording: false, pendingUpload: true, bufferedPackets: n });
  });

  test("busy and unknown opcodes answer with error events", () => {
    const e = engine({ eraseMs: 1000 });
    e.command(CMD.UPLOAD);
    e.command(CMD.SLEEP);
    e.command(0x42);
    expect(events(e).map((s) => s.eventValue)).toEqual([ERR.BUSY, ERR.BUSY, ERR.BAD_OPCODE]);
  });
});
