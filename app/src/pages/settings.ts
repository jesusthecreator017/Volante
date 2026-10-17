// Settings page.

import { app } from "../app";
import { DEFAULT_SETTINGS, type Settings } from "../settings";

type Field = [keyof Settings, string, "number" | "select" | "checkbox" | "text", (string | number)[]?, string?];

const FIELDS: [string, Field[]][] = [
  [
    "Player & racquet",
    [
      ["handedness", "Handedness", "select", ["right", "left"]],
      ["lengthM", "Racquet length (m)", "number", [0.01]],
      ["headCenterM", "Head-center distance from the butt (m)", "number", [0.01], "used for head speed = |ω| × distance"],
      ["speedUnit", "Speed unit", "select", ["m/s", "km/h", "mph"]],
    ],
  ],
  [
    "Swing detection",
    [
      ["startDps", "Start when |ω| above (dps)", "number", [1]],
      ["endDps", "End when |ω| below (dps)", "number", [1]],
      ["endHoldMs", "… for at least (ms)", "number", [1]],
      ["minDurationMs", "Discard swings shorter than (ms)", "number", [1]],
      ["rallyGapS", "Rally: hits closer than (s)", "number", [0.5]],
    ],
  ],
  [
    "Orientation",
    [["beta", "Madgwick beta", "number", [0.005], "default 0.04; higher trusts the accelerometer more"]],
  ],
  [
    "Mock device",
    [
      ["mockEnabled", "Mock device available", "checkbox"],
      ["mockLossPct", "Packet-loss injection (%)", "number", [0.5], "applies live; some losses come in bursts of 5–20"],
      ["mockJitterMs", "Notification latency jitter (ms)", "number", [1]],
    ],
  ],
  [
    "Pi server (optional)",
    [["serverUrl", "Server URL", "text", [], "e.g. http://oso.local:8080 — leave empty when the page is served by the Pi"]],
  ],
];

export function renderSettings(root: HTMLElement) {
  const s = app.settings;
  root.innerHTML = `<h1>Settings</h1>${FIELDS.map(
    ([title, fs]) =>
      `<h2>${title}</h2>${fs
        .map(([k, label, type, opt, note]) => {
          const val = s[k];
          const input =
            type === "select"
              ? `<select data-k="${k}">${opt!.map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`
              : type === "checkbox"
                ? `<input type="checkbox" data-k="${k}" ${val ? "checked" : ""}>`
                : type === "text"
                  ? `<input type="text" data-k="${k}" value="${String(val)}" size="28">`
                  : `<input type="number" data-k="${k}" value="${val}" step="${opt![0]}" min="0">`;
          return `<label class="field"><span>${label}</span> ${input} <small class="note">${note ?? ""}</small></label>`;
        })
        .join("")}`,
  ).join("")}
  <p><button id="reset">Reset to defaults</button> <span id="ok" class="note"></span></p>
  <p class="note">Changing analysis settings re-analyses saved sessions when you open them. Live sessions use the settings at their start.</p>`;
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-k]").forEach((el) =>
    el.addEventListener("change", () => {
      const k = el.dataset.k as keyof Settings;
      const val =
        el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : el instanceof HTMLInputElement && el.type === "number" ? Number(el.value) : el.value;
      if (typeof val === "number" && !Number.isFinite(val)) return;
      app.updateSettings({ [k]: val } as Partial<Settings>);
      root.querySelector("#ok")!.textContent = "saved";
    }),
  );
  root.querySelector("#reset")!.addEventListener("click", () => {
    app.updateSettings({ ...DEFAULT_SETTINGS });
    renderSettings(root);
  });
}
