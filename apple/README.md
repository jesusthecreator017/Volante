# oso for Apple: iPhone app (Swift), Apple Watch app, Fitbit heart rate

Native SwiftUI apps next to the web/Capacitor app in `../app/` (which stays the web + Android client).

| Piece | What it does | Sensors / data |
|---|---|---|
| **OsoKit** (`OsoKit/`, Swift package, pure Swift) | Everything that is math: BLE protocol constants and the 24-byte status, `imu.bin` read/write and packet accounting, Madgwick fusion, spikes/claps, swing detection + per-swing metrics, on-device stroke models, session summary, wrist-sample encoding, heart-rate alignment, Google Health query building. Unit tested with `swift test`. | — |
| **iPhone app** (`OsoPhone/`, sideloaded) | Pod over CoreBluetooth (stream, record, upload + mark uploaded, sync marker, sleep/wake), live swings, saved sessions, receives watch sessions, uploads sessions to the Pi and fetches replays, Fitbit sign-in + heart-rate overlay, share `imu.bin`. | racquet pod 500 Hz accel + gyro |
| **Watch app** (`OsoWatch/`) | Starts a HealthKit **badminton** workout, records the wrist IMU with `CMBatchedSensorManager` (device motion 200 Hz: rotation rate + gravity + user acceleration), counts swings live on the wrist, heart rate + calories from the workout, sends the session to the phone. | wrist 200 Hz accel + gyro, heart rate, energy |
| **Raspberry Pi server** (`../app/server`, `../deploy/pi`) | Hosts the web app, the session exchange with the video pipeline, and the Fitbit proxy (keeps the Google token, calls the Google Health API). | — |
| **Fitbit** (sign-in on the iPhone, API calls on the Pi) | Google Health API (`health.googleapis.com/v4`), the successor of the Fitbit Web API: heart rate at 1 s resolution for the session's time window, overlaid on the swings. | heart rate |

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
Fitbit -> Google Health API --HTTPS--> Raspberry Pi (/api/fitbit) --LAN--> iPhone -- HR for the session --/
iPhone --LAN http--> Raspberry Pi (/api/sessions) <-- video pipeline (replay.bin)
```
Watch sessions are written in the **same `imu.bin` format** (`docs/data-format.md`), quantized to the pod's scales, with
`mount_id = 1` (wrist). So every tool that reads a pod session (`oso-imu`, the web app, OsoKit) reads a watch session.
The analysis in OsoKit is rate-aware (block sizes and spike widths scale with `rate_hz`). The web app's analysis
is rate-aware the same way (so it opens watch sessions too), and so is `oso-imu` (spike widths, median window, stationary blocks); it still needs a still second and a clap to sync, so it is for pod sessions.

**Clocks.** Pod time is the pod's `micros()`. To put pod sessions on wall-clock time (needed for heart rate), the phone
sends a sync marker (0x06) right after connecting and records (marker `t_us`, phone time) as the session's clock
anchor. Watch samples carry boot-relative timestamps that CoreMotion maps to `Date`, so watch sessions are
on wall-clock time directly.

## Build and sideload (no App Store)

The iPhone and Watch apps are installed straight from Xcode onto your own devices ("sideloaded"); all server work
runs on the Raspberry Pi (`../deploy/pi/README.md`).

1. Install **Xcode 26+**. Then `brew install xcodegen && cd apple && xcodegen` creates `Oso.xcodeproj` (the iPhone
   app with the watch app embedded, both linking OsoKit).
2. Xcode → Settings → Accounts: add your Apple ID. A **free Apple ID works** (a "Personal Team"): Apple's capability
   table lists HealthKit as available to free accounts on iOS and watchOS. Then
   `cp Signing.local.xcconfig.example Signing.local.xcconfig` (gitignored) and set your team id and a bundle-id prefix
   you own (a free team can't use `com.oso.*`), then rerun `xcodegen`. Both apps and the watch's companion id follow
   the prefix. The team id is the one Xcode shows for the account (Settings → Accounts → the team), or
   `defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier | grep teamID`; an old certificate in the
   keychain may carry a different one.
   Command line instead of Xcode: `xcodebuild -project Oso.xcodeproj -scheme OsoPhone -destination 'id=<iPhone UDID>'
   -allowProvisioningUpdates build`, then `xcrun devicectl device install app --device <UDID> <path>/OsoPhone.app`.
3. iPhone: Settings → Privacy & Security → **Developer Mode** on (restart). Watch: same, on the watch, after it has
   been connected to Xcode once (Xcode → Window → Devices and Simulators).
4. Plug in the iPhone, pick the **OsoPhone** scheme and your iPhone, Run. The watch app is embedded: install it from the
   iPhone's Watch app (Available Apps → oso → Install), or run the **OsoWatch** scheme on the watch directly once the
   watch has been paired with Xcode (otherwise devicectl reports "The device rejected the Bluetooth connection attempt").
5. First launch: iPhone Settings → General → VPN & Device Management → trust your developer certificate. On the
   watch, open the app once and accept the prompt.
6. In the app's Connections tab, set the Pi address (`http://<pi>.local:8080`) and check the connection.

