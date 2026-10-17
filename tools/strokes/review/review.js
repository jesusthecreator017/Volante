"use strict";
// Local review UI. Data is only ever put in the page with textContent / setAttribute, never innerHTML.
const SVGNS = "http://www.w3.org/2000/svg";
const KEYS = { "0": "not_a_hit", "1": "serve_short", "2": "serve_long", "3": "clear", "4": "smash", "5": "drop", "6": "drive",
  "7": "lift", "8": "net_shot", "9": "block_push", "-": "net_kill" };
const AXES = ["ax g", "ay g", "az g", "wx dps", "wy dps (shaft roll)", "wz dps"];
const COLORS = ["#d62728", "#2ca02c", "#1f77b4", "#d62728", "#2ca02c", "#1f77b4"];
const $ = (id) => document.getElementById(id);

let items = [];
let cur = 0;
let pick = { cls: null, side: "FH" };
const undo = [];
let hint = "";

function el(tag, attrs, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
  if (text !== undefined) e.textContent = text;
  return e;
}

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}

function line(xs, ys, x0, x1, y0, y1, w, h, cls, stroke) {
  const pts = ys.map((y, i) => `${(((xs[i] - x0) / (x1 - x0)) * w).toFixed(1)},${(h - ((y - y0) / (y1 - y0)) * h).toFixed(1)}`);
  const p = el("polyline", { points: pts.join(" "), class: cls });
  if (stroke) p.setAttribute("stroke", stroke);
  return p;
}

// One plot: series = [{ys, cls, stroke}], shared x (ms), sat flags shaded, a zero line and the peak marker at 0 ms
function plot(svg, t, series, sat, label, w, h) {
  svg.replaceChildren();
  const all = series.flatMap((s) => s.ys || []);
  let lo = Math.min(...all, 0), hi = Math.max(...all, 1);
  const pad = (hi - lo) * 0.05;
  lo -= pad; hi += pad;
  const x0 = t[0], x1 = t[t.length - 1], dx = (x1 - x0) / t.length;
  sat.forEach((s, i) => { if (s) svg.appendChild(el("rect", { class: "sat", x: (((t[i] - x0) / (x1 - x0)) * w).toFixed(1), y: 0, width: ((dx / (x1 - x0)) * w + 0.5).toFixed(1), height: h })); });
  const yz = h - ((0 - lo) / (hi - lo)) * h;
  svg.appendChild(el("line", { class: "zero", x1: 0, x2: w, y1: yz, y2: yz }));
  const xp = ((0 - x0) / (x1 - x0)) * w;
  svg.appendChild(el("line", { class: "zero", x1: xp, x2: xp, y1: 0, y2: h }));
  for (const s of series) if (s.ys) svg.appendChild(line(t, s.ys, x0, x1, lo, hi, w, h, s.cls, s.stroke));
  svg.appendChild(el("text", { class: "lbl", x: 4, y: 11 }, `${label}  [${lo.toFixed(0)}, ${hi.toFixed(0)}]`));
}

function renderQueue() {
  const ul = $("queue");
  ul.replaceChildren();
  items.forEach((it, i) => {
    const li = document.createElement("li");
    li.textContent = `${i + 1}. ${it.session.slice(9, 15)} b${it.block} ${it.label} ${it.side}${it.override ? "  -> " + it.override.cls + " " + it.override.side : ""}`;
    li.className = (i === cur ? "cur " : "") + (it.override ? (it.override.cls === "discard" ? "done disc" : "done") : "");
    li.onclick = () => go(i);
    ul.appendChild(li);
  });
  $("progress").textContent = `${items.filter((i) => i.override).length} / ${items.length} reviewed`;
  const c = ul.children[cur];
  if (c) c.scrollIntoView({ block: "nearest" });
}

