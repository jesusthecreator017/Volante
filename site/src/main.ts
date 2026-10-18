// Volante website: shell (header, nav, account menu, theme, Arena sub-nav). Routes live in routes.ts.

import "./style.css";
import { api, loginUrl, me } from "./api";
import { h, link, replace } from "./dom";
import "./routes";
import { setOnNavigate, start } from "./router";
import type { Me } from "./types";
import { icon, type IconName } from "./icons";
import appIcon from "./assets/app-icon.png";

declare const __MOCK__: boolean;

const REPO = "https://github.com/jesusthecreator017/Volante";

/** Top nav: [href, label]. Features is an anchor on the landing page. */
const NAV: [string, string][] = [
  ["/#features", "Features"],
  ["/how-it-works", "How it works"],
  ["/arena", "Arena"],
];

/** Arena sub-nav, shown inside /arena/*. */
const ARENA_NAV: [string, string][] = [
  ["/arena", "Home"],
  ["/arena/ladder", "Ladder"],
  ["/arena/tournaments", "Tournaments"],
  ["/arena/get-started", "Train & upload"],
];

// ---------------------------------------------------------------- theme (per-viewer convenience only)

/** Light or dark. Until the visitor picks one, the site follows the system setting. */
type Theme = "light" | "dark";
function savedTheme(): Theme | null {
  try {
    const t = localStorage.getItem("oso-theme");
    return t === "light" || t === "dark" ? t : null; // older visits may have stored "system"
  } catch {
    return null;
  }
}
const systemTheme = (): Theme => (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const getTheme = (): Theme => savedTheme() ?? systemTheme();
function applyTheme(t: Theme | null, save = true) {
  if (t === null) document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  if (!save || t === null) return;
  try {
    localStorage.setItem("oso-theme", t);
  } catch {
    // private mode
  }
}

// ---------------------------------------------------------------- shell

/** Logo mark: the iPhone app's icon (apple/OsoPhone/Assets.xcassets/AppIcon), so the site and the app look like one product. */
function logoMark(): HTMLImageElement {
  return h("img", { src: appIcon, class: "mark", alt: "", width: 30, height: 30, decoding: "async" });
}

function shell(): HTMLElement {
  const account = h("div", { class: "account" });
  const navLinks = NAV.map(([href, label]) => link(href, label));
  const nav = h("nav", { class: "main-nav", id: "main-nav", "aria-label": "Main" }, navLinks);
  // Two states only: the button shows what a click switches to (moon in light mode, sun in dark mode).
  const THEME_ICON: Record<Theme, IconName> = { light: "moon", dark: "sun" };
  const themeBtn = h("button", { type: "button", class: "icon-btn" });
  const drawTheme = () => {
    const next: Theme = getTheme() === "dark" ? "light" : "dark";
    themeBtn.replaceChildren(icon(THEME_ICON[getTheme()]));
    themeBtn.title = `Switch to ${next} mode`;
    themeBtn.setAttribute("aria-label", `Switch to ${next} mode`);
  };
  themeBtn.addEventListener("click", () => {
    applyTheme(getTheme() === "dark" ? "light" : "dark");
    drawTheme();
  });
  // While nothing is saved, follow the system if it changes.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => savedTheme() === null && drawTheme());
  drawTheme();
  // Phones: the nav is a disclosure behind a 44x44 button. Esc closes it and returns focus to the button.
  const menuBtn = h("button", { type: "button", class: "icon-btn menu-btn", "aria-label": "Menu", "aria-expanded": "false", "aria-controls": "main-nav" });
  menuBtn.append(icon("list"));
  const setOpen = (open: boolean) => {
    nav.classList.toggle("open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    menuBtn.replaceChildren(icon(open ? "x" : "list"));
  };
  menuBtn.addEventListener("click", () => setOpen(!nav.classList.contains("open")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("open")) (setOpen(false), menuBtn.focus());
  });

  const sub = h("nav", { class: "sub-nav", "aria-label": "Arena", hidden: true }, h("div", { class: "bar" }, ARENA_NAV.map(([href, label]) => link(href, label))));
  const subLinks = [...sub.querySelectorAll("a")];
  const subBar = sub.querySelector<HTMLElement>(".bar")!;
  const edges = () => {
    subBar.classList.toggle("more-left", subBar.scrollLeft > 2);
    subBar.classList.toggle("more-right", subBar.scrollLeft + subBar.clientWidth < subBar.scrollWidth - 2);
  };
  subBar.addEventListener("scroll", edges, { passive: true });
  addEventListener("resize", edges);
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
    if (inArena) {
      // keep the current tab in view on narrow screens (e.g. "Get started" at the far end)
      requestAnimationFrame(() => {
        const cur = subLinks.find((a) => a.hasAttribute("aria-current"));
        if (cur) {
          const x = cur.getBoundingClientRect().left - subBar.getBoundingClientRect().left + subBar.scrollLeft;
          subBar.scrollTo({ left: Math.max(0, x - (subBar.clientWidth - cur.offsetWidth) / 2), behavior: "instant" });
        }
        edges();
      });
    }
  });
  void me().then((m) => renderAccount(account, m), () => renderAccount(account, null));
  return header;
}

