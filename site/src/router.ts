// History-API router. The server must answer every page path with index.html (deploy notes in site/README.md).

export type Params = Record<string, string>;
export type Page = (root: HTMLElement, params: Params, query: URLSearchParams) => void | (() => void) | Promise<void | (() => void)>;

interface Route {
  re: RegExp;
  keys: string[];
  page: Page;
  title: string;
}

const routes: Route[] = [];

export function route(pattern: string, title: string, page: Page) {
  const keys: string[] = [];
  const re = new RegExp(
    // `:id` is numeric; `:id*` is an opaque app id (the server's session id rule: 1-64 of [A-Za-z0-9._-], starting alphanumeric, so never "." or "..")
    "^" + pattern.replace(/\/:(\w+)(\*?)/g, (_, k, str) => (keys.push(k), str ? "/([A-Za-z0-9][A-Za-z0-9._-]{0,63})" : "/(\\d+)")).replace(/\/$/, "") + "/?$",
  );
  routes.push({ re, keys, page, title });
}

export function match(path: string): { route: Route; params: Params } | null {
  for (const r of routes) {
    const m = r.re.exec(path);
    if (m) return { route: r, params: Object.fromEntries(r.keys.map((k, i) => [k, m[i + 1]])) };
  }
  return null;
}

/** Pre-/arena paths (the Arena used to own the site root): client-side 301s so old links keep working. */
const LEGACY = /^\/(ladder|models\/\d+|matches\/\d+|tournaments(\/\d+)?|my-models|get-started)\/?$/;
export function redirectFor(path: string): string | null {
  if (/^\/arena\/twins\/?$/.test(path)) return "/arena/ladder?ladder=twins"; // the Twins ladder is a switch on the Ladder page
  const s = /^\/sessions(\/[^/]+)?\/?$/.exec(path); // the app's "See them on the website" link (PR #78): Account → Dashboard
  if (s) return "/account/dashboard" + (s[1] ? "/sessions" + s[1] : "");
  return LEGACY.test(path) ? "/arena" + path.replace(/\/$/, "") : null;
}

function scrollToHash() {
  const id = decodeURIComponent(location.hash.slice(1));
  const el = id ? document.getElementById(id) : null;
  if (el) el.scrollIntoView();
}

let firstRender = true;
let cleanup: (() => void) | void = undefined;
let notFound: Page = () => {};
let onNavigate: (path: string) => void = () => {};

export function setNotFound(p: Page) {
  notFound = p;
}
export function setOnNavigate(f: (path: string) => void) {
  onNavigate = f;
}

export async function render(root: HTMLElement) {
  if (typeof cleanup === "function") cleanup();
  cleanup = undefined;
  root.replaceChildren();
  let url = new URL(location.href);
  const to = redirectFor(url.pathname);
  if (to) {
    const t = new URL(to, location.origin);
    for (const [k, v] of url.searchParams) if (!t.searchParams.has(k)) t.searchParams.set(k, v);
    history.replaceState(null, "", t.pathname + t.search + url.hash);
    url = new URL(location.href);
  }
  const m = match(url.pathname);
  document.title = m ? `${m.route.title} · Volante` : "Not found · Volante";
  onNavigate(url.pathname);
  cleanup = await (m ? m.route.page(root, m.params, url.searchParams) : notFound(root, {}, url.searchParams));
  // Not on the first load: focusing <main> moves the Tab start point past the skip link and header.
  if (!firstRender) root.focus({ preventScroll: true });
  firstRender = false;
  scrollToHash();
}

export function navigate(href: string, replace = false) {
  const u = new URL(href, location.href);
  if (u.hash && u.pathname === location.pathname && u.search === location.search && !replace) {
    // same page, new anchor: no re-render
    history.pushState(null, "", href);
    onNavigate(u.pathname);
    scrollToHash();
    return;
  }
  if (replace) history.replaceState(null, "", href);
  else history.pushState(null, "", href);
  window.scrollTo(0, 0);
  const root = document.getElementById("main")!;
  // Cross-fade between pages where the browser supports it; CSS turns the animation off for reduced motion.
  if (document.startViewTransition) document.startViewTransition(() => render(root));
  else void render(root);
}

export function start(root: HTMLElement) {
  document.addEventListener("click", (e) => {
    const a = (e.target as Element).closest?.("a[data-nav]") as HTMLAnchorElement | null;
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(a.getAttribute("href")!);
  });
  window.addEventListener("popstate", () => void render(root));
  void render(root);
}
