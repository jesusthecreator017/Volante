// /sessions: the signed-in player's badminton sessions, uploaded by the iPhone app to their GitHub account
// (PUT /api/me/sessions/{id}). /sessions/:id shows one with its report (docs/strokes/report-format.md).

import { api, loginUrl, me } from "../api";
import { empty, errorBox, fmtDate, loading } from "../components";
import { h, link, replace } from "../dom";
import { icon } from "../icons";
import type { PlaySession, Report } from "../types";

const SENSOR: Record<PlaySession["sensor"], string> = { pod: "Racquet pod", watch: "Apple Watch", "pod+watch": "Pod + Apple Watch" };

const mins = (s: number) => (s >= 60 ? `${Math.round(s / 60)} min` : `${Math.round(s)} s`);
const stat = (value: string, label: string) => h("div", { class: "stat" }, h("strong", null, value), h("span", { class: "muted small" }, label));

async function signedIn(root: HTMLElement): Promise<boolean> {
  if ((await me().catch(() => null))?.user) return true;
  root.append(
    h("p", null, "Sign in with the GitHub account you use in the Volante app to see your sessions."),
    h("p", null, h("a", { href: loginUrl(), class: "btn primary with-icon" }, icon("github-logo"), "Sign in with GitHub")),
  );
  return false;
}

export async function sessions(root: HTMLElement) {
  root.append(h("h1", null, "Your sessions"));
  if (!(await signedIn(root))) return;
  const list = h("div", { "aria-busy": "true" }, loading());
  root.append(h("p", { class: "muted" }, "Every session you finish in the iPhone app shows up here."), list);
  const fill = async () => {
    try {
      const items = await api.mySessions();
      list.removeAttribute("aria-busy");
      if (!items.length) return replace(list, empty("No sessions yet. Play one with the pod or your Apple Watch, signed in to the Arena in the app."));
      replace(
        list,
        h(
          "ul",
          { class: "session-list" },
          items.map((s) =>
            h(
              "li",
              { class: "card session-row" },
              link(`/sessions/${s.id}`, h("strong", null, fmtDate(s.started_at)), h("span", { class: "muted small" }, `${SENSOR[s.sensor]} · ${mins(s.duration_s)}`)),
              h(
                "div",
                { class: "stats" },
                stat(String(s.swings), "swings"),
                stat(String(s.hits), "hits"),
                stat(s.best_kmh == null ? "—" : `${Math.round(s.best_kmh)}`, "best km/h"),
                stat(s.avg_bpm == null ? "—" : `${Math.round(s.avg_bpm)}`, "avg bpm"),
              ),
            ),
          ),
        ),
      );
    } catch (e) {
      replace(list, errorBox(e, fill));
    }
  };
  await fill();
}

export async function sessionDetail(root: HTMLElement, params: Record<string, string>) {
  if (!(await signedIn(root))) return;
  const body = h("div", { "aria-busy": "true" }, loading());
  root.append(body);
  try {
    const s = await api.mySession(params.id);
    const r: Report | null = s.report;
    replace(
      body,
      h("p", null, link("/sessions", "← All sessions")),
      h("h1", null, fmtDate(s.started_at)),
      h("p", { class: "muted" }, `${SENSOR[s.sensor]} · ${mins(s.duration_s)}`),
      h(
        "div",
        { class: "stats card" },
        stat(String(s.swings), "swings"),
        stat(String(s.hits), "hits"),
        stat(s.best_kmh == null ? "—" : `${Math.round(s.best_kmh)}`, "best km/h"),
        stat(s.avg_bpm == null ? "—" : `${Math.round(s.avg_bpm)}`, "avg bpm"),
        r?.speed ? stat(`${Math.round(r.speed.p90_kmh)}`, "p90 km/h") : null,
        r?.tempo ? stat(`${r.tempo.median_gap_s.toFixed(1)} s`, "between shots") : null,
        r?.rallies ? stat(String(r.rallies.longest_shots), "longest rally") : null,
      ),
      r?.insights?.length
        ? h("section", null, h("h2", null, "Insights"), h("ul", { class: "insights" }, r.insights.map((i) => h("li", { class: "card" }, h("strong", null, i.title), h("p", { class: "muted" }, i.body)))))
        : null,
      r?.timeline?.length
        ? h(
            "section",
            null,
            h("h2", null, "Minute by minute"),
            h(
              "table",
              { class: "timeline" },
              h("thead", null, h("tr", null, h("th", null, "Minute"), h("th", null, "Swings"), h("th", null, "p90 km/h"), h("th", null, "bpm"))),
              h("tbody", null, r.timeline.map((t) => h("tr", null, h("td", null, String(t.t_min + 1)), h("td", null, String(t.swings)), h("td", null, t.p90_kmh == null ? "—" : t.p90_kmh.toFixed(0)), h("td", null, t.hr_bpm == null ? "—" : t.hr_bpm.toFixed(0))))),
            ),
          )
        : null,
    );
  } catch (e) {
    replace(body, errorBox(e));
  }
}
