import { api, ApiError } from "../api";
import { fmtDate, load, ownerChip } from "../components";
import { h, link } from "../dom";
import type { Tie, Tournament, TournamentSummary } from "../types";

const ROUND = ["Quarterfinals", "Semifinals", "Final"];

export function tournaments(root: HTMLElement, _p: unknown, q: URLSearchParams) {
  const season = q.get("season") ? Number(q.get("season")) : undefined;
  const current = h("div");
  const past = h("div");
  root.append(
    h("h1", null, "Tournaments"),
    h("p", { class: "lead" }, "Every Sunday at 18:00 (server time) the top 8 by rating with at least 20 games play single elimination, one best-of-3 per tie. Tournament matches don't change ratings."),
    current,
    h("h2", null, "Past tournaments"),
    past,
  );
  void load(current, async () => {
    try {
      return await api.currentTournament();
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }, (t) => {
    if (t) current.append(h("section", { class: "card" }, h("h2", null, t.status === "done" ? "This week" : `This week · ${t.status}`), bracket(t)));
  });
  void load(past, () => api.tournaments(season), (list: TournamentSummary[]) => {
    const done = list.filter((t) => t.status === "done").sort((a, b) => b.starts_at.localeCompare(a.starts_at));
    past.append(
      done.length
        ? h("ul", { class: "plain" }, done.map((t) => h("li", null, link(`/tournaments/${t.id}`, `${fmtDate(t.starts_at, false)}`), h("span", { class: "muted small" }, ` · season ${t.season_id}`))))
        : h("p", { class: "muted" }, "None yet."),
    );
  });
}

export function tournament(root: HTMLElement, p: Record<string, string>) {
  void load(root, () => api.tournament(Number(p.id)), (t) => {
    root.append(h("h1", null, `Tournament · ${fmtDate(t.starts_at, false)}`), h("p", { class: "muted" }, `Season ${t.season_id} · ${t.status}`), bracket(t), link("/tournaments", "All tournaments →"));
  });
}

/** 8 → 4 → 2 → 1 as columns; each tie links to its match. */
export function bracket(t: Tournament): HTMLElement {
  const final = t.rounds[t.rounds.length - 1]?.[0];
  const champ = final?.winner ? (final.winner === "a" ? final.a : final.b) : null;
  return h(
    "div",
    { class: "bracket-wrap" },
    h(
      "ol",
      { class: "bracket", "aria-label": "Bracket" },
      t.rounds.map((round, i) =>
        h("li", { class: "round" }, h("h3", null, ROUND[i + (3 - t.rounds.length)] ?? `Round ${i + 1}`), h("ul", null, round.map(tie))),
      ),
      h("li", { class: "round champ" }, h("h3", null, "Champion"), champ ? h("div", { class: "tie" }, h("div", { class: "slot won" }, link(`/models/${champ.id}`, champ.name), ownerChip(champ.owner))) : h("p", { class: "muted" }, "TBD")),
    ),
  );
}

function tie(x: Tie): HTMLElement {
  const slot = (side: "a" | "b") => {
    const m = side === "a" ? x.a : x.b;
    const seed = side === "a" ? x.seed_a : x.seed_b;
    return h("div", { class: x.winner === side ? "slot won" : x.winner ? "slot lost" : "slot" }, h("span", { class: "seed" }, seed), m ? link(`/models/${m.id}`, m.name) : h("span", { class: "muted" }, "TBD"));
  };
  return h("li", { class: "tie" }, slot("a"), slot("b"), x.match_id ? link(`/matches/${x.match_id}`, x.winner ? "Watch" : "Match") : null);
}
