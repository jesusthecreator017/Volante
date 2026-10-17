// Formatting helpers. Every value shows units; missing values show "—" with the reason.

export const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

/** "—" with a reason, as HTML */
export const na = (reason: string) => `<span class="na">— <small>(${esc(reason)})</small></span>`;

/** number with unit, or "—" + reason when null/NaN */
export function v(x: number | null | undefined, unit: string, digits = 1, reason = "not available"): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return na(reason);
  return `${x.toFixed(digits)}${unit ? " " + esc(unit) : ""}`;
}

export const est = (note?: string) => `<span class="est" title="${esc(note ?? "heuristic estimate")}">estimate</span>`;
export const lb = (note = "lower bound: gyro saturated") => `<span class="warn" title="${esc(note)}">lower bound</span>`;

export function dur(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return na("not available");
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${(s - m * 60).toFixed(0)} s`;
}

export const date = (ms: number) => new Date(ms).toLocaleString();
export const pct = (x: number | null | undefined, d = 1) => v(x === null || x === undefined ? null : x * 100, "%", d);

/** key/value table rows: [label, valueHtml, note?] */
export function kv(rows: [string, string, string?][]): string {
  return `<table class="kv"><tbody>${rows
    .map(([k, val, note]) => `<tr><th>${esc(k)}</th><td>${val}${note ? ` <small class="note">${esc(note)}</small>` : ""}</td></tr>`)
    .join("")}</tbody></table>`;
}

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return na("not reported");
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

export function downloadBlob(name: string, data: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
