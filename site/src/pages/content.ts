// Static pages: Get started, Privacy, Not found.

import { BUILD_TOTAL, MAX_UPLOAD_BYTES, PLAY_STYLES, STATS, STAT_EFFECT, STAT_LABEL, UPLOADS_PER_DAY } from "../arena";
import { buildBars } from "../components";
import { h, link } from "../dom";
import { icon } from "../icons";

/** A command block with a copy button (the button only appears where the Clipboard API is available). */
const code = (t: string) => {
  const pre = h("pre", { class: "code" }, h("code", null, t));
  if (!navigator.clipboard) return pre;
  const btn = h("button", { type: "button", class: "icon-btn copy-btn", "aria-label": "Copy commands", title: "Copy" }, icon("copy"));
  btn.addEventListener("click", () => {
    void navigator.clipboard.writeText(t).then(() => {
      btn.replaceChildren(icon("check-circle-fill"));
      btn.setAttribute("aria-label", "Copied");
      setTimeout(() => (btn.replaceChildren(icon("copy")), btn.setAttribute("aria-label", "Copy commands")), 1600);
    });
  });
  return h("div", { class: "code-wrap" }, pre, btn);
};

export function getStarted(root: HTMLElement) {
  root.append(
    h("h1", null, "Get started"),
    h("p", { class: "lead" }, "Train a model with the kit, upload it, and it plays the ladder around the clock."),
    h("h2", null, "1. Install the kit"),
    code("git clone https://github.com/jesusthecreator017/Volante.git\ncd Volante\npython3 -m venv .venv && source .venv/bin/activate\npip install \"./arena/kit[train]\""),
    h("p", { class: "muted" }, "Python 3.10 or newer. The kit has the simulator, the trainer, the check tool and the base model, House Base."),
    h("h2", null, "2. Choose a build"),
    h("p", { class: "muted" }, `Four stats from 1 to 10 that add up to ${BUILD_TOTAL}.`),
    h(
      "div",
      { class: "table-wrap" },
      h("table", null, h("thead", null, h("tr", null, h("th", { scope: "col" }, "Stat"), h("th", { scope: "col" }, "Effect"))), h("tbody", null, STATS.map((k) => h("tr", null, h("td", null, STAT_LABEL[k]), h("td", null, STAT_EFFECT[k]))))),
    ),
    h("h3", null, "Presets"),
    h(
      "ul",
      { class: "presets" },
      PLAY_STYLES.map((p) => h("li", null, h("span", { class: "preset-name" }, p.name), h("span", { class: "preset-num" }, STATS.map((k) => p.build[k]).join("/")), buildBars(p.build))),
    ),
    h("h2", null, "3. Train"),
    code("oso-arena train --build 9,6,5,4 --minutes 60 --out runs/try1\noso-arena export runs/try1/best -o mymodel.safetensors --name \"My Model\"\noso-arena check mymodel.safetensors"),
    h(
      "p",
      { class: "muted" },
      h("code", null, "check"),
      " runs the same test as the upload. Want a coach? Add ",
      h("code", null, "skills/oso-arena-coach"),
      " to Claude Code.",
    ),
    h("h3", null, "Optional: your twin"),
    h("p", { class: "muted" }, "Have a pod? Train a model that plays like you. It joins the ", link("/arena/twins", "Twins ladder"), " once it proves it does."),
    code("oso-rec profile <session folder> -o my_style.json\noso-arena train --style my_style.json --minutes 60 --out runs/twin\noso-arena export runs/twin/best -o mytwin.safetensors --name \"My Twin\" --style my_style.json"),
    h("h2", null, "4. Upload"),
    h(
      "ul",
      { class: "muted" },
      h("li", null, `One .safetensors file, up to ${MAX_UPLOAD_BYTES / 1024} KiB. It holds only numbers, never code.`),
      h("li", null, "Name, description and build come from the file."),
      h("li", null, `${UPLOADS_PER_DAY} uploads a day. A new upload replaces your active model.`),
    ),
    h("p", null, link("/arena/my-models", h("span", { class: "btn primary" }, "Upload a model"))),
    h("h2", null, "Rules"),
    h(
      "ul",
      { class: "muted" },
      h("li", null, "Best of 3 games to 15 (win by 2, capped at 21), BWF singles laws."),
      h("li", null, "Elo from 1200. 10 placement matches; provisional under 20 games."),
      h("li", null, "Weekly tournament on Sundays at 18:00: the top 8 with at least 20 games, no rating change."),
      h("li", null, "House Base and House Bot are reference players and skip tournaments."),
      h("li", null, "A new season resets ratings."),
    ),
  );
}

export function privacy(root: HTMLElement) {
  root.append(
    h("h1", null, "Privacy"),
    h("p", null, "We store only your GitHub user id, login and avatar URL, plus the models you upload and the matches they play."),
    h("p", null, "Sign-in uses a session cookie. There are no trackers or third-party analytics on this site."),
    h("h2", null, "Volante Twins"),
    h("p", null, "Your raw pod sessions stay on your computer or phone. Only the style profile (a few numbers: speeds, tempo, counts) is uploaded, inside the model file."),
    h("p", null, "The profile contains no name, no pod ID and no timestamps except its creation time. It is shown publicly on the model's page. Deleting the model or your account deletes it."),
    h("p", null, "Delete your account any time from the account menu: your account is removed."),
    h("p", { class: "muted small" }, "Draft text, set by the project owner."),
  );
}

export function notFound(root: HTMLElement) {
  root.append(h("h1", null, "Not found"), h("p", null, "That page doesn't exist. ", link("/", "Go home"), "."));
}
