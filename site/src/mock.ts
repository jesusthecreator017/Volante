// Dev-only fake of the Pi agent's API (`npm run dev:mock`), so every page can be built and checked before the server
// exists. Deterministic fixtures; same shapes as src/types.ts. Never included in the production build (__MOCK__).

import { checkPodName } from "./pods";
import type { Best, Dashboard, GroupShares, LadderItem, LadderName, MatchDetail, Me, ModelDetail, ModelSummary, Pod, Rally, PlaySession, Report, Season, Side, StyleProfile, Tie, Tournament, Twin } from "./types";

/** Real kit matches from scripts/make-mock-replays.sh (gitignored); empty when they haven't been made. Keyed by match id. */
type Fixture = Pick<MatchDetail, "games_won" | "winner" | "games" | "seed" | "rallies"> & { a: Pick<ModelSummary, "name" | "build">; b: Pick<ModelSummary, "name" | "build"> };
const FIXTURES: Record<string, Fixture> = Object.values(import.meta.glob<Record<string, Fixture>>("../.mock-replays/matches.json", { eager: true, import: "default" }))[0] ?? {};

let seed = 7;
const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const pick = <T,>(xs: T[]) => xs[Math.floor(rnd() * xs.length)];
const iso = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();

const BUILDS = [
  { power: 9, speed: 6, reach: 5, accuracy: 4 },
  { power: 3, speed: 8, reach: 8, accuracy: 5 },
  { power: 3, speed: 6, reach: 7, accuracy: 8 },
  { power: 4, speed: 6, reach: 5, accuracy: 9 },
  { power: 4, speed: 9, reach: 5, accuracy: 6 },
  { power: 6, speed: 7, reach: 7, accuracy: 4 },
  { power: 6, speed: 6, reach: 6, accuracy: 6 },
  { power: 10, speed: 5, reach: 5, accuracy: 4 },
];
const NAMES = ["Shuttlecock Sam", "NetNinja", "Drop Shot Dana", "Clearmaster 3000", "Lob Goblin", "Feather Fury", "Backhand Bandit", "Smash Mouth", "The Wall", "Rally Raccoon", "Kill Shot Kim", "Drive Thru", "Overhead Ollie", "Crosscourt Cass", "Flick Serve Fox", "<script>alert(1)</script> (escaping test)", "Tumbling Net", "Deep Lift", "Push & Press", "Midcourt Max", "A very long model name to test wrapping ok"];
const LOGINS = ["kirbyfan5000", "jesusthecreator017", "shuttlequeen", "neuralnet_nate", "badmintonbot", "rl_rachel", "gradient_greg", "smashlab"];
const user = (i: number) => ({ id: 100 + i, login: LOGINS[i % LOGINS.length], avatar_url: `https://avatars.githubusercontent.com/u/${583231 + i}?s=64` });

