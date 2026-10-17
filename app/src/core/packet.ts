// imu.bin + packet v1 decoding. Port of context/imu-reference/packet.rs.
// 16-byte header ("OSOI", u16 version=1, u16 rate_hz, u32 mount_id, u32 reserved) + 18-byte LE packets
// `u16 seq, u32 t_us, i16 ax ay az gx gy gz` in arrival order.

import { G } from "./quat";

export const HEADER_LEN = 16;
export const PACKET_LEN = 18;
export const ACC_LSB_PER_G = 2048;
export const GYRO_LSB_PER_DPS = 16.4;
const ACC_K = G / ACC_LSB_PER_G;
const GYRO_K = Math.PI / 180 / GYRO_LSB_PER_DPS;

export interface Sample {
  /** Device clock, seconds (unwrapped) */
  t: number;
  /** Specific force, sensor frame, m/s^2 (f32 precision like the Rust reference) */
  acc: [number, number, number];
  /** Angular rate, sensor frame, rad/s, bias NOT removed */
  gyro: [number, number, number];
  /** A gyro axis at the int16 limit (>= 2000 dps) */
  saturated: boolean;
  /** An accel axis at the int16 limit (>= 16 g) */
  accSaturated: boolean;
}

export interface Gap {
  tStart: number;
  tEnd: number;
  missing: number;
}

export interface Stats {
  arrivals: number;
  unique: number;
  lost: number;
  duplicate: number;
  reordered: number;
}

export interface Capture {
  rateHz: number;
  samples: Sample[];
  gaps: Gap[];
  stats: Stats;
}

/** One decoded wire packet before unwrapping. */
export interface RawPacket {
  seq16: number;
  t32: number;
  v: [number, number, number, number, number, number];
}

/**
 * Marker packet emitted by the device on control 0x06 (see docs/ble-profile.md). It reuses the 18-byte layout with a
 * signature in the six value slots that a real sensor can't produce (alternating rail values + ASCII "OSMK"):
 * ax=+32767 ay=-32768 az=+32767 gx=-32768 gy=0x534F ("OS") gz=0x4B4D ("MK"). seq = seq of the last real sample,
 * t_us = device time of the button press. Markers are routed out of the sample stream and never written to imu.bin.
 */
export const MARKER_V = [32767, -32768, 32767, -32768, 0x534f, 0x4b4d] as const;
export function isMarker(p: RawPacket): boolean {
  return p.v.every((x, i) => x === MARKER_V[i]);
}

export function decodePacket(dv: DataView, off: number): RawPacket {
  return {
    seq16: dv.getUint16(off, true),
    t32: dv.getUint32(off + 2, true),
    v: [
      dv.getInt16(off + 6, true),
      dv.getInt16(off + 8, true),
      dv.getInt16(off + 10, true),
      dv.getInt16(off + 12, true),
      dv.getInt16(off + 14, true),
      dv.getInt16(off + 16, true),
    ],
  };
}

export function encodePacket(p: RawPacket, out: DataView, off: number) {
  out.setUint16(off, p.seq16 & 0xffff, true);
  out.setUint32(off + 2, p.t32 >>> 0, true);
  for (let i = 0; i < 6; i++) out.setInt16(off + 6 + 2 * i, p.v[i], true);
}

/** Signed 16-bit difference a - b. */
const d16 = (a: number, b: number) => (((a - b) & 0xffff) << 16) >> 16;
/** Signed 32-bit difference a - b. */
const d32 = (a: number, b: number) => (a - b) | 0;

/** Unwraps seq/t_us against the previous arrival (signed 16/32-bit difference, as in packet.rs). */
export class Unwrapper {
  private prev: { s16: number; s: number; t32: number; t: number } | null = null;
  next(p: RawPacket): { seq: number; tUs: number } {
    let seq: number;
    let tUs: number;
    if (!this.prev) {
      seq = p.seq16;
      tUs = p.t32;
    } else {
      seq = this.prev.s + d16(p.seq16, this.prev.s16);
      tUs = this.prev.t + d32(p.t32 >>> 0, this.prev.t32 >>> 0);
    }
    this.prev = { s16: p.seq16, s: seq, t32: p.t32, t: tUs };
    return { seq, tUs };
  }
  /** Unwrap a marker's time against the current state without advancing it. */
  peekTime(t32: number): number | null {
    return this.prev ? this.prev.t + d32(t32 >>> 0, this.prev.t32 >>> 0) : null;
  }
}

