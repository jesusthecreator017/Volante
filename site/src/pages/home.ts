import { api, ApiError } from "../api";
import { buildBars, fmtDate, fmtRating, load, matchRow, modelLink, ownerChip, provisional } from "../components";
import { append, h, link } from "../dom";
import type { Tournament, TournamentSummary } from "../types";

export function home(root: HTMLElement) {
  const featured = h("section", { class: "card featured", "aria-labelledby": "feat-h" });
  const top = h("div");
  const latest = h("div");
  root.append(
    h(
      "section",
      { class: "hero" },
      h("h1", null, "Train a badminton AI. Watch it play."),
      h(
        "p",
        { class: "lead" },
        "Start from our base model, pick a build and a play style, and train your own neural-network player. Upload it and it plays the ladder around the clock, plus a tournament every Sunday, all watchable in 3D.",
      ),
      h("div", { class: "row" }, link("/get-started", h("span", { class: "btn primary" }, "Train your own player")), link("/ladder", h("span", { class: "btn" }, "See the ladder"))),
    ),
    featured,
    h("div", { class: "grid2" }, h("section", null, h("h2", null, "Top 10"), top, link("/ladder", "Full ladder →")), h("section", null, h("h2", null, "Latest matches"), latest)),
  );

  void load(featured, featuredTournament, (t) => renderFeatured(featured, t));
  void load(top, () => api.ladder(undefined, 0, 10), (l) => {
    if (!l.items.length) return top.append(h("p", { class: "muted" }, "No rated models yet. Be the first."));
    top.append(
      h(
        "ol",
        { class: "top10" },
        l.items.map((it) =>
          h(
            "li",
            null,
            h("span", { class: "rank" }, it.rank),
            h("span", { class: "t10-name" }, modelLink(it.model), ownerChip(it.model.owner)),
            buildBars(it.model.build),
            h("span", { class: "rating" }, fmtRating(it.rating), provisional(it.games)),
          ),
        ),
      ),
    );
  });
  void load(latest, () => api.matches({ limit: 8 }), (p) => {
    latest.append(p.items.length ? h("ul", { class: "match-list" }, p.items.map((m) => matchRow(m))) : h("p", { class: "muted" }, "No matches yet."));
  });
}

/** The current tournament if it has a final, else the latest finished one. */
async function featuredTournament(): Promise<Tournament | null> {
  try {
    const cur = await api.currentTournament();
    if (cur.status !== "scheduled") return cur;
  } catch (e) {
    if (!(e instanceof ApiError && e.status === 404)) throw e;
  }
  const all: TournamentSummary[] = await api.tournaments();
  const done = all.filter((t) => t.status === "done").sort((a, b) => b.starts_at.localeCompare(a.starts_at))[0];
  return done ? api.tournament(done.id) : null;
}

function renderFeatured(el: HTMLElement, t: Tournament | null) {
  if (!t) {
    el.append(
      h("h2", { id: "feat-h" }, "Weekly tournament"),
      h("p", null, "Every Sunday at 18:00 the top 8 models with at least 20 games play a single-elimination bracket. The first one hasn't happened yet."),
    );
    return;
  }
  const final = t.rounds[t.rounds.length - 1]?.[0];
  const champ = final?.winner ? (final.winner === "a" ? final.a : final.b) : null;
  append(el, [
    h("h2", { id: "feat-h" }, t.status === "done" ? "Latest tournament final" : "Tournament in progress"),
    h("p", { class: "muted" }, fmtDate(t.starts_at)),
    h("h3", null, final?.winner ? "Final" : "Final (not played yet)"),
    final?.a && final?.b
      ? h(
          "div",
          { class: "final" },
          h("span", { class: final.winner === "a" ? "won" : "" }, modelLink(final.a)),
          h("span", { class: "vs" }, "vs"),
          h("span", { class: final.winner === "b" ? "won" : "" }, modelLink(final.b)),
        )
      : h("p", null, "The final isn't set yet."),
    champ ? h("p", null, "Champion: ", h("strong", null, champ.name), " ", ownerChip(champ.owner)) : null,
    h(
      "div",
      { class: "row" },
      final?.match_id ? link(`/matches/${final.match_id}`, h("span", { class: "btn primary" }, "Watch the final")) : null,
      link(`/tournaments/${t.id}`, h("span", { class: "btn" }, "Bracket")),
    ),
  ]);
}
