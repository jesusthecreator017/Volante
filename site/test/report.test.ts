import { describe, expect, test } from "vitest";
import { reportCard, sessionPage } from "../src/pages/session";
import { EXAMPLE_REPORT, parseReport, reportInsights } from "../src/volanteReport";

import plain from "../../docs/strokes/report.example.json";
import strokesExample from "../../docs/strokes/report.example-strokes.json";
import vector from "../../tools/strokes/data/report_vector.json";

const DOCS: Record<string, string> = { "report.example.json": JSON.stringify(plain), "report.example-strokes.json": JSON.stringify(strokesExample) };
const doc = (name: string) => DOCS[name];

describe("session report file (volante-report/1)", () => {
  test("reads both contract examples", () => {
    const plain = parseReport(doc("report.example.json"));
    expect(plain.swings.count).toBe(8);
    expect(plain.strokes).toBeNull();
    expect(plain.heart_rate).toBeNull();
    const withStrokes = parseReport(doc("report.example-strokes.json"));
    expect(withStrokes.strokes?.groups.power).toBe(3);
    expect(withStrokes.model?.name).toBe("stroke_group_v0");
    expect(reportInsights(withStrokes)).toEqual([{ icon: "timer", title: "Quicker than usual", text: expect.stringContaining("2.0 s"), tone: "info" }]);
  });
  test("reads the iPhone parity vector", () => {
    const r = parseReport(JSON.stringify(vector));
    expect(r.schema).toBe("volante-report/1");
  });
  test("the example is a valid report whose totals add up", () => {
    const r = parseReport(JSON.stringify(EXAMPLE_REPORT));
    expect(r.swings.count).toBe(r.timeline.reduce((a, m) => a + m.swings, 0));
    expect(Object.values(r.strokes!.groups).reduce((a, b) => a + b, 0)).toBe(r.swings.count);
  });
  test("wrong files say why", () => {
    expect(() => parseReport("nope")).toThrow(/valid JSON/);
    expect(() => parseReport('{"schema":"oso-style/1"}')).toThrow(/isn't a Volante report/);
    expect(() => parseReport('{"schema":"volante-report/2"}')).toThrow(/volante-report\/2/);
    const broken = JSON.parse(doc("report.example.json"));
    broken.speed.max_kmh = "fast";
    expect(() => parseReport(JSON.stringify(broken))).toThrow(/speed\.max_kmh/);
  });
  test("renders without inline styles, with strokes marked experimental", () => {
    const card = reportCard(EXAMPLE_REPORT, "Example");
    expect(card.querySelectorAll("[style]").length).toBe(0);
    expect(card.querySelectorAll(".tl-chart .sbar").length).toBe(24);
    expect(card.querySelector(".tl-hr")).not.toBeNull();
    expect(card.querySelector(".strokes .badge")?.textContent).toBe("Experimental");
    expect(card.querySelectorAll(".insight").length).toBe(3);
    expect(card.textContent).toContain("estimates");
  });
  test("a report with no heart rate or strokes leaves them out", () => {
    const card = reportCard(parseReport(doc("report.example.json")), "x");
    expect(card.querySelector(".strokes")).toBeNull();
    expect(card.querySelector(".tl")).toBeNull(); // one minute: no timeline
    expect(card.textContent).not.toContain("bpm");
  });
  test("the page offers a JSON file picker", () => {
    const root = document.createElement("main");
    sessionPage(root);
    expect(root.querySelector<HTMLInputElement>("#report-file")?.accept).toContain(".json");
    expect(root.querySelector("h1")?.textContent).toBe("Session report");
  });
});