const models: ModelDetail[] = [];
function addModel(name: string, owner: ModelSummary["owner"], build = pick(BUILDS), status: ModelSummary["status"] = "active"): ModelDetail {
  const id = models.length + 1;
  const games = Math.floor(rnd() * 120);
  let r = 1200;
  const history = Array.from({ length: Math.min(games, 40) }, (_, k) => ({ t: iso((40 - k) * 90), rating: (r += (rnd() - 0.45) * 30), match_id: 1000 + id * 50 + k }));
  const m: ModelDetail = {
    id, name, owner, build, status, rating: status === "pending" ? null : Math.round(r * 10) / 10, games,
    description: rnd() > 0.3 ? `Trained ${Math.floor(rnd() * 20) + 2} h with the kit. ${pick(["Loves the net.", "Smashes everything.", "Patient rallies, then a kill.", "Mostly lifts and waits."])}` : null,
    created_at: iso(5000 + id * 30), retired_at: status === "retired" ? iso(100) : null, season_id: 2, rating_history: history,
  };
  models.push(m);
  return m;
}
addModel("House Base", { house: true }, { power: 6, speed: 6, reach: 6, accuracy: 6 });
addModel("House Bot", { house: true }, { power: 5, speed: 7, reach: 6, accuracy: 6 });
NAMES.forEach((n, i) => addModel(n, user(i)));
// Volante Twins: one model per state (docs/twins-site.md), on the Twins ladder or still on the open one
const profile = (swings: number, minutes: number, p50: number, p90: number, cv: number, gap: number, clipped = 0.05, mix = false): StyleProfile => ({
  schema: "volante-style/1", created_at: iso(9000), source: { sessions: Math.max(1, Math.round(minutes / 40)), swings, minutes, pod_fw: "0.1", tool: "oso-app 1.0" },
  speed: { p50_kmh: p50, p90_kmh: p90, cv, clipped_frac: clipped }, tempo: { median_gap_s: gap, p25_gap_s: Math.round(gap * 82) / 100, p75_gap_s: Math.round(gap * 121) / 100 },
  stroke_mix: mix ? { clear: 0.31, drop: 0.22, smash: 0.18, lift: 0.14, drive: 0.09, net: 0.06 } : null, side_mix: mix ? { fh: 0.72, bh: 0.28 } : null,
  notes: "speeds are estimates (gyro x 0.53 m) and lower bounds when clipped",
});
const twin = (name: string, i: number, t: Twin, lad: LadderName) => Object.assign(addModel(name, user(i)), { twin: t, ladder: lad });
twin("Jesus's Twin", 1, { profile: profile(1240, 52, 61, 82, 0.14, 1.9), fidelity: { ok: true, tempo_ratio: 1.08, mix_js: null, checked_at: iso(600), reasons: [] } }, "twins");
twin("Rachel (twin)", 5, { profile: profile(3310, 140, 55, 77, 0.08, 2.3, 0.24, true), fidelity: { ok: true, tempo_ratio: 0.93, mix_js: 0.04, checked_at: iso(1200), reasons: [] } }, "twins");
twin("Greg's Twin", 6, { profile: profile(410, 18, 48, 69, 0.23, 2.6), fidelity: { ok: false, tempo_ratio: null, mix_js: null, checked_at: null } }, "open");
twin("Nate's Twin", 3, { profile: profile(890, 37, 66, 91, 0.12, 1.6), fidelity: { ok: false, tempo_ratio: 1.52, mix_js: null, checked_at: iso(300), reasons: ["tempo ratio 1.52 is outside 0.75-1.33"] } }, "open");
const me0 = user(0);
addModel("Old Smasher v1", me0, BUILDS[0], "retired");
const myRejected = addModel("Broken build", me0, { power: 9, speed: 9, reach: 4, accuracy: 3 }, "rejected");
myRejected.reject_reasons = ["build must sum to 24 (got 25)"];

const active = () => models.filter((m) => m.status === "active");
const summary = ({ description: _d, created_at: _c, retired_at: _r, season_id: _s, rating_history: _h, reject_reasons: _x, ...s }: ModelDetail): ModelSummary => s;

function makeMatch(id: number, a: ModelDetail, b: ModelDetail, kind: MatchDetail["kind"], minutesAgo: number): MatchDetail {
  const games: [number, number][] = [];
  const rallies: Rally[] = [];
  let won = [0, 0];
  let t = 0;
  while (won[0] < 2 && won[1] < 2) {
    let s: [number, number] = [0, 0];
    const bias = 0.5 + ((a.rating ?? 1200) - (b.rating ?? 1200)) / 2000;
    let server: Side = "a";
    while (!((s[0] >= 15 || s[1] >= 15) && Math.abs(s[0] - s[1]) >= 2) && s[0] < 21 && s[1] < 21) {
      const w: Side = rnd() < bias ? "a" : "b";
      const shots = 1 + Math.floor(rnd() * 14);
      s = w === "a" ? [s[0] + 1, s[1]] : [s[0], s[1] + 1];
      rallies.push({ game: games.length + 1, t_start: t, t_end: t + shots * 0.9, server, winner: w, reason: pick(["missed", "out", "net", "missed", "out", "serve"] as const), shots, score: s });
      t += shots * 0.9 + 4;
      server = w;
    }
    games.push(s);
    won = s[0] > s[1] ? [won[0] + 1, won[1]] : [won[0], won[1] + 1];
  }
  const winner: Side = won[0] > won[1] ? "a" : "b";
  const d = Math.round((8 + rnd() * 8) * 10) / 10;
  return {
    id, kind, season_id: 2, created_at: iso(minutesAgo + 3), finished_at: iso(minutesAgo), status: "done",
    a: summary(a), b: summary(b), games_won: won as [number, number], winner, games,
    rating_delta: kind === "tournament" ? null : winner === "a" ? { a: d, b: -d } : { a: -d, b: d }, seed: 1000 + id, rallies,
  };
}
const matches: MatchDetail[] = [];
for (let i = 0; i < 60; i++) {
  const ms = active();
  const a = pick(ms);
  let b = pick(ms);
  while (b.id === a.id) b = pick(ms);
  const m = makeMatch(500 - i, a, b, pick(["ladder", "ladder", "ladder", "placement"] as const), i * 17 + 2);
  const f = FIXTURES[m.id];
  if (f) Object.assign(m, f, { a: { ...m.a, ...f.a }, b: { ...m.b, ...f.b }, rating_delta: f.winner === "a" ? { a: 9.4, b: -9.4 } : { a: -9.4, b: 9.4 } });
  matches.push(m);
}
const running: MatchDetail = { ...makeMatch(501, models[3], models[4], "ladder", 0), status: "running", finished_at: null, winner: null, games: [], games_won: [0, 0], rallies: [], rating_delta: null };
matches.unshift(running);

