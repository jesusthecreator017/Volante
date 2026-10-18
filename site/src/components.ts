// Shared UI pieces. Everything goes through h()/s(): user strings are text nodes.

import { ApiError, api, loginUrl } from "./api";
import { STATS, STAT_LABEL, STAT_SHORT, suggestStyle } from "./arena";
import { h, link, replace, s } from "./dom";
import { isHouse, type Build, type Me, type MatchSummary, type ModelStatus, type ModelSummary, type Owner, type Side } from "./types";
import { icon } from "./icons";

export const fmtRating = (r: number | null | undefined) => (r == null ? "—" : r.toFixed(0));
export const fmtDelta = (d: number) => `${d > 0 ? "+" : ""}${d.toFixed(1)}`;

export function fmtDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return withTime
    ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return fmtDate(iso, false);
}

export function ownerChip(o: Owner): HTMLElement {
  if (isHouse(o)) return h("span", { class: "owner house" }, icon("house-fill", { cls: "house-badge" }), "House");
  return h(
    "span",
    { class: "owner" },
    h("img", { class: "avatar", src: o.avatar_url, alt: "", width: 20, height: 20, loading: "lazy", referrerpolicy: "no-referrer" }),
    o.login,
  );
}

export const modelLink = (m: Pick<ModelSummary, "id" | "name">) => link(`/arena/models/${m.id}`, m.name);

/** An internal link styled as an action (accent colour, no underline until hover). */
export const actionLink = (href: string, ...children: (Node | string)[]) => h("a", { href, "data-nav": true, class: "act" }, ...children);
/** "Full ladder ->" style link: text plus a trailing arrow icon. */
export const moreLink = (href: string, text: string) => h("a", { href, "data-nav": true, class: "act more" }, text, icon("arrow-right"));

/** Game scores as compact "set" chips. */
export const setChips = (games: [number, number][]) => h("span", { class: "sets" }, games.map((g) => h("span", { class: "set" }, `${g[0]}–${g[1]}`)));

export function statusPill(st: ModelStatus | MatchSummary["status"]): HTMLElement {
  return h("span", { class: `pill pill-${st}` }, st);
}


/** Four small bars, compact enough for a table cell; the numbers stay readable as text. */
export function buildBars(b: Build, compact = true): HTMLElement {
  return h(
    "span",
    { class: compact ? "build compact" : "build", role: "img", "aria-label": STATS.map((k) => `${STAT_LABEL[k]} ${b[k]}`).join(", ") },
    STATS.map((k) =>
      h(
        "span",
        { class: "build-stat" },
        h("span", { class: "build-key" }, STAT_SHORT[k]),
        h("span", { class: "build-track" }, h("span", { class: `build-fill v${b[k]}` })),
        compact ? null : h("span", { class: "build-num" }, b[k]),
      ),
    ),
  );
}

/** Radar of the four stats (1-10). */
export function buildRadar(b: Build): SVGSVGElement {
  const R = 70;
  const c = 90;
  const pt = (i: number, v: number) => {
    const a = -Math.PI / 2 + (i * Math.PI) / 2;
    return [c + Math.cos(a) * R * (v / 10), c + Math.sin(a) * R * (v / 10)];
  };
  const ring = (v: number) => STATS.map((_, i) => pt(i, v).join(",")).join(" ");
  return s(
    "svg",
    { viewBox: "-16 -6 212 192", class: "radar", role: "img", "aria-label": `Build: ${STATS.map((k) => `${STAT_LABEL[k]} ${b[k]}`).join(", ")}` },
    [2.5, 5, 7.5, 10].map((v) => s("polygon", { points: ring(v), class: "radar-grid" })),
    STATS.map((_, i) => s("line", { x1: c, y1: c, x2: pt(i, 10)[0], y2: pt(i, 10)[1], class: "radar-grid" })),
    s("polygon", { points: STATS.map((k, i) => pt(i, b[k]).join(",")).join(" "), class: "radar-shape" }),
    STATS.map((k, i) => {
      const [x, y] = pt(i, 12.4);
      return s("text", { x, y, class: "radar-label", "text-anchor": "middle", "dominant-baseline": "middle" }, `${STAT_SHORT[k]} ${b[k]}`);
    }),
  );
}

export function styleGuess(b: Build): HTMLElement {
  return h("span", { class: "style-guess", title: "Nearest play-style preset to this build. A guess: the server stores no style." }, `${suggestStyle(b)}?`);
}

