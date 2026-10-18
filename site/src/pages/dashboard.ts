// Account → Dashboard: the signed-in owner's Volante app sessions (summaries the app syncs). Private: only the owner sees it.
// /account/dashboard is the overview, /account/dashboard/sessions/:id one session. Speeds say "estimated", strokes "experimental",
// watch sessions "wrist" (the watch measures hand speed, never mixed with racquet speed). /sessions[/:id] (PR #78) redirect here.

import { ApiError, api, loginUrl, me } from "../api";
import { barChart, lineChart, mixBar, sparkline } from "../charts";
import { empty, fmtDate, load, moreLink } from "../components";
import { h, link } from "../dom";
import { icon, type IconName } from "../icons";
import type { Best, Dashboard, GroupShares, PlaySession, Report, StrokeGroup } from "../types";

const HOME = "/account/dashboard";
const sessionHref = (id: string) => `${HOME}/sessions/${encodeURIComponent(id)}`;
const n0 = (v: number) => Math.round(v).toLocaleString();
const n1 = (v: number | null | undefined) => (v == null ? "—" : String(Math.round(v * 10) / 10));
const day = (iso: string) => fmtDate(iso, false);

/** docs/strokes/report-format.md §3 (the 4-group model). */
export const GROUP_LABEL: Record<StrokeGroup, string> = {
  power: "Power (smash, clear)",
  mid: "Mid (drive, drop, lift, net kill, long serve)",
  touch: "Touch (net shot, push, short serve)",
  not_a_hit: "Not a hit",
  unclassified: "Unclassified",
};

/** Signed out: a sign-in link. Not linked to the app: the explainer. Neither makes a data call. */
async function gate(root: HTMLElement): Promise<boolean> {
  const m = await me().catch(() => null);
  if (!m?.user) {
    root.append(h("p", null, "Sign in to see your training stats."), h("p", null, h("a", { href: loginUrl(), class: "btn primary" }, "Sign in with GitHub")));
    return false;
  }
  if (!m.app_linked) {
    root.append(
      h(
        "section",
        { class: "card dash-explainer" },
        h("h2", null, icon("device-mobile"), "Connect the Volante app"),
        h("p", null, "Sign in to the Arena in the Volante app to see your stats here."),
        h("p", { class: "muted" }, "After that the app sends a short summary of each session (no raw motion data). Only you can see it. ", link("/privacy", "Privacy")),
      ),
    );
    return false;
  }
  return true;
}

function tile(value: string, unit: string, label: string): HTMLElement {
  return h("div", { class: "stat" }, h("strong", null, value, unit ? h("small", null, ` ${unit}`) : null), h("span", null, label));
}

export const SENSOR_LABEL: Record<PlaySession["sensor"], string> = { pod: "racquet", watch: "wrist", "pod+watch": "racquet + watch" };
const sensorTag = (sensor: PlaySession["sensor"]) => h("span", { class: sensor === "watch" ? "badge wrist" : "badge" }, SENSOR_LABEL[sensor]);

function strokeMix(g: GroupShares | Record<string, number>, label: string): HTMLElement {
  const total = Object.values(g).reduce((a, b) => a + b, 0) || 1;
  const parts = (Object.keys(GROUP_LABEL) as StrokeGroup[]).map((k) => ({ key: k, label: GROUP_LABEL[k], share: (g[k] ?? 0) / total }));
  return mixBar(parts, `${label}: ${parts.filter((p) => p.share > 0).map((p) => `${p.label} ${Math.round(p.share * 100)}%`).join(", ")}`);
}

const experimental = () => h("span", { class: "badge" }, "experimental");

// ---------------------------------------------------------------- overview

function trendCard(title: string, unit: string, values: (number | null)[]): HTMLElement {
  // per session: a missing value (e.g. no heart rate that day) is skipped, not drawn as a gap
  const nums = values.filter((v): v is number => v !== null);
  const last = nums.length ? nums[nums.length - 1] : null;
  return h(
    "div",
    { class: "trend" },
    h("h3", null, title),
    h("p", { class: "trend-now" }, h("strong", null, n1(last)), h("small", null, ` ${unit}`)),
    nums.length >= 2
      ? sparkline(nums, `${title}: ${nums.length} sessions, from ${n1(nums[0])} to ${n1(nums[nums.length - 1])} ${unit}, range ${n1(Math.min(...nums))} to ${n1(Math.max(...nums))}`)
      : h("p", { class: "muted small" }, "Not enough sessions yet."),
  );
}

