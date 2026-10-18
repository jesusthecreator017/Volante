// "/how-it-works": the four steps, an example session report with insights, and "try it with your own swings.csv" (read
// in the browser; nothing is uploaded).

import { reveal } from "../anim";
import { h, link } from "../dom";
import { icon, type IconName } from "../icons";
import { dropZone } from "../drop";
import { analyze, exampleSession, parseSwingsCsv } from "../insights";
import { sessionReport } from "../report";

export { SESSION } from "../insights";

const STEPS: [IconName, string, string][] = [
  ["racquet", "Clip it on", "The pod sits just above the grip. Calibrate once by holding the racquet still, tip up and then flat; it remembers the mount until you move it."],
  ["waveform", "Play", "A six-axis motion sensor samples the racquet 500 times a second and keeps up to 12 minutes of play on the pod. No phone needed on court."],
  ["bluetooth-connected", "Sync", "Afterwards the Volante app pulls the session over Bluetooth and finds every swing: when it started, when you hit and how fast the racquet head moved."],
  ["sparkle-fill", "Improve", "Insights compare your swings: your top speed, how repeatable your power is, whether you tired, and how well you timed your hits."],
];

function step([ico, title, text]: [IconName, string, string], i: number): HTMLElement {
  return h("li", { class: `step card glass lift reveal d${i}` }, h("span", { class: "step-ico" }, icon(ico)), h("span", { class: "step-n" }, `Step ${i + 1}`), h("h3", null, title), h("p", null, text));
}

export function howItWorks(root: HTMLElement) {
  const example = () => sessionReport(analyze(exampleSession()), "Smash session", "Example");
  const slot = h("div", { class: "report-slot" }, example());
  const note = h("p", { class: "muted small foot" }, "Totals from a real 41-smash session; the swing-by-swing detail is simulated around them. All speeds are estimates from the sensor.");
  const status = h("p", { class: "small", role: "status" });
  const back = h("button", { type: "button", class: "btn small", hidden: true }, icon("arrow-clockwise"), "Back to the example");
  back.addEventListener("click", () => {
    slot.replaceChildren(example());
    note.hidden = false;
    back.hidden = true;
    status.textContent = "";
  });
  const load = async (f: File) => {
    status.className = "small";
    status.textContent = `Reading ${f.name}…`;
    try {
      if (f.size > 5_000_000) throw new Error("That file is too large for a swings.csv.");
      const rep = analyze(parseSwingsCsv(await f.text()));
      slot.replaceChildren(sessionReport(rep, "Your session", f.name));
      reveal(slot);
      note.hidden = true;
      back.hidden = false;
      status.textContent = "";
      slot.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (e) {
      status.className = "small error";
      status.textContent = e instanceof Error ? e.message : String(e);
    }
  };

  root.append(
    h(
      "header",
      { class: "page-hero" },
      h("h1", null, "How it works"),
      h("p", { class: "lead" }, "Volante is a small motion sensor that clips onto your racquet. It records every swing while you play, then turns the session into numbers and tips you can use at your next practice."),
    ),
    h("ol", { class: "steps" }, STEPS.map(step)),
    h(
      "section",
      { class: "l-sec", id: "insights", "aria-labelledby": "insights-h", tabindex: "-1" },
      h("h2", { id: "insights-h" }, "Read a session at a glance"),
      h("p", { class: "sec-lead" }, "Every swing becomes a bar you can inspect. Insights then point out what went well and what to work on next."),
      slot,
      note,
      h(
        "div",
        { class: "try" },
        h("h3", null, "Try it with your own session"),
        dropZone({ id: "swings-file", accept: ".csv,text/csv", ico: "file-csv", title: "Drop your swings.csv here, or choose a file", note: "Export it from the Volante app's Export page. It's read in your browser and never uploaded." }, (f) => void load(f)),
        status,
        back,
        h("p", { class: "muted small" }, icon("device-mobile"), " Using the iPhone app? Each session exports a report.json with rallies, heart rate and strokes: ", link("/report", "open it here"), "."),
      ),
    ),
  );
  return reveal(root);
}
