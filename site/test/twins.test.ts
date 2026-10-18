import { afterEach, describe, expect, test, vi } from "vitest";
import { api } from "../src/api";
import { consistency, dataVolume, fidelityBadge, ladderOf, ladderToggle, tempoText, twinCard, verdict } from "../src/twins";
import type { ModelSummary, StyleProfile, Twin } from "../src/types";

const profile = (mix: boolean, clipped = 0.05): StyleProfile => ({
  schema: "volante-style/1", created_at: "2026-10-17T18:00:00Z",
  source: { sessions: 3, swings: 1240, minutes: 51.6, pod_fw: "0.1", tool: "oso-app 1.0" },
  speed: { p50_kmh: 61, p90_kmh: 82, cv: 0.14, clipped_frac: clipped },
  tempo: { median_gap_s: 1.9, p25_gap_s: 1.56, p75_gap_s: 2.3 },
  stroke_mix: mix ? { smash: 0.2, clear: 0.5, drop: 0.3 } : null, side_mix: mix ? { fh: 0.7, bh: 0.3 } : null,
});
const model = (t: Twin): ModelSummary & { twin: Twin } => ({
  id: 1, name: "Twin", owner: { id: 1, login: "x", avatar_url: "" }, build: { power: 6, speed: 6, reach: 6, accuracy: 6 },
  status: "active", rating: 1200, games: 12, ladder: t.fidelity.ok ? "twins" : "open", twin: t,
});
const ok: Twin = { profile: profile(false), fidelity: { ok: true, tempo_ratio: 1.08, mix_js: null, checked_at: "2026-10-17T20:00:00Z" } };
const checking: Twin = { profile: profile(false), fidelity: { ok: false, tempo_ratio: null, mix_js: null, checked_at: null } };
const fail: Twin = { profile: profile(false), fidelity: { ok: false, tempo_ratio: 1.52, mix_js: null, checked_at: "2026-10-17T20:00:00Z", reasons: ["tempo ratio 1.52 is outside 0.75-1.33"] } };

describe("twins", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("verdict and badge for every fidelity state", () => {
    expect([verdict(ok), verdict(checking), verdict(fail)]).toEqual(["ok", "checking", "fail"]);
    expect(fidelityBadge(ok).textContent).toBe("Plays like its profile");
    expect(fidelityBadge(checking).textContent).toBe("Checking");
    expect(fidelityBadge(fail).textContent).toBe("Does not match its profile yet");
  });

  test("wording rules from docs/twins-site.md", () => {
    expect(dataVolume(profile(false))).toBe("1,240 swings, 52 min");
    expect([consistency(0.05), consistency(0.1), consistency(0.2), consistency(0.21)]).toEqual(["steady", "mixed", "mixed", "wild"]);
    expect(tempoText(1.1)).toBe("plays 1.10x as slow as its profile");
    expect(tempoText(0.8)).toBe("plays 1.25x as fast as its profile");
    expect(tempoText(1.02)).toBe("plays at its profile's tempo");
  });

  test("twin card: strokes pending vs present, clipping note, reasons, no inline styles", () => {
    const a = twinCard(model(ok));
    expect(a.textContent).toContain("Stroke mix: coming with stroke recognition");
    expect(a.textContent).toContain("self-reported");
    expect(a.textContent).not.toContain("lower bounds");
    expect(a.querySelectorAll("[style], script").length).toBe(0);
    const b = twinCard(model({ ...ok, profile: profile(true, 0.3), fidelity: { ...ok.fidelity, mix_js: 0.04 } }));
    expect(b.querySelectorAll(".mix-bars li").length).toBe(5); // 3 strokes + forehand/backhand
    expect(b.querySelector(".mix-bars li .mix-k")?.textContent).toBe("clear"); // sorted by share
    expect(b.textContent).toContain("lower bounds");
    expect(b.textContent).toContain("Stroke mix difference: 0.04");
    expect(twinCard(model(fail)).textContent).toContain("tempo ratio 1.52 is outside 0.75-1.33");
    expect(twinCard(model(checking)).textContent).toContain("after its first 10 matches");
  });

  test("toggle and ladder parameter", () => {
    expect(ladderOf(new URLSearchParams("ladder=twins"))).toBe("twins");
    expect(ladderOf(new URLSearchParams("ladder=nope"))).toBe("open");
    const t = ladderToggle("twins", (l) => (l === "twins" ? "/arena/twins" : "/arena/ladder"));
    expect(t.querySelector('[aria-current="page"]')?.textContent).toBe("Twins");
  });

  test("API: ?ladder=twins only for the Twins ladder (older servers see the same URLs as before)", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (u: string) => (urls.push(u), new Response(JSON.stringify({ items: [] }), { headers: { "content-type": "application/json" } })));
    await api.ladder(undefined, 0, 10);
    await api.ladder(undefined, 0, 10, "twins");
    await api.tournaments(undefined, "twins");
    expect(urls).toEqual(["/api/ladder?offset=0&limit=10", "/api/ladder?ladder=twins&offset=0&limit=10", "/api/tournaments?ladder=twins"]);
  });
});
