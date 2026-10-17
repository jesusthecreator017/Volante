// Tiny DOM builder. Strings always become text nodes, so user data (model names, descriptions, logins) can never be
// parsed as HTML. There is no innerHTML anywhere in the site.

type Child = Node | string | number | null | undefined | false | Child[];
type Attrs = Record<string, string | number | boolean | null | undefined | EventListener>;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs | null = null, ...children: Child[]): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function s<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Attrs | null = null, ...children: Child[]): SVGElementTagNameMap[K] {
  const el = document.createElementNS(SVG_NS, tag);
  setAttrs(el, attrs);
  append(el, children);
  return el;
}

function setAttrs(el: Element, attrs: Attrs | null) {
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "style") throw new Error("style attributes are blocked by the CSP: use a class");
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

export function append(el: Node, children: Child[]) {
  for (const c of children.flat(Infinity as 1) as Child[]) {
    if (c === null || c === undefined || c === false) continue;
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : (c as Node));
  }
}

export function replace(el: Element, ...children: Child[]) {
  el.replaceChildren();
  append(el, children);
}

/** Internal link handled by the router (history navigation, no reload). */
export function link(href: string, ...children: Child[]): HTMLAnchorElement {
  return h("a", { href, "data-nav": true }, ...children);
}
