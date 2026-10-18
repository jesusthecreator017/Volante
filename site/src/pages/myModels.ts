// "Your models" panel on the Train & upload page: upload (client checks size + extension only), poll until the checker decides, retire.

import { api, ApiError, loginUrl, me } from "../api";
import { MAX_UPLOAD_BYTES, UPLOADS_PER_DAY, checkUploadFile } from "../arena";
import { buildBars, errorBox, fmtDate, fmtRating, load, modelLink, statusPill } from "../components";
import { h, replace } from "../dom";
import { icon } from "../icons";
import type { ModelDetail, ModelSummary } from "../types";
import { dataVolume } from "../twins";

const POLL_MS = 3000;

/** Fills `slot` (signed out: a sign-in prompt). Returns a cleanup that stops polling. */
export function modelsPanel(slot: HTMLElement): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const stop = () => void (timer && clearTimeout(timer));
  void load(slot, () => me(true), async (m) => {
    if (!m.user) {
      slot.append(
        h("p", { class: "muted" }, "Sign in with GitHub to upload a model and follow its matches. We only store your GitHub name and avatar."),
        h("a", { href: loginUrl("/arena/get-started"), class: "btn primary with-icon" }, icon("github-logo"), "Sign in with GitHub"),
      );
      return;
    }
    const status = h("div", { "aria-live": "polite" });
    const list = h("div");
    const leftText = (left: number | undefined) =>
      left === undefined ? `Up to ${UPLOADS_PER_DAY} uploads per day.` : `${left} of ${UPLOADS_PER_DAY} uploads left today.`;
    const left = m.uploads_left_today;
    const leftEl = h("p", { class: "muted small" }, leftText(left));
    const fileInput = h("input", { type: "file", id: "model-file", accept: ".safetensors", required: true, class: "sr-only" });
    const chosen = h("strong", null, "Drop a .safetensors file, or choose one");
    const zone = h("label", { class: "drop", for: "model-file" }, fileInput, h("span", { class: "drop-ico" }, icon("upload-simple")), chosen, h("span", { class: "muted small" }, "Up to 1 MiB. Name, description and build come from inside the file."));
    const pick = () => (chosen.textContent = fileInput.files?.[0]?.name ?? "Drop a .safetensors file, or choose one");
    fileInput.addEventListener("change", pick);
    zone.addEventListener("dragover", (e) => (e.preventDefault(), zone.classList.add("over")));
    zone.addEventListener("dragleave", () => zone.classList.remove("over"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("over");
      if (e.dataTransfer?.files?.length) (fileInput.files = e.dataTransfer.files), pick();
    });
    const submit = h("button", { type: "submit", class: "btn primary", disabled: left === 0 }, icon("upload-simple"), "Upload");
    const form = h("form", { class: "upload", novalidate: true }, zone, h("div", { class: "row" }, submit, leftEl));
    slot.append(form, status, h("h3", { class: "list-h" }, "Your models"), list);

    let mine: ModelSummary[] = [];
    const refreshList = () =>
      load(list, () => api.myModels(), (ms) => {
        mine = ms;
        list.append(
          ms.length
            ? h("ul", { class: "my-models" }, ms.map((x) => modelCard(x, () => void refreshList())))
            : h("p", { class: "muted small" }, "No models yet. Follow the steps to train your first one."),
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
        pick();
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
  if (err instanceof ApiError && err.status === 401) return h("p", { class: "error" }, "Your session ended. ", h("a", { href: loginUrl("/arena/get-started") }, "Sign in again"), ".");
  return errorBox(err);
}

function pendingCard(d: ModelDetail): HTMLElement {
  return h(
    "div",
    { class: `card result-${d.status}` },
    h("p", null, statusPill(d.status), " ", h("strong", null, d.name)),
    d.status === "pending" ? h("p", { class: "loading" }, "Checking the model… this takes a few seconds.") : null,
    d.twin ? h("p", null, `This file carries a style profile: ${dataVolume(d.twin.profile)}. It plays the open ladder until it passes the twin check after 10 matches.`) : null,
    d.status === "active" ? h("p", null, "Accepted. It now plays its 10 placement matches. ", modelLink(d)) : null,
    d.status === "rejected"
      ? [h("p", null, "The checker rejected it:"), h("ul", { class: "reasons" }, (d.reject_reasons ?? ["no reason given"]).map((r) => h("li", null, r))), h("p", { class: "muted small" }, "Fix it with the kit and upload again.")]
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
    { class: "my-model" },
    h("div", { class: "row" }, modelLink(m), statusPill(m.status)),
    buildBars(m.build),
    h("p", { class: "muted small" }, `Rating ${fmtRating(m.rating)} · ${m.games} games`),
    m.status === "active" ? retire : null,
  );
}

export const _test = { pendingCard, uploadError, fmtDate };
