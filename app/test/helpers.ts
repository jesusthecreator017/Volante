import { readFileSync } from "node:fs";
import { G } from "../src/core/quat";
import type { Sample } from "../src/core/packet";

export const sampleBytes = () => new Uint8Array(readFileSync(new URL("../public/sample/imu.bin", import.meta.url)));

export function sample(t: number, acc: number[], gyro: number[], saturated = false): Sample {
  return {
    t,
    acc: acc.map(Math.fround) as [number, number, number],
    gyro: gyro.map(Math.fround) as [number, number, number],
    saturated,
    accSaturated: false,
  };
}
export const level = (n: number, gyro: number[] = [0, 0, 0]) =>
  Array.from({ length: n }, (_, i) => sample(i * 0.002, [0, 0, G], gyro));
