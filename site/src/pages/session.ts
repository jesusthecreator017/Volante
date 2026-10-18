// "/report": a session report from the iPhone app (report.json, `volante-report/1`), read in the browser and never uploaded.
// Shows an illustrative example until a file is opened. Format: docs/strokes/report-format.md.

import { countUp, reveal } from "../anim";
import { h, link, s } from "../dom";
import { dropZone } from "../drop";
import { icon } from "../icons";
import { insightList } from "../report";
import { EXAMPLE_REPORT, parseReport, reportInsights, type Minute, type VolanteReport } from "../volanteReport";

const r0 = (x: number) => String(Math.round(x));
const r1 = (x: number) => (Math.round(x * 10) / 10).toFixed(1);
const dash = "–";

function minutes(sec: number): string {
  const m = Math.floor(sec / 60);
  const s2 = Math.round(sec % 60);
  return m ? `${m} min ${String(s2).padStart(2, "0")} s` : `${s2} s`;
}

function when(iso: string): string {
  return new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}

function tile(value: number | null, unit: string, label: string, fmt = r0): HTMLElement {
  const v = value === null ? h("span", null, dash) : countUp(h("span"), value, fmt);
  return h("div", { class: "stat" }, h("strong", null, v, unit && value !== null ? h("small", null, ` ${unit}`) : null), h("span", null, label));
}

/** Swings per minute as bars, hard-swing speed (p90) and heart rate as lines; pointer or arrow keys read out one minute. */
function timeline(r: VolanteReport): HTMLElement | null {
  const t = r.timeline;
  if (t.length < 2) return null;
  const W = 600;
  const H = 170;
  const n = t.length;
  const bw = W / n;
  const maxSw = Math.max(1, ...t.map((m) => m.swings));
  const ySw = (v: number) => H - (v / maxSw) * H * 0.62;
  const line = (vals: (number | null)[], lo: number, hi: number, cls: string) => {
    let d = "";
    let pen = false;
    vals.forEach((v, i) => {
      if (v === null) return void (pen = false);
      const y = H - 8 - ((v - lo) / Math.max(1, hi - lo)) * (H - 16);
      d += `${pen ? "L" : "M"}${(i * bw + bw / 2).toFixed(1)} ${y.toFixed(1)}`;
      pen = true;
    });
    return d ? s("path", { d, class: cls }) : null;
  };
  const range = (vals: (number | null)[]) => {
    const xs = vals.filter((v): v is number => v !== null);
    return xs.length ? [Math.min(...xs) - 5, Math.max(...xs) + 5] : [0, 1];
  };
  const p90 = t.map((m) => m.p90_kmh);
  const hr = t.map((m) => m.hr_bpm);
  const hasHr = hr.some((v) => v !== null);
  const bars = t.map((m, i) => s("rect", { x: (i * bw + bw * 0.16).toFixed(1), y: ySw(m.swings).toFixed(1), width: (bw * 0.68).toFixed(1), height: (H - ySw(m.swings)).toFixed(1), rx: Math.min(3, bw * 0.3).toFixed(1), class: "sbar" }));
  const svg = s(
    "svg",
    { viewBox: `0 0 ${W} ${H}`, class: "swing-chart tl-chart", role: "img", "aria-label": `Minute by minute: ${n} minutes, up to ${maxSw} swings a minute.`, preserveAspectRatio: "none" },
    s("g", { class: "bars" }, bars),
    line(p90, ...(range(p90) as [number, number]), "tl-speed"),
    hasHr ? line(hr, ...(range(hr) as [number, number]), "tl-hr") : null,
  );
  const read = h("p", { class: "chart-read", "aria-live": "polite" });
  const describe = (m: Minute) =>
    [`Minute ${m.t_min + 1}`, `${m.swings} swing${m.swings === 1 ? "" : "s"}`, m.p90_kmh === null ? null : `hard swings ${r0(m.p90_kmh)} km/h`, m.hr_bpm === null ? null : `${r0(m.hr_bpm)} bpm`].filter(Boolean).join(" · ");
  const idle = "Hover or tap a minute.";
  read.textContent = idle;
  let cur = -1;
  const select = (i: number) => {
    if (cur >= 0) bars[cur].classList.remove("on");
    cur = i;
    if (i < 0) return void (read.textContent = idle);
    bars[i].classList.add("on");
    read.textContent = describe(t[i]);
  };
  const at = (e: PointerEvent) => {
    const box = svg.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - box.left) / box.width) * n)));
  };
  svg.addEventListener("pointermove", (e) => select(at(e)));
  svg.addEventListener("pointerdown", (e) => select(at(e)));
  svg.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && select(-1));
  const wrap = h("div", { class: "chart-wrap", tabindex: "0", "aria-label": "Minute chart. Left and right arrows step through minutes." }, svg);
  wrap.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    select(Math.max(0, Math.min(n - 1, (cur < 0 ? 0 : cur) + (e.key === "ArrowRight" ? 1 : -1))));
  });
  const key = (cls: string, label: string) => h("span", { class: `tl-key ${cls}` }, label);
  return h(
    "div",
    { class: "tl" },
    h("h4", { class: "sub-h" }, "Minute by minute"),
    wrap,
    h("p", { class: "tl-legend small" }, key("k-bars", "swings"), key("k-speed", "hard swings (p90)"), hasHr ? key("k-hr", "heart rate") : null),
    read,
  );
}