function bestRow(label: string, b: Best | null, fmt: (v: number) => string): HTMLElement {
  return h(
    "li",
    null,
    h("span", { class: "best-label" }, label),
    b ? [h("strong", { class: "best-value" }, fmt(b.value)), link(sessionHref(b.session_id), day(b.started_at))] : h("span", { class: "muted" }, "—"),
  );
}

function sessionRow(x: PlaySession): HTMLElement {
  // best_kmh is the racquet's fastest swing; the wrist's hand speed is never shown as one
  const speed = x.best_kmh == null || x.sensor === "watch" ? null : `best ${n1(x.best_kmh)} km/h`;
  return h(
    "li",
    null,
    link(sessionHref(x.id), fmtDate(x.started_at)),
    sensorTag(x.sensor),
    h("span", { class: "muted small" }, [`${Math.round(x.duration_s / 60)} min`, `${n0(x.swings)} swings`, `${n0(x.hits)} hits`, speed].filter(Boolean).join(" · ")),
  );
}

function sessionsList(el: HTMLElement, before: string | null, ul: HTMLUListElement) {
  return load(el, () => api.mySessions(before), (page) => {
    ul.append(...page.items.map(sessionRow));
    if (!ul.parentNode) el.append(ul); // later pages render into a slot below and add to the same list
    if (page.next_before) {
      const more = h("button", { type: "button", class: "btn more-sessions", onclick: () => (more.remove(), void sessionsList(slot, page.next_before, ul)) }, "Older sessions");
      const slot = h("div");
      el.append(slot, more);
    }
  });
}

function overview(d: Dashboard, list: HTMLElement): HTMLElement[] {
  const t = d.totals;
  const weekLabel = (w: string) => `Week ${Number(w.split("-W")[1])}`;
  const busiest = d.weekly.reduce((a, w) => (w.active_min > a.active_min ? w : a), d.weekly[0]);
  const out: HTMLElement[] = [
    h("div", { class: "stats-row totals" }, tile(n0(t.sessions), "", "sessions"), tile(n0(t.swings), "", "swings"), tile(n0(t.active_min), "min", "active"), tile(n0(t.rallies), "", "rallies")),
    h(
      "section",
      { class: "card weekly", "aria-labelledby": "wk-h" },
      h("h2", { id: "wk-h" }, "Active minutes per week"),
      barChart(
        d.weekly.map((w) => ({ label: `${weekLabel(w.week)}, ${w.sessions} sessions, ${n0(w.swings)} swings`, value: w.active_min })),
        { label: `Active minutes per week over the last 12 weeks. Busiest: ${busiest ? `${weekLabel(busiest.week)}, ${n0(busiest.active_min)} min` : "none"}.`, unit: "min", ends: ["12 weeks ago", "This week"] },
      ),
    ),
  ];
  const tr = d.trends;
  out.push(
    h(
      "section",
      { class: "dash-sec", "aria-labelledby": "tr-h" },
      h("h2", { id: "tr-h" }, "Trends"),
      h(
        "p",
        { class: "muted small" },
        "Racquet sessions, oldest to newest. Speeds are estimated.",
        d.wrist.sessions ? ` ${d.wrist.sessions} wrist ${d.wrist.sessions === 1 ? "session is" : "sessions are"} left out: the watch measures hand speed, not the racquet.` : null,
      ),
      tr.length
        ? h(
            "div",
            { class: "trends" },
            trendCard("Hard swing speed (estimated)", "km/h", tr.map((x) => x.p90_kmh)),
            trendCard("Swings per minute", "/min", tr.map((x) => x.swings_per_min)),
            trendCard("Time between shots", "s", tr.map((x) => x.median_gap_s)),
            trendCard("Heart rate", "bpm", tr.map((x) => x.hr_avg)),
          )
        : h("p", { class: "empty" }, "No racquet sessions yet."),
    ),
  );
  const b = d.bests;
  const side: HTMLElement[] = [
    h(
      "section",
      { class: "card", "aria-labelledby": "be-h" },
      h("h2", { id: "be-h" }, icon("trophy-fill"), "Bests"),
      h(
        "ul",
        { class: "plain bests" },
        bestRow("Fastest swing (estimated)", b.max_kmh, (v) => `${n1(v)} km/h`),
        bestRow("Most swings", b.most_swings, n0),
        bestRow("Longest rally", b.longest_rally, (v) => `${n0(v)} shots`),
        bestRow("Highest pace", b.best_rate, (v) => `${n1(v)} swings/min`),
      ),
    ),
  ];
  if (d.strokes)
    side.push(
      h(
        "section",
        { class: "card", "aria-labelledby": "st-h" },
        h("div", { class: "card-h" }, h("h2", { id: "st-h" }, "Strokes"), experimental()),
        strokeMix(d.strokes.groups, "Stroke groups, recent sessions"),
        h("p", { class: "muted small" }, `Your last ${d.strokes.sessions} sessions with stroke data. Stroke groups are experimental guesses from the pod.`),
      ),
    );
  if (d.style) {
    const st = d.style;
    side.push(
      h(
        "section",
        { class: "card", "aria-labelledby": "sy-h" },
        h("h2", { id: "sy-h" }, "Your style"),
        h(
          "dl",
          { class: "stats" },
          h("dt", null, "Typical speed"), h("dd", null, `${n1(st.speed.p50_kmh)} km/h (estimated)`),
          h("dt", null, "Hard swings"), h("dd", null, `${n1(st.speed.p90_kmh)} km/h (estimated)`),
          h("dt", null, "Time between shots"), h("dd", null, `${n1(st.tempo.median_gap_s)} s`),
          h("dt", null, "Built from"), h("dd", null, `${n0(st.source.swings)} swings, ${n0(st.source.minutes)} min`),
        ),
        d.twin_model_id ? h("p", null, moreLink(`/arena/models/${d.twin_model_id}`, "Your twin")) : null,
      ),
    );
  }
  out.push(h("div", { class: "dash-cols" }, side));
  out.push(h("section", { class: "dash-sec", "aria-labelledby": "se-h" }, h("h2", { id: "se-h" }, "Sessions"), list));
  return out;
}