**Free-account limits:** the install expires after **7 days**. Run it from Xcode again to renew it; the app's data
stays. There is also a small cap on how many apps one free account can sideload per device. A paid developer
account ($99/yr) gives 1-year installs and TestFlight.

**Simulators can't test the real thing:** no Bluetooth (pod), no HealthKit workouts or batched motion. They can
build and run the UI with the bundled sample session.

OsoKit is plain Swift and is tested without a device:
```bash
cd apple/OsoKit && swift test
```

**Fitbit:** create an OAuth client of type **iOS** in Google Cloud (bundle ID = the phone app's), enable the Google
Health API, and add your Google account as a test user. Put the client ID and its reversed form in
`OsoPhone/Config.xcconfig` (`GOOGLE_CLIENT_ID`, `GOOGLE_REDIRECT_SCHEME`). You sign in once on the phone, the
refresh token goes to the Pi, and every Google Health API call runs on the Pi after that.

## Stroke models (experimental)
OsoKit runs two gradient-boosted tree models on the phone, pure Swift (`StrokeWindow` -> `StrokeFeatures` -> `TreeEnsemble` ->
`StrokeClassifier`): the power/mid/touch group (its FH/BH head is parsed but not shown: near chance) and a 10-stroke "beta" guess, both on the 24 orientation-free
features. Racquet sessions only; results are cached per session in `strokes.json` and recomputed when the model version changes.
The models are `OsoKit/Sources/OsoKit/Resources/*.trees.json`, copies of `models/strokes/`. To update them:
`tools/.venv/bin/python tools/strokes/export_trees.py` (or `train.py --final`), then `apple/scripts/sync-models.sh`, then commit both.
`swift test` and `tools/strokes/test_export_trees.py` fail if the copies drift. Details: `docs/strokes/model-card.md`.

## Status

- [x] OsoKit: protocol, imu.bin, fusion, spikes, swings, summary, wrist encoding, HR alignment, Google Health queries + tests
- [x] iPhone app source: pod link, sessions, live view, watch receiver, Fitbit connect + heart-rate fetch
- [x] Watch app source: workout + batched motion + live swings + transfer
- [x] `project.yml` generates the project (`xcodegen` checked here: watch app embedded, OsoKit linked, sample bundled)
- [ ] Compile in Xcode, fix what the compiler finds (the app sources are only syntax-checked so far), run on hardware
- [ ] Watch: use the 800 Hz accelerometer for impact detection (device motion at 200 Hz smears 3 ms impacts)
- [ ] Pod + watch in the same session (wrist and racquet together: the arm-vs-racquet timing)
