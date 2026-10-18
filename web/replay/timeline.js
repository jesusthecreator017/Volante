// Timeline bar: polls oso_status() every animation frame and sends commands through the oso_* exports,
// the same Command queue the native keyboard uses.
const $ = (id) => document.getElementById(id);

// mm:ss.ff where ff is the frame within the second
function clock(t, fps) {
  const s = Math.floor(t);
  const ff = Math.min(Math.floor((t - s) * fps), Math.ceil(fps) - 1);
  const p = (n, w = 2) => String(n).padStart(w, "0");
  return `${p(Math.floor(s / 60))}:${p(s % 60)}.${p(ff)}`;
}

// stroke row columns (docs/data-format.md, swings track)
const C = { t0: 0, tp: 1, contact: 3, player: 4, type: 5, peak: 6, head: 7, impact: 9, timing: 10, sat: 14 };
export const LABELS = { serve: "SV", clear: "CL", smash: "SM", drop: "DR", drive: "DV", lift: "LI", net: "NT", unknown: "?", swing_only: "\u00b7", block: "BK", push: "PS", press: "PR", net_kill: "NK", flick_serve: "FS" };
export const LEAD_S = 0.4; // = replay::STROKE_LEAD_S
export const KMH = 3.6;
export const PLAYER_COLORS = ["#ff9a3c", "#35d0e8"];
// Stats panel colour per stroke type (the timeline markers are coloured by player instead)
export const TYPE_COLORS = { serve: "#f2c14e", clear: "#5bc0eb", smash: "#e5484d", drop: "#9bc53d", drive: "#fa7921", lift: "#8e7dff", net: "#4ecdc4", unknown: "#999", swing_only: "#999", block: "#c97bd6", push: "#ff8fab", press: "#b8b08d", net_kill: "#ff5d8f", flick_serve: "#ffd166" };

// Where a click on a stroke lands: just before the contact, or the swing start for an air swing
const seekTime = (row) => (Number.isNaN(row[C.contact]) ? row[C.t0] : Math.max(0, row[C.contact] - LEAD_S));
const strokeTime = (row) => (Number.isNaN(row[C.contact]) ? row[C.tp] : row[C.contact]);

function summary(row, types) {
  const type = types[row[C.type]] ?? "?";
  const parts = [`${type.replace("_", " ")}, player ${row[C.player] + 1}, at ${strokeTime(row).toFixed(2)} s`];
  if (!Number.isNaN(row[C.head])) parts.push(`head speed ${(row[C.head] * KMH).toFixed(0)} km/h`);
  if (!Number.isNaN(row[C.peak])) parts.push(`peak ${row[C.peak].toFixed(0)} dps`);
  if (!Number.isNaN(row[C.impact])) parts.push(`impact ${row[C.impact].toFixed(1)} g`);
  if (!Number.isNaN(row[C.timing])) parts.push(`timing ${row[C.timing].toFixed(0)} ms`);
  if (row[C.sat] > 0) parts.push("saturated: peak under-reported");
  return parts.join(", ");
}

