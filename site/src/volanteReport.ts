// Session report files: the `volante-report/1` JSON the iPhone app exports as report.json (contract: docs/strokes/report-format.md,
// schema docs/strokes/report.schema.json). Parsed and checked in the browser; nothing is uploaded.

import type { IconName } from "./icons";
import type { Insight } from "./insights";

export const REPORT_SCHEMA = "volante-report/1";

export interface Minute {
  t_min: number;
  swings: number;
  p90_kmh: number | null;
  hr_bpm: number | null;
}

export interface ReportInsight {
  id: string;
  severity: "info" | "tip" | "praise";
  title: string;
  body: string;
  evidence: Record<string, number>;
}

export interface Strokes {
  groups: Record<string, number>;
  counts: Record<string, Record<string, number>>;
  speed_by_class: Record<string, { p50_kmh: number; p90_kmh: number }>;
  unclassified: number;
  experimental: boolean;
}

export interface VolanteReport {
  schema: typeof REPORT_SCHEMA;
  session: { started_at: string; duration_s: number; active_s: number; source: "pod" | "watch"; pod_fw: string };
  swings: { count: number; per_min_active: number | null };
  speed: { p50_kmh: number | null; p90_kmh: number | null; max_kmh: number | null; clipped_frac: number; estimated: true };
  tempo: { median_gap_s: number | null; p25_gap_s: number | null; p75_gap_s: number | null };
  rallies: { count: number; median_shots: number | null; longest_shots: number };
  timeline: Minute[];
  heart_rate: { avg_bpm: number; max_bpm: number } | null;
  strokes: Strokes | null;
  insights: ReportInsight[];
  model: { name: string; version: string } | null;
}

// ---------------------------------------------------------------- parsing

class Bad extends Error {}
type Obj = Record<string, unknown>;

const obj = (v: unknown, where: string): Obj => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) throw new Bad(`${where} is missing.`);
  return v as Obj;
};
const num = (o: Obj, k: string, where: string, signed = false): number => {
  const v = o[k];
  if (typeof v !== "number" || !Number.isFinite(v) || (v < 0 && !signed)) throw new Bad(`${where}.${k} should be a number.`);
  return v;
};
const numOrNull = (o: Obj, k: string, where: string): number | null => (o[k] === null ? null : num(o, k, where));
const str = (o: Obj, k: string, where: string, max = 200): string => {
  const v = o[k];
  if (typeof v !== "string" || v.length > max) throw new Bad(`${where}.${k} should be text.`);
  return v;
};
const counts = (v: unknown, where: string, signed = false): Record<string, number> => {
  const o = obj(v, where);
  const out: Record<string, number> = {};
  for (const k of Object.keys(o).slice(0, 40)) out[k.slice(0, 40)] = num(o, k, where, signed);
  return out;
};

