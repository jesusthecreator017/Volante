// Static pages: Get started, Terms, Privacy, Not found.

import { BUILD_TOTAL, MAX_UPLOAD_BYTES, PLAY_STYLES, STATS, STAT_EFFECT, STAT_LABEL, STAT_SHORT, UPLOADS_PER_DAY } from "../arena";
import { buildBars } from "../components";
import { h, link } from "../dom";

const code = (t: string) => h("pre", { class: "code" }, h("code", null, t));

export function getStarted(root: HTMLElement) {
  root.append(
    h("h1", null, "Get started"),
    h(
      "p",
      { class: "lead" },
      "Arena is a badminton game for AIs. You train a small neural network to play singles, upload it, and it plays everyone else's around the clock. Every match can be watched in 3D.",
    ),
    h("h2", null, "1. Get the training kit"),
    code("git clone https://github.com/jesusthecreator017/oso.git\ncd oso\npython3 -m venv .venv && source .venv/bin/activate\npip install \"./arena/kit[train]\""),
    h(
      "p",
      null,
      "The kit needs Python 3.10 or newer. It isn't on PyPI: install it from the repository as above, or build a wheel with ",
      h("code", null, "python -m build arena/kit"),
      " and install that. It contains the simulator, a scripted bot, the trainer, export and check tools, a local match runner and an animator for your own matches (drag the resulting match.bin onto the viewer; nothing is uploaded). It also contains the base model, House Base, to start training from (--init base).",
    ),
    h("h2", null, "2. Choose a build"),
    h("p", null, `Four stats, each 1–10, summing to ${BUILD_TOTAL}. 6/6/6/6 is balanced.`),
    h(
      "div",
      { class: "table-wrap" },
      h("table", null, h("thead", null, h("tr", null, h("th", { scope: "col" }, "Stat"), h("th", { scope: "col" }, "Effect"))), h("tbody", null, STATS.map((k) => h("tr", null, h("td", null, STAT_LABEL[k]), h("td", null, STAT_EFFECT[k]))))),
    ),
    h("h2", null, "3. Pick a play style"),
    h("p", null, "Suggested starting points from the kit's coaching guide. Styles aren't stored; the site guesses one from your build."),
    h(
      "div",
      { class: "table-wrap" },
      h(
        "table",
        null,
        h("thead", null, h("tr", null, h("th", { scope: "col" }, "Style"), h("th", { scope: "col" }, STATS.map((k) => STAT_SHORT[k]).join("/")), h("th", { scope: "col", class: "hide-sm" }, ""))),
        h("tbody", null, PLAY_STYLES.map((p) => h("tr", null, h("td", null, p.name), h("td", { class: "num" }, STATS.map((k) => p.build[k]).join("/")), h("td", { class: "hide-sm" }, buildBars(p.build))))),
      ),
    ),
    h("h2", null, "4. Train"),
    code("oso-arena train --build 9,6,5,4 --minutes 60 --out runs/try1\noso-arena export runs/try1/best -o mymodel.safetensors --name \"My Model\"\noso-arena check mymodel.safetensors"),
    h(
      "p",
      null,
      "Pick a build above and train; the export step takes the build from the run, and ",
      h("code", null, "check"),
      " runs the same test the upload does. The kit's README has a five-minute quickstart and TRAINING.md has compute tips. To have a coach: copy the ",
      h("code", null, "skills/oso-arena-coach"),
      " folder from the repository into your project's ",
      h("code", null, ".claude/skills/"),
      " and ask Claude Code with the ",
      h("strong", null, "oso-arena-coach"),
      " skill to train toward a style.",
    ),
    h("h2", null, "5. Upload"),
    h(
      "ul",
      null,
      h("li", null, `One .safetensors file, at most ${MAX_UPLOAD_BYTES / 1024} KiB (models are about 300 KB). It holds only numbers, never code.`),
      h("li", null, "Name (1–40 characters), description (up to 280) and build are set inside the file when you export it."),
      h("li", null, `${UPLOADS_PER_DAY} uploads per day. You have one active model; a new upload retires the previous one (its history stays).`),
      h("li", null, "The server checks the file; if it's rejected you get the reasons in plain language."),
    ),
    h("p", null, link("/arena/my-models", h("span", { class: "btn primary" }, "Upload a model"))),
    h("h2", null, "The rules"),
    h(
      "ul",
      null,
      h("li", null, "Best of 3 games under BWF singles laws, 15-point games (win by 2, capped at 21), interval at 8 and between games, players change ends."),
      h("li", null, "Every match is fully determined by the two models and a seed, so it can be replayed exactly."),
      h("li", null, "Ladder: Elo from 1200. New models play 10 placement matches; ratings are provisional below 20 games. The server keeps pairing nearby-rated models."),
      h("li", null, "Weekly tournament: Sunday 18:00, top 8 with at least 20 games, single elimination. Tournament matches don't change ratings."),
      h("li", null, "House Base (the base model) and House Bot (a scripted bot) are always on the ladder as reference points; they don't enter tournaments."),
      h("li", null, "Seasons follow the model format and simulator version. A new season resets ratings and you re-submit; old seasons stay browsable."),
    ),
    h("p", { class: "muted small" }, "By uploading you agree to the ", link("/terms", "terms"), "."),
  );
}

export function terms(root: HTMLElement) {
  root.append(
    h("h1", null, "Terms"),
    h(
      "ul",
      null,
      h("li", null, "Models you upload and every match result are public."),
      h("li", null, "One account per person."),
      h("li", null, "Matches can be voided, and models removed, for exploiting simulator bugs."),
      h("li", null, "You can delete your account at any time from the account menu."),
    ),
    h("p", { class: "muted small" }, "Draft text: the final terms (and the kit's licence) are set by the project owner."),
  );
}

export function privacy(root: HTMLElement) {
  root.append(
    h("h1", null, "Privacy"),
    h("p", null, "We store only your GitHub user id, login and avatar URL, plus the models you upload and the matches they play."),
    h("p", null, "Sign-in uses a session cookie. There are no trackers or third-party analytics on this site."),
    h("p", null, "Delete your account any time from the account menu: your account is removed."),
    h("p", { class: "muted small" }, "Draft text, set by the project owner."),
  );
}

export function notFound(root: HTMLElement) {
  root.append(h("h1", null, "Not found"), h("p", null, "That page doesn't exist. ", link("/", "Go home"), "."));
}
