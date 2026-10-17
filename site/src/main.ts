// Volante website: shell (header, nav, account menu, theme, Arena sub-nav). Routes live in routes.ts.

import "./style.css";
import { api, loginUrl, me } from "./api";
import { h, link, replace, s } from "./dom";
import "./routes";
import { setOnNavigate, start } from "./router";
import type { Me } from "./types";

declare const __MOCK__: boolean;

const REPO = "https://github.com/jesusthecreator017/oso";

/** Top nav: [href, label]. Features and How it works are anchors on the landing page. */
const NAV: [string, string][] = [
  ["/#features", "Features"],
  ["/#how", "How it works"],
  ["/arena", "Arena"],
];

/** Arena sub-nav, shown inside /arena/*. */
const ARENA_NAV: [string, string][] = [
  ["/arena", "Home"],
  ["/arena/ladder", "Ladder"],
  ["/arena/tournaments", "Tournaments"],
  ["/arena/my-models", "My models"],
  ["/arena/get-started", "Get started"],
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

const MENU_ICON = ["M4 7h16", "M4 12h16", "M4 17h16"];

/** Logo mark: a square, the shuttle's circle and a swing diagonal. */
function logoMark(): SVGSVGElement {
  return s(
    "svg",
    { viewBox: "0 0 32 32", class: "mark", "aria-hidden": "true", focusable: "false" },
    s("rect", { width: 32, height: 32, class: "mark-sq" }),
    s("circle", { cx: 10, cy: 22, r: 5, class: "mark-cut" }),
    s("path", { d: "M14 28 30 4", class: "mark-bar" }),
  );
}

function shell(): HTMLElement {
  const account = h("div", { class: "account" });
  const navLinks = NAV.map(([href, label]) => link(href, label));
  const nav = h("nav", { class: "main-nav", id: "main-nav", "aria-label": "Main" }, navLinks);
  const themeBtn = h("button", { type: "button", class: "icon-btn", "aria-label": "Switch theme", title: "Theme" }, "◐");
  themeBtn.addEventListener("click", () => {
    const order: Theme[] = ["system", "light", "dark"];
    const next = order[(order.indexOf(getTheme()) + 1) % 3];
    applyTheme(next);
    themeBtn.title = `Theme: ${next}`;
  });
  // Phones: the nav is a disclosure behind a 44x44 button. Esc closes it and returns focus to the button.
  const menuBtn = h("button", { type: "button", class: "icon-btn menu-btn", "aria-label": "Menu", "aria-expanded": "false", "aria-controls": "main-nav" });
  menuBtn.append(s("svg", { viewBox: "0 0 24 24", class: "ico", "aria-hidden": "true" }, MENU_ICON.map((d) => s("path", { d }))));
  const setOpen = (open: boolean) => {
    nav.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
  };
  menuBtn.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("open")) (setOpen(false), menuBtn.focus());
  });

  const sub = h("nav", { class: "sub-nav", "aria-label": "Arena", hidden: true }, h("div", { class: "bar" }, ARENA_NAV.map(([href, label]) => link(href, label))));
  const subLinks = [...sub.querySelectorAll("a")];
  const header = h(
    "header",
    { class: "site-header" },
    h("div", { class: "bar" }, link("/", h("span", { class: "brand" }, logoMark(), "Volante")), nav, themeBtn, account, menuBtn),
    sub,
  );
  setOnNavigate((path) => {
    setOpen(false);
    const inArena = path === "/arena" || path.startsWith("/arena/");
    sub.hidden = !inArena;
    const on = (a: HTMLAnchorElement, current: boolean) => (current ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
    for (const a of navLinks) on(a, a.getAttribute("href") === "/arena" && inArena);
    for (const a of subLinks) {
      const href = a.getAttribute("href")!;
      on(a, href === "/arena" ? path.replace(/\/$/, "") === "/arena" : path === href || path.startsWith(href + "/"));
    }
  });
  void me().then((m) => renderAccount(account, m), () => renderAccount(account, null));
  return header;
}

function renderAccount(el: HTMLElement, m: Me | null) {
  if (!m?.user) {
    replace(el, h("a", { href: loginUrl(), class: "btn small" }, "Sign in"));
    return;
  }
  const u = m.user;
  const menu = h(
    "div",
    { class: "menu", role: "menu", hidden: true },
    link("/settings", "Settings"),
    link("/arena/my-models", "My models"),
    h("button", { type: "button", role: "menuitem", onclick: () => void signOut() }, "Sign out"),
  );
  const btn = h(
    "button",
    { type: "button", class: "account-btn", "aria-haspopup": "menu", "aria-expanded": "false", "aria-label": `Account menu for ${u.login}` },
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

function footer(): HTMLElement {
  return h(
    "footer",
    { class: "site-footer" },
    h("nav", { "aria-label": "Footer" }, link("/terms", "Terms"), link("/privacy", "Privacy"), link("/arena", "Arena"), h("a", { href: REPO, rel: "noopener noreferrer" }, "GitHub")),
    h("p", { class: "muted small" }, "Volante · the racquet pod for badminton"),
  );
}

async function boot() {
  if (__MOCK__) await (await import("./mock")).installMock();
  applyTheme(getTheme());
  const main = h("main", { id: "main", tabindex: "-1" });
  const toast = h("div", { id: "toast", class: "toast", role: "status", "aria-live": "polite" });
  document.body.replaceChildren(h("a", { href: "#main", class: "skip" }, "Skip to content"), shell(), main, footer(), toast);
  start(main);
}

void boot();
