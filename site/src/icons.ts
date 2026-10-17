// Phosphor icons (https://phosphoricons.com, MIT). Path data lives in icons.gen.ts (npm run icons); this builds the <svg>.
// Decorative by default (aria-hidden); pass `label` when the icon is the only thing that says what a control does.

import { s } from "./dom";
import { PHOSPHOR } from "./icons.gen";

export type IconName = keyof typeof PHOSPHOR;

export function icon(name: IconName, opts: { label?: string; cls?: string } = {}): SVGSVGElement {
  const a11y = opts.label ? { role: "img", "aria-label": opts.label } : { "aria-hidden": "true" };
  return s(
    "svg",
    { viewBox: "0 0 256 256", fill: "currentColor", class: opts.cls ? `ph ${opts.cls}` : "ph", focusable: "false", ...a11y },
    PHOSPHOR[name].map((d) => s("path", { d })),
  );
}
