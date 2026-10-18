import { beforeAll, describe, expect, test } from "vitest";
import { api, me } from "../src/api";
import { installMock } from "../src/mock";
import type { PlaySession, Report } from "../src/types";

const TOP = ["schema", "session", "swings", "speed", "tempo", "rallies", "timeline", "heart_rate", "strokes", "insights", "model"];
const TS = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/;

function checkReport(r: Report) {
  expect(Object.keys(r).sort()).toEqual([...TOP].sort());
  expect(r.schema).toBe("volante-report/1");
  expect(r.session.started_at).toMatch(TS);
  expect(["pod", "watch"]).toContain(r.session.source);
  expect(r.session.active_s).toBeLessThanOrEqual(r.session.duration_s);
  expect(r.speed.estimated).toBe(true);
  expect(r.speed.clipped_frac).toBeGreaterThanOrEqual(0);
  expect(r.speed.clipped_frac).toBeLessThanOrEqual(1);
  expect(r.timeline.length).toBeLessThanOrEqual(600);
  expect(r.insights.length).toBeLessThanOrEqual(4);
  expect(r.strokes === null).toBe(r.model === null);
  if (r.strokes) expect(Object.values(r.strokes.groups).reduce((a: number, b: number) => a + b, 0)).toBeLessThanOrEqual(r.swings.count);
}

async function allSessions(): Promise<PlaySession[]> {
  const out: PlaySession[] = [];
  let before: string | null = null;
  do {
    const page = await api.mySessions(before, 7);
    expect(page.items.length).toBeLessThanOrEqual(7);
    out.push(...page.items);
    before = page.next_before;
  } while (before);
  return out;
}

describe("mock dashboard API (linked user)", () => {
  beforeAll(async () => {
    await installMock();
    await me(true);
  });

  test("me is app_linked", async () => expect((await me(true)).app_linked).toBe(true));

  test("sessions paginate newest first with a started_at~id cursor", async () => {
    const first = await api.mySessions(undefined, 7);
    expect(first.next_before).toMatch(/^\d{4}-.*Z~.+/);
    const all = await allSessions();
    expect(all.length).toBe(33);
    expect(new Set(all.map((s) => s.id)).size).toBe(33);
    expect(all.filter((s) => s.sensor === "watch").length).toBe(3);
    expect(all.some((s) => s.sensor === "pod+watch")).toBe(true);
    expect(all.some((s) => s.avg_bpm !== null)).toBe(true);
    expect(all.every((s) => !("report" in s))).toBe(true); // the list leaves reports out
    const t = all.map((s) => s.started_at);
    expect([...t].sort().reverse()).toEqual(t);
  });

  test("a session returns a valid volante-report/1", { timeout: 30_000 }, async () => {
    for (const s of (await allSessions()).slice(0, 33)) {
      const d = await api.mySession(s.id);
      expect(d.id).toBe(s.id);
      expect(d.swings).toBe(s.swings);
      // the app sends no report for watch sessions (and older builds sent none at all)
      if (s.sensor === "watch") expect(d.report).toBeNull();
      if (d.report) {
        checkReport(d.report);
        expect(d.report.swings.count).toBe(s.swings);
      }
    }
    await expect(api.mySession("nope")).rejects.toMatchObject({ status: 404 });
  });

  test("dashboard is well formed", async () => {
    const d = await api.myDashboard();
    expect(d.totals.sessions).toBe(33);
    expect(d.weekly).toHaveLength(12);
    expect(d.wrist.sessions).toBe(3);
    expect(d.trends.length).toBe(30);
    expect(d.bests.max_kmh!.value).toBeGreaterThan(0);
    expect(d.strokes!.experimental).toBe(true);
    expect(Object.values(d.strokes!.groups).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 3);
    expect(d.style).not.toBeNull();
  });

  test("unlinked user: app_linked false, empty dashboard", async () => {
    sessionStorage.setItem("oso-mock-unlinked", "1");
    try {
      expect((await me(true)).app_linked).toBe(false);
      expect((await api.mySessions()).items).toEqual([]);
      const d = await api.myDashboard();
      expect(d.totals.sessions).toBe(0);
      expect(d.strokes).toBeNull();
      expect(d.bests.max_kmh).toBeNull();
    } finally {
      sessionStorage.removeItem("oso-mock-unlinked");
    }
  });
});
