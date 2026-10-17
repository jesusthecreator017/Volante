// Transport-independent IMU device interface, implemented by the real BLE transport and the mock.

import type { DeviceStatus } from "./profile";

export type ConnState = "disconnected" | "connecting" | "connected";

export interface DeviceEvents {
  /** One notification of the imu_stream characteristic; hostMs = performance.timeOrigin + performance.now() at arrival */
  data: (dv: DataView, hostMs: number) => void;
  status: (s: DeviceStatus) => void;
  disconnect: () => void;
}

export interface ImuDevice {
  readonly kind: "ble" | "mock";
  readonly id: string;
  readonly name: string;
  /** negotiated ATT MTU when the platform exposes it */
  readonly mtu: number | null;
  /** whether the standard Battery Service was found */
  readonly hasBatteryService: boolean;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  command(cmd: number): Promise<void>;
  readStatus(): Promise<DeviceStatus | null>;
  readRssi(): Promise<number | null>;
  readBattery(): Promise<number | null>;
  on<K extends keyof DeviceEvents>(ev: K, cb: DeviceEvents[K]): void;
}

export class Emitter {
  private handlers: { [K in keyof DeviceEvents]?: DeviceEvents[K][] } = {};
  on<K extends keyof DeviceEvents>(ev: K, cb: DeviceEvents[K]) {
    (this.handlers[ev] ??= [] as never[]).push(cb as never);
  }
  emit<K extends keyof DeviceEvents>(ev: K, ...args: Parameters<DeviceEvents[K]>) {
    for (const h of this.handlers[ev] ?? []) (h as (...a: unknown[]) => void)(...args);
  }
}

export const hostNowMs = () => performance.timeOrigin + performance.now();
