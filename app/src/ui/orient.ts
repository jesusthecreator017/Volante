// Simple live orientation widget: a racquet-shaped wireframe (handle + head) and the court axes, drawn on a 2D canvas
// with a fixed oblique camera. Deliberately NOT the replay renderer (see EMBEDDING.md limits).

import { rotate, type Quat, type Vec3 } from "../core/quat";

// Racquet frame: +Y handle (butt -> tip), +Z string-bed normal, +X across the head. Units: m.
const HEAD_C = 0.53;
function racquetLines(): [Vec3, Vec3][] {
  const lines: [Vec3, Vec3][] = [[[0, 0, 0], [0, HEAD_C - 0.145, 0]]];
  const n = 16;
  for (let k = 0; k < n; k++) {
    const a = (k / n) * 2 * Math.PI;
    const b = ((k + 1) / n) * 2 * Math.PI;
    lines.push([
      [0.115 * Math.sin(a), HEAD_C + 0.145 * -Math.cos(a), 0],
      [0.115 * Math.sin(b), HEAD_C + 0.145 * -Math.cos(b), 0],
    ]);
  }
  return lines;
}
const RACQUET = racquetLines();

export function drawOrientation(canvas: HTMLCanvasElement, q: Quat | null, label = "") {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 220;
  const h = canvas.clientHeight || 220;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const fg = getComputedStyle(canvas).color || "#222";
  // Camera: court frame (+Y up). View from +X+Z side, slightly above.
  const yaw = Math.PI / 5;
  const pitch = 0.35;
  const s = Math.min(w, h) * 0.75;
  const P = (p: Vec3): [number, number] => {
    const x1 = p[0] * Math.cos(yaw) - p[2] * Math.sin(yaw);
    const z1 = p[0] * Math.sin(yaw) + p[2] * Math.cos(yaw);
    const y2 = p[1] * Math.cos(pitch) - z1 * Math.sin(pitch);
    return [w / 2 + x1 * s, h * 0.72 - y2 * s];
  };
  const seg = (a: Vec3, b: Vec3, color: string, width = 1) => {
    const [ax, ay] = P(a);
    const [bx, by] = P(b);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
  };
  // Floor grid + court axes
  for (let k = -2; k <= 2; k++) {
    seg([k * 0.1, 0, -0.2], [k * 0.1, 0, 0.2], "rgba(128,128,128,0.3)");
    seg([-0.2, 0, k * 0.1], [0.2, 0, k * 0.1], "rgba(128,128,128,0.3)");
  }
  seg([0, 0, 0], [0, 0.25, 0], "rgba(128,128,128,0.8)", 1);
  ctx.fillStyle = fg;
  ctx.font = "11px system-ui";
  const [ux, uy] = P([0, 0.26, 0]);
  ctx.fillText("up", ux + 3, uy);
  if (!q) {
    ctx.fillText("no orientation yet (hold still to calibrate)", 6, 14);
    return;
  }
  for (const [a, b] of RACQUET) seg(rotate(q, a), rotate(q, b), fg, 2);
  // Racquet axes: X red, Y (handle) green, Z (face normal) blue
  seg([0, 0, 0], rotate(q, [0.12, 0, 0]), "#d62728", 2);
  seg([0, 0, 0], rotate(q, [0, 0.12, 0]), "#2ca02c", 2);
  seg(rotate(q, [0, HEAD_C, 0]), rotate(q, [0, HEAD_C, 0.12]), "#1f77b4", 2);
  if (label) ctx.fillText(label, 6, 14);
}
