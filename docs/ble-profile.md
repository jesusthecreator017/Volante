# Racquet pod BLE profile (firmware 0.1)

Implemented by `firmware/` (ESP32-S3, esp-hal + trouble), spoken by `src/bin/oso-rec.rs`; the constants live in `proto/` (`oso-proto`).
The app in `app/` follows this document (`app/src/ble/profile.ts`, checked by `app/test/protocol.test.ts`). Everything is little-endian.

## Advertising and link
- Local name `OSO-xxxx` (last two bytes of the Bluetooth MAC, hex), advertised service UUID 128-bit `6f736f00-0001-...`.
- One connection at a time. After connecting the firmware requests a 7.5-15 ms connection interval, latency 0, 4 s supervision timeout and
  the 251 byte data length. The central should request MTU 247 (GATT MTU exchange). Notifications carry
  N = floor((MTU - 3) / 18) packets, at most 13.

## Service `6f736f00-0001-4c45-8a00-6261646d696e`
| Characteristic | UUID | Properties | Payload |
|---|---|---|---|
| `imu_stream` | `6f736f00-0002-4c45-8a00-6261646d696e` | notify | N concatenated 18 byte packets (packet v1, docs/data-format.md) |
| `control` | `6f736f00-0003-4c45-8a00-6261646d696e` | write, write without response | one opcode byte |
| `status` | `6f736f00-0004-4c45-8a00-6261646d696e` | read, notify | 24 bytes, below |

Standard Battery Service 0x180F / Battery Level 0x2A19 is present. It reads 255 (unknown) until the divider is soldered and
`HAS_BATTERY_DIVIDER` in `firmware/src/battery.rs` is flipped.

## `control` opcodes
| Op | Action |
|---|---|
| `0x01` / `0x02` | start / stop streaming live packets on `imu_stream` (lossy: the oldest packets are dropped when the link is slow) |
| `0x03` | start recording to flash. If the stored session was not uploaded, the first `0x03` only answers with error 3 (`need confirm`); a second `0x03` within 5 s erases and records. Erasing takes a few seconds to ~20 s, during which the link can stall or drop |
| `0x04` | stop recording (marks the session complete) |
| `0x05` | upload: stops a running recording, stops streaming, then sends the stored packets on `imu_stream` as fast as flow control allows, then the stored markers as status events, then status event `upload_done{packet_count}` |
| `0x06` | sync marker now (status event `marker_cmd`, stored in the session when recording) |
| `0x07` | **new, not in the app spec:** mark uploaded. The host sends it only after the packet count equals `upload_done` and its file is written; a failed upload never sets the flag |

| `0x08` | **sleep (new):** stops a running recording (marks the session complete, like `0x04`), parks the IMU and enters deep sleep, see "Sleep". Error 4 (`busy`) while erasing or uploading |
| `0x09` | **wake (new):** only honored while sleeping: software reset into the normal boot |

Unknown opcodes: status event `error` with code 1.

## `status` (24 bytes)
| Offset | Field | Type | Notes |
|---|---|---|---|
| 0 | `fw_major`, `fw_minor` | u8, u8 | 0.1 |
| 2 | `rate_hz` | u16 | 500 |
| 4 | `battery_pct` | u8 | 255 = unknown |
| 5 | `state_bits` | u8 | 1 streaming, 2 recording, 4 uploading, 8 pending_upload, 16 flash_full, 32 imu_ok, 64 imu_is_6500, 128 erasing (the last is new: recording has not started yet) |
| 6 | `accel_g` | u8 | 16 |
| 7 | `gyro_dps` | u16 | 2000 |
| 9 | `buffered_packets` | u32 | packets in the stored session (durably in flash) |
| 13 | `fifo_overflows` | u16 | IMU FIFO overflows since boot / recording start |
| 15 | `event` | u8 | 0 none, 1 marker_tap, 2 marker_cmd, 3 upload_done, 4 error |
| 16 | `event_value` | u32 | marker `t_us` / packet count / error code |
| 20 | `state_ext` | u8 | 1 sleeping (the pod is in its sleeping cycle). `state_bits` is full, hence the extra byte |
| 21 | reserved | 3 bytes | 0 |

