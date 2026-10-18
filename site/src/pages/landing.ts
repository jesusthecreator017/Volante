// "/" : the racquet pod landing page. Rendered stills of our own renderer plus inline SVG; no live 3D, no data collected.
// Features that haven't shipped say so in their text: flip `soon` in FEATURES when something ships. How it works: pages/how.ts.

import { api } from "../api";
import { h, link, s } from "../dom";
import { navigate } from "../router";
import { mountViewer } from "../viewer";
import { icon, type IconName } from "../icons";

type Viz = "hits" | "gauge" | "tags" | "bars";

interface Feature {
  id: string;
  icon: IconName;
  title: string;
  text: string;
  /** Not shipped yet: the card is dashed and the text ends with "Coming soon." */
  soon: boolean;
  viz: Viz;
}

export const FEATURES: Feature[] = [
  { id: "hits", icon: "target", title: "Hit tracking", text: "Every swing detected and timestamped.", soon: false, viz: "hits" },
  { id: "speed", icon: "gauge", title: "Swing speed", text: "Estimated racquet head speed for each swing.", soon: false, viz: "gauge" },
  { id: "strokes", icon: "list-checks", title: "Stroke recognition", text: "Smash, clear, drop and drive, named automatically.", soon: true, viz: "tags" },
  { id: "insights", icon: "chart-line-up", title: "Insights", text: "Session reports that show what to work on.", soon: true, viz: "bars" },
];

// ---------------------------------------------------------------- imagery

const STILLS = {
  ready: [new URL("../assets/ready.webp", import.meta.url).href, new URL("../assets/ready.png", import.meta.url).href],
} as const;

/** Our own render. Width/height reserve the space (CLS); below-the-fold images load lazily. */
function picture(webp: string, png: string, alt: string, w: number, hgt: number): HTMLElement {
  return h("picture", null, h("source", { type: "image/webp", srcset: webp }), h("img", { src: png, alt, width: w, height: hgt, loading: "lazy", decoding: "async" }));
}

const POSTER = [new URL("../assets/arena-poster.webp", import.meta.url).href, new URL("../assets/arena-poster.png", import.meta.url).href];

/** A poster of the Arena viewer; once the latest finished match is known, a button loads the viewer in place (never
 *  autoplays the 10-30 MB download). With no finished match the poster just links to /arena. */
export function arenaWatch(): HTMLElement {
  const box = h("div", { class: "viewer watch" });
  const poster = picture(POSTER[0], POSTER[1], "A 3D badminton rally in the Arena viewer: two AI players on a green court.", 1280, 720);
  box.append(poster);
  void api.matches({ limit: 5 }).then(
    (page) => {
      const m = page.items.find((x) => x.status === "done");
      if (!m) return box.replaceChildren(link("/arena", poster));
      const play = h(
        "button",
        {
          type: "button",
          class: "btn primary watch-play",
          "aria-label": `Watch ${m.a.name} vs ${m.b.name}`,
          onclick: () => {
            box.classList.remove("watch");
            mountViewer(box, m, false);
          },
        },
        icon("play-fill"),
        "Watch a match",
      );
      box.append(play);
    },
    () => box.replaceChildren(link("/arena", poster)),
  );
  return box;
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
  return h(
    "article",
    { class: f.soon ? "feature reveal soon" : "feature reveal" },
    h("div", { class: "feature-head" }, icon(f.icon, { cls: "feature-ico" }), h("h3", null, f.title)),
    h("p", null, f.text, f.soon ? h("span", { class: "soon-note" }, "Coming soon") : null),
    viz(f.viz),
  );
}

// ---------------------------------------------------------------- page

/** The Arena and Twins in one compact block: the poster (plays the latest match in place) and two short lines. */
function arenaStrip(): HTMLElement {
  return h(
    "section",
    { class: "l-sec arena-strip", "aria-labelledby": "arena-h" },
    arenaWatch(),
    h(
      "div",
      { class: "arena-copy" },
      h("h2", { id: "arena-h" }, "Arena"),
      h("p", null, "Train a small badminton AI, upload it and watch it climb the ladder."),
      h("p", null, link("/arena", h("span", { class: "btn primary" }, "Explore the Arena"))),
      h(
        "p",
        { class: "twin-line" },
        h("strong", null, "Twins. "),
        "Turn your pod sessions into an AI that plays like you. ",
        link("/arena/twins", "Twins ladder"),
      ),
    ),
  );
}

export function landing(root: HTMLElement) {
  if (location.hash === "#how") return void queueMicrotask(() => navigate("/how-it-works", true)); // old anchor: How it works has its own page now
  root.append(
    h(
      "section",
      { class: "l-hero" },
      h(
        "div",
        { class: "l-hero-copy" },
        h("h1", { class: "display" }, h("span", { class: "lt" }, "Every swing,"), h("br"), h("span", { class: "hv" }, "measured.")),
        h("p", { class: "lead" }, "A small pod for your racquet that counts every swing and measures its speed."),
        h("div", { class: "row cta" }, link("/how-it-works", h("span", { class: "btn primary" }, "How it works")), link("/arena", h("span", { class: "btn" }, "Arena"))),
      ),
      collage(),
    ),
    arenaStrip(),
    h("section", { class: "l-sec", id: "features", "aria-labelledby": "features-h", tabindex: "-1" }, h("h2", { id: "features-h" }, "Features"), h("div", { class: "features" }, FEATURES.map(feature))),
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