async function show() {
  renderQueue();
  const it = items[cur];
  if (!it) { $("title").textContent = "Queue empty"; return; }
  $("title").textContent = `${it.id}`;
  $("info").textContent = `Block ${it.block}: ${it.label} ${it.side || ""}. Why: ${it.reasons || "-"}. ` +
    (it.notes ? `Note: ${it.notes}. ` : "") + (it.override ? `Your label: ${it.override.cls} ${it.override.side}.` : "");
  const q = pick.cls ? `?class=${encodeURIComponent(pick.cls)}&side=${pick.side}` : "";
  const mine = cur;
  const d = await api("/api/swing/" + encodeURIComponent(it.id) + q);
  if (mine !== cur) return;
  const cmp = d.compare;
  $("status").textContent = `Comparing with ${cmp.n} labelled ${cmp.cls} ${cmp.side} swings (dashed). Yellow = saturated. Peak at 0 ms.` +
    (pick.cls ? ` Picked: ${pick.cls} ${pick.side}` : "");
  plot($("plot-main"), d.t_ms, [
    { ys: d.mean_w, cls: "mean mean-w" }, { ys: d.mean_a && d.mean_a.map((v) => v * 100), cls: "mean mean-a" },
    { ys: d.w, cls: "cur-w" }, { ys: d.a.map((v) => v * 100), cls: "cur-a" }], d.sat, "|w| dps (red), |a| g x100 (blue)", 600, 220);
  const box = $("axes");
  box.replaceChildren();
  AXES.forEach((name, k) => {
    const s = el("svg", { class: "plot small", viewBox: "0 0 240 80" });
    box.appendChild(s);
    plot(s, d.t_ms, [{ ys: d.axes[k], cls: "ax", stroke: COLORS[k] }], d.sat, name, 240, 80);
  });
}

function go(i) {
  if (!items.length) return;
  cur = (i + items.length) % items.length;
  const it = items[cur];
  pick = { cls: null, side: (it.override && it.override.side) || it.side || "FH" };
  show().catch(fail);
}

function fail(e) { $("status").textContent = "Error: " + e.message; }

async function submit(cls, side) {
  const it = items[cur];
  const sideless = cls === "discard" || cls === "not_a_hit";
  const body = { swing_id: it.id, class: cls, side: sideless ? "" : side };
  await api("/api/label", body);
  undo.push({ i: cur, prev: it.override });
  it.override = { cls, side: body.side };
  const next = items.findIndex((x, j) => j > cur && !x.override);
  go(next >= 0 ? next : cur + 1);
}

async function undoLast() {
  const u = undo.pop();
  if (!u) return;
  const it = items[u.i];
  await api("/api/label", u.prev ? { swing_id: it.id, class: u.prev.cls, side: u.prev.side } : { swing_id: it.id, clear: true });
  it.override = u.prev;
  go(u.i);
}

function onKey(ev) {
  if (ev.ctrlKey || ev.metaKey || ev.altKey || !items.length) return;
  const it = items[cur], k = ev.key;
  let p = null;
  if (k === "Enter" || k === "a") p = submit(it.label, pick.side);
  else if (k === "x") p = submit("discard", "");
  else if (k in KEYS) { pick.cls = KEYS[k]; p = submit(pick.cls, pick.side); }
  else if (k === "f" || k === "b") { pick.side = k === "f" ? "FH" : "BH"; p = submit(pick.cls || it.label, pick.side); }
  else if (k === "j" || k === "ArrowDown" || k === "ArrowRight") go(cur + 1);
  else if (k === "k" || k === "ArrowUp" || k === "ArrowLeft") go(cur - 1);
  else if (k === "u") p = undoLast();
  else return;
  ev.preventDefault();
  if (p) p.catch(fail);
}

async function init() {
  const q = await api("/api/queue");
  items = q.items;
  hint = q.version_hint;
  $("hint").textContent = "When done, stop the server (Ctrl-C) and " + hint;
  $("keymap").textContent = "Enter / a accept the block label, x discard, f / b side, j k or arrows next / previous, u undo. Class: " +
    Object.entries(KEYS).map(([k, v]) => `${k} ${v}`).join(", ") + ". A class key or f / b labels immediately.";
  document.addEventListener("keydown", onKey);
  const first = items.findIndex((x) => !x.override);
  go(first >= 0 ? first : 0);
}

init().catch(fail);
