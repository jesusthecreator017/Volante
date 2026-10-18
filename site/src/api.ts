// JSON API client (same origin, session cookie, CSRF header on state-changing requests).

import type { Dashboard, Ladder, LadderName, MatchDetail, MatchKind, MatchPage, Me, ModelDetail, ModelSummary, PlaySession, Pod, Season, SessionPage, Tournament, TournamentSummary } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly detail: string,
    /** seconds, from Retry-After on 429 */
    readonly retryAfter: number | null = null,
  ) {
    super(detail || code || `HTTP ${status}`);
  }
}

let csrf = "";
let meCache: Promise<Me> | null = null;

async function call<T>(method: string, path: string, body?: BodyInit): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (typeof body === "string") headers["content-type"] = "application/json";
  if (method !== "GET") headers["x-csrf-token"] = csrf || (await me()).csrf;
  const r = await fetch(path, { method, headers, body, credentials: "same-origin" });
  if (r.status === 204) return undefined as T;
  const text = await r.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    // non-JSON error page from a proxy
  }
  if (!r.ok) {
    const e = (json ?? {}) as { error?: string; detail?: string };
    const ra = r.headers.get("retry-after");
    throw new ApiError(r.status, e.error ?? "error", e.detail ?? defaultDetail(r.status), ra ? Number(ra) : null);
  }
  return json as T;
}

function defaultDetail(status: number): string {
  return (
    {
      400: "That request wasn't valid.",
      401: "Please sign in first.",
      403: "That isn't yours.",
      404: "Not found.",
      413: "That file is too large.",
      429: "Too many requests. Try again later.",
    }[status] ?? `Server error (${status}).`
  );
}

const qs = (p: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const q = s.toString();
  return q ? `?${q}` : "";
};

export function me(refresh = false): Promise<Me> {
  if (!meCache || refresh) {
    meCache = call<Me>("GET", "/api/me").then((m) => {
      csrf = m.csrf;
      return m;
    });
    meCache.catch(() => (meCache = null));
  }
  return meCache;
}

export const api = {
  me,
  seasons: () => call<Season[]>("GET", "/api/seasons"),
  ladder: (season?: number, offset = 0, limit = 50, ladder: LadderName = "open") =>
    call<Ladder>("GET", `/api/ladder${qs({ season, ladder: ladder === "twins" ? ladder : undefined, offset, limit })}`),
  model: (id: number) => call<ModelDetail>("GET", `/api/models/${id}`),
  matches: (p: { model?: number; kind?: MatchKind; before?: number; limit?: number } = {}) =>
    call<MatchPage>("GET", `/api/matches${qs({ ...p, limit: p.limit ?? 20 })}`),
  match: (id: number) => call<MatchDetail>("GET", `/api/matches/${id}`),
  currentTournament: (ladder: LadderName = "open") =>
    call<Tournament>("GET", `/api/tournaments/current${qs({ ladder: ladder === "twins" ? ladder : undefined })}`),
  tournaments: (season?: number, ladder: LadderName = "open") =>
    call<TournamentSummary[]>("GET", `/api/tournaments${qs({ season, ladder: ladder === "twins" ? ladder : undefined })}`),
  tournament: (id: number) => call<Tournament>("GET", `/api/tournaments/${id}`),
  uploadModel: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<{ id: number; status: "pending" }>("POST", "/api/models", form);
  },
  retireModel: (id: number) => call<void>("DELETE", `/api/models/${id}`),
  /** Requested from the Pi agent as an additive endpoint (site/README.md): the contract has no "my models" list. */
  myModels: () => call<{ items: ModelSummary[] }>("GET", "/api/models?owner=me").then((r) => r.items),
  /** Additive endpoints (site/README.md): the signed-in user's pods and the display-name rename. */
  pods: () => call<{ items: Pod[] }>("GET", "/api/me/pods").then((r) => r.items),
  renamePod: (id: string, name: string) => call<Pod>("PATCH", `/api/me/pods/${encodeURIComponent(id)}`, JSON.stringify({ name })),
  /** Account → Dashboard: sessions the app uploaded, newest first; `before` is a previous page's next_before. */
  mySessions: (before?: string | null, limit = 20) => call<SessionPage>("GET", `/api/me/sessions${qs({ before, limit })}`),
  /** One session with its report (null when the app sent none). */
  mySession: (id: string) => call<PlaySession>("GET", `/api/me/sessions/${encodeURIComponent(id)}`),
  myDashboard: () => call<Dashboard>("GET", "/api/me/dashboard"),
  deleteAccount: () => call<void>("DELETE", "/api/me"),
  logout: () => call<void>("POST", "/auth/logout"),
};

export const replayUrl = (matchId: number) => `/api/matches/${matchId}/replay.bin`;
export const loginUrl = (returnTo = location.pathname) => `/auth/github/login${qs({ next: returnTo })}`;