export async function dashboard(root: HTMLElement) {
  root.classList.add("dash");
  root.append(h("h1", null, "Dashboard"), h("p", { class: "lead" }, "Your sessions from the Volante app. Only you can see this page."));
  if (!(await gate(root))) return;
  const slot = h("div");
  root.append(slot);
  const list = h("div");
  const sessions = sessionsList(list, null, h("ul", { class: "plain sessions" })); // in parallel with the aggregates
  await load(slot, api.myDashboard, (d) =>
    slot.append(...(d.totals.sessions ? overview(d, list) : [h("p", { class: "empty" }, "No sessions synced yet. They show up here after you record one in the app.")])),
  );
  await sessions;
}

// ---------------------------------------------------------------- one session

const TONE: Record<Report["insights"][number]["severity"], [string, IconName]> = { praise: ["good", "seal-check"], tip: ["tip", "target"], info: ["info", "info"] };

/** A session without a report (watch sessions, older app builds): the flat numbers the app sent. */
function flat(p: PlaySession): HTMLElement[] {
  const wrist = p.sensor === "watch";
  return [
    h(
      "div",
      { class: "stats-row" },
      tile(n0(p.swings), "", "swings"),
      tile(n0(p.hits), "", "hits"),
      tile(n0(p.duration_s / 60), "min", "duration"),
      wrist ? tile(n1(p.avg_bpm), "bpm", "average heart rate") : tile(n1(p.best_kmh), "km/h", "fastest swing (estimated)"),
    ),
    h("p", { class: "empty" }, wrist ? "Wrist sessions have no detailed report: the watch measures hand speed, not the racquet." : "No detailed report for this session."),
  ];
}

