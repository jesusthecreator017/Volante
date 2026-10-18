// Pages: Train & upload (guide + your models), Privacy, Not found.

import { reveal } from "../anim";
import { BUILD_TOTAL, PLAY_STYLES, STATS, STAT_EFFECT, STAT_LABEL, UPLOADS_PER_DAY } from "../arena";
import { buildBars } from "../components";
import { h, link } from "../dom";
import { icon, type IconName } from "../icons";
import { modelsPanel } from "./myModels";

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

function stepCard(n: number, ico: IconName, title: string, ...body: (Node | string | null)[]): HTMLElement {
  return h("section", { class: `card glass guide-step reveal d${Math.min(n, 4)}` }, h("div", { class: "guide-head" }, h("span", { class: "step-ico" }, icon(ico)), h("span", { class: "step-n" }, `Step ${n}`), h("h2", null, title)), ...body);
}

/** Train & upload: the training guide plus the "Your models" panel (upload and your models; signed out: sign in). */
export function getStarted(root: HTMLElement) {
  const panel = h("div");
  const stop = modelsPanel(panel);
  root.append(
    h(
      "header",
      { class: "page-hero" },
      h("h1", null, "Train & upload"),
      h("p", { class: "lead" }, "Train a small neural network to play badminton with our free kit, then upload it here. It plays ranked matches against everyone else's around the clock, and you can watch every one in 3D."),
    ),
    h(
      "div",
      { class: "start-grid" },
      h("aside", { class: "card glass mine", id: "mine", "aria-labelledby": "mine-h" }, h("h2", { id: "mine-h" }, icon("robot"), "Your models"), panel),
      h(
        "div",
        { class: "guide" },
        stepCard(
          1,
          "terminal-window",
          "Install the kit",
          code("git clone https://github.com/jesusthecreator017/Volante.git\ncd Volante\npython3 -m venv .venv && source .venv/bin/activate\npip install \"./arena/kit[train]\""),
          h("p", { class: "muted" }, "Needs Python 3.10 or newer. The kit has the simulator, a scripted bot, the trainer, the same check the upload runs, and House Base, the base model to start training from."),
        ),
        stepCard(
          2,
          "sliders-horizontal",
          "Choose a build",
          h("p", { class: "muted" }, `Four stats from 1 to 10 that add up to ${BUILD_TOTAL}. Start from a preset or make your own.`),
          h("dl", { class: "stat-effects" }, STATS.map((k) => [h("dt", null, STAT_LABEL[k]), h("dd", null, STAT_EFFECT[k])])),
          h(
            "ul",
            { class: "presets" },
            PLAY_STYLES.map((p) => h("li", null, h("span", { class: "preset-name" }, p.name), h("span", { class: "preset-num" }, STATS.map((k) => p.build[k]).join("/")), buildBars(p.build))),
          ),
        ),
        stepCard(
          3,
          "robot",
          "Train and export",
          code("oso-arena train --build 9,6,5,4 --minutes 60 --out runs/try1\noso-arena export runs/try1/best -o mymodel.safetensors --name \"My Model\"\noso-arena check mymodel.safetensors"),
          h("p", { class: "muted" }, h("code", null, "check"), " runs the same test as the upload, so a file that passes here is accepted. Want a coach? Add ", h("code", null, "skills/oso-arena-coach"), " to Claude Code and ask it to train toward a style."),
        ),
        stepCard(
          4,
          "users-three",
          "Optional: your twin",
          h("p", { class: "muted" }, "Have a Volante pod? Turn your sessions into a style profile and train a model that plays like you. It joins the ", link("/arena/ladder?ladder=twins", "Twins ladder"), " once it proves it does."),
          code("oso-rec profile <session folder> -o my_style.json\noso-arena train --style my_style.json --minutes 60 --out runs/twin\noso-arena export runs/twin/best -o mytwin.safetensors --name \"My Twin\" --style my_style.json"),
        ),
        h(
          "details",
          { class: "card glass rules reveal" },
          h("summary", null, icon("book-open"), "Rules", icon("caret-down-bold", { cls: "chev" })),
          h(
            "ul",
            { class: "muted" },
            h("li", null, "Best of 3 games to 15 (win by 2, capped at 21) under BWF singles laws."),
            h("li", null, "Ratings are Elo from 1200. A new model plays 10 placement matches; ratings settle after 20 games."),
            h("li", null, "Weekly tournament on Sundays at 18:00: the top 8 with at least 20 games, single elimination, no rating change."),
            h("li", null, `${UPLOADS_PER_DAY} uploads a day; one active model each. A new upload retires the previous one, and its history stays.`),
            h("li", null, "House Base and House Bot are reference players and skip tournaments. A new season resets ratings."),
          ),
        ),
      ),
    ),
  );
  const unreveal = reveal(root);
  return () => (stop(), unreveal());
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
    h("h2", null, "Your dashboard"),
    h("p", null, "If you sign in to the Arena in the Volante app, it sends a summary of each session (counts, estimated speeds, rhythm, heart-rate averages, experimental stroke groups) so you can see your stats under Account → Dashboard. No raw motion data leaves your phone."),
    h("p", null, "Only you can see these summaries: they are never public and never shown to other users."),
    h("p", null, "To delete them, use \"Delete my web data\" in the app's settings, or delete your account here (Settings), which removes them with it. Turning sync off in the app stops new uploads."),
    h("p", { class: "muted small" }, "Draft text, set by the project owner."),
  );
}

export function notFound(root: HTMLElement) {
  root.append(h("h1", null, "Not found"), h("p", null, "That page doesn't exist. ", link("/", "Go home"), "."));
}
