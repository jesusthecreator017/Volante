// Quaternion / vector helpers. Port of context/imu-reference/mod.rs.
// Quaternions are [x, y, z, w], unit length, rotating a local-frame vector into the parent frame:
// v_parent = q * v_local * conj(q).

export type Quat = [number, number, number, number];
export type Vec3 = [number, number, number];

export const G = 9.80665;
export const IDENTITY: Quat = [0, 0, 0, 1];
export const DEG = Math.PI / 180;

export function mul(a: Quat, b: Quat): Quat {
  const [ax, ay, az, aw] = a;
  const [bx, by, bz, bw] = b;
  return [
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
    aw * bw - ax * bx - ay * by - az * bz,
  ];
}

export function conj([x, y, z, w]: Quat): Quat {
  return [-x, -y, -z, w];
}

export function normalize(q: Quat): Quat {
  const n = Math.hypot(q[0], q[1], q[2], q[3]);
  return n < 1e-12 ? [...IDENTITY] : [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

export function rotate(q: Quat, v: Vec3): Vec3 {
  const p = mul(mul(q, [v[0], v[1], v[2], 0]), conj(q));
  return [p[0], p[1], p[2]];
}

export function fromAxisAngle(axis: Vec3, angle: number): Quat {
  const n = norm(axis);
  const s = Math.sin(angle / 2) / n;
  return [axis[0] * s, axis[1] * s, axis[2] * s, Math.cos(angle / 2)];
}

/** Shortest-arc rotation taking direction a to direction b (any lengths). */
export function fromTwoVectors(a: Vec3, b: Vec3): Quat {
  const na = norm(a);
  const nb = norm(b);
  const an: Vec3 = [a[0] / na, a[1] / na, a[2] / na];
  const bn: Vec3 = [b[0] / nb, b[1] / nb, b[2] / nb];
  const d = dot(an, bn);
  if (d < -0.999999) {
    const axis: Vec3 = Math.abs(an[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0];
    return fromAxisAngle(cross(an, axis), Math.PI);
  }
  const c = cross(an, bn);
  return normalize([c[0], c[1], c[2], 1 + d]);
}

export const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: Vec3, b: Vec3): Vec3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
export const norm = (a: Vec3) => Math.sqrt(dot(a, a));

/** Spherical interpolation along the shortest arc (nlerp when nearly parallel). */
export function slerp(a: Quat, b0: Quat, t: number): Quat {
  let b = b0;
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  if (d < 0) {
    b = [-b[0], -b[1], -b[2], -b[3]];
    d = -d;
  }
  let ka: number;
  let kb: number;
  if (d > 0.9995) {
    ka = 1 - t;
    kb = t;
  } else {
    const th = Math.acos(d);
    const s = Math.sin(th);
    ka = Math.sin((1 - t) * th) / s;
    kb = Math.sin(t * th) / s;
  }
  return normalize([a[0] * ka + b[0] * kb, a[1] * ka + b[1] * kb, a[2] * ka + b[2] * kb, a[3] * ka + b[3] * kb]);
}

/** Angle in radians between two orientations. */
export function angleBetween(a: Quat, b: Quat): number {
  const r = mul(conj(a), b);
  return 2 * Math.atan2(Math.sqrt(r[0] * r[0] + r[1] * r[1] + r[2] * r[2]), Math.abs(r[3]));
}

/**
 * Euler angles (degrees) of a sensor->court quaternion, court frame +Y up.
 * yaw = heading of the handle (+Y racquet axis) projected on the floor (about court +Y; drifts),
 * pitch = handle elevation above horizontal, roll = rotation of the face about the handle.
 */
export function eulerYPR(q: Quat): { yaw: number; pitch: number; roll: number } {
  const handle = rotate(q, [0, 1, 0]);
  const normal = rotate(q, [0, 0, 1]);
  const pitch = Math.asin(Math.max(-1, Math.min(1, handle[1])));
  const yaw = Math.atan2(handle[0], handle[2]);
  // Roll: angle of the face normal about the handle, measured from the "up-most" direction perpendicular to the handle
  const up: Vec3 = [0, 1, 0];
  let ref = cross(cross(handle, up), handle); // component of up perpendicular to handle
  if (norm(ref) < 1e-6) ref = cross(handle, [1, 0, 0]);
  const rn = norm(ref);
  ref = [ref[0] / rn, ref[1] / rn, ref[2] / rn];
  const side = cross(handle, ref);
  const roll = Math.atan2(dot(normal, side), dot(normal, ref));
  return { yaw: yaw / DEG, pitch: pitch / DEG, roll: roll / DEG };
}
