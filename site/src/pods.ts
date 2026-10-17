// Pod display names: validation and the inline rename form. The name lives on the server, linked to the pod's id; it
// never changes the pod's Bluetooth name.

import { ApiError, api } from "./api";
import { h } from "./dom";
import type { Pod } from "./types";

export const NAME_MAX = 40;

/** Returns the trimmed name, or an error message. 1-40 printable characters: no control, format or line-break chars. */
export function checkPodName(raw: string): { name: string; error: null } | { name: null; error: string } {
  const name = raw.trim();
  const len = [...name].length;
  if (len < 1) return { name: null, error: "Enter a name." };
  if (len > NAME_MAX) return { name: null, error: `Use at most ${NAME_MAX} characters (now ${len}).` };
  if (/[\p{C}\p{Zl}\p{Zp}]/u.test(name)) return { name: null, error: "Use printable characters only." };
  return { name, error: null };
}

let toastEl: HTMLElement | null = null;
let toastTimer = 0;
/** Polite live-region message. The region exists from boot so screen readers announce text changes. */
export function toast(msg: string) {
  toastEl ??= document.getElementById("toast");
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => toastEl?.classList.remove("show"), 4000);
}

let formSeq = 0;

/** Inline rename form: visible label, Save/Cancel, error next to the field. `onSaved` gets the updated pod. */
export function renameForm(pod: Pod, onSaved: (p: Pod) => void, onCancel: () => void): HTMLFormElement {
  const n = ++formSeq;
  const input = h("input", { id: `pod-name-${n}`, name: "name", type: "text", value: pod.name, autocomplete: "off", "aria-describedby": `pod-err-${n}`, maxlength: 80 });
  const err = h("p", { id: `pod-err-${n}`, class: "field-error", role: "alert" });
  const save = h("button", { type: "submit", class: "btn primary small" }, "Save");
  const cancel = h("button", { type: "button", class: "btn small", onclick: onCancel }, "Cancel");
  const showError = (msg: string) => {
    err.textContent = msg;
    input.setAttribute("aria-invalid", msg ? "true" : "false");
  };
  const form = h(
    "form",
    { class: "rename", novalidate: true },
    h("label", { for: input.id }, "Pod name"),
    h("div", { class: "row" }, input, save, cancel),
    err,
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const r = checkPodName(input.value);
    if (r.error !== null) return showError(r.error);
    showError("");
    save.disabled = true;
    try {
      onSaved(await api.renamePod(pod.id, r.name));
    } catch (ex) {
      showError(ex instanceof ApiError && ex.code === "invalid_name" ? `Name must be 1-${NAME_MAX} printable characters.` : ex instanceof Error ? ex.message : String(ex));
    } finally {
      save.disabled = false;
    }
  });
  return form;
}
