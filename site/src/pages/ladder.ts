import { api } from "../api";
import { buildBars, load, modelLink, ownerChip, provisional } from "../components";
import { h, replace } from "../dom";
import { navigate } from "../router";
import type { Ladder, LadderItem, Season } from "../types";
import { icon } from "../icons";

const PAGE = 50;
type SortKey = "rank" | "name" | "rating" | "games" | "wins";

export function ladder(root: HTMLElement, _p: unknown, q: URLSearchParams) {
  const season = q.get("season") ? Number(q.get("season")) : undefined;
  const offset = Math.max(0, Number(q.get("offset") ?? 0) || 0);
  let sort: SortKey = "rank";
  let asc = true;
  const picker = h("div", { class: "row" });
  const table = h("div");
  root.append(h("h1", null, "Ladder"), picker, table);

  void load(picker, api.seasons, (seasons) => renderPicker(picker, seasons, season));
  void load(table, () => api.ladder(season, offset, PAGE), (l) => draw(l));

  function draw(l: Ladder) {
    const rows = [...l.items].sort((a, b) => cmp(a, b, sort) * (asc ? 1 : -1));
    const th = (key: SortKey, label: string, cls = "") =>
      h(
        "th",
        { class: cls, "aria-sort": sort === key ? (asc ? "ascending" : "descending") : "none", scope: "col" },
        h("button", { type: "button", class: "sort", onclick: () => ((asc = sort === key ? !asc : key === "rank" || key === "name"), (sort = key), draw(l)) }, label,
          sort === key ? icon(asc ? "caret-up-bold" : "caret-down-bold", { cls: "sort-ico" }) : null),
      );
    replace(
      table,
      l.season.active ? null : h("p", { class: "note" }, "Past season: read-only."),
      l.items.length
        ? h(
            "div",
            { class: "table-wrap sticky" },
            h(
              "table",
              { class: "ladder" },
              h("caption", { class: "sr-only" }, `Ladder, season ${l.season.id}. Sorting applies to this page.`),
              h(
                "thead",
                null,
                h("tr", null, th("rank", "#", "num"), th("name", "Model"), h("th", { scope: "col", class: "hide-sm" }, "Build"), th("rating", "Rating", "num"), th("games", "Games", "num hide-sm"), th("wins", "W–L", "num")),
              ),
              h(
                "tbody",
                null,
                rows.map((it) =>
                  h(
                    "tr",
                    { class: it.rank <= 3 ? "podium" : null },
                    h("td", { class: "num rank" }, it.rank),
                    h("td", null, h("div", { class: "cell-model" }, modelLink(it.model), ownerChip(it.model.owner))),
                    h("td", { class: "hide-sm" }, buildBars(it.model.build)),
                    h("td", { class: it.provisional ? "num rating provisional" : "num rating" }, it.rating.toFixed(0), it.provisional ? provisional(0) : null),
                    h("td", { class: "num hide-sm" }, it.games),
                    h("td", { class: "num" }, `${it.wins}–${it.losses}`),
                  ),
                ),
              ),
            ),
          )
        : h("p", { class: "muted" }, "No models on this ladder yet."),
      pager(l),
    );
  }

  function pager(l: Ladder) {
    if (l.total <= PAGE) return null;
    const go = (o: number) => () => navigate(`/arena/ladder?${new URLSearchParams({ ...(season ? { season: String(season) } : {}), offset: String(o) })}`);
    return h(
      "nav",
      { class: "pager", "aria-label": "Ladder pages" },
      h("button", { type: "button", class: "btn", disabled: offset === 0, onclick: go(Math.max(0, offset - PAGE)) }, icon("caret-left-bold"), "Previous"),
      h("span", { class: "muted" }, `${offset + 1}–${Math.min(l.total, offset + PAGE)} of ${l.total}`),
      h("button", { type: "button", class: "btn", disabled: offset + PAGE >= l.total, onclick: go(offset + PAGE) }, "Next", icon("caret-right-bold")),
    );
  }
}

function cmp(a: LadderItem, b: LadderItem, k: SortKey): number {
  switch (k) {
    case "name":
      return a.model.name.localeCompare(b.model.name);
    case "rating":
      return a.rating - b.rating;
    case "games":
      return a.games - b.games;
    case "wins":
      return a.wins - b.wins;
    default:
      return a.rank - b.rank;
  }
}

function renderPicker(el: HTMLElement, seasons: Season[], current?: number) {
  if (seasons.length < 2) return;
  const active = seasons.find((s) => s.active)?.id;
  const sel = h(
    "select",
    { id: "season", onchange: (e: Event) => navigate(`/arena/ladder?season=${(e.target as HTMLSelectElement).value}`) },
    seasons.map((s) => h("option", { value: s.id, selected: (current ?? active) === s.id }, `Season ${s.id}${s.active ? " (current)" : ""}`)),
  );
  el.append(h("label", { for: "season" }, "Season "), sel);
}
