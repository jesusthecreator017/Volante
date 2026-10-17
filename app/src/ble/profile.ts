// BLE GATT profile (contract with the firmware). See docs/ble-profile.md.

export const OSO_SERVICE = "6f736f00-0001-4c45-8a00-6261646d696e";
export const IMU_STREAM_CHAR = "6f736f00-0002-4c45-8a00-6261646d696e";
export const CONTROL_CHAR = "6f736f00-0003-4c45-8a00-6261646d696e";
export const STATUS_CHAR = "6f736f00-0004-4c45-8a00-6261646d696e";
export const BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb";
export const BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb";
export const REQUESTED_MTU = 247;

export const CMD = {
  START_STREAM: 0x01,
  STOP_STREAM: 0x02,
  START_RECORDING: 0x03,
  STOP_RECORDING: 0x04,
  UPLOAD: 0x05,
  SYNC_MARK: 0x06,
} as const;

/** status characteristic payload: UTF-8 JSON (docs/ble-profile.md). All fields optional for forward compatibility. */
export interface DeviceStatus {
  v?: number;
  fw?: string;
  rate_hz?: number;
  battery_pct?: number;
  streaming?: boolean;
  recording?: boolean;
  buffered_bytes?: number;
  upload_total_bytes?: number;
  upload_sent_bytes?: number;
  acc_range_g?: number;
  gyro_range_dps?: number;
}

export function parseStatus(dv: DataView): DeviceStatus | null {
  try {
    const text = new TextDecoder().decode(new Uint8Array(dv.buffer, dv.byteOffset, dv.byteLength));
    return JSON.parse(text) as DeviceStatus;
  } catch {
    return null;
  }
}

/** Packets per notification for an ATT MTU. */
export const packetsPerNotification = (mtu: number) => Math.floor((mtu - 3) / 18);
