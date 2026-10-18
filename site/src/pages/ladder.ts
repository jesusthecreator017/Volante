import { api } from "../api";
import { buildBars, load, modelLink, ownerChip, provisional } from "../components";
import { append, h, replace } from "../dom";
import { navigate } from "../router";
import type { Ladder, LadderItem, LadderName, Season } from "../types";
import { icon } from "../icons";
import { dataVolume, fidelityBadge, ladderOf, ladderToggle, TWINS_EMPTY, TWINS_INTRO } from "../twins";

/** The Twins ladder's own address (docs/twins-site.md); /arena/ladder?ladder=twins works too. */
export const twinsLadder = (root: HTMLElement, p: unknown, q: URLSearchParams) => ladder(root, p, new URLSearchParams([...q, ["ladder", "twins"]]));
const ladderPath = (l: LadderName) => (l === "twins" ? "/arena/twins" : "/arena/ladder");

const PAGE = 50;
type SortKey = "rank" | "name" | "rating" | "games" | "wins";

export function ladder(root: HTMLElement, _p: unknown, q: URLSearchParams) {
  const season = q.get("season") ? Number(q.get("season")) : undefined;
  const offset = Math.max(0, Number(q.get("offset") ?? 0) || 0);
  const lad = ladderOf(q);
  const twins = lad === "twins";
  if (twins) document.title = "Twins · Arena · Volante";
  let sort: SortKey = "rank";
  let asc = true;
  const picker = h("div", { class: "row" });
  const table = h("div");
  const keep = (l: LadderName) => ladderPath(l) + (season ? `?season=${season}` : "");
  append(root, [
    h("div", { class: "page-head" }, h("h1", null, twins ? "Twins ladder" : "Ladder"), ladderToggle(lad, keep)),
    twins ? h("p", { class: "lead" }, TWINS_INTRO) : null,
    picker,
    table,
  ]);

  void load(picker, api.seasons, (seasons) => renderPicker(picker, seasons, season, lad));
  void load(table, () => api.ladder(season, offset, PAGE, lad), (l) => draw(l));

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
                h("tr", null, th("rank", "#", "num"), th("name", "Model"), twins ? h("th", { scope: "col", class: "hide-sm" }, "Player data") : null, h("th", { scope: "col", class: "hide-sm" }, "Build"), th("rating", "Rating", "num"), th("games", "Games", "num hide-sm"), th("wins", "W–L", "num")),
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
                    twins
                      ? h("td", { class: "hide-sm" }, it.model.twin ? h("div", { class: "cell-twin" }, h("span", { class: "small" }, dataVolume(it.model.twin.profile)), fidelityBadge(it.model.twin, true)) : h("span", { class: "muted small" }, "House player"))
                      : null,
                    h("td", { class: "hide-sm" }, buildBars(it.model.build)),
                    h("td", { class: it.provisional ? "num rating provisional" : "num rating" }, it.rating.toFixed(0), it.provisional ? provisional(0) : null),
                    h("td", { class: "num hide-sm" }, it.games),
                    h("td", { class: "num" }, `${it.wins}–${it.losses}`),
                  ),
                ),
              ),
            ),
          )
        : h("p", { class: "muted" }, twins ? TWINS_EMPTY : "No models on this ladder yet."),
      twins && l.items.length > 0 && l.items.every((it) => "house" in it.model.owner) ? h("p", { class: "muted" }, TWINS_EMPTY) : null,
      pager(l),
    );
  }

  function pager(l: Ladder) {
    if (l.total <= PAGE) return null;
    const go = (o: number) => () => navigate(`${ladderPath(lad)}?${new URLSearchParams({ ...(season ? { season: String(season) } : {}), offset: String(o) })}`);
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

function renderPicker(el: HTMLElement, seasons: Season[], current?: number, lad: LadderName = "open") {
  if (seasons.length < 2) return;
  const active = seasons.find((s) => s.active)?.id;
  const sel = h(
    "select",
    { id: "season", onchange: (e: Event) => navigate(`${ladderPath(lad)}?season=${(e.target as HTMLSelectElement).value}`) },
    seasons.map((s) => h("option", { value: s.id, selected: (current ?? active) === s.id }, `Season ${s.id}${s.active ? " (current)" : ""}`)),
  );
  el.append(h("label", { for: "season" }, "Season "), sel);
}