const ladderItems = (): LadderItem[] =>
  active()
    .filter((m) => m.rating !== null)
    .sort((x, y) => y.rating! - x.rating!)
    .map((m, i) => {
      const wins = Math.round(m.games * (0.3 + rnd() * 0.4));
      return { rank: i + 1, model: summary(m), rating: m.rating!, provisional: m.games < 20, games: m.games, wins, losses: m.games - wins };
    });

let nextTourneyMatchId = 900; // unique ids: the old `900 + id * 10 + matches.length` collided across tournaments
function makeTournament(id: number, daysAgo: number, finished: boolean): Tournament {
  const top = ladderItems().filter((x) => x.games >= 20 && !("house" in x.model.owner)).slice(0, 8).map((x) => models[x.model.id - 1]);
  const order = [[0, 7], [3, 4], [2, 5], [1, 6]];
  let round: Tie[] = order.map(([i, j]) => ({ seed_a: i + 1, seed_b: j + 1, a: summary(top[i]), b: summary(top[j]), match_id: null, winner: null }));
  const rounds: Tie[][] = [];
  for (let r = 0; r < 3; r++) {
    const live = !finished && r === 2;
    round = round.map((t) => {
      if (live || !t.a || !t.b) return t;
      const m = makeMatch(nextTourneyMatchId++, models[t.a.id - 1], models[t.b.id - 1], "tournament", daysAgo * 1440 + 60 - r * 10);
      matches.push(m);
      return { ...t, match_id: m.id, winner: m.winner };
    });
    rounds.push(round);
    if (r < 2) {
      const next: Tie[] = [];
      for (let k = 0; k < round.length; k += 2) {
        const w = (t: Tie) => (t.winner === "a" ? t.a : t.winner === "b" ? t.b : null);
        const ws = (t: Tie) => (t.winner === "a" ? t.seed_a : t.seed_b);
        next.push({ seed_a: ws(round[k]), seed_b: ws(round[k + 1]), a: w(round[k]), b: w(round[k + 1]), match_id: null, winner: null });
      }
      round = next;
    }
  }
  return { id, season_id: 2, starts_at: iso(daysAgo * 1440), status: finished ? "done" : "running", rounds };
}
const tournaments = [makeTournament(3, 0, false), makeTournament(2, 7, true), makeTournament(1, 14, true)];

const seasons: Season[] = [
  { id: 1, contract: "v1", kit_version: "0.9.0", started_at: iso(60 * 24 * 60), ended_at: iso(60 * 24 * 20), active: false },
  { id: 2, contract: "v2", kit_version: "1.0.0", started_at: iso(60 * 24 * 20), ended_at: null, active: true },
];

const pods: Pod[] = [
  { id: "pod_1", hw_id: "OSO-4C99", name: "Jesus's racquet", last_seen: iso(25) },
  { id: "pod_2", hw_id: "OSO-71AE", name: "Spare pod", last_seen: null },
];

// ---- Web dashboard fixtures (GET /api/me/sessions, /sessions/{id}, /dashboard). Two mock users: the default has a linked app
// (33 sessions over 10 weeks); open the site with `?unlinked` (or `?linked` to undo; kept in sessionStorage) for one that never synced.
let dseed = 11;
const drnd = () => ((dseed = (dseed * 16807) % 2147483647) / 2147483647);
const r1 = (x: number) => Math.round(x * 10) / 10;
const ts = (d: Date) => d.toISOString().replace(/\.\d+Z$/, "Z");
const STROKES = { power: ["smash", "clear"], mid: ["drive", "drop"], touch: ["net_shot", "block_push"] } as const; // two classes per group (report-format.md §3)

