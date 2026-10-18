import { api, ApiError } from "../api";
import { moreLink, buildBars, fmtDate, fmtRating, load, matchRow, modelLink, ownerChip, provisional } from "../components";
import { append, h, link } from "../dom";
import type { LadderName, Tournament, TournamentSummary } from "../types";
import { ladderToggle } from "../twins";

export function arenaHome(root: HTMLElement) {
  const featured = h("section", { class: "card featured", "aria-labelledby": "feat-h" });
  const top = h("div");
  const latest = h("div");
  // Top 10 with an Open | Twins switch that swaps the list in place (no navigation)
  const topHead = h("div", { class: "section-head" }, h("h2", null, "Top 10"));
  const topMore = h("div");
  const showTop = (lad: LadderName) => {
    const sw = ladderToggle(lad, (l) => (l === "twins" ? "/arena/twins" : "/arena/ladder"), "Top 10");
    for (const a of sw.querySelectorAll("a")) {
      a.removeAttribute("data-nav");
      a.addEventListener("click", (e) => (e.preventDefault(), showTop(a.textContent === "Twins" ? "twins" : "open")));
    }
    topHead.replaceChildren(h("h2", null, "Top 10"), sw);
    topMore.replaceChildren(moreLink(lad === "twins" ? "/arena/twins" : "/arena/ladder", lad === "twins" ? "Full Twins ladder" : "Full ladder"));
    top.replaceChildren();
    void loadTop(lad);
  };
  root.append(
    h("div", { class: "page-head" }, h("h1", null, "Arena"), link("/arena/get-started", h("span", { class: "btn primary" }, "Train your own"))),
    featured,
    h("div", { class: "grid2" }, h("section", null, topHead, top, topMore), h("section", null, h("div", { class: "section-head" }, h("h2", null, "Latest matches")), latest)),
  );

  void load(featured, featuredTournament, (t) => renderFeatured(featured, t));
  const loadTop = (lad: LadderName) => load(top, () => api.ladder(undefined, 0, 10, lad), (l) => {
    if (!l.items.length || (lad === "twins" && l.items.every((it) => "house" in it.model.owner)))
      return top.append(h("p", { class: "muted" }, lad === "twins" ? "No twins yet." : "No rated models yet. Be the first."));
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
  showTop("open");
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
      h("p", { class: "muted" }, "Sundays at 18:00. The top 8 with at least 20 games."),
    );
    return;
  }
  const final = t.rounds[t.rounds.length - 1]?.[0];
  const champ = final?.winner ? (final.winner === "a" ? final.a : final.b) : null;
  append(el, [
    h("div", { class: "feat-head" }, h("h2", { id: "feat-h" }, t.status === "done" ? "Latest tournament" : "Tournament in progress"), h("span", { class: "muted small" }, fmtDate(t.starts_at))),
    h("h3", null, final?.winner ? "Final" : "Final, not played yet"),
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
