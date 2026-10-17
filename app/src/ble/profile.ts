// BLE GATT profile: the firmware's contract (../docs/ble-profile.md, constants mirrored from ../proto/src/lib.rs).

export const OSO_SERVICE = "6f736f00-0001-4c45-8a00-6261646d696e";
export const IMU_STREAM_CHAR = "6f736f00-0002-4c45-8a00-6261646d696e";
export const CONTROL_CHAR = "6f736f00-0003-4c45-8a00-6261646d696e";
export const STATUS_CHAR = "6f736f00-0004-4c45-8a00-6261646d696e";
export const BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb";
export const BATTERY_LEVEL_CHAR = "00002a19-0000-1000-8000-00805f9b34fb";
export const REQUESTED_MTU = 247;

/** `control` opcodes (proto `op`) */
export const CMD = {
  START_STREAM: 0x01,
  STOP_STREAM: 0x02,
  START_RECORDING: 0x03,
  STOP_RECORDING: 0x04,
  UPLOAD: 0x05,
  SYNC_MARK: 0x06,
  /** host confirms a complete upload; only then does the next power-on record over the session */
  MARK_UPLOADED: 0x07,
  /** stop recording, park the IMU, deep sleep with a 2 s advert every 30 s */
  SLEEP: 0x08,
  /** only honored while sleeping: reset into the normal boot */
  WAKE: 0x09,
} as const;

/** `status.state_bits` (proto `state`) */
export const STATE = {
  STREAMING: 1,
  RECORDING: 2,
  UPLOADING: 4,
  PENDING_UPLOAD: 8,
  FLASH_FULL: 16,
  IMU_OK: 32,
  IMU_IS_6500: 64,
  /** pre-erasing flash, recording has not started yet */
  ERASING: 128,
} as const;

/** `status.state_ext` (proto `state_ext`) */
export const STATE_EXT = { SLEEPING: 1 } as const;

/** `status.event` (proto `event`) */
export const EVENT = { NONE: 0, MARKER_TAP: 1, MARKER_CMD: 2, UPLOAD_DONE: 3, ERROR: 4 } as const;

/** error codes in `event_value` of an ERROR event (proto `err`) */
export const ERR = { BAD_OPCODE: 1, NO_SESSION: 2, NEED_CONFIRM: 3, BUSY: 4, SLEEPING: 5 } as const;

export const ERR_TEXT: Record<number, string> = {
  [ERR.BAD_OPCODE]: "unknown command",
  [ERR.NO_SESSION]: "no stored session (or nothing to mark uploaded)",
  [ERR.NEED_CONFIRM]: "the stored session was not uploaded yet: repeat within 5 s to overwrite it",
  [ERR.BUSY]: "busy (erasing flash or uploading)",
  [ERR.SLEEPING]: "the pod is sleeping: wake it first",
};

export const errorText = (code: number) => ERR_TEXT[code] ?? `error ${code}`;

export const STATUS_LEN = 24;

/** `status` characteristic: 24 bytes, little-endian (docs/ble-profile.md). Flags are decoded from the bit fields. */
export interface DeviceStatus {
  fwMajor: number;
  fwMinor: number;
  rateHz: number;
  /** null when the firmware reports 255 (no battery divider) */
  batteryPct: number | null;
  stateBits: number;
  accelG: number;
  gyroDps: number;
  /** packets in the stored session (durably in flash) */
  bufferedPackets: number;
  fifoOverflows: number;
  event: number;
  eventValue: number;
  stateExt: number;
  streaming: boolean;
  recording: boolean;
  uploading: boolean;
  pendingUpload: boolean;
  flashFull: boolean;
  imuOk: boolean;
  imuIs6500: boolean;
  erasing: boolean;
  sleeping: boolean;
}

export function parseStatus(dv: DataView): DeviceStatus | null {
  if (dv.byteLength < STATUS_LEN) return null;
  const b = dv.getUint8(5);
  const ext = dv.getUint8(20);
  const bat = dv.getUint8(4);
  return {
    fwMajor: dv.getUint8(0),
    fwMinor: dv.getUint8(1),
    rateHz: dv.getUint16(2, true),
    batteryPct: bat === 255 ? null : bat,
    stateBits: b,
    accelG: dv.getUint8(6),
    gyroDps: dv.getUint16(7, true),
    bufferedPackets: dv.getUint32(9, true),
    fifoOverflows: dv.getUint16(13, true),
    event: dv.getUint8(15),
    eventValue: dv.getUint32(16, true),
    stateExt: ext,
    streaming: !!(b & STATE.STREAMING),
    recording: !!(b & STATE.RECORDING),
    uploading: !!(b & STATE.UPLOADING),
    pendingUpload: !!(b & STATE.PENDING_UPLOAD),
    flashFull: !!(b & STATE.FLASH_FULL),
    imuOk: !!(b & STATE.IMU_OK),
    imuIs6500: !!(b & STATE.IMU_IS_6500),
    erasing: !!(b & STATE.ERASING),
    sleeping: !!(ext & STATE_EXT.SLEEPING),
  };
}

export interface StatusFields {
  fwMajor: number;
  fwMinor: number;
  rateHz: number;
  batteryPct: number | null;
  stateBits: number;
  accelG: number;
  gyroDps: number;
  bufferedPackets: number;
  fifoOverflows: number;
  event: number;
  eventValue: number;
  stateExt: number;
}

/** Inverse of parseStatus (used by the mock device and the tests; mirrors proto `Status::encode`). */
export function encodeStatus(s: StatusFields): DataView {
  const dv = new DataView(new ArrayBuffer(STATUS_LEN));
  dv.setUint8(0, s.fwMajor);
  dv.setUint8(1, s.fwMinor);
  dv.setUint16(2, s.rateHz, true);
  dv.setUint8(4, s.batteryPct ?? 255);
  dv.setUint8(5, s.stateBits);
  dv.setUint8(6, s.accelG);
  dv.setUint16(7, s.gyroDps, true);
  dv.setUint32(9, s.bufferedPackets, true);
  dv.setUint16(13, s.fifoOverflows, true);
  dv.setUint8(15, s.event);
  dv.setUint32(16, s.eventValue >>> 0, true);
  dv.setUint8(20, s.stateExt);
  return dv;
}

/** Packets per notification for an ATT MTU (the firmware sends at most 13). */
export const packetsPerNotification = (mtu: number) => Math.min(13, Math.floor((mtu - 3) / 18));
