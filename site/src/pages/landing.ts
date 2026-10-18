// "/" : the racquet pod landing page. Rendered stills of our own renderer plus inline SVG; no live 3D, no data collected.
// Features that haven't shipped say so in their text: flip `soon` in FEATURES when something ships. How it works: pages/how.ts.

import { api } from "../api";
import { reveal } from "../anim";
import { h, link, s } from "../dom";
import { analyze, exampleSession } from "../insights";
import { navigate } from "../router";
import { mountViewer } from "../viewer";
import { icon, type IconName } from "../icons";

type Viz = "hits" | "gauge" | "tags" | "bars";

interface Feature {
  id: string;
  icon: IconName;
  title: string;
  text: string;
  /** Not shipped yet: the card is dashed and says "Coming soon". */
  soon: boolean;
  viz: Viz;
  /** where the card's "Try it" link goes */
  href?: string;
}

export const FEATURES: Feature[] = [
  { id: "hits", icon: "target", title: "Hit tracking", text: "Every swing is detected and timestamped, so you can see when you hit and how often.", soon: false, viz: "hits" },
  { id: "speed", icon: "gauge", title: "Swing speed", text: "Estimated racquet head speed for every swing, worked out from the gyro.", soon: false, viz: "gauge" },
  { id: "insights", icon: "sparkle-fill", title: "Insights", text: "Tips from each session: your top speed, consistency, fatigue and timing.", soon: false, viz: "bars", href: "/how-it-works#insights" },
  { id: "strokes", icon: "list-checks", title: "Stroke recognition", text: "Smash, clear, drop and drive, named automatically for every swing.", soon: true, viz: "tags" },
];

// ---------------------------------------------------------------- imagery

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

function feature(f: Feature, i: number): HTMLElement {
  return h(
    "article",
    { class: `feature card glass lift reveal d${i}${f.soon ? " soon" : ""}` },
    h("div", { class: "feature-head" }, h("span", { class: "feature-ico" }, icon(f.icon)), h("h3", null, f.title)),
    h("p", null, f.text, f.soon ? h("span", { class: "soon-note" }, "Coming soon") : null),
    viz(f.viz),
    f.href ? link(f.href, h("span", { class: "act more" }, "Try it", icon("arrow-right"))) : null,
  );
}

/** Hero readout: the example session's headline numbers in a glass card, with its swing bars as a backdrop. */
function readout(): HTMLElement {
  const r = analyze(exampleSession());
  const n = r.swings.length;
  const bars = s(
    "svg",
    { viewBox: `0 0 ${n * 10} 60`, class: "readout-bars", "aria-hidden": "true", preserveAspectRatio: "none" },
    r.swings.map((sw, i) => s("rect", { x: i * 10 + 2, y: 60 - ((sw.kmh - 30) / 70) * 60, width: 6, height: ((sw.kmh - 30) / 70) * 60, rx: 2, class: i === r.topIndex ? "top" : null })),
  );
  const fig = (v: string, l: string) => h("div", null, h("strong", null, v), h("span", null, l));
  return h(
    "figure",
    { class: "readout glass", "aria-label": `Example session: ${n} smashes, average ${Math.round(r.meanKmh)} km/h, top ${Math.round(r.topKmh)} km/h.` },
    h("figcaption", null, icon("waveform"), "Example session"),
    h("div", { class: "readout-figs" }, fig(String(n), "smashes"), fig(`${Math.round(r.meanKmh)}`, "km/h average"), fig(`${Math.round(r.topKmh)}`, "km/h top")),
    bars,
  );
}

// ---------------------------------------------------------------- page

/** The Arena and Twins in one compact block: the poster (plays the latest match in place) and two short lines. */
function arenaStrip(): HTMLElement {
  return h(
    "section",
    { class: "l-sec arena-strip card glass reveal", "aria-labelledby": "arena-h" },
    arenaWatch(),
    h(
      "div",
      { class: "arena-copy" },
      h("span", { class: "eyebrow" }, icon("robot"), "Arena"),
      h("h2", { id: "arena-h" }, "Train an AI. Watch it play."),
      h("p", null, "Train a small badminton AI with our free kit, upload it, and it plays ranked matches around the clock. Every match can be replayed in 3D."),
      h("p", { class: "row" }, link("/arena", h("span", { class: "btn primary" }, "Explore the Arena", icon("arrow-right"))), link("/arena/get-started", h("span", { class: "btn" }, "Train your own"))),
      h(
        "p",
        { class: "twin-line" },
        icon("users-three"),
        h("span", null, h("strong", null, "Twins. "), "Turn your pod sessions into an AI that plays like you, on its own ", link("/arena/ladder?ladder=twins", "Twins ladder"), "."),
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
      h("h1", { class: "display" }, h("span", { class: "lt" }, "Every swing,"), " ", h("span", { class: "hv grad" }, "measured.")),
      h("p", { class: "lead" }, "Volante is a small sensor that clips onto your badminton racquet. It counts every swing, estimates how fast your racquet head moves, and turns each session into insights you can act on."),
      h("div", { class: "row cta" }, link("/how-it-works", h("span", { class: "btn primary" }, "How it works", icon("arrow-right"))), link("/arena", h("span", { class: "btn" }, icon("robot"), "Arena"))),
      readout(),
    ),
    arenaStrip(),
    h(
      "section",
      { class: "l-sec", id: "features", "aria-labelledby": "features-h", tabindex: "-1" },
      h("h2", { id: "features-h" }, "Features"),
      h("p", { class: "sec-lead" }, "Built for players who want to know what each session actually did."),
      h("div", { class: "features" }, FEATURES.map(feature)),
    ),
  );
  return reveal(root);
}
