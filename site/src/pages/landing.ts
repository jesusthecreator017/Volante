// "/" : the racquet pod landing page. Rendered stills of our own renderer plus inline SVG; no live 3D, no data collected.
// Feature statuses are honest (Available / In development): flip them in FEATURES when something ships.

import { h, link, s } from "../dom";

type Status = "Available" | "In development";
type Viz = "hits" | "gauge" | "tags" | "bars" | "replay" | "live";

interface Feature {
  id: string;
  icon: string;
  title: string;
  text: string;
  status: Status;
  viz: Viz;
}

export const FEATURES: Feature[] = [
  { id: "hits", icon: "hit", title: "Hit tracking", text: "Every swing is detected and timestamped, so you can see exactly when you hit.", status: "Available", viz: "hits" },
  { id: "speed", icon: "speed", title: "Swing speed", text: "Estimated racquet head speed for each swing, worked out from the gyro.", status: "Available", viz: "gauge" },
  { id: "strokes", icon: "strokes", title: "Stroke recognition", text: "Smash, clear, drop, drive, forehand or backhand, named automatically.", status: "In development", viz: "tags" },
  { id: "insights", icon: "insights", title: "AI-tailored insights", text: "Post-match reports that compare sessions and suggest what to work on.", status: "In development", viz: "bars" },
  { id: "replay", icon: "replay", title: "3D replay", text: "Watch a recorded match rebuilt in 3D. Needs the camera setup.", status: "Available", viz: "replay" },
  { id: "live", icon: "live", title: "Live racquet view", text: "See your racquet move on screen in real time while you swing.", status: "Available", viz: "live" },
];

// Numbers from the 2026-10-17 smash session analysis (estimates from the sensor).
export const SESSION = { smashes: 41, meanKmh: 69, maxKmh: 95 };

/** Deterministic example series for the sparkline: 41 swings with exactly the session's mean and max. Illustrative. */
export function exampleSwings(): number[] {
  let seed = 11;
  const r = Array.from({ length: SESSION.smashes }, () => ((seed = (seed * 16807) % 2147483647) / 2147483647));
  const mean = r.reduce((a, b) => a + b, 0) / r.length;
  const k = (SESSION.maxKmh - SESSION.meanKmh) / (Math.max(...r) - mean);
  return r.map((x) => SESSION.meanKmh + (x - mean) * k);
}

// ---------------------------------------------------------------- imagery

const STILLS = {
  ready: [new URL("../assets/ready.webp", import.meta.url).href, new URL("../assets/ready.png", import.meta.url).href],
  swing: [new URL("../assets/swing.webp", import.meta.url).href, new URL("../assets/swing.png", import.meta.url).href],
  follow: [new URL("../assets/follow.webp", import.meta.url).href, new URL("../assets/follow.png", import.meta.url).href],
} as const;

/** Our own render (640x512 source). Width/height reserve the space (CLS); below-the-fold stills load lazily. */
function still(name: keyof typeof STILLS, alt: string, lazy: boolean): HTMLElement {
  const [webp, png] = STILLS[name];
  return h(
    "picture",
    null,
    h("source", { type: "image/webp", srcset: webp }),
    h("img", { src: png, alt, width: 640, height: 512, loading: lazy ? "lazy" : "eager", decoding: "async", ...(lazy ? {} : { fetchpriority: "high" }) }),
  );
}

// ---------------------------------------------------------------- icons: 24px grid, one stroke width (CSS .ico)

const ICONS: Record<string, string[]> = {
  hit: ["M12 4a8 8 0 1 0 0 16a8 8 0 0 0 0-16z", "M12 9a3 3 0 1 0 0 6a3 3 0 0 0 0-6z", "M12 1v3M12 20v3M1 12h3M20 12h3"],
  speed: ["M4 17a8 8 0 1 1 16 0", "M12 17l4-5", "M7 17h.01M17 17h.01"],
  strokes: ["M9 6h11M9 12h11M9 18h11", "M4 6h.01M4 12h.01M4 18h.01"],
  insights: ["M4 4v16h16", "M8 15l4-4 3 3 5-6"],
  replay: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M12 12l8-4.5M12 12v9M12 12L4 7.5"],
  live: ["M12 11a1 1 0 1 0 0 2a1 1 0 0 0 0-2z", "M8 8a5.7 5.7 0 0 0 0 8M16 8a5.7 5.7 0 0 1 0 8", "M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"],
};

