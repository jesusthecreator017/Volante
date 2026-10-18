import { api, ApiError } from "../api";
import { reveal } from "../anim";
import { moreLink, fmtDate, fmtRating, load, matchRow, modelLink, ownerChip } from "../components";
import { icon } from "../icons";
import { append, h, link } from "../dom";
import type { LadderName, Tournament, TournamentSummary } from "../types";
import { ladderToggle } from "../twins";

export function arenaHome(root: HTMLElement) {
  const featured = h("section", { class: "card glass featured reveal", "aria-labelledby": "feat-h" });
  const top = h("div");
  const latest = h("div");
  // Top 10 with an Open | Twins switch that swaps the list in place (no navigation)
  const topHead = h("div", { class: "section-head" });
  const topMore = h("div", { class: "list-foot" });
  const showTop = (lad: LadderName) => {
    const sw = ladderToggle(lad, (l) => (l === "twins" ? "/arena/ladder?ladder=twins" : "/arena/ladder"), "Top 10");
    for (const a of sw.querySelectorAll("a")) {
      a.removeAttribute("data-nav");
      a.addEventListener("click", (e) => (e.preventDefault(), showTop(a.textContent === "Twins" ? "twins" : "open")));
    }
    topHead.replaceChildren(h("h2", null, icon("ranking"), "Top 10"), sw);
    topMore.replaceChildren(moreLink(lad === "twins" ? "/arena/ladder?ladder=twins" : "/arena/ladder", lad === "twins" ? "Full Twins ladder" : "Full ladder"));
    top.replaceChildren();
    void loadTop(lad);
  };
  root.append(
    h(
      "header",
      { class: "page-hero arena-hero" },
      h("div", null, h("h1", null, "Arena"), h("p", { class: "lead" }, "AI badminton players, trained by the community with our free kit, play ranked matches around the clock. Open any match to watch it in 3D.")),
      h("div", { class: "row" }, link("/arena/get-started", h("span", { class: "btn primary" }, icon("upload-simple"), "Train your own")), link("/arena/ladder", h("span", { class: "btn" }, "Ladder"))),
    ),
    featured,
    h(
      "div",
      { class: "grid2" },
      h("section", { class: "card glass list-card reveal d1" }, topHead, top, topMore),
      h("section", { class: "card glass list-card reveal d2" }, h("div", { class: "section-head" }, h("h2", null, icon("play-fill"), "Latest matches")), latest),
    ),
  );

  void load(featured, featuredTournament, (t) => renderFeatured(featured, t));
  const loadTop = (lad: LadderName) => load(top, () => api.ladder(undefined, 0, 10, lad), (l) => {
    if (!l.items.length || (lad === "twins" && l.items.every((it) => "house" in it.model.owner)))
      return top.append(h("p", { class: "muted empty-row" }, lad === "twins" ? "No twins yet." : "No rated models yet. Be the first."));
    top.append(
      h(
        "ul",
        { class: "match-list" },
        l.items.map((it) =>
          h(
            "li",
            { class: it.rank <= 3 ? "rank-row podium" : "rank-row" },
            h("span", { class: "rank-badge" }, it.rank),
            h("span", { class: "rr-name" }, modelLink(it.model), ownerChip(it.model.owner)),
            h("span", { class: "rr-rating" }, h("span", { class: "rating" }, fmtRating(it.rating)), h("span", { class: "mr-meta" }, `${it.wins}–${it.losses} · ${it.games} games`)),
          ),
        ),
      ),
    );
  });
  showTop("open");
  void load(latest, () => api.matches({ limit: 10 }), (p) => {
    latest.append(p.items.length ? h("ul", { class: "match-list" }, p.items.map((m) => matchRow(m))) : h("p", { class: "muted empty-row" }, "No matches yet."));
  });
  return reveal(root);
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
      h("div", { class: "feat-head" }, h("h2", { id: "feat-h" }, icon("trophy-fill"), "Weekly tournament"), h("span", { class: "badge" }, "Sundays 18:00")),
      h("p", { class: "muted" }, "The top 8 models with at least 20 games play a best-of-3 knockout. Ratings don't change, and house players sit it out."),
    );
    return;
  }
  const final = t.rounds[t.rounds.length - 1]?.[0];
  const champ = final?.winner ? (final.winner === "a" ? final.a : final.b) : null;
  append(el, [
    h("div", { class: "feat-head" }, h("h2", { id: "feat-h" }, icon("trophy-fill"), t.status === "done" ? "Latest tournament" : "Tournament in progress"), h("span", { class: "muted small" }, fmtDate(t.starts_at))),
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