function makeReport(daysAgo: number, source: "pod" | "watch", strokes: boolean, hr: boolean): Report {
  const d = new Date(Date.now() - daysAgo * 86_400_000);
  d.setUTCHours(17 + Math.floor(drnd() * 4), Math.floor(drnd() * 60), Math.floor(drnd() * 60), 0);
  const duration = Math.round(1200 + drnd() * 2400);
  const active = Math.round(duration * (0.5 + drnd() * 0.3));
  const swings = Math.round(active / 60 * (24 + drnd() * 16));
  const gap = r1(1.6 + drnd() * 1.2);
  const pod = source === "pod";
  // the watch measures the wrist, not the racquet head: much lower "hand speed" (p90 about 15-35 km/h)
  const p90 = pod ? r1((32 + drnd() * 25 + (daysAgo < 35 ? 4 : 0)) * 1.4) : r1(15 + drnd() * 20);
  const p50 = r1(p90 / 1.4), max = r1(p90 * 1.2);
  const rallies = Math.round(swings / 6); // about 6 shots a rally
  const median = 4 + Math.floor(drnd() * 3);
  const mins = Math.min(Math.ceil(duration / 60), 60);
  const avg = Math.round(118 + drnd() * 30);
  const g = { power: 0, mid: 0, touch: 0, not_a_hit: 0, unclassified: 0 };
  const counts: Record<string, Record<string, number>> = {};
  if (strokes) {
    const hits = Math.round(swings * 0.9);
    g.unclassified = Math.round(hits * 0.08);
    g.not_a_hit = swings - hits;
    let left = hits - g.unclassified;
    for (const k of ["power", "mid"] as const) (g[k] = Math.round(left * (k === "power" ? 0.3 : 0.4)));
    g.touch = left - g.power - g.mid;
    for (const k of ["power", "mid", "touch"] as const) {
      const [a, b] = STROKES[k];
      const n = Math.round(g[k] * 0.6);
      counts[a] = { all: n };
      counts[b] = { all: g[k] - n };
    }
  }
  return {
    schema: "volante-report/1",
    session: { started_at: ts(d), duration_s: duration, active_s: active, source, pod_fw: pod ? "0.1" : "unknown" },
    swings: { count: swings, per_min_active: r1(swings / (active / 60)) },
    speed: { p50_kmh: p50, p90_kmh: p90, max_kmh: max, clipped_frac: r1(drnd() * 0.2) , estimated: true },
    tempo: { median_gap_s: gap, p25_gap_s: r1(gap * 0.82), p75_gap_s: r1(gap * 1.21) },
    rallies: { count: rallies, median_shots: median, longest_shots: median + 4 + Math.floor(drnd() * 14) },
    timeline: Array.from({ length: mins }, (_, t) => ({ t_min: t, swings: Math.round((swings / mins) * (0.5 + drnd())), p90_kmh: r1(p90 * (0.9 + drnd() * 0.2)), hr_bpm: hr ? avg - 6 + Math.round(drnd() * 12) : null })),
    heart_rate: hr ? { avg_bpm: avg, max_bpm: avg + 25 } : null,
    strokes: strokes ? { groups: g, counts, speed_by_class: { smash: { p50_kmh: r1(p90 * 1.1), p90_kmh: r1(max * 0.95) } }, unclassified: g.unclassified, experimental: true } : null,
    insights: [{ id: "tempo_vs_usual", severity: "info", title: "Quicker than usual", body: `Your median time between shots was ${gap} s.`, evidence: { median_gap_s: gap } }],
    model: strokes ? { name: "stroke_group_v0", version: "0.1" } : null,
  };
}

/** The flat PUT body of a session (what the app sends besides the report). Watch sessions come without a report (the app builds
 *  reports for the racquet only), and one old pod session too (an app build before reports). */
