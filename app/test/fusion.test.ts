import { describe, expect, test } from "vitest";
import { calibrateAndRun, defaultParams, findStationary, gyroBias, run, LiveFusion, type Params } from "../src/core/fusion";
import { parse } from "../src/core/packet";
import { G, DEG, angleBetween, fromAxisAngle, mul, rotate, conj } from "../src/core/quat";
import { level, sample, sampleBytes } from "./helpers";

const fwdOnly = (): Params => ({ ...defaultParams(), twoPass: false });

describe("fusion (port of fusion.rs tests)", () => {
  test("stationary level sensor stays level", () => {
    const t = run(level(2000), [0, 0, 0], [0, 0, G], defaultParams());
    const up = rotate(t.q.at(-1)!, [0, 0, 1]);
    expect(Math.abs(up[1] - 1)).toBeLessThan(1e-6);
    expect(angleBetween(t.q[0], t.q.at(-1)!)).toBeLessThan(1e-6);
  });

  test("tilted start converges to gravity", () => {
    const s = Array.from({ length: 3000 }, (_, i) => sample(i * 0.002, [G, 0, 0], [0, 0, 0]));
    const t = run(s, [0, 0, 0], [0, 0, G], { ...defaultParams(), beta: 0.5 });
    expect(Math.abs(rotate(t.q.at(-1)!, [1, 0, 0])[1] - 1)).toBeLessThan(1e-3);
  });

  test("constant rotation about z integrates; bias is subtracted", () => {
    const w = 90 * DEG;
    let t = run(level(501, [0, 0, w]), [0, 0, 0], [0, 0, G], defaultParams());
    const want = mul(fromAxisAngle([0, 1, 0], 90 * DEG), t.q[0]);
    expect(angleBetween(t.q.at(-1)!, want)).toBeLessThan(0.5 * DEG);
    t = run(level(501, [0, 0, w + 0.1]), [0, 0, 0.1], [0, 0, G], defaultParams());
    expect(angleBetween(t.q.at(-1)!, want)).toBeLessThan(0.5 * DEG);
  });

  test("finds stationary window and bias", () => {
    const bias = [0.05, -0.04, 0.03];
    const s = level(1500, bias);
    for (let i = 1500; i < 1600; i++) s.push(sample(i * 0.002, [3, 0, G], [2, 0, 0]));
    const w = findStationary(s)!;
    expect(w.start).toBe(0);
    expect(w.end).toBeGreaterThanOrEqual(1400);
    expect(w.end).toBeLessThanOrEqual(1500);
    gyroBias(s, w).forEach((b, i) => expect(Math.abs(b - bias[i])).toBeLessThan(1e-6));
    expect(findStationary(level(400))).toBeNull();
    const moving = Array.from({ length: 2000 }, (_, i) => sample(i * 0.002, [0, 0, G], [i % 2, 0, 0]));
    expect(findStationary(moving)).toBeNull();
  });

  test("saturation lowers confidence where both passes are near it", () => {
    let s = level(1000);
    s[200].saturated = true;
    let t = run(s, [0, 0, 0], [0, 0, G], defaultParams());
    expect([t.conf[199], t.conf[200], t.conf[201]]).toEqual([1, 0.5, 1]);
    s[300].saturated = true;
    t = run(s, [0, 0, 0], [0, 0, G], defaultParams());
    expect(t.conf.slice(200, 301).every((c) => c === 0.5)).toBe(true);
    expect(t.conf[320]).toBe(1);
    s = level(1000);
    s[200].saturated = true;
    t = run(s, [0, 0, 0], [0, 0, G], fwdOnly());
    expect([t.conf[199], t.conf[200], t.conf[449], t.conf[460]]).toEqual([1, 0.5, 0.5, 1]);
  });

  test("short gaps are integrated across", () => {
    const rate = (t: number) => 1500 * DEG * Math.sin(2 * Math.PI * 5 * t) ** 2;
    const full = Array.from({ length: 251 }, (_, i) => sample(i * 0.002, [0, 0, G], [0, 0, rate(i * 0.002)]));
    const holey = full.filter((_, i) => !(i >= 60 && i < 74));
    const end = (s: typeof full) => run(s, [0, 0, 0], [0, 0, G], fwdOnly()).q.at(-1)!;
    expect(angleBetween(end(full), end(holey)) / DEG).toBeLessThan(0.5);
    const t = run(holey, [0, 0, 0], [0, 0, G], fwdOnly());
    expect(t.conf[59]).toBe(1);
    expect(t.conf[60]).toBe(0.5);
  });

  test("lost packets re-init tilt", () => {
    const s = level(251, [0, 0, 90 * DEG]);
    const tilt = fromAxisAngle([1, 0, 0], 30 * DEG);
    const a = rotate(conj(tilt), [0, 0, G]);
    for (let i = 0; i < 250; i++) s.push(sample(0.6 + i * 0.002, a, [0, 0, 0]));
    const t = run(s, [0, 0, 0], [0, 0, G], fwdOnly());
    expect(t.conf[251]).toBe(0.5);
    expect(Math.abs(rotate(t.q[251], a.map(Math.fround) as [number, number, number])[1] / G - 1)).toBeLessThan(1e-5);
  });

  test("backward pass removes the error a hole leaves behind", () => {
    const burst = (t: number) => t >= 2.0 && t < 2.044;
    const rate = (t: number) => (burst(t) ? 1000 : 20) * DEG;
    let th = 0;
    const full: ReturnType<typeof sample>[] = [];
    for (let i = 0; i < 3000; i++) {
      const t = i * 0.002;
      th += rate(t) * 0.002;
      full.push(sample(t, [0, G * Math.sin(th), G * Math.cos(th)], [rate(t), 0, 0]));
    }
    const keep = [...Array(3000).keys()].filter((i) => !burst(i * 0.002));
    expect(3000 - keep.length).toBe(22);
    const holey = keep.map((i) => full[i]);
    const ref = run(full, [0, 0, 0], [0, 0, G], fwdOnly());
    const errAt = (p: Params, t: number) => {
      const out = run(holey, [0, 0, 0], [0, 0, G], p);
      const j = keep.findIndex((i) => i * 0.002 >= t);
      return angleBetween(out.q[j], ref.q[keep[j]]) / DEG;
    };
    expect(errAt(fwdOnly(), 2.8)).toBeGreaterThan(20);
    expect(errAt(defaultParams(), 2.8)).toBeLessThan(3);
  });

  test("sample session gyro bias ≈ (-1.820, -2.075, -1.108) dps from the stationary start", () => {
    const c = parse(sampleBytes());
    const r = calibrateAndRun(c.samples, defaultParams())!;
    expect(r.window.start).toBe(0);
    const dps = r.bias.map((b) => b / DEG);
    expect(dps[0]).toBeCloseTo(-1.82, 3);
    expect(dps[1]).toBeCloseTo(-2.075, 3);
    expect(dps[2]).toBeCloseTo(-1.108, 3);
  });

  test("live forward fusion matches the offline forward pass on the sample", () => {
    const c = parse(sampleBytes());
    const r = calibrateAndRun(c.samples, fwdOnly())!;
    const lf = new LiveFusion(r.bias, fwdOnly());
    const acc = r.track.q; // offline track
    lf.reset([0, 0, 0].map((_, a) => c.samples.slice(r.window.start, r.window.end).reduce((s, x) => s + x.acc[a], 0) / (r.window.end - r.window.start)) as [number, number, number]);
    let worst = 0;
    c.samples.forEach((s, i) => {
      const q = lf.push(s).q;
      worst = Math.max(worst, angleBetween(q, acc[i]));
    });
    // Live can't see the sample after a hole (Hermite end slope = 0), so allow a small difference
    expect(worst / DEG).toBeLessThan(3);
  });
});
