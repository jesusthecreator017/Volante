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
  for (const b of bar.querySelectorAll("[data-preset]")) {
    b.addEventListener("click", () => oso.oso_preset(Number(b.dataset.preset)));
  }

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
          bar.querySelector('[data-preset="4"]').disabled = n < 1;
          bar.querySelector('[data-preset="5"]').disabled = n < 2;
        });
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