export function setupTimeline(oso) {
  const bar = $("timeline"), scrub = $("scrub"), canvas = $("canvas");
  const dragging = { on: false, resume: false };
  let ready = false;
  let playing = true;

  // After a mouse/touch click give keys back to the canvas. Keyboard activation (detail 0) keeps focus
  // so tabbing through the bar still works, and Space on a focused button only presses that button.
  bar.addEventListener("click", (e) => {
    if (e.detail > 0 && e.target.closest("button")) canvas.focus();
  });
  $("play").addEventListener("click", () => oso.oso_toggle_play());
  $("step-back").addEventListener("click", () => oso.oso_step(-1));
  $("step-fwd").addEventListener("click", () => oso.oso_step(1));
  $("skeleton").addEventListener("click", () => oso.oso_toggle_skeleton());
  $("big-shuttle").addEventListener("click", () => oso.oso_toggle_big_shuttle());
  $("speed").addEventListener("change", (e) => {
    oso.oso_set_speed(parseFloat(e.target.value));
    canvas.focus();
  });
  const presets = [...bar.querySelectorAll("[data-preset]")];
  for (const b of presets) {
    b.addEventListener("click", () => oso.oso_preset(Number(b.dataset.preset)));
  }
  // Presets glide unless the user asked for reduced motion
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  oso.oso_set_reduced_motion(reduced.matches);
  reduced.addEventListener("change", (e) => oso.oso_set_reduced_motion(e.matches));

  // Dragging pauses playback and resumes afterwards if it was playing
  const endDrag = () => {
    if (!dragging.on) return;
    dragging.on = false;
    if (dragging.resume) oso.oso_set_playing(true);
    canvas.focus();
  };
  scrub.addEventListener("pointerdown", () => {
    dragging.on = true;
    dragging.resume = playing;
    oso.oso_set_playing(false);
  });
  scrub.addEventListener("input", () => oso.oso_seek(parseFloat(scrub.value)));
  for (const ev of ["pointerup", "pointercancel", "change"]) scrub.addEventListener(ev, endDrag);

  // Strokes, markers and the head-speed sparkline: built once when the status reports strokes
  let rows = [], types = [], head = new Float32Array(0), duration = 0, laidOutWidth = -1;
  const markers = $("markers"), wrap = $("spark-wrap"), spark = $("spark"), sparkHead = $("spark-head");
  $("prev-stroke").addEventListener("click", () => oso.oso_prev_stroke());
  $("next-stroke").addEventListener("click", () => oso.oso_next_stroke());

  function buildStrokes(count) {
    const flat = oso.oso_strokes();
    types = oso.oso_stroke_types();
    head = oso.oso_head_speed(0);
    rows = [];
    for (let i = 0; i < count; i++) rows.push(flat.subarray(i * 16, (i + 1) * 16));
    markers.replaceChildren(...rows.map((row, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "stroke";
      b.dataset.player = String(row[C.player]);
      b.textContent = LABELS[types[row[C.type]]] ?? "?";
      b.title = b.ariaLabel = summary(row, types);
      b.addEventListener("click", () => {
        oso.oso_seek(seekTime(row));
        oso.oso_set_playing(false);
      });
      return b;
    }));
    markers.hidden = wrap.hidden = $("stroke-nav").hidden = false;
  }

  // Markers sit at their time, share the row's width, and drop to a new lane when they would overlap
  function layoutMarkers() {
    const w = markers.clientWidth;
    if (w === laidOutWidth || !duration) return;
    laidOutWidth = w;
    const laneEnd = [];
    const LANE_H = 28, PAD = 2;
    rows.forEach((row, i) => {
      const b = markers.children[i], bw = Math.max(b.offsetWidth, 30);
      const frac = Math.min(Math.max(strokeTime(row) / duration, 0), 1);
      // The marker's edge-relative anchor keeps it inside the row at either end
      const x = frac * (w - bw);
      let lane = laneEnd.findIndex((end) => x >= end + PAD);
      if (lane < 0) lane = laneEnd.push(0) - 1;
      laneEnd[lane] = x + bw;
      b.style.left = `${frac * 100}%`;
      b.style.transform = `translateX(-${frac * 100}%)`;
      b.style.top = `${lane * LANE_H}px`;
    });
    markers.style.height = `${Math.max(laneEnd.length, 1) * LANE_H}px`;
  }

  // Static layer (curve + stroke ticks): redrawn only when the canvas size changes
  let sparkSize = "";
  function drawSpark() {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(spark.clientWidth * dpr), h = Math.round(spark.clientHeight * dpr);
    if (!w || !h || `${w}x${h}` === sparkSize) return;
    sparkSize = `${w}x${h}`;
    spark.width = sparkHead.width = w;
    spark.height = sparkHead.height = h;
    headDrawn = null;
    const g = spark.getContext("2d");
    g.clearRect(0, 0, w, h);
    let max = 1;
    for (const v of head) if (v > max) max = v;
    for (const row of rows) { // ticks
      g.fillStyle = PLAYER_COLORS[row[C.player]] ?? "#aaa";
      g.globalAlpha = 0.5;
      g.fillRect(Math.round(strokeTime(row) / duration * w), 0, Math.max(1, dpr), h);
    }
    g.globalAlpha = 1;
    g.strokeStyle = PLAYER_COLORS[0];
    g.lineWidth = Math.max(1, dpr);
    g.beginPath();
    let pen = false;
    head.forEach((v, f) => {
      if (Number.isNaN(v)) { pen = false; return; }
      const x = f / (head.length - 1) * w, y = h - 2 - v / max * (h - 4);
      if (pen) g.lineTo(x, y); else g.moveTo(x, y);
      pen = true;
    });
    g.stroke();
  }
  // Playhead: its own canvas so the curve is not redrawn per frame; only touched when the x moves
  let headDrawn = null;
  function drawPlayhead(frac) {
    const w = sparkHead.width, x = Math.round(frac * w);
    if (x === headDrawn || !w) return;
    headDrawn = x;
    const g = sparkHead.getContext("2d");
    g.clearRect(0, 0, w, sparkHead.height);
    g.fillStyle = "#fff";
    g.fillRect(x, 0, Math.max(1, Math.round(window.devicePixelRatio || 1)), sparkHead.height);
  }
  new ResizeObserver(() => { layoutMarkers(); drawSpark(); }).observe(bar);

  // Write to the DOM only when a value changed
  const cache = {};
  const set = (key, value, write) => {
    if (cache[key] !== value) {
      cache[key] = value;
      write(value);
    }
  };

  function poll() {
    const s = oso.oso_status();
    try {
      if (s.loaded) {
        if (!ready) {
          ready = true;
          bar.hidden = false;
          scrub.max = s.duration;
          scrub.step = 1 / s.fps;
        }
        playing = s.playing;
        if (!dragging.on) scrub.value = s.time;
        set("readout", `${clock(s.time, s.fps)} / ${clock(s.duration, s.fps)}  frame ${s.frame + 1}/${s.frame_count}`,
          (v) => { $("readout").textContent = v; });
        set("playing", s.playing, (v) => {
          const play = $("play");
          play.innerHTML = v ? "&#10074;&#10074;" : "&#9654;";
          play.setAttribute("aria-label", v ? "Pause" : "Play");
        });
        set("skeleton", s.skeleton, (v) => $("skeleton").setAttribute("aria-pressed", String(v)));
        set("has_shuttle", s.has_shuttle, (v) => { $("big-shuttle").hidden = !v; });
        set("big_shuttle", s.big_shuttle, (v) => $("big-shuttle").setAttribute("aria-pressed", String(v)));
        set("cameras", s.cameras, (n) => {
          bar.querySelector('[data-preset="4"]').hidden = n < 1;
          bar.querySelector('[data-preset="5"]').hidden = n < 2;
        });
        set("preset", s.preset, (v) => {
          for (const b of presets) b.setAttribute("aria-pressed", String(Number(b.dataset.preset) === v));
        });
        if (s.stroke_count > 0 && !rows.length) {
          duration = s.duration;
          buildStrokes(s.stroke_count);
          layoutMarkers();
          drawSpark();
        }
        if (rows.length) {
          drawPlayhead(s.time / s.duration);
          const v = head[s.frame];
          const cur = Number.isNaN(v) || v === undefined ? "-" : (v * KMH).toFixed(0);
          // Last stroke at or before now: its peak head speed
          let last = null;
          for (const row of rows) if (strokeTime(row) <= s.time) last = row;
          const lp = last && !Number.isNaN(last[C.head]) ? (last[C.head] * KMH).toFixed(0) : "-";
          set("head", `head ${cur} km/h, last stroke peak ${lp} km/h`, (t) => { $("head-readout").textContent = t; });
        }
        // Keyboard [ ] changes speed too; keep the select in sync unless the user is using it
        const sel = $("speed");
        if (document.activeElement !== sel) {
          set("speed", String(Number(s.speed.toFixed(2))), (v) => { sel.value = v; });
        }
      }
    } finally {
      s.free();
    }
    requestAnimationFrame(poll);
  }
  requestAnimationFrame(poll);
}
