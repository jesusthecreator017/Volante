// Volante Twins on the site (docs/twins-site.md): the Open | Twins toggle, the fidelity badge, the data-volume line and the twin card.
// The profile is self-reported by the player and shown as given; only its fidelity is checked by the server.

import { fmtDate } from "./components";
import { h } from "./dom";
import { icon } from "./icons";
import type { LadderName, ModelSummary, StyleProfile, Twin } from "./types";

/** Section 14: a twin joins after 10 matches, with tempo within this band of its player's. */
export const TEMPO_BAND: [number, number] = [0.75, 1.33];

export const ladderOf = (q: URLSearchParams): LadderName => (q.get("ladder") === "twins" ? "twins" : "open");

/** "Open | Twins" segmented links. `href` builds each side's URL so pages keep their other parameters. */
export function ladderToggle(current: LadderName, href: (l: LadderName) => string, label = "Ladder"): HTMLElement {
  const item = (l: LadderName, text: string) =>
    h("a", { href: href(l), "data-nav": true, class: "seg", "aria-current": l === current ? "page" : null }, text);
  return h("nav", { class: "segmented", "aria-label": `${label}: open or twins` }, item("open", "Open"), item("twins", "Twins"));
}

const n0 = (x: number) => Math.round(x).toLocaleString("en-US");

/** "1,240 swings, 52 min" */
export function dataVolume(p: StyleProfile): string {
  return `${n0(p.source.swings)} swings, ${n0(p.source.minutes)} min`;
}

type Verdict = "ok" | "checking" | "fail";
export function verdict(t: Twin): Verdict {
  if (t.fidelity.ok) return "ok";
  return t.fidelity.checked_at ? "fail" : "checking";
}

const VERDICT_TEXT: Record<Verdict, string> = {
  ok: "Plays like its profile",
  checking: "Checking",
  fail: "Does not match its profile yet",
};

export function fidelityBadge(t: Twin, short = false): HTMLElement {
  const v = verdict(t);
  const text = short ? { ok: "Verified", checking: "Checking", fail: "No match" }[v] : VERDICT_TEXT[v];
  return h(
    "span",
    { class: `chip fid-${v}`, title: VERDICT_TEXT[v] },
    icon(v === "ok" ? "check-circle-fill" : v === "checking" ? "calendar-blank" : "warning-circle"),
    text,
  );
}

/** "steady" < 0.1, "mixed" 0.1-0.2, "wild" > 0.2 (cv over the power swings). */
export function consistency(cv: number): string {
  return cv < 0.1 ? "steady" : cv <= 0.2 ? "mixed" : "wild";
}

/** "plays 1.1x as slow as its profile" (ratio > 1 slower, < 1 faster). */
export function tempoText(r: number): string {
  if (Math.abs(r - 1) < 0.05) return "plays at its profile's tempo";
  return r > 1 ? `plays ${r.toFixed(2)}x as slow as its profile` : `plays ${(1 / r).toFixed(2)}x as fast as its profile`;
}

const fmt1 = (x: number) => (Math.round(x * 10) / 10).toString();

function mixBars(title: string, mix: Record<string, number>): HTMLElement {
  const rows = Object.entries(mix).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((s, [, v]) => s + v, 0) || 1;
  return h(
    "div",
    { class: "mix" },
    h("h4", null, title),
    h(
      "ul",
      { class: "mix-bars" },
      rows.map(([k, v]) => {
        const pct = Math.round((100 * v) / total);
        return h("li", null, h("span", { class: "mix-k" }, k), h("meter", { min: 0, max: 100, value: pct, "aria-label": `${k} ${pct}%` }), h("span", { class: "mix-v" }, `${pct}%`));
      }),
    ),
  );
}

/** The twin card on a model page. */
export function twinCard(m: ModelSummary & { twin: Twin }): HTMLElement {
  const { profile: p, fidelity: f } = m.twin;
  const clipped = p.speed.clipped_frac > 0.2;
  return h(
    "section",
    { class: "card twin-card", "aria-labelledby": "twin-h" },
    h("div", { class: "twin-head" }, h("h2", { id: "twin-h" }, icon("users-three"), "Twin"), fidelityBadge(m.twin)),
    h("p", { class: "muted small" }, "Trained from a real player's racquet sessions. The profile is self-reported by the player and shown as given; the Arena checks only that the model plays like it."),
    h(
      "dl",
      { class: "twin-stats" },
      h("dt", null, "Data"),
      h("dd", null, `${n0(p.source.swings)} swings in ${n0(p.source.sessions)} session${p.source.sessions === 1 ? "" : "s"}, ${n0(p.source.minutes)} min`),
      h("dt", null, "Power"),
      h("dd", null, `${fmt1(p.speed.p50_kmh)} km/h typical, ${fmt1(p.speed.p90_kmh)} km/h hard`, clipped ? h("span", { class: "muted" }, " (lower bounds: the sensor clips on hard swings)") : null),
      h("dt", null, "Consistency"),
      h("dd", null, h("span", { title: `cv ${p.speed.cv.toFixed(3)} over the power swings (at or above the median speed)` }, consistency(p.speed.cv))),
      h("dt", null, "Tempo"),
      h("dd", null, `${fmt1(p.tempo.median_gap_s)} s between shots (${fmt1(p.tempo.p25_gap_s)}–${fmt1(p.tempo.p75_gap_s)} s)`),
    ),
    p.stroke_mix ? mixBars("Stroke mix", p.stroke_mix) : h("p", { class: "muted small" }, "Stroke mix: coming with stroke recognition."),
    p.side_mix ? mixBars("Forehand / backhand", { forehand: p.side_mix.fh, backhand: p.side_mix.bh }) : null,
    h(
      "div",
      { class: "twin-fid" },
      h("h3", null, "Fidelity"),
      f.checked_at === null
        ? h("p", null, "Checked after its first 10 matches: the build must match the profile and the tempo must be within ", `${TEMPO_BAND[0]}–${TEMPO_BAND[1]}`, " of the player's.")
        : h(
            "ul",
            { class: "plain" },
            f.tempo_ratio !== null ? h("li", null, `Tempo: ${tempoText(f.tempo_ratio)} (pass ${TEMPO_BAND[0]}–${TEMPO_BAND[1]})`) : null,
            f.mix_js !== null ? h("li", null, `Stroke mix difference: ${f.mix_js.toFixed(2)}`) : null,
            (f.reasons ?? []).map((r) => h("li", { class: "bad" }, r)),
            h("li", { class: "muted small" }, `Checked ${fmtDate(f.checked_at)}. Rechecked weekly.`),
          ),
    ),
  );
}

export const TWINS_INTRO =
  "A twin is a model trained from a real player's racquet sessions. It joins this ladder after at least 10 matches, once it passes the fidelity check: its build matches the profile and its tempo is within 0.75–1.33 of the player's. The check repeats weekly. Twins play twins and the house players.";

export const TWINS_EMPTY = "No twins yet. Record with the Volante pod, make your style profile, train a twin.";
