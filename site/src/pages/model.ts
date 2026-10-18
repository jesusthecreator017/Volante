import { api } from "../api";
import { STATS, STAT_EFFECT, STAT_LABEL } from "../arena";
import { buildRadar, fmtDate, fmtRating, load, matchRow, ownerChip, ratingChart, statusPill, styleGuess } from "../components";
import { append, h, link } from "../dom";
import type { MatchSummary, ModelDetail } from "../types";
import { mountViewer } from "../viewer";
import { twinCard } from "../twins";

export function model(root: HTMLElement, p: Record<string, string>) {
  const id = Number(p.id);
  void load(root, () => api.model(id), (m) => render(root, m));
}

function render(root: HTMLElement, m: ModelDetail) {
  document.title = `${m.name} · Arena · Volante`;
  const matches = h("div");
  const latest = h("section");
  append(root, [
    h(
      "header",
      { class: "model-head" },
      h("h1", null, m.name),
      h("div", { class: "row" }, ownerChip(m.owner), statusPill(m.status), h("span", { class: "muted" }, `Season ${m.season_id}`)),
      m.description ? h("p", { class: "desc" }, m.description) : null,
    ),
    m.reject_reasons?.length
      ? h("div", { class: "error-box" }, h("p", null, "Rejected by the checker:"), h("ul", null, m.reject_reasons.map((r) => h("li", null, r))))
      : null,
    m.twin ? twinCard({ ...m, twin: m.twin }) : null,
    h(
      "div",
      { class: "grid2" },
      h(
        "section",
        { class: "card" },
        h("h2", null, "Build"),
        buildRadar(m.build),
        h("p", null, "Suggested style: ", styleGuess(m.build)),
        m.twin ? h("p", { class: "muted small" }, "Build derived from the player's profile.") : null,
        h(
          "dl",
          { class: "stats" },
          STATS.map((k) => [h("dt", null, `${STAT_LABEL[k]} ${m.build[k]}`), h("dd", { class: "muted" }, STAT_EFFECT[k])]),
        ),
      ),
      h(
        "section",
        { class: "card" },
        h("h2", null, m.ladder === "twins" ? "Twins rating" : "Rating"),
        h("p", { class: "big" }, fmtRating(m.rating), h("span", { class: "muted small" }, ` after ${m.games} game${m.games === 1 ? "" : "s"}${m.games < 20 ? " (provisional)" : ""}`)),
        ratingChart(m.rating_history),
        h("p", { class: "muted small" }, `Uploaded ${fmtDate(m.created_at)}`, m.retired_at ? ` · retired ${fmtDate(m.retired_at)}` : ""),
      ),
    ),
    latest,
    h("section", null, h("h2", null, "Recent matches"), matches),
  ]);
  void showLatest(latest, m.id);
  loadMatches(matches, m.id);
}

/** The model's most recent finished match, inline (nothing when it has none or the lookup fails). */
export async function showLatest(slot: HTMLElement, modelId: number) {
  const m = await api.matches({ model: modelId, limit: 10 }).then((p) => p.items.find((x) => x.status === "done"), () => undefined);
  if (!m) return;
  const box = h("div", { class: "viewer" });
  slot.append(
    h("h2", null, "Latest match"),
    h("p", { class: "muted small" }, `${m.a.name} ${m.games_won[0]}–${m.games_won[1]} ${m.b.name} · `, link(`/arena/matches/${m.id}`, "Open match")),
    box,
  );
  mountViewer(box, m);
}

function loadMatches(el: HTMLElement, id: number, before?: number, list?: HTMLUListElement) {
  const slot = h("div");
  el.append(slot);
  void load(slot, () => api.matches({ model: id, before, limit: 20 }), (page) => {
    const ul = list ?? h("ul", { class: "match-list" });
    if (!list) slot.append(page.items.length ? ul : h("p", { class: "muted" }, "No matches yet. New models play 10 placement matches first."));
    page.items.forEach((mm: MatchSummary) => ul.append(matchRow(mm, id)));
    if (page.next_before) {
      const more = h("button", { type: "button", class: "btn", onclick: () => (more.remove(), loadMatches(el, id, page.next_before!, ul)) }, "Older matches");
      el.append(more);
    }
  });
}