const f32 = Math.fround;
export function toSample(tUs: number, v: readonly number[]): Sample {
  return {
    t: tUs * 1e-6,
    acc: [f32(v[0] * ACC_K), f32(v[1] * ACC_K), f32(v[2] * ACC_K)],
    gyro: [f32(v[3] * GYRO_K), f32(v[4] * GYRO_K), f32(v[5] * GYRO_K)],
    saturated: v[3] === 32767 || v[3] === -32768 || v[4] === 32767 || v[4] === -32768 || v[5] === 32767 || v[5] === -32768,
    accSaturated: v[0] === 32767 || v[0] === -32768 || v[1] === 32767 || v[1] === -32768 || v[2] === 32767 || v[2] === -32768,
  };
}

export interface Header {
  version: number;
  rateHz: number;
  mountId: number;
}

export function parseHeader(bytes: Uint8Array): Header {
  if (bytes.length < HEADER_LEN) throw new Error("imu file shorter than its header");
  if (String.fromCharCode(bytes[0], bytes[1], bytes[2], bytes[3]) !== "OSOI") throw new Error("bad imu magic");
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const version = dv.getUint16(4, true);
  if (version !== 1) throw new Error(`unsupported imu version ${version}`);
  return { version, rateHz: dv.getUint16(6, true), mountId: dv.getUint32(8, true) };
}

/** Decodes the packet body (arrival order) of an imu.bin file. */
export function rawPackets(bytes: Uint8Array): RawPacket[] {
  const body = bytes.subarray(HEADER_LEN);
  if (body.length % PACKET_LEN !== 0) {
    throw new Error(`imu body of ${body.length} bytes is not a whole number of ${PACKET_LEN}-byte packets`);
  }
  const dv = new DataView(body.buffer, body.byteOffset, body.byteLength);
  const out: RawPacket[] = [];
  for (let off = 0; off < body.length; off += PACKET_LEN) out.push(decodePacket(dv, off));
  return out;
}

export interface Marker {
  /** Unwrapped device time, s */
  t: number;
}

/**
 * Packets (arrival order) -> sorted, deduplicated samples, gaps and accounting. Same rules as packet.rs `parse`.
 * Marker packets are split out (returned separately) before accounting.
 */
export function assemble(rateHz: number, packets: RawPacket[]): Capture & { markers: Marker[] } {
  const un = new Unwrapper();
  const raws: { seq: number; tUs: number; v: number[] }[] = [];
  const markers: Marker[] = [];
  for (const p of packets) {
    if (isMarker(p)) {
      const t = un.peekTime(p.t32);
      if (t !== null) markers.push({ t: t * 1e-6 });
      continue;
    }
    const { seq, tUs } = un.next(p);
    raws.push({ seq, tUs, v: p.v });
  }
  const stats: Stats = { arrivals: raws.length, unique: 0, lost: 0, duplicate: 0, reordered: 0 };
  let top = -Infinity;
  const seen = new Set<number>();
  for (const r of raws) {
    if (seen.has(r.seq)) stats.duplicate++;
    else {
      seen.add(r.seq);
      if (r.seq < top) stats.reordered++;
    }
    top = Math.max(top, r.seq);
  }
  // Stable sort + keep first arrival of a repeated seq
  const sorted = raws.map((r, i) => ({ r, i })).sort((a, b) => a.r.seq - b.r.seq || a.i - b.i);
  const uniq: typeof raws = [];
  for (const { r } of sorted) if (!uniq.length || uniq[uniq.length - 1].seq !== r.seq) uniq.push(r);
  stats.unique = uniq.length;
  const gaps: Gap[] = [];
  for (let i = 1; i < uniq.length; i++) {
    const missing = uniq[i].seq - uniq[i - 1].seq - 1;
    if (missing > 0) {
      stats.lost += missing;
      gaps.push({ tStart: uniq[i - 1].tUs * 1e-6, tEnd: uniq[i].tUs * 1e-6, missing });
    }
  }
  return { rateHz, samples: uniq.map((r) => toSample(r.tUs, r.v)), gaps, stats, markers };
}

/** Full imu.bin parse (packet.rs `parse`). */
export function parse(bytes: Uint8Array): Capture & { markers: Marker[]; header: Header } {
  const header = parseHeader(bytes);
  return { ...assemble(header.rateHz, rawPackets(bytes)), header };
}

/** Writes an imu.bin file: header + packets in the given (arrival) order. */
export function encode(rateHz: number, packets: RawPacket[]): Uint8Array {
  const out = new Uint8Array(HEADER_LEN + packets.length * PACKET_LEN);
  out.set([0x4f, 0x53, 0x4f, 0x49]);
  const dv = new DataView(out.buffer);
  dv.setUint16(4, 1, true);
  dv.setUint16(6, rateHz, true);
  packets.forEach((p, i) => encodePacket(p, dv, HEADER_LEN + i * PACKET_LEN));
  return out;
}
