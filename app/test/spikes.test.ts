import { describe, expect, test } from "vitest";
import { findSpikes, fitClaps, IMPACT_G, CLAP_G, findClaps, spikeStrength } from "../src/core/spikes";
import { G } from "../src/core/quat";
import { level } from "./helpers";

describe("spikes (port of sync.rs tests)", () => {
  test("spikes found with width limit and gap guard", () => {
    const s = level(2000);
    for (const k of [500, 501]) s[k].acc[2] += 12 * G;
    s[1000].acc[2] -= 5.5 * G;
    for (let k = 1300; k < 1320; k++) s[k].acc[0] += 10 * G;
    for (let k = 1500; k < 1600; k++) s[k].acc[1] += (k - 1500) * 0.5;
    const found = findSpikes(s, 0, IMPACT_G);
    expect(found.length).toBe(2);
    expect(Math.abs(found[0].t - 0.001 - 0.002 * 500)).toBeLessThan(1e-9);
    expect(Math.abs(found[1].t - 2.0)).toBeLessThan(1e-9);
    expect(found[0].peakG).toBeGreaterThan(11);
    expect(findSpikes(s, 600, CLAP_G).length).toBe(0);
    const g = level(200);
    for (let k = 100; k < 200; k++) { g[k].acc[0] += 20; g[k].t += 0.1; }
    expect(findSpikes(g, 0, IMPACT_G)).toEqual([]);
  });

  test("two claps give exact a, b; one clap or implausible drift falls back", () => {
    const a = -40e-6, b = -3.217;
    const v = (t: number) => t * (1 + a) + b;
    const [a2, b2, known] = fitClaps([[3.467, v(3.467)], [12.8, v(12.8)]]);
    expect(known).toBe(true);
    expect(Math.abs(a2 - a)).toBeLessThan(1e-12);
    expect(Math.abs(b2 - b)).toBeLessThan(1e-12);
    expect(fitClaps([[3.4, 0.25]])).toEqual([0, 0.25 - 3.4, false]);
    expect(fitClaps([[3.4, 0.25], [13.4, 10.26]])).toEqual([0, 0.25 - 3.4, false]);
  });

  test("a clap needs the racquet still before it", () => {
    const s = level(4000);
    for (const k of [500, 501, 3000, 3001]) s[k].acc[2] += 12 * G;
    expect(findClaps(s, spikeStrength(s)).length).toBe(2);
    for (let k = 2700; k < 2990; k++) s[k].gyro[0] = 20;
    expect(findClaps(s, spikeStrength(s)).length).toBe(1);
  });
});
