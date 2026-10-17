import { describe, expect, test } from "vitest";
import { MockEngine, DEFAULT_MOCK } from "../src/ble/mock";
import { LiveSession } from "../src/core/live";
import { parse } from "../src/core/packet";
import { analyzeSession, fitClock } from "../src/core/analysis";
const fitClockFrom = (a: [number, number][]) => fitClock(a)!.driftPpm;
import { CMD } from "../src/ble/profile";
import { sampleBytes } from "./helpers";

function runMock(lossProb = 0, seed = 1) {
  let x = seed;
  const rand = () => ((x = (x * 16807) % 2147483647) / 2147483647);
  const e = new MockEngine(sampleBytes(), { ...DEFAULT_MOCK, loop: false, lossProb }, rand);
  const live = new LiveSession(e.rateHz);
  e.command(CMD.START_STREAM);
  let host = 0;
  for (let k = 0; k < 600; k++) {
    host += 26;
    for (const dv of e.advance(26)) live.ingest(dv, host);
  }
  live.finish();
  return { e, live };
}

describe("mock device + live pipeline", () => {
  test("mock paces packets by their timestamps: device time tracks real time", () => {
    const e = new MockEngine(sampleBytes(), { ...DEFAULT_MOCK, loop: true });
    const live = new LiveSession(500);
    e.command(CMD.START_STREAM);
    let host = 0;
    for (let k = 0; k < 2000; k++) {
      host += 26;
      for (const dv of e.advance(26)) live.ingest(dv, host);
    }
    // 52 s of real time across 4 loops of the file: device clock vs host clock within the file's own +40 ppm drift
    const f = fitClockFrom(live.arrivals);
    expect(Math.abs(f)).toBeLessThan(500);
  });

  test("notifications carry 13 packets at MTU 247", () => {
    const e = new MockEngine(sampleBytes(), { ...DEFAULT_MOCK, loop: false });
    e.command(CMD.START_STREAM);
    const out = e.advance(26);
    expect(out[0].byteLength).toBe(13 * 18);
  });

  test("streamed session reproduces the file exactly (imu.bin) and the offline accounting", () => {
    const { live } = runMock();
    const bin = live.toImuBin();
    expect(bin).toEqual(sampleBytes());
    expect(live.stats.arrivals).toBe(6547);
    expect(live.stats.unique).toBe(6534);
    expect(live.stats.duplicate).toBe(13);
    expect(live.stats.reordered).toBe(99);
    expect(live.stats.lost).toBe(66);
  });

  test("live calibration, swings and impacts agree with the offline analysis", () => {
    const { live } = runMock();
    const off = analyzeSession(parse(live.toImuBin()));
    expect(live.bias).not.toBeNull();
    live.bias!.forEach((b, i) => expect(Math.abs(b - off.calibration.bias![i]) * 57.3).toBeLessThan(0.1));
    expect(live.swings.length).toBe(off.swings.length);
    expect(live.swings.map((s) => s.impact)).toEqual(off.swings.map((s) => s.impact));
    live.swings.forEach((s, i) => expect(Math.abs(s.peakDps - off.swings[i].peakDps)).toBeLessThan(2));
  });

  test("packet-loss injection lowers delivered packets and is visible in the stats", () => {
    const { live } = runMock(0.05, 7);
    expect(live.stats.lost).toBeGreaterThan(66 + 150);
    expect(live.stats.gaps).toBeGreaterThan(44);
  });

  test("record -> upload delivers the recorded packets; sync marker arrives as a marker", () => {
    const e = new MockEngine(sampleBytes(), { ...DEFAULT_MOCK, loop: false });
    e.command(CMD.START_RECORDING);
    e.advance(2000);
    e.command(CMD.STOP_RECORDING);
    const recorded = e.status().buffered_bytes! / 18;
    expect(Math.abs(recorded - 1000)).toBeLessThan(25); // 2 s at 500 Hz, minus the file's own losses
    e.command(CMD.UPLOAD);
    const up = new LiveSession(500, undefined, "upload");
    let guard = 0;
    while (e.uploading && guard++ < 1000) for (const dv of e.advance(26)) up.ingest(dv, 0);
    expect(up.stats.arrivals).toBe(recorded);
    expect(e.status().upload_sent_bytes).toBe(e.status().upload_total_bytes);
    e.command(CMD.START_STREAM);
    const s = new LiveSession(500);
    for (const dv of e.advance(26)) s.ingest(dv, 0);
    for (const dv of e.command(CMD.SYNC_MARK)) s.ingest(dv, 0);
    expect(s.stats.markers).toBe(1);
    expect(s.markers[0].t).toBeGreaterThan(0);
  });
});

test("live clap detection finds the same sync claps as the offline analysis", () => {
  const { live } = runMock();
  const off = analyzeSession(parse(live.toImuBin()));
  const offClaps = off.syncEvents.filter((e) => e.kind === "clap").map((e) => e.t);
  expect(live.claps.length).toBe(offClaps.length);
  live.claps.forEach((c, i) => expect(c.t).toBeCloseTo(offClaps[i], 6));
});
