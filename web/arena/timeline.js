// Minimal Arena timeline: polls oso_status() every animation frame, sends commands through the oso_* exports
// and shows the score from oso_score() ("no score data" when the replay has no point events). With a match header the score is [A, B]
// (it does not swap at a change of ends), labelled A / B and under the names header; without one it is [P1, P2] by court side.
import { showPlayers } from "./players.js";

const $ = (id) => document.getElementById(id);

const p2 = (n) => String(n).padStart(2, "0");
const clock = (t) => `${p2(Math.floor(t / 60))}:${p2(Math.floor(t % 60))}`;

export function setupTimeline(oso) {
  const bar = $("timeline"), scrub = $("scrub"), canvas = $("canvas");
  const drag = { on: false, resume: false };
  let ready = false, playing = true, labels = ["P1", "P2"];
  bar.addEventListener("click", (e) => {
    if (e.detail > 0 && e.target.closest("button")) canvas.focus();
  });
  $("play").addEventListener("click", () => oso.oso_toggle_play());
  $("step-back").addEventListener("click", () => oso.oso_step(-1));
  $("step-fwd").addEventListener("click", () => oso.oso_step(1));
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
  let shown = 0;
  const endDrag = () => {
    if (!drag.on) return;
    drag.on = false;
    if (drag.resume) oso.oso_set_playing(true);
    canvas.focus();
  };
  scrub.addEventListener("pointerdown", () => {
    drag.on = true;
    drag.resume = playing;
    oso.oso_set_playing(false);
  });
  scrub.addEventListener("input", () => oso.oso_seek(parseFloat(scrub.value)));
  for (const ev of ["pointerup", "pointercancel", "change"]) scrub.addEventListener(ev, endDrag);

  let last = "";
  function poll() {
    const s = oso.oso_status();
    try {
      if (s.loaded) {
        if (!ready) {
          ready = true;
          bar.hidden = false;
          scrub.max = s.duration;
          scrub.step = 1 / s.fps;
          const players = window.oso_players();
          showPlayers(document, players);
          if (players) labels = ["A", "B"];
        }
        playing = s.playing;
        if (s.preset !== shown) {
          shown = s.preset;
          for (const b of presets) b.setAttribute("aria-pressed", String(Number(b.dataset.preset) === shown));
        }
        if (!drag.on) scrub.value = s.time;
        const sc = oso.oso_score(s.time);
        const text = `${sc.length ? `${labels[0]} ${sc[0]} – ${sc[1]} ${labels[1]}` : "no score data"}   ${clock(s.time)} / ${clock(s.duration)}`;
        if (text !== last) {
          last = text;
          $("readout").textContent = text;
        }
        $("play").innerHTML = s.playing ? "&#10074;&#10074;" : "&#9654;";
      }
    } finally {
      s.free();
    }
    requestAnimationFrame(poll);
  }
  requestAnimationFrame(poll);
}
