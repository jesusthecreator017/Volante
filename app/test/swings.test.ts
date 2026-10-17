import { describe, expect, test } from "vitest";
import { detectSwings, classifyStroke, analyzeSwing, DEFAULT_GEOMETRY } from "../src/core/swings";
import { run, defaultParams } from "../src/core/fusion";
import { G, DEG } from "../src/core/quat";
import { sample } from "./helpers";

const ts = (n: number) => Array.from({ length: n }, (_, i) => i * 0.002);

describe("swing state machine", () => {
  test("start > 300 dps, end after 80 ms below 120 dps", () => {
    const t = ts(1000);
    const w = t.map((x) => (x >= 0.5 && x < 0.8 ? 800 : 10));
    const sw = detectSwings(t, w);
    expect(sw.length).toBe(1);
    expect(sw[0].tStart).toBeCloseTo(0.5, 9);
    expect(sw[0].tEnd).toBeCloseTo(0.8, 9);
    expect(sw[0].i1).toBe(400);
  });

  test("a dip below 120 dps shorter than 80 ms does not end the swing", () => {
    const t = ts(1000);
    const w = t.map((x) => (x >= 0.5 && x < 1.2 ? (x >= 0.7 && x < 0.76 ? 50 : 800) : 10));
    expect(detectSwings(t, w).length).toBe(1);
    const w2 = t.map((x) => (x >= 0.5 && x < 1.2 ? (x >= 0.7 && x < 0.8 ? 50 : 800) : 10));
    expect(detectSwings(t, w2).length).toBe(2);
  });

  test("hysteresis: 120-300 dps neither starts nor ends a swing", () => {
    const t = ts(1000);
    expect(detectSwings(t, t.map(() => 250)).length).toBe(0);
    const w = t.map((x) => (x < 0.3 ? 10 : x < 0.4 ? 600 : x < 1.5 ? 200 : 10));
    const sw = detectSwings(t, w);
    expect(sw.length).toBe(1);
    expect(sw[0].tEnd).toBeCloseTo(1.5, 9);
  });

  test("swings shorter than 80 ms are discarded", () => {
    const t = ts(1000);
    expect(detectSwings(t, t.map((x) => (x >= 0.5 && x < 0.56 ? 800 : 10))).length).toBe(0);
    expect(detectSwings(t, t.map((x) => (x >= 0.5 && x < 0.6 ? 800 : 10))).length).toBe(1);
  });

  test("open swing at the end of data is closed", () => {
    const t = ts(500);
    const sw = detectSwings(t, t.map((x) => (x > 0.5 ? 800 : 0)));
    expect(sw.length).toBe(1);
  });
});

describe("swing metric formulas", () => {
  // Level racquet (handle +Y horizontal, face up), spinning about the sensor z (face normal) with a 2-peak profile,
  // plus a 10 g, 2-sample impact at the second peak.
  const N = 1500;
  const prof = (t: number) =>
    t < 1 ? 0 : t < 1.1 ? 900 * Math.sin(((t - 1) / 0.1) * Math.PI) : t < 1.25 ? 1400 * Math.sin(((t - 1.1) / 0.15) * Math.PI) : 0;
  const samples = Array.from({ length: N }, (_, i) => {
    const t = i * 0.002;
    return sample(t, [0, 0, G], [0, 0, prof(t) * DEG]);
  });
  const iImp = Math.round(1.175 / 0.002);
  samples[iImp].acc[2] += 10 * G;
  samples[iImp + 1].acc[2] += 10 * G;
  const track = run(samples, [0, 0, 0], [0, 0, G], defaultParams());
  const wDps = Float64Array.from(samples.map((s) => Math.hypot(...s.gyro) / DEG));
  const win = detectSwings(track.t, wDps)[0];
  const ctx = {
    samples, wDps, track, gaps: [], geometry: DEFAULT_GEOMETRY, handedness: "right" as const, t0: 0,
    impacts: [{ t: 1.175 + 0.001, peakG: 10, index: iImp }],
  };
  const s = analyzeSwing(ctx, win, 1);

  test("head speed = peak |ω| (rad/s) x 0.53 m", () => {
    expect(s.peakDps).toBeCloseTo(1400, -1);
    expect(s.headSpeed).toBeCloseTo(s.peakDps * DEG * 0.53, 9);
  });
  test("phases split at the |ω| valley and the impact", () => {
    expect(s.impact).toBe(true);
    expect(s.backswingMs).toBeCloseTo(win.tStart > 1 ? (1.1 - win.tStart) * 1000 : 0, -1);
    expect(s.forwardMs! + s.backswingMs! + s.followMs!).toBeCloseTo(s.durationMs, 6);
    expect(Math.abs(s.impactVsPeakMs!)).toBeLessThan(3);
    expect(s.timing).toBe("on peak");
  });
  test("orientation at impact: level handle, face up, not overhead", () => {
    expect(Math.abs(s.handleTiltDeg!)).toBeLessThan(2);
    expect(s.faceAngleDeg!).toBeGreaterThan(85);
    expect(s.overhead).toBe(false);
  });
  test("side from rate about the face normal, flipped by handedness", () => {
    expect(s.side).toBe("forehand");
    expect(analyzeSwing({ ...ctx, handedness: "left" }, win, 1).side).toBe("backhand");
  });
  test("confidence drops with gaps and saturation", () => {
    expect(s.confidence).toBe(1);
    const g = analyzeSwing({ ...ctx, gaps: [{ tStart: 1.17, tEnd: 1.19, missing: 9 }] }, win, 1);
    expect(g.confidence).toBeLessThan(0.7);
  });
  test("stroke heuristic", () => {
    expect(classifyStroke(17, true, 60, true, false)).toBe("smash");
    expect(classifyStroke(12, true, 60, true, false)).toBe("clear");
    expect(classifyStroke(8, true, 60, true, false)).toBe("drop");
    expect(classifyStroke(8, true, 60, true, true)).toBe("smash");
    expect(classifyStroke(12, false, 0, true, false)).toBe("drive");
    expect(classifyStroke(12, false, -50, true, false)).toBe("lift");
    expect(classifyStroke(5, false, -50, true, false)).toBe("net");
    expect(classifyStroke(20, true, 60, false, false)).toBe("no contact");
  });
});
