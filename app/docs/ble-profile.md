# oso BLE profile: app side

The contract is the firmware's: **[`../../docs/ble-profile.md`](../../docs/ble-profile.md)** (constants in
[`../../proto/src/lib.rs`](../../proto/src/lib.rs)). The app mirrors those constants in `src/ble/profile.ts`, and the
mock pod (`src/ble/mock.ts`) implements the same state machine; `test/protocol.test.ts` checks both. This page
only lists how the app uses the profile.

## What the app does with it
- **Status** is the 24-byte struct. Reads always carry `event = none`; events (sync markers, `upload_done`, errors)
  only arrive as notifications, so the app subscribes to `status` on connect.
- **Sync markers**: `marker_tap` (detected on the pod) and `marker_cmd` (0x06) events carry the device `t_us`; the
  app unwraps it against the newest packet of the running stream or upload (`LiveSession.addDeviceMarker`).
  The app also detects claps in the data itself; both show on the Device and Session pages.
- **Record** (0x03): if the pod holds a session that was not uploaded, it answers error 3; the app asks the user and
  sends 0x03 again within the 5 s window.
- **Upload** (0x05): packets on `imu_stream`, then the stored markers, then `upload_done{count}`. Only when the
  received packet count equals `count` does the app send **0x07 (mark uploaded)**. A short or user-finished upload
  is saved with "incomplete" in its name and the pod keeps the session. 20 s without data = stalled.
- **Sleep / wake**: 0x08 closes the link. Connecting to a pod whose status says `sleeping` makes the app send 0x09
  at once (the pod gives a host 10 s); it resets, and the user connects again after ~7 s.
- **Battery**: Battery Service 0x2A19, else `battery_pct`; 255 = unknown (no divider fitted yet).

## MTU and notification size
Request ATT MTU 247; the firmware sends `min(13, floor((MTU - 3) / 18))` whole packets per notification. Android
negotiates on connect (the plugin requests it), iOS picks it, **Web Bluetooth does not expose it** (the Device page
shows "—"). Notifications that are not a whole number of packets are counted and dropped ("bad notifications").

## Platform notes
- **Web Bluetooth**: Chrome/Edge on desktop and Android only, HTTPS (or `localhost`) only, and the connect call must
  come from a click. No Safari/iOS. No RSSI on a connected device, no MTU. The web app detects missing support and
  offers the mock device and `imu.bin` import instead.
- **iOS/Android apps** (Capacitor + `@capacitor-community/bluetooth-le`): full access, including RSSI and MTU.
  Foreground streaming only: for long play, record on the pod and upload later.
- A sleeping pod advertises only 2 s every 30 s: keep the system picker open up to ~30 s.

## Legacy
The pre-firmware app spec put a fake "marker packet" on `imu_stream` and a JSON status. Neither exists on the
device; `isMarker` in `src/core/packet.ts` only remains so older captures still parse.