function facts(r: VolanteReport): HTMLElement {
  const row = (label: string, value: string) => h("div", null, h("dt", null, label), h("dd", null, value));
  const { tempo, rallies, heart_rate: hr, speed } = r;
  return h(
    "dl",
    { class: "facts" },
    row("Recorded", minutes(r.session.duration_s)),
    row("Swings a minute in play", r.swings.per_min_active === null ? dash : r0(r.swings.per_min_active)),
    row("Time between shots", tempo.median_gap_s === null ? dash : `${r1(tempo.median_gap_s)} s` + (tempo.p25_gap_s !== null && tempo.p75_gap_s !== null ? ` (mostly ${r1(tempo.p25_gap_s)}–${r1(tempo.p75_gap_s)})` : "")),
    row("Rallies", rallies.count ? `${rallies.count}, ${rallies.median_shots === null ? dash : r0(rallies.median_shots)} shots typical, longest ${rallies.longest_shots}` : "none"),
    row("Typical swing", speed.p50_kmh === null ? dash : `${r0(speed.p50_kmh)} km/h`),
    hr ? row("Heart rate", `${r0(hr.avg_bpm)} average, ${r0(hr.max_bpm)} max bpm`) : null,
    speed.clipped_frac > 0 ? row("Beyond the sensor's range", `${r0(speed.clipped_frac * 100)} % of swings`) : null,
  );
}

const GROUPS: [string, string, string][] = [
  ["power", "Power", "smash, clear"],
  ["mid", "Mid", "drive, drop, lift, kill, long serve"],
  ["touch", "Touch", "net shot, block, short serve"],
  ["unclassified", "Unsure", "the model couldn't tell"],
];
const label = (k: string) => (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, " ");

function strokes(r: VolanteReport): HTMLElement | null {
  const st = r.strokes;
  if (!st) return null;
  const total = Math.max(1, Object.values(st.groups).reduce((a, b) => a + b, 0));
  const classes = Object.entries(st.counts)
    .map(([k, v]) => [k, Object.values(v).reduce((a, b) => a + b, 0)] as const)
    .sort((a, b) => b[1] - a[1]);
  return h(
    "div",
    { class: "strokes" },
    h("h4", { class: "sub-h" }, "Strokes", st.experimental ? h("span", { class: "badge" }, "Experimental") : null),
    h(
      "ul",
      { class: "groups" },
      GROUPS.filter(([k]) => st.groups[k] !== undefined).map(([k, name, what]) => {
        const pct = (st.groups[k] / total) * 100;
        const meter = s("svg", { viewBox: "0 0 100 6", class: "meter", preserveAspectRatio: "none", "aria-hidden": "true" }, s("rect", { width: "100", height: "6", rx: "3", class: "track" }), s("rect", { width: Math.max(1, pct).toFixed(1), height: "6", rx: "3", class: `fill g-${k}` }));
        return h("li", null, h("div", { class: "g-head" }, h("strong", null, name), h("span", { class: "muted small" }, what), h("span", { class: "g-n" }, `${st.groups[k]}`, h("small", null, ` ${r0(pct)} %`))), meter);
      }),
    ),
    classes.length
      ? h(
          "ul",
          { class: "stroke-chips" },
          classes.map(([k, n]) => {
            const sp = st.speed_by_class[k];
            return h("li", { class: "stroke-chip" }, h("strong", null, label(k)), ` ${n}`, sp ? h("span", { class: "muted" }, ` · ${r0(sp.p90_kmh)} km/h`) : null);
          }),
        )
      : null,
    h("p", { class: "muted small" }, `Named by ${r.model ? `${r.model.name} ${r.model.version}` : "the stroke model"}. It's still learning, so treat these counts as rough.`),
  );
}

