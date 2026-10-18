// Small inline-SVG charts for the dashboard, built with s() (no chart library, no inline styles: colours are CSS classes).
// Every chart is role="img" with an aria-label that says what it shows; each mark has a <title> for the hover tooltip.
// One axis per chart: two measures of different units get two charts (small multiples), never a second y-scale.

import { h, s } from "./dom";

// The plot SVG stretches to its box (preserveAspectRatio none, non-scaling strokes); axis labels are HTML so they stay
// a readable size from 390 px phones to desktops. Ticks are evenly spaced (lo, mid, hi), so a flex column lines them up.
const W = 600;
const H = 100;
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : String(Math.round(v * 10) / 10));

/** A rounded y range; `zero` pins the bottom at 0 (bars must start at 0). */
function scale(vals: number[], zero: boolean) {
  const max = Math.max(...vals, zero ? 1 : -Infinity);
  const min = zero ? 0 : Math.min(...vals);
  const span = max - min || Math.abs(max) || 1;
  const lo = zero ? 0 : Math.floor(min - span * 0.1);
  const hi = Math.ceil(max + (zero ? 0 : span * 0.1));
  const y = (v: number) => (1 - (v - lo) / Math.max(1e-9, hi - lo)) * H;
  return { y, ticks: [hi, (lo + hi) / 2, lo] };
}

/** Grid lines + plot SVG + the HTML axes around it. */
function frame(svgClass: string, label: string, ticks: number[], ends: [string, string], height: "tall" | "short", marks: SVGElement[]): HTMLElement {
  return h(
    "div",
    { class: `dchart-wrap ${height}` },
    h("div", { class: "yaxis", "aria-hidden": "true" }, ticks.map((t) => h("span", null, fmt(t)))),
    s(
      "svg",
      { viewBox: `0 0 ${W} ${H}`, class: svgClass, role: "img", "aria-label": label, preserveAspectRatio: "none" },
      [0, H / 2, H].map((y) => s("line", { x1: 0, x2: W, y1: y, y2: y, class: "chart-grid" })),
      marks,
    ),
    h("div", { class: "xaxis", "aria-hidden": "true" }, h("span", null, ends[0]), h("span", null, ends[1])),
  );
}

/** Vertical bars from zero, one per point (hover: a <title> per bar). `ends` labels the first and last bar under the axis. */
export function barChart(points: { label: string; value: number }[], o: { label: string; unit: string; ends: [string, string]; short?: boolean }): HTMLElement {
  const { y, ticks } = scale(points.map((p) => p.value), true);
  const bw = W / Math.max(1, points.length);
  const gap = Math.min(bw * 0.25, 6);
  const bars = points.map((p, i) =>
    s(
      "rect",
      { x: (i * bw + gap / 2).toFixed(1), y: y(p.value).toFixed(1), width: Math.max(1, bw - gap).toFixed(1), height: Math.max(0, y(0) - y(p.value)).toFixed(1), class: "dbar" },
      s("title", null, `${p.label}: ${fmt(p.value)} ${o.unit}`),
    ),
  );
  return frame("dchart", o.label, ticks, o.ends, o.short ? "short" : "tall", bars);
}

/** "M x y L x y ..." that lifts the pen over missing values, so a gap shows as a gap (never a line through it). */
export function gappedPath(values: (number | null)[], x: (i: number) => number, y: (v: number) => number): string {
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) return void (pen = false);
    d += `${pen ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;
    pen = true;
  });
  return d;
}

export interface Series {
  name: string;
  values: (number | null)[];
  /** CSS class for the colour: s1 (ink) or s2 (shuttle orange) */
  cls: "s1" | "s2";
}

/** One or more lines on one shared axis (same unit), with gaps where a value is missing. A lone point gets a dot. */
export function lineChart(series: Series[], o: { label: string; unit: string; ends: [string, string]; short?: boolean }): HTMLElement {
  const all = series.flatMap((x) => x.values).filter((v): v is number => v !== null);
  const { y, ticks } = scale(all.length ? all : [0], false);
  const n = Math.max(...series.map((x) => x.values.length));
  const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : W / 2);
  const marks = series.flatMap((sr) => [
    s("path", { d: gappedPath(sr.values, x, y), class: `dline ${sr.cls}` }),
    // an isolated point (missing neighbours) draws nothing as a line: a zero-length round-capped stroke shows it as a dot
    ...sr.values.flatMap((v, i) =>
      v !== null && (sr.values[i - 1] ?? null) === null && (sr.values[i + 1] ?? null) === null
        ? [s("path", { d: `M${x(i).toFixed(1)} ${y(v).toFixed(1)}h0.01`, class: `ddot ${sr.cls}` }, s("title", null, `${sr.name}: ${fmt(v)} ${o.unit}`))]
        : [],
    ),
  ]);
  return frame("dchart lines", o.label, ticks, o.ends, o.short ? "short" : "tall", marks);
}

/** A tiny trend line with no axes (the card around it carries the numbers). */
export function sparkline(values: (number | null)[], label: string): SVGSVGElement {
  const SW = 160;
  const SH = 40;
  const nums = values.filter((v): v is number => v !== null);
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const x = (i: number) => 2 + (i / Math.max(1, values.length - 1)) * (SW - 4);
  const y = (v: number) => 3 + (1 - (v - lo) / (hi - lo || 1)) * (SH - 6);
  const last = values.length - 1;
  return s(
    "svg",
    { viewBox: `0 0 ${SW} ${SH}`, class: "spark", role: "img", "aria-label": label, preserveAspectRatio: "none" },
    nums.length ? s("path", { d: gappedPath(values, x, y), class: "dline s1" }) : null,
    values[last] != null ? s("path", { d: `M${x(last).toFixed(1)} ${y(values[last]!).toFixed(1)}h0.01`, class: "ddot s2" }) : null,
  );
}

/** One horizontal bar split into shares (0-1), with a legend that gives every share as a number (colour is never the only cue). */
export function mixBar(parts: { key: string; label: string; share: number }[], label: string): HTMLElement {
  const MW = 600;
  let at = 0;
  const shown = parts.filter((p) => p.share > 0);
  return h(
    "div",
    { class: "mix-wrap" },
    s(
      "svg",
      { viewBox: `0 0 ${MW} 24`, class: "mix", role: "img", "aria-label": label, preserveAspectRatio: "none" },
      shown.map((p) => {
        const w = p.share * MW;
        const r = s("rect", { x: (at + 1).toFixed(1), y: 0, width: Math.max(0, w - 2).toFixed(1), height: 24, class: `seg seg-${p.key}` }, s("title", null, `${p.label} ${Math.round(p.share * 100)}%`));
        at += w;
        return r;
      }),
    ),
    h("ul", { class: "mix-legend" }, shown.map((p) => h("li", null, h("span", { class: `key seg-${p.key}`, "aria-hidden": "true" }), `${p.label} `, h("strong", null, `${Math.round(p.share * 100)}%`)))),
  );
}
