// "/how-it-works": three steps and one real session's numbers (estimates from the sensor).

import { h, link, s } from "../dom";

// Numbers from the 2026-10-17 smash session analysis (estimates from the sensor).
export const SESSION = { smashes: 41, meanKmh: 69, maxKmh: 95 };

/** The pod: a small rounded body clipped to a racquet grip, with a status LED. */
function podArt(): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 120 80", class: "step-art", "aria-hidden": "true", focusable: "false" },
    s("path", { d: "M8 40h32M80 40h32", class: "art-line" }),
    s("rect", { x: 40, y: 26, width: 40, height: 28, rx: 8, class: "art-body" }),
    s("circle", { cx: 52, cy: 40, r: 3.5, class: "art-led" }),
    s("path", { d: "M62 36h10M62 44h6", class: "art-line" }),
  );
}

function swingArt(): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 120 80", class: "step-art", "aria-hidden": "true", focusable: "false" },
    s("path", { d: "M20 64A52 52 0 0 1 96 20", class: "art-line art-dash" }),
    s("path", { d: "M90 14l8 5-9 4", class: "art-line" }),
    s("circle", { cx: 22, cy: 62, r: 5, class: "art-led" }),
  );
}

function reportArt(): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 120 80", class: "step-art", "aria-hidden": "true", focusable: "false" },
    s("rect", { x: 30, y: 8, width: 60, height: 64, rx: 6, class: "art-body" }),
    [44, 56, 68].map((x, i) => s("rect", { x, y: 60 - [14, 26, 18][i], width: 8, height: [14, 26, 18][i], rx: 2, class: "art-led" })),
  );
}

function step(n: number, art: SVGSVGElement, title: string, text: string): HTMLElement {
  return h("li", { class: "step" }, art, h("h3", null, h("span", { class: "step-n" }, n), title), h("p", null, text));
}

export function howItWorks(root: HTMLElement) {
  const stat = (v: string, l: string) => h("div", { class: "stat" }, h("strong", null, v), h("span", null, l));
  root.append(
    h("h1", null, "How it works"),
    h(
      "ol",
      { class: "steps" },
      step(1, podArt(), "Attach", "Clip the pod onto your racquet."),
      step(2, swingArt(), "Play", "It records up to 12 minutes of swings on its own."),
      step(3, reportArt(), "Review", "Sync to see every swing counted, with its head speed."),
    ),
    h(
      "section",
      { class: "session", "aria-labelledby": "session-h" },
      h("h2", { id: "session-h" }, "A real session"),
      h("div", { class: "session-stats" }, stat(String(SESSION.smashes), "smashes"), stat(`${SESSION.meanKmh} km/h`, "average speed"), stat(`${SESSION.maxKmh} km/h`, "top speed")),
      h("p", { class: "muted small" }, "Speeds are estimates from the sensor."),
    ),
    h("p", { class: "row" }, link("/#features", h("span", { class: "btn" }, "Features")), link("/arena", h("span", { class: "btn" }, "Arena"))),
  );
}
