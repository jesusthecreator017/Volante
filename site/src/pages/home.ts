import { api, ApiError } from "../api";
import { actionLink, buildBars, fmtDate, fmtRating, load, matchRow, modelLink, ownerChip, provisional } from "../components";
import { append, h, link, s } from "../dom";
import type { Tournament, TournamentSummary } from "../types";

export function arenaHome(root: HTMLElement) {
  const featured = h("section", { class: "card featured", "aria-labelledby": "feat-h" });
  const top = h("div");
  const latest = h("div");
  root.append(
    h(
      "section",
      { class: "hero" },
      court(),
      h("h1", null, "Train a badminton AI. Watch it play."),
      h(
        "p",
        { class: "lead" },
        "Start from our base model, pick a build and a play style, and train your own neural-network player. Upload it and it plays the ladder around the clock, plus a tournament every Sunday, all watchable in 3D.",
      ),
      h("div", { class: "row" }, link("/arena/get-started", h("span", { class: "btn primary" }, "Train your own player")), link("/arena/ladder", h("span", { class: "btn" }, "See the ladder"))),
    ),
    featured,
    h("div", { class: "grid2" }, h("section", null, h("h2", null, "Top 10"), top, actionLink("/arena/ladder", "Full ladder →")), h("section", null, h("h2", null, "Latest matches"), latest)),
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
            { class: it.rank <= 3 ? "podium" : null },
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
      final?.match_id ? link(`/arena/matches/${final.match_id}`, h("span", { class: "btn primary" }, "Watch the final")) : null,
      link(`/arena/tournaments/${t.id}`, h("span", { class: "btn" }, "Bracket")),
    ),
  ]);
}

/** Decorative top-down badminton court (half-court view): thin lines, themed by CSS classes. */
export function court(): SVGSVGElement {
  // 6.1 x 13.4 m court; 1 unit = 0.1 m. Singles 5.18 wide, doubles-long service line 0.76 m inside the back line.
  const line = (x1: number, y1: number, x2: number, y2: number, cls = "court-line") => s("line", { x1, y1, x2, y2, class: cls });
  return s(
    "svg",
    { viewBox: "-6 -6 73 146", class: "court", "aria-hidden": "true", focusable: "false" },
    s("rect", { x: 0, y: 0, width: 61, height: 134, class: "court-line" }),
    line(4.5, 0, 4.5, 134),
    line(56.5, 0, 56.5, 134),
    line(0, 7.6, 61, 7.6),
    line(0, 126.4, 61, 126.4),
    line(0, 46.9, 61, 46.9),
    line(0, 87.1, 61, 87.1),
    line(30.5, 46.9, 30.5, 0),
    line(30.5, 87.1, 30.5, 134),
    line(-4, 67, 65, 67, "court-net"),
  );
}