/** Rating over time: a line with the 1200 start as a reference. */
export function ratingChart(points: { t: string; rating: number }[]): SVGSVGElement | HTMLElement {
  if (points.length < 2) return h("p", { class: "muted" }, "Not enough games for a chart yet.");
  const W = 600;
  const H = 180;
  const pad = { l: 40, r: 10, t: 10, b: 24 };
  const ts = points.map((p) => Date.parse(p.t));
  const rs = points.map((p) => p.rating);
  const lo = Math.floor(Math.min(1200, ...rs) / 50) * 50;
  const hi = Math.ceil(Math.max(1200, ...rs) / 50) * 50;
  const x = (t: number) => pad.l + ((t - ts[0]) / Math.max(1, ts[ts.length - 1] - ts[0])) * (W - pad.l - pad.r);
  const y = (r: number) => pad.t + (1 - (r - lo) / Math.max(1, hi - lo)) * (H - pad.t - pad.b);
  const ticks = [lo, (lo + hi) / 2, hi];
  return s(
    "svg",
    { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `Rating from ${rs[0].toFixed(0)} to ${rs[rs.length - 1].toFixed(0)} over ${points.length} games` },
    ticks.map((v) => [
      s("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "chart-grid" }),
      s("text", { x: pad.l - 6, y: y(v), class: "chart-tick", "text-anchor": "end", "dominant-baseline": "middle" }, v.toFixed(0)),
    ]),
    s("line", { x1: pad.l, x2: W - pad.r, y1: y(1200), y2: y(1200), class: "chart-ref" }),
    s("polyline", { points: points.map((p, i) => `${x(ts[i])},${y(p.rating)}`).join(" "), class: "chart-line" }),
    s("circle", { cx: x(ts[ts.length - 1]), cy: y(rs[rs.length - 1]), r: 3.5, class: "chart-dot" }),
    s("text", { x: pad.l, y: H - 6, class: "chart-tick" }, fmtDate(points[0].t, false)),
    s("text", { x: W - pad.r, y: H - 6, class: "chart-tick", "text-anchor": "end" }, fmtDate(points[points.length - 1].t, false)),
  );
}

const sideName = (m: MatchSummary, side: Side) => (side === "a" ? m.a : m.b);

/** One match as a compact row: players, games, result, watch link. */
export function matchRow(m: MatchSummary, perspective?: number): HTMLElement {
  const player = (side: Side) => {
    const p = sideName(m, side);
    const won = m.winner === side;
    return h(
      "span",
      { class: won ? "mr-player won" : "mr-player" },
      modelLink(p),
      won && perspective === undefined ? h("span", { class: "wchip", title: "Winner" }, "W") : null,
      m.rating_delta ? h("span", { class: "delta" }, fmtDelta(m.rating_delta[side])) : null,
    );
  };
  let result: HTMLElement | null = null;
  if (perspective !== undefined && m.winner) {
    const mine: Side = m.a.id === perspective ? "a" : "b";
    result = h("span", { class: m.winner === mine ? "result win" : "result loss", title: m.winner === mine ? "Win" : "Loss" }, m.winner === mine ? "W" : "L");
  }
  return h(
    "li",
    { class: "match-row" },
    result,
    h("span", { class: "mr-players" }, player("a"), h("span", { class: "vs" }, "vs"), player("b")),
    h("span", { class: "mr-score" }, m.status === "done" ? [h("span", { class: "sets-won" }, `${m.games_won[0]}–${m.games_won[1]}`), setChips(m.games)] : statusPill(m.status)),
    h("span", { class: "mr-meta" }, h("span", { class: `kind kind-${m.kind}` }, m.kind), " ", fmtAgo(m.finished_at ?? m.created_at)),
    actionLink(`/arena/matches/${m.id}`, m.status === "done" ? "Watch" : "Open"),
  );
}

export function loading(label = "Loading…"): HTMLElement {
  return h("p", { class: "loading", role: "status" }, label);
}

export function errorBox(e: unknown, retry?: () => void): HTMLElement {
  const msg = e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e);
  const offline = e instanceof TypeError; // fetch network failure
  return h(
    "div",
    { class: "error-box", role: "alert" },
    h("p", null, offline ? "Can't reach the server. Check your connection." : msg),
    retry ? h("button", { type: "button", class: "btn", onclick: retry }, "Try again") : null,
  );
}

export function empty(text: string, ...extra: (Node | string)[]): HTMLElement {
  return h("div", { class: "empty" }, h("p", null, text), ...extra);
}

/** Runs a loader, shows loading/error states in `slot`, then calls render. */
export async function load<T>(slot: HTMLElement, f: () => Promise<T>, render: (v: T) => void) {
  slot.replaceChildren(loading());
  try {
    const v = await f();
    slot.replaceChildren();
    render(v);
  } catch (e) {
    slot.replaceChildren(errorBox(e, () => void load(slot, f, render)));
  }
}

/** The header's account slot: Sign in, or the avatar menu (Dashboard when the account is linked to the Volante app or has sessions: the server's app_linked). */
export function renderAccount(el: HTMLElement, m: Me | null) {
  if (!m?.user) {
    replace(el, h("a", { href: loginUrl(), class: "btn small with-icon signin", "aria-label": "Sign in with GitHub" }, icon("github-logo"), h("span", { class: "signin-label" }, "Sign in")));
    return;
  }
  const u = m.user;
  const menu = h(
    "div",
    { class: "menu", role: "menu", hidden: true },
    m.app_linked ? link("/account/dashboard", "Dashboard") : null,
    link("/settings", "Settings"),
    link("/arena/get-started#mine", "My models"),
    h("button", { type: "button", role: "menuitem", onclick: () => void signOut() }, icon("sign-out"), "Sign out"),
  );
  const btn = h(
    "button",
    { type: "button", class: "account-btn", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Account menu for ${u.login}` },
    h("img", { class: "avatar", src: u.avatar_url, alt: "", width: 28, height: 28, referrerpolicy: "no-referrer" }),
    h("span", { class: "login" }, u.login),
  );
  const toggle = (open = menu.hidden) => {
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", (e) => (e.stopPropagation(), toggle()));
  document.addEventListener("click", () => toggle(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && toggle(false));
  replace(el, btn, menu);
}

async function signOut() {
  try {
    await api.logout();
  } finally {
    location.href = "/";
  }
}