function renderAccount(el: HTMLElement, m: Me | null) {
  if (!m?.user) {
    replace(el, h("a", { href: loginUrl(), class: "btn small with-icon signin", "aria-label": "Sign in with GitHub" }, icon("github-logo"), h("span", { class: "signin-label" }, "Sign in")));
    return;
  }
  const u = m.user;
  const menu = h(
    "div",
    { class: "menu", role: "menu", hidden: true },
    link("/settings", "Settings"),
    link("/arena/get-started#mine", "My models"),
    h("button", { type: "button", role: "menuitem", onclick: () => void signOut() }, icon("sign-out"), "Sign out"),
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
    h("nav", { "aria-label": "Footer" }, link("/privacy", "Privacy"), link("/arena", "Arena"), h("a", { href: REPO, rel: "noopener noreferrer", class: "with-icon" }, icon("github-logo"), "GitHub")),
    h("p", { class: "muted small" }, "Volante · the racquet pod for badminton"),
  );
}

/** What a failed GitHub sign-in reason means for the person trying (the server sends ?signin=failed&reason=...). */
export const SIGNIN_REASONS: Record<string, string> = {
  denied: "You cancelled the GitHub sign-in.",
  no_state_cookie: "Your browser didn't keep the sign-in cookie. Allow cookies for this site and try again.",
  state_expired: "The sign-in took longer than 10 minutes. Try again.",
  state_mismatch: "The sign-in link was out of date. Try again.",
  bad_verification_code: "GitHub's sign-in code had expired or was already used. Try again.",
  incorrect_client_credentials: "Sign-in is misconfigured on the server (the GitHub app's ID or secret). The site owner needs to fix it.",
  redirect_uri_mismatch: "Sign-in is misconfigured on the server (the GitHub app's callback URL). The site owner needs to fix it.",
  github_unreachable: "The server couldn't reach GitHub. Try again in a minute.",
  banned: "This account can't sign in.",
};

/** A banner for ?signin=failed (then the parameters are removed from the address bar). */
function signInNotice(): HTMLElement | null {
  const q = new URLSearchParams(location.search);
  if (q.get("signin") !== "failed") return null;
  const reason = (q.get("reason") ?? "").replace(/[^a-z0-9_]/g, "").slice(0, 40);
  q.delete("signin");
  q.delete("reason");
  history.replaceState(null, "", location.pathname + (q.size ? `?${q}` : "") + location.hash);
  const close = h("button", { type: "button", class: "icon-btn notice-close", "aria-label": "Dismiss" }, icon("x"));
  const box = h(
    "div",
    { class: "notice notice-bad", role: "alert" },
    icon("warning-circle"),
    h("p", null, h("strong", null, "Sign-in didn't work. "), SIGNIN_REASONS[reason] ?? `Something went wrong (${reason || "unknown"}). Try again.`),
    h("a", { href: loginUrl("/arena"), class: "btn small with-icon" }, icon("github-logo"), "Try again"),
    close,
  );
  close.addEventListener("click", () => box.remove());
  return box;
}

async function boot() {
  if (__MOCK__) await (await import("./mock")).installMock();
  applyTheme(savedTheme(), false);
  const main = h("main", { id: "main", tabindex: "-1" });
  const toast = h("div", { id: "toast", class: "toast", role: "status", "aria-live": "polite" });
  document.body.replaceChildren(h("a", { href: "#main", class: "skip" }, "Skip to content"), shell(), signInNotice() ?? "", main, footer(), toast);
  start(main);
}

void boot();
