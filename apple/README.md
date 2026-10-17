# oso for Apple: iPhone app (Swift), Apple Watch app, Fitbit heart rate

Native SwiftUI apps next to the web/Capacitor app in `../app/` (which stays the web + Android client).

| Piece | What it does | Sensors / data |
|---|---|---|
| **OsoKit** (`OsoKit/`, Swift package, pure Swift) | Everything that is math: BLE protocol constants and the 24-byte status, `imu.bin` read/write and packet accounting, Madgwick fusion, spikes/claps, swing detection + per-swing metrics, session summary, wrist-sample encoding, heart-rate alignment, Google Health query building. Unit tested with `swift test`. | — |
| **iPhone app** (`OsoPhone/`) | Pod over CoreBluetooth (stream, record, upload + mark uploaded, sync marker, sleep/wake), live swings, saved sessions, receives watch sessions, Fitbit connect + heart-rate overlay, share `imu.bin`. | racquet pod 500 Hz accel + gyro |
| **Watch app** (`OsoWatch/`) | Starts a HealthKit **badminton** workout, records the wrist IMU with `CMBatchedSensorManager` (device motion 200 Hz: rotation rate + gravity + user acceleration), counts swings live on the wrist, heart rate + calories from the workout, sends the session to the phone. | wrist 200 Hz accel + gyro, heart rate, energy |
| **Fitbit** (in the iPhone app) | Google Health API (`health.googleapis.com/v4`), the successor of the Fitbit Web API: heart rate at 1 s resolution, exercise sessions, calories for the session's time window, overlaid on the swings. | heart rate, calories |

## What the platforms allow (checked Oct 2026)

- **Apple Watch:** `CMBatchedSensorManager` (watchOS 10+) gives 800 Hz accelerometer and 200 Hz device motion, in 1 s
  batches, **only while a HealthKit workout session is running**: that's why the watch app is a workout app. Lower rate
  than the pod (500 Hz) and a different frame (wrist, not racquet), so tilt/face angles from the watch describe the
  forearm, not the racquet face. Swing timing, |ω|, impacts and counts carry over.
- **Fitbit: no raw accelerometer/gyro for us.** Current Fitbits (Versa 4, Sense 2, Charge 6) run no third-party apps;
  only the older Fitbit OS SDK devices (Versa 2/3, Sense) had device-side sensor APIs, which we are not targeting.
  The cloud side: the legacy Fitbit Web API stopped being supported Sept 30, 2026 and is turned off Oct 30, 2026; the
  **Google Health API** replaces it and has no motion data types. So Fitbit contributes **heart rate (1 s), calories,
  active-zone minutes and exercise sessions**; the pod and the watch contribute the motion.
- Google Health API scopes are restricted (OAuth app verification; past 100 users a yearly security assessment). For
  the hackathon, testing users on an unverified OAuth client are enough.

## Data flow

```
racquet pod --BLE--> iPhone --- imu.bin (500 Hz, mount 0) ---\
Apple Watch --WatchConnectivity--> iPhone -- imu.bin (200 Hz, mount 1) + HR series --> OsoKit analysis --> UI
Fitbit -> Google Health API --HTTPS--> iPhone -- HR samples for the session window --/
```
Watch sessions are written in the **same `imu.bin` format** (`docs/data-format.md`), quantized to the pod's scales, with
`mount_id = 1` (wrist). So every tool that reads a pod session (`oso-imu`, the web app, OsoKit) reads a watch session.
The analysis in OsoKit is rate-aware (block sizes and spike widths scale with `rate_hz`). The TypeScript app and
`oso-imu` still assume 500 Hz.

**Clocks.** Pod time is the pod's `micros()`. To put pod sessions on wall-clock time (needed for heart rate), the phone
sends a sync marker (0x06) right after connecting and records (marker `t_us`, phone time) as the session's clock
anchor. Watch samples carry boot-relative timestamps that CoreMotion maps to `Date`, so watch sessions are
on wall-clock time directly.

## Build

1. Install **Xcode 26+** (App Store). This Mac only has the Command Line Tools, whose SDK can't build Foundation,
   SwiftUI or CoreBluetooth code, so the app targets have not been compiled yet. OsoKit is plain Swift and is tested here:
   ```bash
   cd apple/OsoKit && swift test
   ```
2. `brew install xcodegen`, then `cd apple && xcodegen` creates `Oso.xcodeproj` from `project.yml` (iPhone app with the
   watch app embedded, both linking OsoKit).
3. In Xcode, set your team under Signing for both targets and run on a real iPhone + paired Watch (BLE, HealthKit
   and batched motion don't work in simulators).
4. Fitbit: create an OAuth client (iOS type) in Google Cloud with the Google Health API enabled, add your test users,
   put the client ID in `OsoPhone/Config.xcconfig` (`GOOGLE_CLIENT_ID`).

## Status

- [x] OsoKit: protocol, imu.bin, fusion, spikes, swings, summary, wrist encoding, HR alignment, Google Health queries + tests
- [x] iPhone app source: pod link, sessions, live view, watch receiver, Fitbit connect + heart-rate fetch
- [x] Watch app source: workout + batched motion + live swings + transfer
- [x] `project.yml` generates the project (`xcodegen` checked here: watch app embedded, OsoKit linked, sample bundled)
- [ ] Compile in Xcode, fix what the compiler finds (the app sources are only syntax-checked so far), run on hardware
- [ ] Watch: use the 800 Hz accelerometer for impact detection (device motion at 200 Hz smears 3 ms impacts)
- [ ] Pod + watch in the same session (wrist and racquet together: the arm-vs-racquet timing)