function icon(name: string): SVGSVGElement {
  return s("svg", { viewBox: "0 0 24 24", class: "ico", "aria-hidden": "true", focusable: "false" }, ICONS[name].map((d) => s("path", { d })));
}

/** The pod: a small rounded body clipped to a racquet grip, with a status LED. Used in the hero and step 1. */
function podArt(cls: string): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 120 80", class: cls, "aria-hidden": "true", focusable: "false" },
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
    s("path", { d: "M70 62h26M78 70h26", class: "art-line" }),
  );
}

function reportArt(): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 120 80", class: "step-art", "aria-hidden": "true", focusable: "false" },
    s("rect", { x: 30, y: 8, width: 60, height: 64, rx: 6, class: "art-body" }),
    [20, 32, 44].map((x, i) => s("rect", { x, y: 56 - [14, 26, 18][i], width: 8, height: [14, 26, 18][i], rx: 2, class: "art-led" })),
    s("path", { d: "M40 22h40", class: "art-line" }),
  );
}

/** Hero collage: our own racquet render layered over the shape kit. The diagonal bar and the dotted arc are the
 *  shuttle's flight path; the orange circle is the shuttle. Pure SVG, classes only (CSP). */
function collage(): HTMLElement {
  const g = (cls: string, ...kids: SVGElement[]) => s("g", { class: cls }, kids);
  return h(
    "figure",
    { class: "collage" },
    s(
      "svg",
      { viewBox: "0 0 600 640", role: "img", "aria-label": "Collage: our rendered racquet, a shuttle flying along a dotted arc, over a diagonal bar, circles and a triangle." },
      g("cl-par1", s("circle", { cx: 360, cy: 270, r: 200, class: "f-paper" })),
      g("cl-in cl-d1", s("rect", { x: -60, y: 395, width: 740, height: 64, class: "f-sage", transform: "rotate(-36 300 427)" })),
      g(
        "cl-par2 cl-in cl-d2",
        s("rect", { x: 300, y: 40, width: 270, height: 300, class: "f-accent" }),
        s("image", { href: STILLS.ready[1], x: 300, y: 82, width: 270, height: 216, preserveAspectRatio: "xMidYMid slice" }),
        s("rect", { x: 300.5, y: 40.5, width: 269, height: 299, class: "f-none s-line" }),
      ),
      s("rect", { x: 40, y: 170, width: 130, height: 130, class: "f-none s-line s-dot" }),
      g("cl-in cl-d3", s("polygon", { points: "60,130 150,130 60,40", class: "f-accent" })),
      s("polygon", { points: "76,146 166,146 76,56", class: "f-none s-line" }),
      s("circle", { cx: 470, cy: 470, r: 112, class: "f-none s-line" }),
      s("path", { d: "M110 610C230 400 380 330 468 458", class: "f-none s-line s-arc" }),
      s("g", { transform: "translate(470 470) rotate(-36)" }, s("circle", { r: 14, class: "f-hot" }), s("path", { d: "M-14 0-46-16M-14 0H-46M-14 0-46 16", class: "f-none s-hot s-2" })),
      s("path", { d: "M20 612h16M28 604v16M572 28h16M580 20v16", class: "f-none s-line" }),
    ),
  );
}

// ---------------------------------------------------------------- feature visuals (decorative)

