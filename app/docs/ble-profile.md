# oso BLE GATT profile (v1, as implemented)

The contract between the racquet sensor firmware (XIAO ESP32-S3 + MPU-9250) and the apps. The firmware does not
speak BLE yet; it must be written to match this document. Implemented by `src/ble/capacitorBle.ts` (real device)
and `src/ble/mock.ts` (mock device). UUIDs and constants live in `src/ble/profile.ts`.

UUIDs are unchanged from the handoff proposal (`6f736f00-…-6261646d696e` = "oso" + "badmin" in ASCII).

## Advertising

- Advertise the **OSO IMU** service UUID so the system picker can filter on it (Web Bluetooth `requestDevice`
  filters by service; the native picker does the same).
- Suggested local name: `oso-XXXX` (last 2 bytes of the MAC). The app does not depend on the name.

## Service `OSO IMU` — `6f736f00-0001-4c45-8a00-6261646d696e`

| Characteristic | UUID | Properties | Payload |
|---|---|---|---|
| `imu_stream` | `6f736f00-0002-4c45-8a00-6261646d696e` | notify | N concatenated 18-byte packets |
| `control` | `6f736f00-0003-4c45-8a00-6261646d696e` | write (with response) | 1 byte command |
| `status` | `6f736f00-0004-4c45-8a00-6261646d696e` | read, notify | UTF-8 JSON (below) |

Plus the standard **Battery Service** `0x180F` / Battery Level `0x2A19` (u8 %) if the firmware provides it. The app
reads it when present and otherwise falls back to `battery_pct` in `status`.

### MTU and notification size

- Request ATT MTU **247**. Each notification carries `N = floor((MTU − 3) / 18)` whole packets: 13 at MTU 247
  (234 bytes), about 39 notifications/s at 500 Hz, about 9 KB/s.
- A notification must contain a whole number of packets; the app counts and drops any that don't
  ("bad notifications" on the Debug page).
- Android negotiates the MTU on connect (the plugin requests it) and iOS chooses it itself; both are read back and
  shown on the Device page. **Web Bluetooth does not expose the MTU**, so the page shows "—" with that reason. The
  firmware must therefore pick N from its own negotiated MTU.

### Packet v1 (little-endian, 18 bytes) — same as `imu.bin` (docs/data-format.md)

`u16 seq | u32 t_us | i16 ax | i16 ay | i16 az | i16 gx | i16 gy | i16 gz`

- `seq` +1 per sample, wraps at 65536. `t_us` = `micros()` at the sample, wraps at 2³².
- Raw MPU-9250 counts in the sensor axes. Accel ±16 g → 2048 LSB/g, gyro ±2000 dps → 16.4 LSB/dps.
  A gyro axis at −32768/+32767 is saturated.
- Packets may be lost, duplicated or reordered in transit. The app unwraps, sorts, de-duplicates and keeps gaps
  (`src/core/packet.ts`, a port of `packet.rs`).

### Sync marker packet (control `0x06`)

A **marker packet** goes on `imu_stream` (and into the on-device recording, so uploads keep it). It uses the
18-byte layout with a signature no real sample can produce:

| field | value |
|---|---|
| `seq` | seq of the most recent real sample |
| `t_us` | device `micros()` at the button press |
| `ax ay az gx` | `+32767, −32768, +32767, −32768` (every rail, alternating) |
| `gy gz` | `0x534F` ("OS"), `0x4B4D` ("MK") |

The app recognizes the signature (`isMarker`), takes `t_us` as the sync event time and removes the marker from the
sample stream. Markers are **not** written into exported `imu.bin` files, so the Rust pipeline's packet accounting
stays exact; they are stored with the session and listed with the detected claps. (Readers that don't know about
markers would see one duplicate seq per marker, which `packet.rs` drops harmlessly.)

## Control commands (`control`, 1 byte)

| byte | command | firmware behaviour |
|---|---|---|
| `0x01` | start streaming | notify packets on `imu_stream` |
| `0x02` | stop streaming | stop notifying |
| `0x03` | start on-device recording | clear the buffer, then append every sample (and markers) to it in production order |
| `0x04` | stop recording | stop appending; keep the buffer |
| `0x05` | upload the buffered session | **stop live streaming**, then send the buffer on `imu_stream` in packet-sized notifications; update `upload_sent_bytes` |
| `0x06` | mark sync event | emit a marker packet (above) |

Upload must be reliable at the application level: the firmware waits for notification flow control (no drops on
purpose). The app treats the upload as finished when `upload_sent_bytes ≥ upload_total_bytes` and the stream has
been quiet for 500 ms, or when the user presses "Finish upload now".

Background BLE is restricted on phones, so the intended pattern is: stream in the foreground, and for longer play
**record on the device (`0x03`/`0x04`) and upload later (`0x05`)**.

## `status` payload (read + notify)

UTF-8 JSON, one object, at most 244 bytes so a notification at MTU 247 fits it whole. Every field is optional, and
unknown fields are ignored.

```json
{"v":1,"fw":"0.1.0","rate_hz":500,"battery_pct":87,"streaming":true,"recording":false,
 "buffered_bytes":0,"upload_total_bytes":0,"upload_sent_bytes":0,"acc_range_g":16,"gyro_range_dps":2000}
```

| key | type | meaning |
|---|---|---|
| `v` | int | status format version (1) |
| `fw` | string | firmware version |
| `rate_hz` | int | configured sample rate |
| `battery_pct` | int | battery %, if there's no Battery Service |
| `streaming` / `recording` | bool | current state |
| `buffered_bytes` | int | bytes in the recording buffer (18 per packet) |
| `upload_total_bytes` / `upload_sent_bytes` | int | progress of the current/last upload |
| `acc_range_g` / `gyro_range_dps` | int | configured full-scale ranges |

Notify `status` on every state change and at least every 2 s while streaming, recording or uploading.

## Platform notes

- **Web Bluetooth**: Chrome/Edge on desktop and Android only, HTTPS (or `localhost`) only, and the connect call
  must come from a click. No Safari/iOS. No RSSI on a connected device, no MTU. The web app detects missing support,
  says why, and offers the mock device and `imu.bin` import instead.
- **iOS/Android apps** (Capacitor + `@capacitor-community/bluetooth-le`): full access, including RSSI and MTU.
  Foreground streaming only.
