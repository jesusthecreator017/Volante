// Dev-only fake of the Pi agent's API (`npm run dev:mock`), so every page can be built and checked before the server
// exists. Deterministic fixtures; same shapes as src/types.ts. Never included in the production build (__MOCK__).

import type { LadderItem, MatchDetail, Me, ModelDetail, ModelSummary, Rally, Season, Side, Tie, Tournament } from "./types";

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
  matches.push(makeMatch(500 - i, a, b, pick(["ladder", "ladder", "ladder", "placement"] as const), i * 17 + 2));
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

function makeTournament(id: number, daysAgo: number, finished: boolean): Tournament {
  const top = ladderItems().filter((x) => x.games >= 20 && !("house" in x.model.owner)).slice(0, 8).map((x) => models[x.model.id - 1]);
  const order = [[0, 7], [3, 4], [2, 5], [1, 6]];
  let round: Tie[] = order.map(([i, j]) => ({ seed_a: i + 1, seed_b: j + 1, a: summary(top[i]), b: summary(top[j]), match_id: null, winner: null }));
  const rounds: Tie[][] = [];
  for (let r = 0; r < 3; r++) {
    const live = !finished && r === 2;
    round = round.map((t) => {
      if (live || !t.a || !t.b) return t;
      const m = makeMatch(900 + id * 10 + matches.length, models[t.a.id - 1], models[t.b.id - 1], "tournament", daysAgo * 1440 + 60 - r * 10);
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
  if (p === "/api/me" && method === "GET") return json({ user: signedIn ? me0 : null, csrf: "mock-csrf", uploads_left_today: uploadsLeft } satisfies Me);
  if (method !== "GET" && new Headers(init.headers).get("x-csrf-token") !== "mock-csrf") return json({ error: "csrf", detail: "missing CSRF token" }, 403);
  if (p === "/api/me" && method === "DELETE") return (signedIn = false), json(null, 204);
  if (p === "/auth/logout") return (signedIn = false), json(null, 204);
  if (p === "/api/seasons") return json(seasons);
  if (p === "/api/ladder") {
    const items = ladderItems();
    const off = n("offset") ?? 0;
    const lim = n("limit") ?? 50;
    return json({ season: seasons.find((s) => s.id === (n("season") ?? 2)) ?? seasons[1], total: items.length, items: items.slice(off, off + lim) });
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
  if (p === "/api/tournaments/current") return json(tournaments[0]);
  if (p === "/api/tournaments") return json(tournaments.map(({ rounds: _r, ...t }) => t));
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
    location.href = "/my-models";
  });
  console.info("[oso] mock API installed (npm run dev:mock)");
}
