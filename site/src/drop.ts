// A styled file input that also accepts drag and drop. The file is handed to `onFile`; nothing is uploaded here.

import { h } from "./dom";
import { icon, type IconName } from "./icons";

export interface DropOpts {
  id: string;
  accept: string;
  ico: IconName;
  title: string;
  note: string;
}

export function dropZone(o: DropOpts, onFile: (f: File) => void): HTMLElement {
  const input = h("input", { type: "file", accept: o.accept, class: "sr-only", id: o.id });
  input.addEventListener("change", () => input.files?.[0] && onFile(input.files[0]));
  const zone = h(
    "label",
    { class: "drop glass", for: o.id },
    input,
    h("span", { class: "drop-ico" }, icon(o.ico)),
    h("strong", null, o.title),
    h("span", { class: "muted small" }, icon("lock-simple"), ` ${o.note}`),
  );
  zone.addEventListener("dragover", (e) => (e.preventDefault(), zone.classList.add("over")));
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("over");
    const f = e.dataTransfer?.files?.[0];
    if (f) onFile(f);
  });
  return zone;
}
