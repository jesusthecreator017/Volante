import { describe, expect, test } from "vitest";
import { ACC_LSB_PER_G, encode, parse, MARKER_V, type RawPacket } from "../src/core/packet";
import { G } from "../src/core/quat";
import { sampleBytes } from "./helpers";

const pk = (seq: number, t32: number): RawPacket => ({ seq16: seq, t32: t32 >>> 0, v: [seq << 16 >> 16, 0, 2048, 0, 0, 0] });

describe("packet (port of packet.rs tests)", () => {
  test("decodes scales and saturation", () => {
    const c = parse(encode(500, [{ seq16: 7, t32: 2000, v: [2048, -2048, 0, 164, 32767, 0] }]));
    const s = c.samples[0];
    expect(c.rateHz).toBe(500);
    expect(Math.abs(s.acc[0] - G)).toBeLessThan(1e-5);
    expect(Math.abs(s.acc[1] + G)).toBeLessThan(1e-5);
    expect(Math.abs(s.gyro[0] - (10 * Math.PI) / 180)).toBeLessThan(1e-6);
    expect(s.saturated).toBe(true);
    expect(s.t).toBeCloseTo(0.002, 12);
    expect(parse(encode(500, [pk(0, 0)])).samples[0].saturated).toBe(false);
  });

  test("unwraps seq and time, sorts, dedupes and counts", () => {
    const t0 = 0xffffffff - 3000;
    const arrivals = [[65534, 0], [65535, 1], [0, 2], [2, 4], [1, 3], [2, 4], [5, 7]];
    const c = parse(encode(500, arrivals.map(([s, k]) => pk(s, (t0 + k * 2000) >>> 0))));
    expect(c.stats).toEqual({ arrivals: 7, unique: 6, lost: 2, duplicate: 1, reordered: 1 });
    const t = c.samples.map((s) => s.t);
    for (let i = 1; i < t.length; i++) expect(t[i]).toBeGreaterThan(t[i - 1]);
    expect(t[5]).toBeGreaterThan(4294.9);
    expect(Math.abs(t[3] - t[2] - 0.002)).toBeLessThan(1e-9);
    expect(c.gaps).toEqual([{ tStart: t[4], tEnd: t[5], missing: 2 }]);
    expect(c.samples[2].acc[0]).toBe(Math.fround((0 * G) / ACC_LSB_PER_G));
  });

  test("rejects malformed input", () => {
    expect(() => parse(new Uint8Array())).toThrow();
    expect(() => parse(new TextEncoder().encode("OSOI"))).toThrow();
    const ok = encode(500, [pk(0, 0)]);
    expect(() => parse(ok)).not.toThrow();
    expect(() => parse(ok.subarray(0, ok.length - 1))).toThrow();
    const bad = ok.slice(); bad[0] = 88;
    expect(() => parse(bad)).toThrow(/magic/);
    const v2 = ok.slice(); v2[4] = 2;
    expect(() => parse(v2)).toThrow(/version/);
    const extra = new Uint8Array(ok.length + 1); extra.set(ok);
    expect(() => parse(extra)).toThrow();
  });

  test("empty capture is fine", () => {
    const c = parse(encode(500, []));
    expect(c.samples).toEqual([]);
    expect(c.stats).toEqual({ arrivals: 0, unique: 0, lost: 0, duplicate: 0, reordered: 0 });
  });

  test("marker packets are split out and not counted", () => {
    const c = parse(encode(500, [pk(10, 1000), pk(11, 3000), { seq16: 11, t32: 3500, v: [...MARKER_V] }, pk(12, 5000)]));
    expect(c.stats).toEqual({ arrivals: 3, unique: 3, lost: 0, duplicate: 0, reordered: 0 });
    expect(c.markers.length).toBe(1);
    expect(c.markers[0].t).toBeCloseTo(0.0035, 9);
  });

  test("sample-data/imu.bin accounting (handoff acceptance numbers)", () => {
    const c = parse(sampleBytes());
    expect(c.stats).toEqual({ arrivals: 6547, unique: 6534, lost: 66, duplicate: 13, reordered: 99 });
    expect(c.gaps.length).toBe(44);
    expect(c.samples.filter((s) => s.saturated).length).toBe(12);
    expect(c.rateHz).toBe(500);
  });

  test("encode round-trips the sample file byte for byte", () => {
    const bytes = sampleBytes();
    const dv = new DataView(bytes.buffer, bytes.byteOffset);
    const packets: RawPacket[] = [];
    for (let o = 16; o < bytes.length; o += 18)
      packets.push({ seq16: dv.getUint16(o, true), t32: dv.getUint32(o + 2, true), v: [0, 1, 2, 3, 4, 5].map((k) => dv.getInt16(o + 6 + 2 * k, true)) as RawPacket["v"] });
    expect(encode(500, packets)).toEqual(bytes);
  });
});