function viz(kind: Viz): HTMLElement {
  if (kind === "replay") return h("div", { class: "viz viz-still" }, still("swing", "Rendered racquet mid-swing, tilted across the frame on a dark 3D grid.", true));
  if (kind === "live") return h("div", { class: "viz viz-still" }, still("follow", "Rendered racquet in follow-through, angled up and to the left on a dark 3D grid.", true));
  if (kind === "tags") return h("div", { class: "viz tags", "aria-hidden": "true" }, ["Smash", "Clear", "Drop", "Drive"].map((t) => h("span", { class: "tag" }, t)));
  const svg = (children: SVGElement[]) => s("svg", { viewBox: "0 0 160 56", class: "viz-svg", "aria-hidden": "true", focusable: "false" }, children);
  if (kind === "hits") {
    const xs = [8, 30, 44, 71, 90, 98, 126, 150];
    return h("div", { class: "viz" }, svg([s("path", { d: "M4 48h152", class: "viz-base" }), ...xs.map((x, i) => s("path", { d: `M${x} 48v-${[22, 34, 18, 40, 26, 30, 36, 20][i]}`, class: "viz-tick" }))]));
  }
  if (kind === "gauge") {
    return h("div", { class: "viz" }, svg([s("path", { d: "M40 52a40 40 0 0 1 80 0", class: "viz-base" }), s("path", { d: "M40 52a40 40 0 0 1 62.5-33", class: "viz-fill" }), s("path", { d: "M80 52l22-22", class: "viz-needle" })]));
  }
  const hs = [14, 22, 18, 30, 26, 40];
  return h("div", { class: "viz" }, svg(hs.map((v, i) => s("rect", { x: 14 + i * 24, y: 52 - v, width: 14, height: v, rx: 2, class: i === 5 ? "viz-fill-bar" : "viz-bar" }))));
}

function feature(f: Feature): HTMLElement {
  const avail = f.status === "Available";
  return h(
    "article",
    { class: avail ? "feature reveal" : "feature reveal dev" },
    h("div", { class: "feature-head" }, icon(f.icon), h("h3", null, f.title), h("span", { class: avail ? "chip chip-ok" : "chip chip-dev" }, f.status)),
    h("p", null, f.text),
    viz(f.viz),
  );
}

// ---------------------------------------------------------------- sample report

