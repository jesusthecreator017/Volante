// Minimal canvas charts (line / bar). No dependencies; redraw is cheap and throttled by the caller.

export interface Series {
  name: string;
  color: string;
  x: ArrayLike<number>;
  y: ArrayLike<number>;
}

export interface ChartOpts {
  xLabel?: string;
  yLabel?: string;
  yMin?: number;
  yMax?: number;
  xMin?: number;
  xMax?: number;
  markers?: { x: number; label: string; color?: string }[];
  points?: boolean;
}

export const COLORS = { x: "#d62728", y: "#2ca02c", z: "#1f77b4", mag: "#555", a: "#9467bd", b: "#ff7f0e" };

function setup(canvas: HTMLCanvasElement) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 160;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const style = getComputedStyle(canvas);
  return { ctx, w, h, fg: style.color || "#222" };
}

const nice = (x: number) => {
  const p = Math.pow(10, Math.floor(Math.log10(Math.abs(x) || 1)));
  return Math.round(x / p) * p;
};

export function lineChart(canvas: HTMLCanvasElement, series: Series[], o: ChartOpts = {}) {
  const { ctx, w, h, fg } = setup(canvas);
  const L = 44;
  const R = 8;
  const T = 18;
  const B = 22;
  let x0 = o.xMin ?? Infinity;
  let x1 = o.xMax ?? -Infinity;
  let y0 = o.yMin ?? Infinity;
  let y1 = o.yMax ?? -Infinity;
  for (const s of series)
    for (let i = 0; i < s.x.length; i++) {
      if (o.xMin === undefined) x0 = Math.min(x0, s.x[i]);
      if (o.xMax === undefined) x1 = Math.max(x1, s.x[i]);
      if (o.yMin === undefined) y0 = Math.min(y0, s.y[i]);
      if (o.yMax === undefined) y1 = Math.max(y1, s.y[i]);
    }
  if (!Number.isFinite(x0) || !Number.isFinite(y0)) {
    ctx.fillStyle = fg;
    ctx.font = "12px system-ui";
    ctx.fillText("no data", L, h / 2);
    return;
  }
  if (x1 === x0) x1 = x0 + 1;
  if (y1 === y0) {
    y0 -= 1;
    y1 += 1;
  }
  const pad = (y1 - y0) * 0.05;
  if (o.yMin === undefined) y0 -= pad;
  if (o.yMax === undefined) y1 += pad;
  const X = (x: number) => L + ((x - x0) / (x1 - x0)) * (w - L - R);
  const Y = (y: number) => T + (1 - (y - y0) / (y1 - y0)) * (h - T - B);
  ctx.strokeStyle = "rgba(128,128,128,0.35)";
  ctx.fillStyle = fg;
  ctx.font = "10px system-ui";
  ctx.lineWidth = 1;
  for (let k = 0; k <= 4; k++) {
    const yv = y0 + ((y1 - y0) * k) / 4;
    ctx.beginPath();
    ctx.moveTo(L, Y(yv));
    ctx.lineTo(w - R, Y(yv));
    ctx.stroke();
    ctx.fillText(Math.abs(yv) >= 100 ? yv.toFixed(0) : yv.toFixed(1), 2, Y(yv) + 3);
  }
  ctx.fillText(x0.toFixed(1), L, h - 6);
  ctx.fillText(x1.toFixed(1) + (o.xLabel ? " " + o.xLabel : ""), w - R - 60, h - 6);
  if (o.yLabel) ctx.fillText(o.yLabel, L, 11);
  for (const m of o.markers ?? []) {
    if (m.x < x0 || m.x > x1) continue;
    ctx.strokeStyle = m.color ?? "#e377c2";
    ctx.beginPath();
    ctx.moveTo(X(m.x), T);
    ctx.lineTo(X(m.x), h - B);
    ctx.stroke();
    ctx.fillStyle = m.color ?? "#e377c2";
    ctx.fillText(m.label, X(m.x) + 2, T + 9);
  }
  // Decimate to ~2 points per pixel column (min/max)
  for (const s of series) {
    ctx.strokeStyle = s.color;
    ctx.fillStyle = s.color;
    ctx.lineWidth = 1.25;
    const n = s.x.length;
    const step = Math.max(1, Math.floor(n / ((w - L - R) * 2)));
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i += step) {
      let lo = s.y[i];
      let hi = s.y[i];
      for (let k = i; k < Math.min(n, i + step); k++) {
        lo = Math.min(lo, s.y[k]);
        hi = Math.max(hi, s.y[k]);
      }
      const px = X(s.x[i]);
      if (!started) {
        ctx.moveTo(px, Y(lo));
        started = true;
      } else ctx.lineTo(px, Y(lo));
      if (hi !== lo) ctx.lineTo(px, Y(hi));
      if (o.points) ctx.fillRect(px - 2, Y(s.y[i]) - 2, 4, 4);
    }
    ctx.stroke();
  }
  // Legend
  let lx = L + (o.yLabel ? ctx.measureText(o.yLabel).width + 14 : 4);
  ctx.font = "10px system-ui";
  for (const s of series) {
    if (!s.name) continue;
    ctx.fillStyle = s.color;
    ctx.fillRect(lx, 4, 8, 8);
    ctx.fillStyle = fg;
    ctx.fillText(s.name, lx + 10, 12);
    lx += ctx.measureText(s.name).width + 22;
  }
}

export function barChart(canvas: HTMLCanvasElement, labels: string[], values: number[], o: { yLabel?: string; color?: string } = {}) {
  const { ctx, w, h, fg } = setup(canvas);
  const L = 34;
  const B = 22;
  const T = 16;
  const max = Math.max(1, ...values);
  const bw = (w - L - 6) / Math.max(1, values.length);
  ctx.font = "10px system-ui";
  ctx.fillStyle = fg;
  ctx.fillText(String(nice(max)), 2, T + 4);
  ctx.fillText("0", 2, h - B);
  if (o.yLabel) ctx.fillText(o.yLabel, L, 11);
  values.forEach((val, i) => {
    const bh = (val / max) * (h - T - B);
    ctx.fillStyle = o.color ?? "#1f77b4";
    ctx.fillRect(L + i * bw + 1, h - B - bh, Math.max(1, bw - 2), bh);
    ctx.fillStyle = fg;
    if (values.length <= 24 || i % Math.ceil(values.length / 12) === 0) ctx.fillText(labels[i], L + i * bw + 1, h - 8);
  });
}