Error codes: 1 bad opcode, 2 no session, 3 un-uploaded session (repeat `0x03` within 5 s), 4 busy, 5 sleeping (any opcode but `0x09` while sleeping; the pod then goes back to sleep).
`status` is notified when an event happens and otherwise once per second. The readable value is refreshed every 20 ms.

## Sync markers (one deviation from the app spec)
The app spec has the device emit a "marker packet" on `0x06`. Packets are 18-byte IMU samples with a contiguous `seq`, so a marker packet would be
a fake sample. Instead markers go out on `status` as events 1 (tap detected on the device) and 2 (`0x06`), with the device-clock `t_us` in
`event_value`. Taps are detected on the device: the gyro quiet (< 20 dps) for 1 s, then an accelerometer spike > 5 g above the 9-sample median,
at most 3 samples wide (the same shape as the clap detector in `src/imu/sync.rs`). While recording, the first 32 markers are stored with the
session and replayed on upload before `upload_done`.

## Timestamps and loss
- `seq` +1 per sample; `t_us` = low 32 bits of the ESP clock in microseconds, reconstructed per FIFO batch from the MPU sample clock
  (accurate to roughly 0.2 ms). The MPU clock can be ~1 % off the ESP clock; the clap fit in `src/imu/sync.rs` handles drift.
- A FIFO overflow resets the FIFO and advances `seq` by the estimated lost samples: a real gap, counted in `fifo_overflows`.

## Flash session (device internal)
Partition `imu` (0x190000, 6.4 MB): sector 0 is an append-only log of 8-byte records (START, PROGRESS every ~2 s, MARKER, COMPLETE,
UPLOADED; see `proto/src/session.rs`), packets follow from sector 1: ~375 k packets, about 12.5 minutes at 500 Hz. The whole area is erased before
recording, so sampling only does small page programs. At power-on the firmware records by itself unless the stored session has data and was not uploaded;
then it keeps it, reports `pending_upload` and does not record.

## Sleep (the pod has no power switch)
`0x08` parks the pod: recording stops, pending flash writes finish, the MPU-6500 gets its sleep bit (~6 uA), a flag is set in RTC fast memory and the
ESP32-S3 enters deep sleep with a timer wake. The flag is not valid after a power loss, so a flat-then-recharged pod boots normally.
Every `SLEEP_INTERVAL_S` = 30 s the timer wakes it for a minimal boot (80 MHz, no flash, no IMU, no LED, no auto-record):
1. It advertises as `OSO-xxxx` (40 ms interval, connectable) for `SLEEP_ADV_MS` = 2000 ms. `status` has `state_ext` bit 0 (`sleeping`) set; `buffered_packets`
   and the other session fields read 0 (the flash is not touched). The session is untouched.
2. No host connected: back to deep sleep for 30 s.
3. A host connected: it has 10 s to write `0x09`. Any other opcode is answered with status error 5 and the pod goes back to sleep; so does a disconnect or the timeout.

`0x09` software-resets into the normal boot (~6 s: the 3.5 s USB wait, IMU init). The normal boot rules apply: an un-uploaded session is kept and not recorded over.
Wake latency: at most 30 s to catch an advert + ~7 s to boot. `oso-rec sleep` / `oso-rec wake` do this; `oso-rec status` works in a wake window (it scans up to 40 s).

Power (estimated, no meter was available): measured from the serial log, a cycle with no host is 2049 ms awake after HAL init (2000 ms advertising + ~50 ms radio start) in a
32.2 s period (30 s sleep + ~0.2 s ROM/bootloader), so the awake duty is ~6.8 %. At an assumed ~27 mA while advertising at 80 MHz that is ~1.9 mA; deep sleep (~10 uA) plus the IMU (6 uA) add ~0.05 mA;
the GY-9250 power LED ~1 mA. Total ~2.9 mA with the LED (~1.9 mA without), so about 70 h from a 200 mAh cell instead of ~3 h awake. The GY-9250 power LED cannot be switched off in software: removing that LED
(or adding a switch in the battery lead) is the hardware fix.
