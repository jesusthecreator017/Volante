// Apple Watch sessions (apple/README.md): 200 Hz, mount_id 1, written by OsoKit's WristEncoder. Same synthetic
// session as apple/OsoKit's wristSessionAnalyzesAt200Hz, so both analyses must agree.
import { expect, test } from "vitest";
import { analyzeSession } from "../src/core/analysis";
import { encode, parse, WRIST_MOUNT, type RawPacket } from "../src/core/packet";

const DEG = Math.PI / 180;
const counts = (x: number) => Math.max(-32768, Math.min(32767, Math.round(x)));

/** 3 s at rest face up, then a 0.4 s rotation about z peaking at 900 dps, with a 10 g tap at its peak. */
function wristFile(): Uint8Array {
  const packets: RawPacket[] = [];
  for (let i = 0; i < 800; i++) {
    const u = i / 200 - 3;
    const w = u > 0 && u < 0.4 ? 900 * DEG * Math.sin((Math.PI * u) / 0.4) : 0;
    const tap = Math.abs(u - 0.2) < 0.003 ? 10 : 0;
    // Apple device motion: gravity z = -1 g face up; the pod format stores specific force (negated)
    const acc = [-(tap + 0), -0, -(0 - 1)].map((g) => counts(g * 2048));
    const gyro = [0, 0, w].map((r) => counts((r / DEG) * 16.4));
    packets.push({ seq16: i, t32: Math.round(i * 5000), v: [...acc, ...gyro] as RawPacket["v"] });
  }
  return encode(200, packets, WRIST_MOUNT);
}

test("a 200 Hz wrist session calibrates, finds the swing and its impact, and skips racquet-only metrics", () => {
  const cap = parse(wristFile());
  expect(cap.header.mountId).toBe(WRIST_MOUNT);
  const a = analyzeSession(cap);
  expect(a.calibration.bias).not.toBeNull();
  expect(a.swings.length).toBe(1);
  const s = a.swings[0];
  expect(Math.abs(s.peakDps - 900)).toBeLessThan(5);
  expect(s.impact).toBe(true);
  expect(s.stroke).toBe("unclassified");
  expect(s.handleTiltDeg).toBeNull();
  expect(s.side).toBeNull();
  expect(s.headSpeed).toBeCloseTo(s.peakDps * DEG * 0.3, 6);
});
