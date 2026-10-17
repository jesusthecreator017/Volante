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
    "^" + pattern.replace(/\/:(\w+)/g, (_, k) => (keys.push(k), "/(\\d+)")).replace(/\/$/, "") + "/?$",
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
  return LEGACY.test(path) ? "/arena" + path.replace(/\/$/, "") : null;
}

function scrollToHash() {
  const id = decodeURIComponent(location.hash.slice(1));
  const el = id ? document.getElementById(id) : null;
  if (el) el.scrollIntoView();
}

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
    history.replaceState(null, "", to + url.search + url.hash);
    url = new URL(location.href);
  }
  const m = match(url.pathname);
  document.title = m ? `${m.route.title} · oso` : "Not found · oso";
  onNavigate(url.pathname);
  cleanup = await (m ? m.route.page(root, m.params, url.searchParams) : notFound(root, {}, url.searchParams));
  root.focus({ preventScroll: true });
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
  void render(document.getElementById("main")!);
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