const playOf = (id: string, r: Report, sensor: PlaySession["sensor"], withReport = true): PlaySession => ({
  id, started_at: r.session.started_at, sensor, duration_s: r.session.duration_s, swings: r.swings.count, hits: Math.round(r.swings.count * 0.8),
  best_kmh: sensor === "watch" ? null : r.speed.max_kmh, avg_bpm: r.heart_rate?.avg_bpm ?? null, uploaded_at: r.session.started_at,
  report: withReport ? r : null,
});
const dashSessions: PlaySession[] = [
  ...Array.from({ length: 30 }, (_, i) => makeReport(70 - i * 2.3, "pod", i % 3 === 0, i % 2 === 0)),
  ...[4, 20, 41].map((days) => makeReport(days, "watch", false, true)),
].map((r, i) => {
  const id = `s${String(i + 1).padStart(2, "0")}`;
  if (r.session.source === "watch") return playOf(id, r, "watch", false);
  return playOf(id, r, r.heart_rate && i % 4 === 0 ? "pod+watch" : "pod", i !== 5);
});

const unlinked = () => {
  try {
    return sessionStorage.getItem("oso-mock-unlinked") === "1";
  } catch {
    return false;
  }
};
const mySessions = () => (unlinked() ? [] : [...dashSessions].sort((a, b) => (b.started_at + b.id).localeCompare(a.started_at + a.id)));
const summaryOf = ({ report, ...x }: PlaySession): PlaySession => x; // the list leaves the report out

// port of arena/server/osoarena/dashboard.py aggregate()
const GROUPS = ["power", "mid", "touch", "not_a_hit", "unclassified"] as const;
const shares = (gs: Record<string, number>[]): GroupShares | null => {
  const sums = Object.fromEntries(GROUPS.map((k) => [k, gs.reduce((a, x) => a + (x[k] ?? 0), 0)])) as GroupShares;
  const total = GROUPS.reduce((a, k) => a + sums[k], 0);
  if (!total) return null;
  const out = Object.fromEntries(GROUPS.map((k) => [k, Math.round((sums[k] / total) * 1e4) / 1e4])) as GroupShares;
  const top = GROUPS.reduce((a, k) => (sums[k] > sums[a] ? k : a));
  out[top] = Math.round((1 - GROUPS.filter((k) => k !== top).reduce((a, k) => a + out[k], 0)) * 1e4) / 1e4;
  return out;
};
const isoWeek = (d: Date) => {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  return `${t.getUTCFullYear()}-W${String(Math.ceil(((+t - Date.UTC(t.getUTCFullYear(), 0, 1)) / 86_400_000 + 1) / 7)).padStart(2, "0")}`;
};
function dashboard(): Dashboard {
  // a session's report when it has one, else its flat numbers (duration as active time); "watch" is the wrist
  const items = [...mySessions()].reverse().map((x) => {
    const r = x.report;
    return {
      id: x.id, started_at: x.started_at, wrist: x.sensor === "watch", active_s: r ? r.session.active_s : x.duration_s, swings: r ? r.swings.count : x.swings,
      rallies: r?.rallies.count ?? 0, longest: r?.rallies.longest_shots ?? 0, p90: r?.speed.p90_kmh ?? null, max: r ? r.speed.max_kmh : x.best_kmh,
      rate: r?.swings.per_min_active ?? null, gap: r?.tempo.median_gap_s ?? null, hr: r?.heart_rate?.avg_bpm ?? x.avg_bpm, groups: r?.strokes?.groups ?? null,
    };
  });
  type Row = (typeof items)[number];
  const pod = items.filter((x) => !x.wrist);
  const watch = items.filter((x) => x.wrist);
  const weeks = new Map<string, [number, number, number]>();
  for (let n = 11; n >= 0; n--) weeks.set(isoWeek(new Date(Date.now() - n * 7 * 86_400_000)), [0, 0, 0]);
  for (const x of items) {
    const w = weeks.get(isoWeek(new Date(x.started_at)));
    if (w) { w[0]++; w[1] += x.swings; w[2] += x.active_s; }
  }
  const best = (xs: Row[], v: (x: Row) => number | null): Best | null => {
    let b: Best | null = null;
    for (const x of xs) { const n = v(x); if (n !== null && n > 0 && (!b || n > b.value)) b = { value: n, session_id: x.id, started_at: x.started_at }; }
    return b;
  };
  const stroked = items.flatMap((x) => (x.groups ? [x.groups] : []));
  const recent = shares(stroked.slice(-10));
  return {
    totals: { sessions: items.length, swings: items.reduce((a, x) => a + x.swings, 0), active_min: r1(items.reduce((a, x) => a + x.active_s, 0) / 60), rallies: items.reduce((a, x) => a + x.rallies, 0) },
    weekly: [...weeks].map(([week, [sessions, swings, a]]) => ({ week, sessions, swings, active_min: r1(a / 60) })),
    trends: pod.slice(-50).map((x) => ({ id: x.id, started_at: x.started_at, p90_kmh: x.p90, swings_per_min: x.rate, median_gap_s: x.gap, hr_avg: x.hr })),
    wrist: { sessions: watch.length, swings: watch.reduce((a, x) => a + x.swings, 0) },
    bests: { max_kmh: best(pod, (x) => x.max), most_swings: best(items, (x) => x.swings), longest_rally: best(items, (x) => x.longest), best_rate: best(items, (x) => x.rate) },
    strokes: recent && { groups: recent, overall: shares(stroked), sessions: stroked.slice(-10).length, experimental: true },
    style: unlinked() ? null : models.find((m) => m.twin)?.twin?.profile ?? null,
    twin_model_id: null,
  };
}

