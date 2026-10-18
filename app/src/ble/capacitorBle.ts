// Real BLE transport via @capacitor-community/bluetooth-le: Web Bluetooth in Chrome/Edge (desktop + Android), native
// CoreBluetooth / Android BLE inside the Capacitor apps.

import { BleClient, numbersToDataView } from "@capacitor-community/bluetooth-le";
import { Capacitor } from "@capacitor/core";
import { Emitter, hostNowMs, type ImuDevice } from "./device";
import {
  BATTERY_LEVEL_CHAR,
  BATTERY_SERVICE,
  CONTROL_CHAR,
  IMU_STREAM_CHAR,
  OSO_SERVICE,
  STATUS_CHAR,
  parseStatus,
  type DeviceStatus,
} from "./profile";

/** Web Bluetooth availability (native apps always have BLE through the plugin). */
export function bleSupport(): { ok: boolean; reason: string | null } {
  if (Capacitor.isNativePlatform()) return { ok: true, reason: null };
  if (!("bluetooth" in navigator)) {
    return {
      ok: false,
      reason:
        "This browser has no Web Bluetooth (Safari/iOS and Firefox don't). Use Chrome/Edge on desktop or Android, the iOS/Android app, or the mock device / imu.bin import.",
    };
  }
  if (!window.isSecureContext) return { ok: false, reason: "Web Bluetooth needs HTTPS (or localhost)." };
  return { ok: true, reason: null };
}

let initialized = false;
async function init() {
  if (initialized) return;
  await BleClient.initialize({ androidNeverForLocation: true });
  initialized = true;
}

export class CapacitorBleDevice extends Emitter implements ImuDevice {
  readonly kind = "ble" as const;
  mtu: number | null = null;
  hasBatteryService = false;
  private constructor(
    readonly id: string,
    readonly name: string,
  ) {
    super();
  }

  /** Shows the system device picker. Must be called from a user gesture on the web. */
  static async request(): Promise<CapacitorBleDevice> {
    await init();
    const d = await BleClient.requestDevice({ services: [OSO_SERVICE], optionalServices: [BATTERY_SERVICE] });
    return new CapacitorBleDevice(d.deviceId, d.name ?? "Volante pod");
  }

  async connect() {
    await BleClient.connect(this.id, () => this.emit("disconnect"));
    // Android negotiates the MTU on connect (plugin requests the max); iOS picks it. Web Bluetooth doesn't expose it.
    try {
      this.mtu = await BleClient.getMtu(this.id);
    } catch {
      this.mtu = null;
    }
    try {
      const services = await BleClient.getServices(this.id);
      this.hasBatteryService = services.some((s) => s.uuid.toLowerCase() === BATTERY_SERVICE);
    } catch {
      this.hasBatteryService = false;
    }
    await BleClient.startNotifications(this.id, OSO_SERVICE, IMU_STREAM_CHAR, (dv) => this.emit("data", dv, hostNowMs()));
    try {
      await BleClient.startNotifications(this.id, OSO_SERVICE, STATUS_CHAR, (dv) => {
        const s = parseStatus(dv);
        if (s) this.emit("status", s);
      });
    } catch {
      // status notify optional; read still works
    }
  }

  async disconnect() {
    await BleClient.disconnect(this.id);
  }

  async command(cmd: number) {
    await BleClient.write(this.id, OSO_SERVICE, CONTROL_CHAR, numbersToDataView([cmd]));
  }

  async readStatus(): Promise<DeviceStatus | null> {
    return parseStatus(await BleClient.read(this.id, OSO_SERVICE, STATUS_CHAR));
  }

  async readRssi(): Promise<number | null> {
    if (!Capacitor.isNativePlatform()) return null; // Web Bluetooth has no RSSI on a connected device
    try {
      return await BleClient.readRssi(this.id);
    } catch {
      return null;
    }
  }

  async readBattery(): Promise<number | null> {
    if (!this.hasBatteryService) return null;
    try {
      return (await BleClient.read(this.id, BATTERY_SERVICE, BATTERY_LEVEL_CHAR)).getUint8(0);
    } catch {
      return null;
    }
  }
}
