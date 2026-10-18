// The session report: stat tiles, a swing-by-swing speed chart you can hover or tap, and the insights (src/insights.ts).

import { countUp } from "./anim";
import { h, s } from "./dom";
import { icon } from "./icons";
import type { Report } from "./insights";

function tile(value: number, unit: string, label: string, fmt?: (x: number) => string): HTMLElement {
  return h("div", { class: "stat" }, h("strong", null, countUp(h("span"), value, fmt), unit ? h("small", null, ` ${unit}`) : null), h("span", null, label));
}

/** Bars per swing (the top one in shuttle orange), the mean as a dashed line. Pointer/keyboard selects a bar; the line below reads it out. */
function chart(r: Report): HTMLElement {
  const W = 600;
  const H = 160;
  const n = r.swings.length;
  const lo = Math.max(0, Math.floor((Math.min(...r.swings.map((x) => x.kmh)) - 10) / 10) * 10);
  const hi = Math.ceil((r.topKmh + 5) / 10) * 10;
  const bw = W / n;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const bars = r.swings.map((sw, i) =>
    s("rect", { x: (i * bw + bw * 0.18).toFixed(1), y: y(sw.kmh).toFixed(1), width: (bw * 0.64).toFixed(1), height: (H - y(sw.kmh)).toFixed(1), rx: Math.min(3, bw * 0.3).toFixed(1), class: i === r.topIndex ? "sbar top" : sw.impact ? "sbar" : "sbar miss" }),
  );
  const svg = s(
    "svg",
    { viewBox: `0 0 ${W} ${H}`, class: "swing-chart", role: "img", "aria-label": `Head speed for each of ${n} swings: average ${Math.round(r.meanKmh)} km/h, top ${Math.round(r.topKmh)} km/h.`, preserveAspectRatio: "none" },
    s("g", { class: "bars" }, bars),
    s("path", { d: `M0 ${y(r.meanKmh).toFixed(1)}H${W}`, class: "mean-line" }),
  );
  const read = h("p", { class: "chart-read", "aria-live": "polite" });
  const describe = (i: number) => {
    const sw = r.swings[i];
    const bits = [`Swing ${i + 1}`, `${Math.round(sw.kmh)} km/h`, sw.timing, sw.side, sw.impact ? null : "no hit"].filter(Boolean);
    return bits.join(" · ");
  };
  const idle = () => `Average ${Math.round(r.meanKmh)} km/h (dashed line). Hover or tap a bar.`;
  read.textContent = idle();
  let cur = -1;
  const select = (i: number) => {
    if (cur >= 0) bars[cur].classList.remove("on");
    cur = i;
    if (i < 0) return void (read.textContent = idle());
    bars[i].classList.add("on");
    read.textContent = describe(i);
  };
  const at = (e: PointerEvent) => {
    const box = svg.getBoundingClientRect();
    return Math.max(0, Math.min(n - 1, Math.floor(((e.clientX - box.left) / box.width) * n)));
  };
  svg.addEventListener("pointermove", (e) => select(at(e)));
  svg.addEventListener("pointerdown", (e) => select(at(e)));
  svg.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && select(-1));
  const wrap = h("div", { class: "chart-wrap", tabindex: "0", "aria-label": "Swing chart. Left and right arrows step through swings." }, svg);
  wrap.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    select(Math.max(0, Math.min(n - 1, (cur < 0 ? 0 : cur) + (e.key === "ArrowRight" ? 1 : -1))));
  });
  return h("div", null, wrap, read);
}

export function insightList(r: Report): HTMLElement {
  return h(
    "ul",
    { class: "insights" },
    r.insights.map((x, i) =>
      h("li", { class: `insight tone-${x.tone} reveal d${Math.min(i, 5)}` }, h("span", { class: "insight-ico" }, icon(x.icon)), h("div", null, h("h4", null, x.title), h("p", null, x.text))),
    ),
  );
}

/** The whole report card. `badge` is a small label next to the title (e.g. "Example"). */
export function sessionReport(r: Report, title: string, badge?: string): HTMLElement {
  const hits = r.scored.length;
  return h(
    "article",
    { class: "card glass report" },
    h("div", { class: "report-head" }, h("h3", null, icon("waveform"), title), badge ? h("span", { class: "badge" }, badge) : null),
    h(
      "div",
      { class: "stats-row" },
      tile(hits, "", hits === r.swings.length ? "swings" : "hits"),
      tile(r.meanKmh, "km/h", "average speed"),
      tile(r.topKmh, "km/h", "top speed"),
      tile(Math.max(1, r.minutes), "min", "on court"),
    ),
    chart(r),
    h("h3", { class: "insights-h" }, icon("sparkle-fill"), "Insights"),
    insightList(r),
  );
}