/** Checks a report.json and returns it typed. Throws a message meant for people when it isn't one. */
export function parseReport(text: string): VolanteReport {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("That file isn't a Volante report: it isn't valid JSON.");
  }
  try {
    const r = obj(raw, "The report");
    if (r.schema !== REPORT_SCHEMA) throw new Bad(typeof r.schema === "string" && r.schema.startsWith("volante-report/") ? `This report uses ${r.schema.slice(0, 40)}; this page reads ${REPORT_SCHEMA}.` : "That file isn't a Volante report. Export report.json from a session in the iPhone app.");
    const se = obj(r.session, "session");
    const started = str(se, "started_at", "session", 40);
    if (Number.isNaN(Date.parse(started))) throw new Bad("session.started_at isn't a date.");
    const source = str(se, "source", "session", 10);
    if (source !== "pod" && source !== "watch") throw new Bad("session.source should be pod or watch.");
    const sw = obj(r.swings, "swings");
    const sp = obj(r.speed, "speed");
    const te = obj(r.tempo, "tempo");
    const ra = obj(r.rallies, "rallies");
    if (!Array.isArray(r.timeline) || r.timeline.length > 1440) throw new Bad("timeline should be a list of minutes.");
    const timeline = r.timeline.map((m, i) => {
      const o = obj(m, `timeline[${i}]`);
      return { t_min: num(o, "t_min", "timeline"), swings: num(o, "swings", "timeline"), p90_kmh: numOrNull(o, "p90_kmh", "timeline"), hr_bpm: numOrNull(o, "hr_bpm", "timeline") };
    });
    const hr = r.heart_rate === null ? null : obj(r.heart_rate, "heart_rate");
    let strokes: Strokes | null = null;
    if (r.strokes !== null) {
      const st = obj(r.strokes, "strokes");
      const byClass: Strokes["speed_by_class"] = {};
      for (const [k, v] of Object.entries(obj(st.speed_by_class, "strokes.speed_by_class")).slice(0, 40)) {
        const o = obj(v, `strokes.speed_by_class.${k}`);
        byClass[k.slice(0, 40)] = { p50_kmh: num(o, "p50_kmh", k), p90_kmh: num(o, "p90_kmh", k) };
      }
      const cls: Strokes["counts"] = {};
      for (const [k, v] of Object.entries(obj(st.counts, "strokes.counts")).slice(0, 40)) cls[k.slice(0, 40)] = counts(v, `strokes.counts.${k}`);
      strokes = { groups: counts(st.groups, "strokes.groups"), counts: cls, speed_by_class: byClass, unclassified: num(st, "unclassified", "strokes"), experimental: st.experimental !== false };
    }
    if (!Array.isArray(r.insights)) throw new Bad("insights should be a list.");
    const insights = r.insights.slice(0, 10).map((x, i) => {
      const o = obj(x, `insights[${i}]`);
      const sev = o.severity === "tip" || o.severity === "praise" ? o.severity : "info";
      const ev = o.evidence && typeof o.evidence === "object" ? counts(o.evidence, "evidence", true) : {};
      return { id: str(o, "id", "insight", 60), severity: sev, title: str(o, "title", "insight"), body: str(o, "body", "insight", 600), evidence: ev } satisfies ReportInsight;
    });
    const mo = r.model === null || r.model === undefined ? null : obj(r.model, "model");
    return {
      schema: REPORT_SCHEMA,
      session: { started_at: started, duration_s: num(se, "duration_s", "session"), active_s: num(se, "active_s", "session"), source, pod_fw: typeof se.pod_fw === "string" ? se.pod_fw.slice(0, 40) : "unknown" },
      swings: { count: num(sw, "count", "swings"), per_min_active: numOrNull(sw, "per_min_active", "swings") },
      speed: { p50_kmh: numOrNull(sp, "p50_kmh", "speed"), p90_kmh: numOrNull(sp, "p90_kmh", "speed"), max_kmh: numOrNull(sp, "max_kmh", "speed"), clipped_frac: num(sp, "clipped_frac", "speed"), estimated: true },
      tempo: { median_gap_s: numOrNull(te, "median_gap_s", "tempo"), p25_gap_s: numOrNull(te, "p25_gap_s", "tempo"), p75_gap_s: numOrNull(te, "p75_gap_s", "tempo") },
      rallies: { count: num(ra, "count", "rallies"), median_shots: numOrNull(ra, "median_shots", "rallies"), longest_shots: num(ra, "longest_shots", "rallies") },
      timeline,
      heart_rate: hr ? { avg_bpm: num(hr, "avg_bpm", "heart_rate"), max_bpm: num(hr, "max_bpm", "heart_rate") } : null,
      strokes,
      insights,
      model: mo ? { name: str(mo, "name", "model", 60), version: str(mo, "version", "model", 20) } : null,
    };
  } catch (e) {
    if (e instanceof Bad) throw new Error(`That report can't be read: ${e.message}`);
    throw e;
  }
}

// ---------------------------------------------------------------- insights