function sparkline(vals: number[]): SVGSVGElement {
  const W = 300;
  const H = 80;
  const lo = 40;
  const hi = 100;
  const x = (i: number) => (i / (vals.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const maxI = vals.indexOf(Math.max(...vals));
  return s(
    "svg",
    { viewBox: `0 0 ${W} ${H}`, class: "spark", role: "img", "aria-label": `Example chart: estimated head speed across ${vals.length} swings, mean ${SESSION.meanKmh} km/h, peak ${SESSION.maxKmh} km/h.` },
    s("path", { d: `M0 ${y(SESSION.meanKmh)}H${W}`, class: "spark-mean" }),
    s("polyline", { points: vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "), class: "spark-line" }),
    s("circle", { cx: x(maxI), cy: y(vals[maxI]), r: 4, class: "spark-max" }),
  );
}

function report(): HTMLElement {
  const stat = (v: string, l: string) => h("div", { class: "stat" }, h("strong", null, v), h("span", null, l));
  return h(
    "div",
    { class: "report card" },
    h("div", { class: "report-head" }, h("h3", null, "Smash session"), h("span", { class: "chip chip-example" }, "Example")),
    h("div", { class: "stats" }, stat(String(SESSION.smashes), "smashes"), stat(`~${SESSION.meanKmh} km/h`, "mean head speed (est.)"), stat(`~${SESSION.maxKmh} km/h`, "top head speed (est.)")),
    h("div", { class: "strokes" }, h("span", null, "Smash"), h("span", { class: "meter", "aria-hidden": "true" }, h("span", { class: "meter-fill" })), h("strong", null, SESSION.smashes)),
    h("p", { class: "muted small" }, "Clear, drop and drive counts will appear here once stroke recognition ships."),
    h("p", { class: "spark-label small muted" }, "Swing speed, swing by swing"),
    sparkline(exampleSwings()),
  );
}

// ---------------------------------------------------------------- page

function step(n: number, art: SVGSVGElement, title: string, text: string): HTMLElement {
  return h("li", { class: `step s${n} reveal` }, art, h("span", { class: "step-n", "aria-hidden": "true" }, `0${n}`), h("h3", null, title), h("p", null, text));
}

/** Section heading: a light lead-in and a heavy payoff. */
function head(id: string, light: string, heavy: string): HTMLElement {
  return h("h2", { id }, h("span", { class: "lt" }, light), " ", h("span", { class: "hv" }, heavy));
}

function teaserArt(cls: string): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 200 200", class: cls, "aria-hidden": "true", focusable: "false" },
    s("circle", { cx: 140, cy: 60, r: 44, class: "tz-c1" }),
    s("circle", { cx: 120, cy: 80, r: 44, class: "f-none tz-c2" }),
    s("path", { d: "M0 200 200 0", class: "tz-bar" }),
  );
}

export function landing(root: HTMLElement) {
  const chip = (t: string) => h("li", { class: "proof" }, t);
  root.append(
    h(
      "section",
      { class: "l-hero" },
      h(
        "div",
        { class: "l-hero-copy" },
        h("h1", { class: "display" }, h("span", { class: "lt" }, "Every swing,"), h("br"), h("span", { class: "hv" }, "measured.")),
        h("p", { class: "lead" }, "A small pod clips onto your racquet, records every swing at 500 samples a second and turns it into swing counts, speed and replays."),
        h("div", { class: "row cta" }, link("/#how", h("span", { class: "btn primary" }, "See how it works")), link("/arena", h("span", { class: "btn" }, "Try the Arena"))),
        h("ul", { class: "proofs", "aria-label": "Key facts" }, chip("500 samples/s"), chip("6-axis motion"), chip("Bluetooth LE")),
      ),
      collage(),
    ),
    h("section", { class: "l-sec", id: "features", "aria-labelledby": "features-h", tabindex: "-1" }, head("features-h", "Hard numbers.", "Honest labels."), h("div", { class: "features" }, FEATURES.map(feature))),
    h(
      "section",
      { class: "l-sec how", id: "how", "aria-labelledby": "how-h", tabindex: "-1" },
      head("how-h", "Three steps,", "one diagonal."),
      h(
        "ol",
        { class: "steps" },
        step(1, podArt("step-art"), "Attach the pod", "Clip it onto the racquet. It has a six-axis motion sensor and talks over Bluetooth LE."),
        step(2, swingArt(), "Play", "The pod can record about 12 minutes on its own, or stream live to your computer while you swing."),
        step(3, reportArt(), "Get your report", "Swings are counted and timed and head speed is estimated, then shown as a report."),
      ),
    ),
    h("section", { class: "l-sec", id: "report", "aria-labelledby": "report-h" }, head("report-h", "Read a session", "in ten seconds."), report(), h("p", { class: "muted small foot" }, "Example. Headline figures come from a real 41-smash session; the swing-by-swing chart is illustrative. All values are estimates from the sensor.")),
    h(
      "section",
      { class: "l-sec teasers" },
      h(
        "article",
        { class: "arena-teaser card reveal", "aria-labelledby": "arena-h" },
        teaserArt("tz-art tz-block"),
        h("h2", { id: "arena-h" }, h("span", { class: "lt" }, "Train a small AI."), " ", h("span", { class: "hv" }, "Watch it play.")),
        h("p", null, "The Arena is a badminton game for AIs: train a small neural network, upload it, and watch it climb the ladder and play weekly tournaments in 3D."),
        h("p", null, link("/arena", h("span", { class: "btn primary" }, "Explore the Arena"))),
      ),
      h(
        "article",
        { class: "twin-teaser card reveal", "aria-labelledby": "twin-h" },
        teaserArt("tz-art"),
        h("span", { class: "chip chip-twin" }, "Twin"),
        h("h2", { id: "twin-h" }, h("span", { class: "lt" }, "Train an AI that"), " ", h("span", { class: "hv" }, "plays like you.")),
        h("p", null, "A Volante Twin learns from your pod data: your power, consistency and rhythm. Twins will compete on their own ladder."),
        h("p", null, link("/arena/ladder", h("span", { class: "btn" }, "See the ladder"))),
      ),
    ),
  );

  // Section reveal: transform/opacity only, and CSS only hides anything when motion is allowed.
  const els = [...root.querySelectorAll<HTMLElement>(".reveal")];
  if (!("IntersectionObserver" in window)) {
    for (const el of els) el.classList.add("in");
    return;
  }
  const io = new IntersectionObserver(
    (es) => {
      for (const e of es) if (e.isIntersecting) (e.target.classList.add("in"), io.unobserve(e.target));
    },
    { rootMargin: "0px 0px -8% 0px" },
  );
  for (const el of els) io.observe(el);
  return () => io.disconnect();
}
