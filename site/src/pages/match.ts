// Match: the 3D viewer (embedded, never rebuilt), scoreboard, per-game scores, rally list with click-to-seek.

import { api, replayUrl } from "../api";
import { RALLY_REASON } from "../arena";
import { buildBars, fmtDate, fmtDelta, load, modelLink, ownerChip, setChips, statusPill, styleGuess } from "../components";
import { h } from "../dom";
import type { MatchDetail, Rally, Side } from "../types";

/** The viewer bundle shipped by the oso repo team (handoff section 7). */
export const viewerSrc = (matchId: number) => `/viewer/index.html?match=${encodeURIComponent(replayUrl(matchId))}`;

interface ViewerWindow extends Window {
  oso_seek?: (t: number) => void;
}

export function match(root: HTMLElement, p: Record<string, string>) {
  const id = Number(p.id);
  void load(root, () => api.match(id), (m) => render(root, m));
}

function render(root: HTMLElement, m: MatchDetail) {
  document.title = `${m.a.name} vs ${m.b.name} · oso Arena`;
  const player = (side: Side) => {
    const x = side === "a" ? m.a : m.b;
    return h(
      "div",
      { class: m.winner === side ? "player won" : "player" },
      h("div", { class: "player-name" }, modelLink(x), m.winner === side ? h("span", { class: "wchip", title: "Winner" }, "W") : null),
      ownerChip(x.owner),
      buildBars(x.build, false),
      h("div", { class: "muted small" }, styleGuess(x.build), m.rating_delta ? ` · ${fmtDelta(m.rating_delta[side])}` : ""),
    );
  };
  const viewerBox = h("div", { class: "viewer" });
  root.append(
    h("p", { class: "muted small" }, `${m.kind} match · season ${m.season_id} · ${fmtDate(m.finished_at ?? m.created_at)} · seed ${m.seed}`),
    h(
      "section",
      { class: "scoreboard", "aria-label": "Scoreboard" },
      player("a"),
      h("div", { class: "sb-center" }, h("div", { class: "games-won" }, `${m.games_won[0]} – ${m.games_won[1]}`), setChips(m.games)),
      player("b"),
    ),
    viewerBox,
  );

  if (m.status === "queued" || m.status === "running") {
    viewerBox.append(h("div", { class: "viewer-wait" }, statusPill(m.status), h("p", null, "This match hasn't finished yet. Check back in a minute.")));
    return;
  }
  if (m.status !== "done") {
    viewerBox.append(h("div", { class: "viewer-wait" }, statusPill(m.status), h("p", null, m.status === "void" ? "This match was voided." : "This match failed to run.")));
    return;
  }

  // Replays are 10-30 MB: don't start the download on its own on phones / metered connections
  const conn = (navigator as Navigator & { connection?: { saveData?: boolean; type?: string } }).connection;
  const small = matchMedia("(max-width: 700px)").matches;
  let frame: HTMLIFrameElement | null = null;
  const mount = () => {
    frame = h("iframe", { src: viewerSrc(m.id), title: `3D replay: ${m.a.name} vs ${m.b.name}`, allow: "fullscreen", class: "viewer-frame" });
    viewerBox.replaceChildren(frame);
  };
  if (small || conn?.saveData || conn?.type === "cellular") {
    viewerBox.append(
      h(
        "div",
        { class: "viewer-wait" },
        h("p", null, "The 3D replay is a 10–30 MB download."),
        h("button", { type: "button", class: "btn primary", onclick: mount }, "Load 3D replay"),
      ),
    );
  } else mount();

  const seek = (r: Rally) => {
    if (!frame) mount();
    const w = frame!.contentWindow as ViewerWindow | null;
    try {
      if (w?.oso_seek) w.oso_seek(r.t_start);
      else frame!.addEventListener("load", () => (frame!.contentWindow as ViewerWindow | null)?.oso_seek?.(r.t_start), { once: true });
    } catch {
      // cross-origin viewer: seeking unavailable
    }
    viewerBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  root.append(rallyList(m, seek));
}

function rallyList(m: MatchDetail, seek: (r: Rally) => void): HTMLElement {
  if (!m.rallies.length) return h("p", { class: "muted" }, "No rally list for this match.");
  const name = (s: Side) => (s === "a" ? m.a.name : m.b.name);
  const games = [...new Set(m.rallies.map((r) => r.game))];
  return h(
    "section",
    null,
    h("h2", null, "Rallies"),
    h("p", { class: "muted small" }, "Select a rally to jump the replay to it."),
    games.map((g) => {
      const final = m.games[g - 1];
      return h(
        "div",
        { class: "rally-game" },
        h("h3", null, `Game ${g}`, final ? h("span", { class: "rg-score" }, `${final[0]}–${final[1]}`) : null),
        h(
          "ol",
          { class: "rally-list" },
          m.rallies
            .filter((r) => r.game === g)
            .map((r) =>
              h(
                "li",
                { class: "rally" },
                h("span", { class: "ry-score" }, `${r.score[0]}–${r.score[1]}`),
                h("span", { class: "ry-text" }, h("strong", null, name(r.winner)), ` · ${RALLY_REASON[r.reason] ?? r.reason} · ${r.shots} ${r.shots === 1 ? "shot" : "shots"}`),
                h("button", { type: "button", class: "link-btn", onclick: () => seek(r), "aria-label": `Jump to game ${r.game} rally at ${r.score.join("–")}` }, "▶ " + fmtT(r.t_start)),
              ),
            ),
        ),
      );
    }),
  );
}

const fmtT = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
