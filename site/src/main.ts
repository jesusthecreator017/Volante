// oso Arena website: shell (header, account menu, theme), routes.

import "./style.css";
import { api, loginUrl, me } from "./api";
import { h, link, replace } from "./dom";
import { route, setNotFound, setOnNavigate, start } from "./router";
import { getStarted, notFound, privacy, terms } from "./pages/content";
import { home } from "./pages/home";
import { ladder } from "./pages/ladder";
import { match } from "./pages/match";
import { model } from "./pages/model";
import { myModels } from "./pages/myModels";
import { tournament, tournaments } from "./pages/tournament";
import type { Me } from "./types";

declare const __MOCK__: boolean;

route("/", "Home", home);
route("/ladder", "Ladder", ladder);
route("/models/:id", "Model", model);
route("/matches/:id", "Match", match);
route("/tournaments", "Tournaments", tournaments);
route("/tournaments/:id", "Tournament", tournament);
route("/my-models", "My models", myModels);
route("/get-started", "Get started", getStarted);
route("/terms", "Terms", terms);
route("/privacy", "Privacy", privacy);
setNotFound(notFound);

const NAV: [string, string][] = [
  ["/ladder", "Ladder"],
  ["/tournaments", "Tournaments"],
  ["/get-started", "Get started"],
];

// ---------------------------------------------------------------- theme (per-viewer convenience only)

type Theme = "light" | "dark" | "system";
function getTheme(): Theme {
  try {
    return (localStorage.getItem("oso-theme") as Theme) || "system";
  } catch {
    return "system";
  }
}
function applyTheme(t: Theme) {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  try {
    localStorage.setItem("oso-theme", t);
  } catch {
    // private mode
  }
}

// ---------------------------------------------------------------- shell

function shell(): HTMLElement {
  const account = h("div", { class: "account" });
  const navLinks = NAV.map(([href, label]) => link(href, label));
  const themeBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "Switch theme", title: "Theme" }, "◐");
  themeBtn.addEventListener("click", () => {
    const order: Theme[] = ["system", "light", "dark"];
    const next = order[(order.indexOf(getTheme()) + 1) % 3];
    applyTheme(next);
    themeBtn.title = `Theme: ${next}`;
  });
  const header = h(
    "header",
    { class: "site-header" },
    h("div", { class: "bar" }, link("/", h("span", { class: "brand" }, "oso", h("span", { class: "brand-sub" }, " Arena"))), h("nav", { class: "main-nav", "aria-label": "Main" }, navLinks), themeBtn, account),
  );
  setOnNavigate((path) => {
    for (const a of navLinks) {
      const on = path === a.getAttribute("href") || path.startsWith(a.getAttribute("href") + "/");
      if (on) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  });
  void me().then((m) => renderAccount(account, m), () => renderAccount(account, null));
  return header;
}

function renderAccount(el: HTMLElement, m: Me | null) {
  if (!m?.user) {
    replace(el, h("a", { href: loginUrl(), class: "btn small" }, "Sign in with GitHub"));
    return;
  }
  const u = m.user;
  const menu = h(
    "div",
    { class: "menu", role: "menu", hidden: true },
    link("/my-models", "My models"),
    h("button", { type: "button", role: "menuitem", onclick: () => void signOut() }, "Sign out"),
    h("button", { type: "button", role: "menuitem", class: "danger", onclick: () => void deleteAccount() }, "Delete account…"),
  );
  const btn = h(
    "button",
    { type: "button", class: "account-btn", "aria-haspopup": "menu", "aria-expanded": "false" },
    h("img", { class: "avatar", src: u.avatar_url, alt: "", width: 28, height: 28, referrerpolicy: "no-referrer" }),
    h("span", { class: "login" }, u.login),
  );
  const toggle = (open = menu.hidden) => {
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", (e) => (e.stopPropagation(), toggle()));
  document.addEventListener("click", () => toggle(false));
  document.addEventListener("keydown", (e) => e.key === "Escape" && toggle(false));
  replace(el, btn, menu);
}

async function signOut() {
  try {
    await api.logout();
  } finally {
    location.href = "/";
  }
}

async function deleteAccount() {
  const m = await me();
  const typed = prompt(`This deletes your account and retires your models. Type your GitHub login (${m.user?.login}) to confirm.`);
  if (typed !== m.user?.login) return;
  try {
    await api.deleteAccount();
    location.href = "/";
  } catch (e) {
    alert(e instanceof Error ? e.message : String(e));
  }
}

function footer(): HTMLElement {
  return h(
    "footer",
    { class: "site-footer" },
    h("nav", { "aria-label": "Footer" }, link("/get-started", "Get started"), link("/terms", "Terms"), link("/privacy", "Privacy")),
    h("p", { class: "muted small" }, "oso Arena · AI badminton"),
  );
}

async function boot() {
  if (__MOCK__) await (await import("./mock")).installMock();
  applyTheme(getTheme());
  const main = h("main", { id: "main", tabindex: "-1" });
  document.body.replaceChildren(h("a", { href: "#main", class: "skip" }, "Skip to content"), shell(), main, footer());
  start(main);
}

void boot();
