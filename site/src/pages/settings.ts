// /settings: the signed-in user's pods (display-name rename) and account deletion.

import { ApiError, api, loginUrl, me } from "../api";
import { fmtAgo } from "../components";
import { h, link, replace } from "../dom";
import { renameForm, toast } from "../pods";
import type { Pod } from "../types";

export async function deleteAccount() {
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

function podItem(pod: Pod): HTMLElement {
  const li = h("li", { class: "pod" });
  const show = (p: Pod) => {
    const rename = h("button", { type: "button", class: "btn small", "aria-label": `Rename ${p.name}` }, "Rename");
    rename.addEventListener("click", () => {
      const form = renameForm(
        p,
        (saved) => {
          toast(`Renamed to ${saved.name}.`);
          show(saved);
        },
        () => {
          show(p);
          rename.focus();
        },
      );
      replace(li, form);
      form.querySelector("input")?.focus();
    });
    replace(
      li,
      h(
        "div",
        { class: "pod-main" },
        h("strong", { class: "pod-name" }, p.name),
        h("code", { class: "pod-id" }, p.hw_id),
        h("span", { class: "muted small" }, p.last_seen ? `Last seen ${fmtAgo(p.last_seen)}` : "Not seen yet"),
      ),
      rename,
    );
  };
  show(pod);
  return li;
}

export async function settings(root: HTMLElement) {
  root.append(h("h1", null, "Settings"));
  if (!(await me().catch(() => null))?.user) {
    root.append(h("p", null, "Sign in to manage your pods and account."), h("p", null, h("a", { href: loginUrl(), class: "btn primary" }, "Sign in with GitHub")));
    return;
  }
  const pods = h("div", { "aria-busy": "true" }, h("p", { class: "muted" }, "Loading…"));
  root.append(
    h("section", { class: "settings-sec", "aria-labelledby": "pods-h" }, h("h2", { id: "pods-h" }, "Your pods"), h("p", { class: "muted small" }, "A display name saved on your account for each pod. It doesn't change the pod's Bluetooth name."), pods),
    h(
      "section",
      { class: "settings-sec", "aria-labelledby": "acct-h" },
      h("h2", { id: "acct-h" }, "Account"),
      h("p", null, "Deleting your account removes it and retires your Arena models. ", link("/privacy", "Privacy")),
      h("button", { type: "button", class: "btn danger", onclick: () => void deleteAccount() }, "Delete account…"),
    ),
  );
  try {
    const items = await api.pods();
    replace(
      pods,
      items.length
        ? h("ul", { class: "plain pods" }, items.map(podItem))
        : // Pairing a pod happens in the Volante app (POST /api/me/pods is future work): nothing to do on the site.
          h("p", { class: "empty" }, "No pods linked yet: pair one in the Volante app."),
    );
  } catch (e) {
    replace(pods, h("p", { class: "error" }, e instanceof ApiError ? e.detail : "Couldn't load your pods."));
  }
  pods.removeAttribute("aria-busy");
}
