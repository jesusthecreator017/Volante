// My models: upload (client checks size + extension only), poll until the checker decides, retire.

import { api, ApiError, loginUrl, me } from "../api";
import { MAX_UPLOAD_BYTES, UPLOADS_PER_DAY, checkUploadFile } from "../arena";
import { buildBars, errorBox, fmtDate, fmtRating, load, modelLink, statusPill } from "../components";
import { h, link, replace } from "../dom";
import type { ModelDetail, ModelSummary } from "../types";

const POLL_MS = 3000;

export async function myModels(root: HTMLElement) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const stop = () => timer && clearTimeout(timer);
  const slot = h("div");
  root.append(h("h1", null, "My models"), slot);
  await load(slot, () => me(true), async (m) => {
    if (!m.user) {
      slot.append(
        h("p", null, "Sign in with GitHub to upload a model."),
        h("a", { href: loginUrl("/my-models"), class: "btn primary" }, "Sign in with GitHub"),
      );
      return;
    }
    const status = h("div", { "aria-live": "polite" });
    const list = h("div");
    const leftText = (left: number | undefined) =>
      left === undefined ? `Up to ${UPLOADS_PER_DAY} uploads per day.` : `${left} of ${UPLOADS_PER_DAY} uploads left today.`;
    const left = m.uploads_left_today;
    const leftEl = h("p", { class: "muted small" }, leftText(left));
    const fileInput = h("input", { type: "file", id: "model-file", accept: ".safetensors", required: true });
    const submit = h("button", { type: "submit", class: "btn primary", disabled: left === 0 }, "Upload");
    const form = h(
      "form",
      { class: "card upload", novalidate: true },
      h("h2", null, "Upload a model"),
      h("label", { for: "model-file" }, "Model file (.safetensors, up to 1 MiB)"),
      fileInput,
      h("p", { class: "muted small" }, "The name, description and build are read from inside the file (set when you export it with the kit)."),
      leftEl,
      submit,
    );
    slot.append(form, status, h("h2", null, "Your models"), list);

    let mine: ModelSummary[] = [];
    const refreshList = () =>
      load(list, () => api.myModels(), (ms) => {
        mine = ms;
        list.append(
          ms.length
            ? h("ul", { class: "my-models" }, ms.map((x) => modelCard(x, () => void refreshList())))
            : h("p", { class: "muted" }, "No models yet. ", link("/get-started", "Get started"), " to train one."),
        );
      });
    void refreshList();

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = fileInput.files?.[0];
      if (!f) return replace(status, h("p", { class: "error" }, "Choose a file first."));
      const bad = checkUploadFile(f);
      if (bad) return replace(status, h("p", { class: "error" }, bad));
      const active = mine.find((x) => x.status === "active");
      if (active && !confirm(`Uploading retires your active model "${active.name}". Its history stays visible. Continue?`)) return;
      submit.disabled = true;
      replace(status, h("p", { class: "loading" }, "Uploading…"));
      try {
        const r = await api.uploadModel(f);
        form.reset();
        poll(r.id);
      } catch (err) {
        replace(status, uploadError(err));
      } finally {
        submit.disabled = false;
        void me(true).then((mm) => {
          leftEl.textContent = leftText(mm.uploads_left_today);
          if (mm.uploads_left_today === 0) submit.disabled = true;
        });
      }
    });

    function poll(id: number) {
      stop();
      const tick = async () => {
        try {
          const d = await api.model(id);
          replace(status, pendingCard(d));
          if (d.status === "pending") timer = setTimeout(tick, POLL_MS);
          else void refreshList();
        } catch (err) {
          replace(status, errorBox(err, () => poll(id)));
        }
      };
      void tick();
    }
  });
  return stop;
}


function uploadError(err: unknown): HTMLElement {
  if (err instanceof ApiError && err.status === 429) {
    const when = err.retryAfter ? ` Try again in ${Math.ceil(err.retryAfter / 60)} min.` : "";
    return h("p", { class: "error" }, `That's the daily limit of ${UPLOADS_PER_DAY} uploads.${when}`);
  }
  if (err instanceof ApiError && err.status === 413) return h("p", { class: "error" }, `The server refused the file as too large (limit ${MAX_UPLOAD_BYTES / 1024} KiB).`);
  if (err instanceof ApiError && err.status === 401) return h("p", { class: "error" }, "Your session ended. ", h("a", { href: loginUrl("/my-models") }, "Sign in again"), ".");
  return errorBox(err);
}

function pendingCard(d: ModelDetail): HTMLElement {
  return h(
    "div",
    { class: `card result-${d.status}` },
    h("p", null, statusPill(d.status), " ", h("strong", null, d.name)),
    d.status === "pending" ? h("p", { class: "loading" }, "Checking the model… this takes a few seconds.") : null,
    d.status === "active" ? h("p", null, "Accepted. It now plays its 10 placement matches. ", modelLink(d)) : null,
    d.status === "rejected"
      ? [h("p", null, "The checker rejected it:"), h("ul", { class: "reasons" }, (d.reject_reasons ?? ["no reason given"]).map((r) => h("li", null, r))), h("p", { class: "muted small" }, "Fix it with the kit and upload again. ", link("/get-started", "How to improve"))]
      : null,
  );
}

function modelCard(m: ModelSummary, changed: () => void): HTMLElement {
  const retire = h(
    "button",
    {
      type: "button",
      class: "btn danger",
      onclick: async () => {
        if (!confirm(`Retire "${m.name}"? It stops playing; its history stays visible.`)) return;
        retire.disabled = true;
        try {
          await api.retireModel(m.id);
          changed();
        } catch (e) {
          retire.replaceWith(errorBox(e));
        }
      },
    },
    "Retire",
  );
  return h(
    "li",
    { class: "card my-model" },
    h("div", { class: "row" }, modelLink(m), statusPill(m.status)),
    buildBars(m.build),
    h("p", { class: "muted small" }, `Rating ${fmtRating(m.rating)} · ${m.games} games`),
    m.status === "active" ? retire : null,
  );
}

export const _test = { pendingCard, uploadError, fmtDate };