function report(r: Report, p: PlaySession): HTMLElement[] {
  const wrist = p.sensor === "watch";
  const speedWord = wrist ? "hand speed" : "hard swings";
  const tl = r.timeline;
  const ends: [string, string] = ["0 min", `${tl.length ? tl[tl.length - 1].t_min + 1 : 0} min`];
  const tempo = [
    `${n0(p.hits)} hits`,
    r.tempo.median_gap_s != null ? `${n1(r.tempo.median_gap_s)} s between shots (median)` : null,
    r.rallies.median_shots != null ? `rallies ${r.rallies.median_shots} shots (median), longest ${r.rallies.longest_shots}` : null,
  ].filter(Boolean);
  const timeline = h(
    "section",
    { class: "card timeline", "aria-labelledby": "tl-h" },
    h("h2", { id: "tl-h" }, "Minute by minute"),
    tl.length
      ? [
          h("h3", null, "Swings per minute"),
          barChart(tl.map((m) => ({ label: `Minute ${m.t_min + 1}`, value: m.swings })), { label: `Swings per minute over ${tl.length} minutes, peak ${Math.max(...tl.map((m) => m.swings))}`, unit: "swings", ends }),
          tl.some((m) => m.p90_kmh !== null)
            ? [
                h("h3", null, wrist ? "Hand speed (wrist, estimated)" : "Hard swing speed (estimated)"),
                lineChart([{ name: speedWord, values: tl.map((m) => m.p90_kmh), cls: "s1" }], { label: `${wrist ? "Hand speed" : "Hard swing speed"} per minute, km/h, estimated; gaps are minutes without swings`, unit: "km/h", ends, short: true }),
              ]
            : null,
          tl.some((m) => m.hr_bpm !== null)
            ? [h("h3", null, "Heart rate"), lineChart([{ name: "heart rate", values: tl.map((m) => m.hr_bpm), cls: "s2" }], { label: "Heart rate per minute, bpm", unit: "bpm", ends, short: true })]
            : null,
        ]
      : h("p", { class: "empty" }, "No timeline for this session."),
  );
  const s = r.strokes;
  return [
    h(
      "div",
      { class: "stats-row" },
      tile(n0(r.swings.count), "", "swings"),
      tile(n0(r.session.active_s / 60), "min", `active of ${n0(r.session.duration_s / 60)}`),
      tile(n1(r.speed.p90_kmh), "km/h", `${speedWord} (estimated)`),
      tile(n0(r.rallies.count), "", "rallies"),
    ),
    h("p", { class: "muted small" }, tempo.join(" · ")),
    timeline,
    h(
      "div",
      { class: "dash-cols" },
      h(
        "section",
        { class: "card strokes", "aria-labelledby": "sk-h" },
        h("div", { class: "card-h" }, h("h2", { id: "sk-h" }, "Strokes"), experimental()),
        s ? [strokeMix(s.groups, "Stroke groups this session"), h("p", { class: "muted small" }, "Stroke groups are experimental guesses from the pod.")] : h("p", { class: "empty" }, wrist ? "Strokes need the racquet pod." : "No stroke data for this session."),
      ),
      r.heart_rate
        ? h(
            "section",
            { class: "card heart", "aria-labelledby": "hr-h" },
            h("h2", { id: "hr-h" }, "Heart rate"),
            h("div", { class: "stats-row two" }, tile(n0(r.heart_rate.avg_bpm), "bpm", "average"), tile(n0(r.heart_rate.max_bpm), "bpm", "max")),
          )
        : null,
    ),
    r.insights.length
      ? h(
          "section",
          { class: "dash-sec", "aria-labelledby": "in-h" },
          h("h2", { id: "in-h" }, "Insights"),
          h(
            "ul",
            { class: "insights" },
            r.insights.map((x) => h("li", { class: `insight tone-${TONE[x.severity][0]}` }, h("span", { class: "insight-ico" }, icon(TONE[x.severity][1])), h("div", null, h("h4", null, x.title), h("p", null, x.body)))),
          ),
        )
      : h("span"),
  ];
}

export async function sessionPage(root: HTMLElement, params: Record<string, string>) {
  root.classList.add("dash");
  root.append(h("p", null, link(HOME, "← Dashboard")));
  const title = h("h1", null, "Session");
  root.append(title);
  if (!(await gate(root))) return;
  const slot = h("div");
  root.append(slot);
  const get = () => api.mySession(params.id).catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e)));
  await load(slot, get, (d) => {
    if (!d) return void slot.append(empty("Session not found — it may have been deleted from the app."));
    title.replaceChildren(fmtDate(d.started_at), " ", sensorTag(d.sensor));
    slot.append(...(d.report ? report(d.report, d) : flat(d)));
  });
}