export function reportCard(r: VolanteReport, badge: string): HTMLElement {
  const watch = r.session.source === "watch";
  const ins = reportInsights(r);
  return h(
    "article",
    { class: "card glass report" },
    h("div", { class: "report-head" }, h("h3", null, icon("waveform"), when(r.session.started_at)), h("span", { class: "badge" }, badge)),
    h(
      "div",
      { class: "stats-row" },
      tile(r.swings.count, "", "swings"),
      tile(r.speed.p90_kmh, "km/h", "hard swings (p90)"),
      tile(r.speed.max_kmh, "km/h", "top speed"),
      tile(r.session.active_s / 60, "min", "in rallies"),
    ),
    timeline(r),
    facts(r),
    strokes(r),
    ins.length ? h("h3", { class: "insights-h" }, icon("sparkle-fill"), "Insights") : null,
    ins.length ? insightList(ins) : null,
    h("p", { class: "muted small foot" }, icon("info"), watch ? " Recorded on the wrist: speeds are hand speeds, lower than racquet head speeds. All speeds are estimates." : " All speeds are estimates from the sensor."),
  );
}

export function sessionPage(root: HTMLElement) {
  const slot = h("div", { class: "report-slot" }, reportCard(EXAMPLE_REPORT, "Example"));
  const note = h("p", { class: "muted small foot" }, "An illustrative session, not a real one. Open your own report below.");
  const status = h("p", { class: "small", role: "status" });
  const back = h("button", { type: "button", class: "btn small", hidden: true }, icon("arrow-clockwise"), "Back to the example");
  back.addEventListener("click", () => {
    slot.replaceChildren(reportCard(EXAMPLE_REPORT, "Example"));
    note.hidden = false;
    back.hidden = true;
    status.textContent = "";
  });
  const load = async (f: File) => {
    status.className = "small";
    status.textContent = `Reading ${f.name}…`;
    try {
      if (f.size > 2_000_000) throw new Error("That file is too large for a session report.");
      slot.replaceChildren(reportCard(parseReport(await f.text()), f.name));
      reveal(slot);
      note.hidden = true;
      back.hidden = false;
      status.textContent = "";
      slot.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status.className = "small error";
      status.textContent = e instanceof Error ? e.message : String(e);
    }
  };
  root.append(
    h(
      "header",
      { class: "page-hero" },
      h("h1", null, "Session report"),
      h("p", { class: "lead" }, "Everything one session was: swings, speed, rallies, tempo, heart rate and strokes, minute by minute. Open a report from the iPhone app to see your own."),
    ),
    slot,
    note,
    h(
      "div",
      { class: "try" },
      h("h3", null, "Open your report"),
      dropZone({ id: "report-file", accept: ".json,application/json", ico: "file-text", title: "Drop your report.json here, or choose a file", note: "In the iPhone app, open a session and tap Export report (JSON). It's read in your browser and never uploaded." }, (f) => void load(f)),
      status,
      back,
      h("p", { class: "muted small" }, "Only have a swings.csv? ", link("/how-it-works#insights", "Try it on How it works"), "."),
    ),
  );
  return reveal(root);
}