const ICON: Record<string, IconName> = {
  speed_fade: "trend-down",
  new_best: "sparkle-fill",
  near_best: "sparkle-fill",
  tempo_vs_usual: "timer",
  short_rallies: "ranking",
  long_rallies: "ranking",
  consistency: "crosshair",
  clipping: "gauge",
  hr_summary: "heartbeat",
  volume_trend: "target",
  stroke_variety: "list-checks",
};
const TONE = { praise: "good", tip: "tip", info: "info" } as const;

/** The report's own insights (written by the app) in the site's insight shape. */
export function reportInsights(r: VolanteReport): Insight[] {
  return r.insights.map((x) => ({ icon: ICON[x.id] ?? "info", title: x.title, text: x.body, tone: TONE[x.severity] }));
}

// ---------------------------------------------------------------- example

const SW = [18, 26, 31, 29, 33, 30, 12, 0, 24, 32, 35, 31, 28, 30, 27, 9, 0, 22, 29, 27, 26, 24, 21, 14];
const P90 = [52, 58, 61, 63, 62, 64, 55, null, 60, 63, 65, 64, 62, 63, 61, 54, null, 57, 58, 56, 55, 54, 53, 52];
const HR = [118, 131, 142, 148, 151, 153, 139, 121, 138, 149, 155, 157, 156, 158, 155, 141, 124, 140, 150, 152, 153, 151, 150, 144];

/** An illustrative 24-minute doubles session with heart rate and the (experimental) stroke model. Not a real session. */
export const EXAMPLE_REPORT: VolanteReport = {
  schema: REPORT_SCHEMA,
  session: { started_at: "2026-10-17T22:10:00Z", duration_s: 1426, active_s: 1104, source: "pod", pod_fw: "unknown" },
  swings: { count: SW.reduce((a, b) => a + b, 0), per_min_active: 30.3 },
  speed: { p50_kmh: 34.8, p90_kmh: 61.2, max_kmh: 97.4, clipped_frac: 0.0412, estimated: true },
  tempo: { median_gap_s: 1.71, p25_gap_s: 1.38, p75_gap_s: 2.15 },
  rallies: { count: 61, median_shots: 7, longest_shots: 23 },
  timeline: SW.map((swings, t) => ({ t_min: t, swings, p90_kmh: P90[t], hr_bpm: HR[t] })),
  heart_rate: { avg_bpm: 145, max_bpm: 166 },
  strokes: {
    groups: { power: 132, mid: 241, touch: 168, unclassified: 17 },
    counts: { smash: { all: 74 }, clear: { all: 58 }, drive: { all: 96 }, drop: { all: 81 }, lift: { all: 52 }, net_kill: { all: 12 }, net_shot: { all: 121 }, block_push: { all: 47 } },
    speed_by_class: { smash: { p50_kmh: 71.5, p90_kmh: 88.2 }, clear: { p50_kmh: 58.9, p90_kmh: 70.3 }, drive: { p50_kmh: 47.1, p90_kmh: 59.0 } },
    unclassified: 17,
    experimental: true,
  },
  insights: [
    { id: "speed_fade", severity: "tip", title: "Your hardest swings faded", body: "Hard swings (p90) went from 63 to 54 km/h between the first and last third, 14 % slower. A rest or water break can help keep power late.", evidence: { first_third_p90_kmh: 63, last_third_p90_kmh: 54, drop_pct: 14 } },
    { id: "tempo_vs_usual", severity: "info", title: "Quicker than usual", body: "Your median time between shots was 1.7 s, 15 % quicker than your last 5 sessions (2.0 s).", evidence: { median_gap_s: 1.71, usual_gap_s: 2.01, change_pct: -14.9 } },
    { id: "hr_summary", severity: "info", title: "Heart rate", body: "Your heart rate averaged 145 bpm and peaked at 166 bpm.", evidence: { avg_bpm: 145, max_bpm: 166 } },
  ],
  model: { name: "stroke_group_v0", version: "0.1" },
};
