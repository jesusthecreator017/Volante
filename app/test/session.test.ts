import { describe, expect, test } from "vitest";
import { parse } from "../src/core/packet";
import { analyzeSession, fitClock, syncFit, percentile } from "../src/core/analysis";
import { swingsCsv, summaryJson } from "../src/core/export";
import { sampleBytes } from "./helpers";

describe("sample session sanity numbers (shown on the Session page)", () => {
  const a = analyzeSession(parse(sampleBytes()));
  test("swing count, impacts, max |ω|, saturation", () => {
    const sanity = {
      swings: a.summary.swingCount,
      impacts: a.summary.hitCount,
      maxOmegaDps: Math.round(a.summary.peakDps! * 10) / 10,
      saturatedGyro: a.summary.quality.saturatedGyro,
    };
    console.log("SANITY", sanity);
    expect(sanity).toEqual({ swings: 4, impacts: 3, maxOmegaDps: 2056.6, saturatedGyro: 12 });
  });
  test("saturated swing is flagged and has lower confidence", () => {
    const sat = a.swings.filter((s) => s.saturatedSamples > 0);
    expect(sat.map((s) => s.index)).toEqual([3]);
    expect(sat[0].saturatedSamples).toBe(12);
    expect(sat[0].confidence).toBeLessThan(0.8);
  });
  test("claps: first and last sync events reproduce the synthetic clock (≈ -40 ppm, -3.217 s)", () => {
    expect(a.syncEvents[0].t).toBeCloseTo(3.467, 2);
    const f = syncFit(a.syncEvents, [0.25, 9.6])!;
    expect(f.driftKnown).toBe(true);
    expect(f.b).toBeCloseTo(-3.217, 2);
    expect(Math.abs(f.driftPpm + 40)).toBeLessThan(15);
  });
  test("summary formulas", () => {
    const s = a.summary;
    expect(s.hitsPerMin).toBeCloseTo(s.hitCount / (s.durationS / 60), 9);
    expect(s.activeS + s.idleS).toBeCloseTo(s.durationS, 9);
    expect(s.rallyCount).toBe(1); // 3 hits < 4 s apart
    expect(s.quality.lossPct).toBeCloseTo((100 * 66) / (6534 + 66), 9);
    expect(s.speedHist.reduce((x, b) => x + b.n, 0)).toBe(4);
    expect(s.fastestIndex).toBe(3);
    expect(a.measuredRateHz).toBeCloseTo(500, 0);
  });
  test("exports", () => {
    const csv = swingsCsv(a.swings, { 2: "smash" });
    expect(csv.trim().split("\n").length).toBe(5);
    expect(csv.split("\n")[2].endsWith(",smash")).toBe(true);
    const j = JSON.parse(summaryJson(a));
    expect(j.summary.swingCount).toBe(4);
    expect(j.calibration.gyro_bias_dps[0]).toBeCloseTo(-1.82, 2);
  });
});

describe("helpers", () => {
  test("clock fit recovers offset and drift from the arrival lower envelope", () => {
    const arr: [number, number][] = [];
    for (let k = 0; k < 2000; k++) {
      const td = k * 0.026;
      arr.push([td, td * (1 + 50e-6) + 1000 + 0.005 + ((k * 7919) % 13) * 0.001]);
    }
    const f = fitClock(arr)!;
    expect(f.driftPpm).toBeCloseTo(50, -1);
    expect(f.offsetS).toBeCloseTo(1000.005, 2);
  });
  test("percentile", () => {
    expect(percentile([1, 2, 3, 4, 5], 0.9)).toBeCloseTo(4.6, 9);
  });
});
