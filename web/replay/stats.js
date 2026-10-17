// Stats panel: oso_stats() JSON (src/stats.rs, docs/data-format.md) as per-player columns, a rally row and a top-down court of hit dots.
import { KMH, LABELS, LEAD_S, PLAYER_COLORS, TYPE_COLORS } from "./timeline.js";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";

function el(tag, props = {}, ...kids) {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
}

function svg(tag, attrs = {}) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

const kmh = (ms) => (ms == null ? "-" : `${(ms * KMH).toFixed(0)} km/h`);
const secs = (t) => (t == null ? "-" : `${t.toFixed(1)} s`);

// Court 13.40 x 6.10 m, x along the length; lines per BWF (singles 5.18 wide, short service 1.98 m from the net, doubles long service 0.76 m in)
function court() {
  const g = svg("svg", { viewBox: "-7.2 -3.55 14.4 7.1", role: "img", "aria-label": "Hit positions on the court, top-down" });
  const line = (x1, z1, x2, z2) => g.append(svg("line", { class: "line", x1, y1: z1, x2, y2: z2 }));
  g.append(svg("rect", { class: "line", x: -6.7, y: -3.05, width: 13.4, height: 6.1 }));
  line(-6.7, -2.59, 6.7, -2.59);
  line(-6.7, 2.59, 6.7, 2.59);
  line(0, -3.05, 0, 3.05);
  for (const s of [-1, 1]) {
    line(s * 1.98, -2.59, s * 1.98, 2.59);
    line(s * 5.94, -3.05, s * 5.94, 3.05);
    line(s * 1.98, 0, s * 6.7, 0);
  }
  return g;
}

export function setupStats(oso) {
  const panel = $("stats"), btn = $("stats-btn"), canvas = $("canvas");
  const seek = (t) => {
    oso.oso_seek(Math.max(0, t - LEAD_S));
    oso.oso_set_playing(false);
  };
  const toggle = (open) => {
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", () => toggle(panel.hidden));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) {
      toggle(false);
      btn.focus();
    }
  });

  const seekButton = (text, t) => {
    const b = el("button", { type: "button", className: "link", textContent: text });
    b.addEventListener("click", () => seek(t));
    return b;
  };

  function column(p, i, max) {
    const col = el("div", { className: "col" }, el("h3", { textContent: `Player ${i + 1}` }));
    col.append(el("p", { textContent: `${p.hits} hits` }));
    if (p.points != null) col.append(el("p", { textContent: `${p.points} points won` }));
    for (const [type, n] of Object.entries(p.types).sort((a, b) => b[1] - a[1])) {
      const bar = el("i");
      bar.style.cssText = `width:${(n / max) * 100}%;background:${TYPE_COLORS[type] ?? "#999"}`;
      col.append(el("div", { className: "bar", title: type.replace("_", " ") },
        el("span", { textContent: LABELS[type] ?? "?" }), el("span", {}, bar), el("span", { className: "num", textContent: n })));
    }
    const f = p.fastest;
    col.append(el("p", {}, "Fastest launch: ", f ? seekButton(`${kmh(f.speed)} (${f.type.replace("_", " ")})`, f.t) : "-"));
    col.append(el("p", { textContent: `Mean launch: ${kmh(p.launch_speed_mean)}` }));
    if (p.head_speed_max != null) col.append(el("p", { textContent: `Max head speed: ${kmh(p.head_speed_max)}` }));
    return col;
  }

  function dots(stats) {
    const g = court();
    stats.players.forEach((p, i) => {
      for (const h of p.positions) {
        const fill = TYPE_COLORS[h.type] ?? "#999", r = 0.28;
        // P0 circle, P1 square; z points down the SVG
        const d = i === 0 ? svg("circle", { cx: h.x, cy: h.z, r }) : svg("rect", { x: h.x - r, y: h.z - r, width: 2 * r, height: 2 * r });
        d.setAttribute("fill", fill);
        d.dataset.t = h.t;
        d.setAttribute("tabindex", "0");
        d.setAttribute("role", "button");
        d.setAttribute("aria-label", `Player ${i + 1}, ${h.type.replace("_", " ")}, ${h.t.toFixed(2)} s`);
        d.append(svg("title"));
        d.firstChild.textContent = d.getAttribute("aria-label");
        d.addEventListener("click", () => seek(h.t));
        d.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            seek(h.t);
          }
        });
        g.append(d);
      }
    });
    return g;
  }

  function render(stats) {
    const total = stats.players.reduce((n, p) => n + p.hits, 0);
    panel.replaceChildren(el("h2", { textContent: "Match stats" }));
    if (!total) {
      panel.append(el("p", { textContent: "No strokes in this replay" }));
      return;
    }
    const max = Math.max(1, ...stats.players.flatMap((p) => Object.values(p.types)));
    const cols = el("div", { className: "cols" });
    cols.append(...stats.players.slice(0, 2).map((p, i) => column(p, i, max)));
    const r = stats.rallies;
    panel.append(cols, el("p", {},
      `${r.count} ${r.count === 1 ? "rally" : "rallies"}, shots mean ${r.shots_mean?.toFixed(1) ?? "-"} / max ${r.shots_max ?? "-"}, duration mean ${secs(r.duration_mean)} / max ${secs(r.duration_max)}; longest at `,
      r.longest_start == null ? "-" : seekButton(secs(r.longest_start), r.longest_start)));
    panel.append(dots(stats));
  }

  // The replay loads asynchronously: wait for the JSON, then show the button
  (function wait() {
    const json = oso.oso_stats();
    if (!json) return void requestAnimationFrame(wait);
    render(JSON.parse(json));
    btn.hidden = false;
  })();
}