let signedIn = true;
let uploadsLeft = 5;
const myPending = new Map<number, { polls: number; bad: boolean }>();

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}
const delay = () => new Promise((r) => setTimeout(r, 150 + rnd() * 250));

async function handle(url: URL, init: RequestInit): Promise<Response> {
  await delay();
  const method = (init.method ?? "GET").toUpperCase();
  const p = url.pathname;
  const q = url.searchParams;
  const n = (k: string) => (q.get(k) ? Number(q.get(k)) : undefined);
  if (p === "/api/me" && method === "GET") return json({ user: signedIn ? me0 : null, csrf: "mock-csrf", uploads_left_today: uploadsLeft, app_linked: !unlinked() } satisfies Me);
  if (method !== "GET" && new Headers(init.headers).get("x-csrf-token") !== "mock-csrf") return json({ error: "csrf", detail: "missing CSRF token" }, 403);
  if (p === "/api/me" && method === "DELETE") return (signedIn = false), json(null, 204);
  if (p === "/auth/logout") return (signedIn = false), json(null, 204);
  if (p === "/api/me/pods") {
    if (!signedIn) return json({ error: "auth", detail: "Please sign in." }, 401);
    return json({ items: pods });
  }
  const pm = /^\/api\/me\/pods\/([\w-]+)$/.exec(p);
  if (pm && method === "PATCH") {
    if (!signedIn) return json({ error: "auth", detail: "Please sign in." }, 401);
    const pod = pods.find((x) => x.id === pm[1]);
    if (!pod) return json({ error: "not_found", detail: "No such pod." }, 404);
    const r = checkPodName(String((JSON.parse(String(init.body ?? "{}")) as { name?: unknown }).name ?? ""));
    if (r.error !== null) return json({ error: "invalid_name", detail: r.error }, 400);
    pod.name = r.name;
    return json(pod);
  }
  if (p === "/api/me/dashboard" || p.startsWith("/api/me/sessions")) {
    if (!signedIn) return json({ error: "auth", detail: "Please sign in." }, 401);
    if (p === "/api/me/dashboard") return json(dashboard());
    if (p === "/api/me/sessions") {
      const all = mySessions().map(summaryOf);
      const [t, , id = ""] = /^([^~]*)(~(.*))?$/.exec(q.get("before") ?? "")?.slice(1) ?? [];
      const older = q.get("before") ? all.filter((x) => x.started_at < t || (x.started_at === t && id !== "" && x.id < id)) : all;
      const lim = n("limit") ?? 20;
      const items = older.slice(0, lim);
      const last = items[items.length - 1];
      return json({ items, next_before: older.length > lim ? `${last.started_at}~${last.id}` : null });
    }
    const sm = /^\/api\/me\/sessions\/([\w.-]+)$/.exec(p);
    const x = sm && mySessions().find((y) => y.id === sm[1]);
    return x ? json(x) : json({ error: "not_found", detail: "No such session." }, 404);
  }
  if (p === "/api/seasons") return json(seasons);
  if (p === "/api/ladder") {
    const items = ladderItems();
    const off = n("offset") ?? 0;
    const lim = n("limit") ?? 50;
    const lad: LadderName = q.get("ladder") === "twins" ? "twins" : "open";
    const xs = items.filter((it) => "house" in it.model.owner || (it.model.ladder ?? "open") === lad);
    return json({ season: seasons.find((s) => s.id === (n("season") ?? 2)) ?? seasons[1], ladder: lad, total: xs.length, items: xs.slice(off, off + lim).map((it, i) => ({ ...it, rank: off + i + 1 })) });
  }
  if (p === "/api/models" && method === "GET" && q.get("owner") === "me") {
    if (!signedIn) return json({ error: "auth", detail: "Please sign in." }, 401);
    return json({ items: models.filter((m) => !("house" in m.owner) && m.owner.id === me0.id).map(summary).reverse() });
  }
  if (p === "/api/models" && method === "POST") {
    if (!signedIn) return json({ error: "auth", detail: "Please sign in." }, 401);
    if (uploadsLeft <= 0) return json({ error: "rate_limited", detail: "Daily upload limit reached." }, 429, { "retry-after": "3600" });
    const f = (init.body as FormData).get("file") as File;
    uploadsLeft--;
    const m = addModel(f.name.replace(/\.safetensors$/i, ""), me0, pick(BUILDS), "pending");
    myPending.set(m.id, { polls: 0, bad: /bad|broken/i.test(f.name) });
    return json({ id: m.id, status: "pending" }, 202);
  }
  let mm = /^\/api\/models\/(\d+)$/.exec(p);
  if (mm) {
    const m = models[Number(mm[1]) - 1];
    if (!m) return json({ error: "not_found", detail: "No such model." }, 404);
    if (method === "DELETE") {
      m.status = "retired";
      m.retired_at = iso(0);
      return json(null, 204);
    }
    const pend = myPending.get(m.id);
    if (pend && ++pend.polls >= 2) {
      myPending.delete(m.id);
      if (pend.bad) {
        m.status = "rejected";
        m.reject_reasons = ["build must sum to 24 (got 25)", "name must be 1-40 characters"];
      } else {
        for (const o of models) if (o !== m && o.status === "active" && !("house" in o.owner) && o.owner.id === me0.id) o.status = "retired";
        m.status = "active";
        m.rating = 1200;
        m.games = 0;
      }
    }
    return json(m);
  }
  if (p === "/api/matches") {
    const model = n("model");
    let xs = matches.filter((m) => !model || m.a.id === model || m.b.id === model);
    if (q.get("kind")) xs = xs.filter((m) => m.kind === q.get("kind"));
    const before = n("before");
    if (before) xs = xs.filter((m) => m.id < before);
    xs = [...xs].sort((x, y) => y.id - x.id);
    const lim = n("limit") ?? 20;
    const items = xs.slice(0, lim).map(({ seed: _s, rallies: _r, ...rest }) => rest);
    return json({ items, next_before: xs.length > lim ? items[items.length - 1].id : null });
  }
  mm = /^\/api\/matches\/(\d+)$/.exec(p);
  if (mm) {
    const m = matches.find((x) => x.id === Number(mm![1]));
    return m ? json(m) : json({ error: "not_found", detail: "No such match." }, 404);
  }
  if (p === "/api/tournaments/current") return q.get("ladder") === "twins" ? json({ error: "not_found", detail: "No tournament is scheduled yet." }, 404) : json(tournaments[0]);
  if (p === "/api/tournaments") return json(q.get("ladder") === "twins" ? [] : tournaments.map(({ rounds: _r, ...t }) => t));
  mm = /^\/api\/tournaments\/(\d+)$/.exec(p);
  if (mm) {
    const t = tournaments.find((x) => x.id === Number(mm![1]));
    return t ? json(t) : json({ error: "not_found", detail: "No such tournament." }, 404);
  }
  return json({ error: "not_found", detail: `mock: no route for ${method} ${p}` }, 404);
}

export async function installMock() {
  const real = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.href);
    if (url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/auth/")) && !url.pathname.endsWith("replay.bin")) {
      return handle(url, init);
    }
    return real(input, init);
  };
  // GitHub sign-in is a full-page redirect: fake it
  document.addEventListener("click", (e) => {
    const a = (e.target as Element).closest?.("a[href^='/auth/github/login']");
    if (!a) return;
    e.preventDefault();
    signedIn = true;
    location.href = "/arena/my-models";
  });
  try {
    const f = new URLSearchParams(location.search);
    if (f.has("unlinked")) sessionStorage.setItem("oso-mock-unlinked", "1");
    if (f.has("linked")) sessionStorage.removeItem("oso-mock-unlinked");
  } catch {
    // storage blocked: stay linked
  }
  console.info("[oso] mock API installed (npm run dev:mock)");
}
