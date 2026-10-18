import { describe, expect, test } from "vitest";
import { SESSION, analyze, exampleSession, parseCsv, parseSwingsCsv, type SwingRow } from "../src/insights";

const HEAD = "index,start_s,head_speed_km_h_rotation_only,impact,timing,side_estimate,saturated_gyro_samples,label";
const csv = (rows: string[]) => [HEAD, ...rows].join("\n") + "\n";
const row = (t: number, kmh: number, extra: Partial<SwingRow> = {}): SwingRow => ({ t, kmh, impact: true, timing: null, side: null, clipped: false, ...extra });

describe("insights: CSV", () => {
  test("quoted cells, CRLF and blank lines", () => {
    expect(parseCsv('a,"b,c","d ""e"""\r\n\r\n1,2,3')).toEqual([["a", "b,c", 'd "e"'], ["1", "2", "3"]]);
  });
  test("reads the app's swings.csv columns", () => {
    const rows = parseSwingsCsv(csv(["1,10.5,72.3,true,on peak,forehand,0,", "0,4.0,55.0,false,,backhand,3,smash"]));
    expect(rows).toEqual([
      { t: 4, kmh: 55, impact: false, timing: null, side: "backhand", clipped: true },
      { t: 10.5, kmh: 72.3, impact: true, timing: "on peak", side: "forehand", clipped: false },
    ]);
  });
  test("a wrong or empty file says so", () => {
    expect(() => parseSwingsCsv("")).toThrow(/empty/);
    expect(() => parseSwingsCsv("a,b\n1,2\n")).toThrow(/isn't a swings.csv/);
    expect(() => parseSwingsCsv(HEAD + "\n")).toThrow(/No swings/);
  });
});

describe("insights: analysis", () => {
  test("the example keeps the real session's count, mean and top speed", () => {
    const r = analyze(exampleSession());
    expect(r.swings.length).toBe(SESSION.smashes);
    expect(r.meanKmh).toBeCloseTo(SESSION.meanKmh, 6);
    expect(r.topKmh).toBeCloseTo(SESSION.maxKmh, 6);
    expect(r.insights[0].title).toBe("Top swing: 95 km/h");
  });
  test("stats use hits when the file marks them", () => {
    const r = analyze([row(0, 200, { impact: false }), row(5, 60), row(10, 80)]);
    expect(r.scored.length).toBe(2);
    expect(r.topKmh).toBe(80);
    expect(r.topIndex).toBe(2);
  });
  test("a late drop-off becomes a tip; steady speed is praised", () => {
    const tired = analyze(Array.from({ length: 12 }, (_, i) => row(i * 5, i < 4 ? 80 : i < 8 ? 72 : 64)));
    expect(tired.trendPct).toBeCloseTo(-20, 6);
    expect(tired.insights.some((x) => x.icon === "trend-down" && x.tone === "tip")).toBe(true);
    const steady = analyze(Array.from({ length: 12 }, (_, i) => row(i * 5, 70 + (i % 2))));
    expect(steady.insights.some((x) => x.title === "No drop-off")).toBe(true);
    expect(steady.insights.some((x) => x.title === "Consistent power")).toBe(true);
  });
  test("timing, backhand share, tempo and clipped speeds", () => {
    const rows = Array.from({ length: 10 }, (_, i) => row(i * 4, 70, { timing: i < 3 ? "on peak" : "early", side: i === 0 ? "backhand" : "forehand", clipped: i === 9 }));
    const r = analyze(rows);
    expect(r.onPeakPct).toBe(30);
    expect(r.backhandPct).toBe(10);
    expect(r.gapS).toBe(4);
    const titles = r.insights.map((x) => x.title);
    expect(titles).toContain("30% hit on peak");
    expect(titles).toContain("10% backhands");
    expect(titles).toContain("A swing every 4.0 s");
    expect(titles).toContain("Some swings maxed out the sensor");
  });
  test("short sessions skip trend, timing and side insights", () => {
    const r = analyze([row(0, 60), row(3, 70)]);
    expect(r.trendPct).toBeNull();
    expect(r.onPeakPct).toBeNull();
    expect(r.backhandPct).toBeNull();
  });
});
