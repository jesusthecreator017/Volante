## Inspiration

- Badminton is one of the fastest racquet sports: smashes leave the racquet faster than anything else in sport, and a whole rally can be over in seconds.
- Players who want to improve have two options: a coach watching, or phone video they scrub through frame by frame. Neither one tells you *how fast* you swung, *when* you hit the shuttle in your swing, or how your strokes change over a match.
- Tracking gear exists for tennis and golf, but badminton gets almost nothing affordable.
- Our goal: a small sensor on the racquet, two ordinary cameras, and the phone or watch you already own, turned into a 3D replay of the rally with numbers for every stroke.
- **oso** comes from the name of our Bluetooth service, `"oso" + "badmin"` in ASCII, and stuck as the project name.
- `[Add your own story here: who plays, what got you into it]`

## What it does

- **Racquet pod:** a tiny sensor in the butt of the handle records 500 motion samples a second (accelerometer + gyroscope) and streams them over Bluetooth, or records up to ~12.5 minutes to its own flash for upload later.
- **Swing analysis:** every swing is detected and broken down into backswing, forward swing and follow-through, with peak rotation, racquet-head speed, whether it made contact, impact timing ("early / on peak / late"), racquet tilt and face angle, forehand vs. backhand, and a stroke guess (smash, clear, drop, drive, lift, net).
- **3D replay:** two cameras on the side of the court become a 3D replay of the players, the shuttle and the racquet, viewable on desktop and in the browser.
- **Stroke labels from real matches:** a classifier trained on 11,301 labelled hits from professional singles matches labels every contact (serve, clear, smash, drop, net, lift, drive, block, push, press, net kill, flick serve).
- **Match stats:** stroke counts, speeds, hit positions and rallies per player.
- **Mobile app:** web, iOS and Android app (one codebase) that connects to the pod, shows live swings, saves sessions on the phone, and plays back the 3D replay. A mock pod means it all works without hardware.
- **Sync without wires:** tap the racquet (or press a button) at the start and end of a session; the pod detects the tap itself, and the two taps line the sensor clock up with the video.
- **Battery that lasts:** the pod has no power switch, so it sleeps on command and wakes when the app connects (estimated ~70 h asleep on a 200 mAh cell vs. ~3 h awake).
- **AI vs. AI badminton:** as a side project, two reinforcement-learning agents trained by self-play play full games that the viewer replays with a 3D scoreboard.
- **In progress:** a native Swift iPhone app, an Apple Watch app that records the wrist's motion and heart rate during a badminton workout, and Fitbit heart rate overlaid on each swing.

## How we built it

- **Hardware:** Seeed XIAO ESP32-S3 + GY-9250 (MPU-9250/6500) IMU on I2C, LiPo battery, mounted at the butt of the racquet
- **Firmware:** Rust (`esp-hal`, `trouble` BLE stack) sampling the IMU FIFO at 500 Hz, a crash-safe session log in flash, and deep sleep with timed wake windows
- **Protocol:** one shared `no_std` Rust crate (`oso-proto`) for packets, status and opcodes, used by the firmware and the laptop tools so they can't drift apart
- **Viewer:** Rust + `wgpu` 3D renderer with skinned player models, a racquet, the shuttle and the court; compiled to WebAssembly for the browser
- **Sensor fusion:** Madgwick orientation filter (forward and backward passes), gyro-bias calibration, and video-based correction of yaw drift
- **Computer vision:** RTMPose + YOLOX (via `rtmlib`, ONNX Runtime with Apple CoreML) for 2D pose, a classical shuttle detector with drag physics, stereo triangulation from two calibrated cameras
- **Machine learning:** scikit-learn gradient boosting for stroke types (BFMD dataset), PyTorch PPO self-play for the badminton agents
- **Mobile:** TypeScript + Vite + Capacitor 8 with `@capacitor-community/bluetooth-le` (Web Bluetooth in Chrome, CoreBluetooth on iOS, Android BLE), IndexedDB storage, Vitest
- **Server:** a zero-dependency Node server on a Raspberry Pi 5 that hosts the app and passes sessions to the video pipeline
- **Apple:** Swift, SwiftUI, CoreBluetooth, HealthKit, CoreMotion (`CMBatchedSensorManager`), WatchConnectivity, and the Google Health API for Fitbit

## Technical details

- **One file format for everything**
  - Pod streams, pod uploads and (soon) Apple Watch sessions all become the same `imu.bin`: a 16-byte header plus 18-byte packets (`seq`, `t_us`, 3-axis accel, 3-axis gyro).
  - Every tool reads it: the Rust pipeline, the mobile app and the Swift library.
- **Surviving a lossy radio link**
  - Packets can be lost, duplicated or reordered over Bluetooth, and the 16-bit counter wraps every 131 s at 500 Hz.
  - The decoder unwraps the counters, sorts, removes duplicates and keeps holes as holes, and the fusion bridges short holes with Hermite interpolation of the gyro.
  - On the sample session the accounting matches the injected faults exactly: 6,547 packets read, 66 lost, 13 duplicated, 99 reordered.
- **Uploads you can trust**
  - The pod never silently records over a session that hasn't been uploaded.
  - After an upload the pod reports how many packets it sent; only when the app received exactly that many does it tell the pod "uploaded", which frees the flash for the next session.
- **Lining up the sensor and the cameras**
  - The pod's clock and the cameras' clocks differ in offset and drift (~40 ppm).
  - A tap at the start and the end of the session gives offset and drift; shuttle contacts seen in the video then refine the fit, and per-hit residuals are reported in milliseconds.
- **Orientation from a 6-axis sensor**
  - Gravity fixes tilt, but heading (yaw) can't be observed without a magnetometer and drifts.
  - We take the forearm's heading from the 3D pose, low-pass it over 4 s, and use it to pull the racquet's yaw back.
- **3D from two phones**
  - Each camera is calibrated from the court lines (32 landmarks).
  - On synthetic tests, triangulation error is 0.7–1.1 cm median.
  - Phone video has a variable frame rate, so the pipeline reads per-frame timestamps and syncs the two cameras from the footage itself (shuttle and wrists), with no clap needed.
- **Fast pose on a laptop**
  - Running the person detector only every 6th frame, tracking players in between, and moving both networks to Apple's CoreML took pose estimation from **0.8 to ~15 frames per second per camera** on an M-series Mac.
- **Built against fake data first**
  - Every stage has a synthetic generator with ground truth (court images, player motion, shuttle flights, IMU packets with injected loss, bias and saturation), so each part was tested before any real recording existed.

## Challenges we ran into

- **Gyro saturation:** the MPU-9250 tops out at 2000 °/s, and a smash at the handle goes past that. Peak speeds on hard smashes are lower bounds, and the app flags them.
- **No heading:** a 6-axis IMU can't tell which way it's pointing horizontally, so racquet yaw drifts until the video corrects it.
- **No power switch:** the GY-9250 board's power LED can't be turned off in software and there's no switch on the pod, so we built a sleep mode that wakes for a 2-second Bluetooth advert every 30 seconds.
- **Flash erase stalls:** erasing the 6.4 MB session area takes up to 20 seconds and can stall the Bluetooth link, so the status now reports "erasing" and recording starts after it.
- **App vs. firmware drift:** the mobile app was written from an early spec before the firmware existed. By the time they met, the status format, sync markers and upload handshake had all changed, and the app couldn't talk to the real pod until we ported it to the firmware's protocol.
- **CoreML gotchas:** the detector needed static input shapes for CoreML, while the pose model must *not* have them or it silently falls back to the CPU. Our tests now compare CoreML output against the CPU.
- **Dataset licensing:** the professional-match dataset (BFMD) is non-commercial research data, so it stays outside the repo and only the trained model is used.
- **Self-play that won't lob:** the AI agents learned to beat the scripted bot 100% of the time, but they favour serve, smash and drop and almost never play clears or lifts, even after adding depth and positioning rewards. Rally length did go from 4.8 to 10.9 shots.
- **Fitbit's platform:** current Fitbits don't run third-party apps, and the old Fitbit Web API shuts down in October 2026. Raw motion from a Fitbit isn't possible, so Fitbit contributes heart rate through the new Google Health API instead.
- `[Add hardware/hackathon-weekend challenges you hit: soldering, mounting the pod, battery, venue Wi-Fi...]`

## Accomplishments that we're proud of

- A full chain from a sensor on a racquet to a 3D replay with numbers for every stroke
- Firmware, decoder and app that agree on one protocol, with the constants shared from a single crate
- A mobile app that runs on the web, iOS and Android from one codebase, with a mock pod that behaves like the real firmware
- An ~18× speed-up of pose estimation on a laptop
- Every stage tested against synthetic ground truth before real data existed
- Two AIs playing full badminton games against each other in our own viewer

## What we learned

- How much sensor fusion depends on clocks: offsets, drift, wrap-around and lost packets
- Why a 6-axis IMU needs a second source (video) for heading
- Designing a Bluetooth protocol for flaky links: flow-controlled uploads, explicit confirms, and never overwriting data silently
- Writing embedded Rust with async on an ESP32
- What each wearable platform actually exposes: Apple Watch gives raw 800 Hz motion during workouts; Fitbit only gives summaries in the cloud

## What's next for oso

- Test the mobile app end to end on the real pod
- Ship the native iPhone and Apple Watch apps, and record wrist and racquet together to compare arm timing with racquet timing
- Use the watch's 800 Hz accelerometer for sharper impact detection
- Overlay Fitbit or Apple Watch heart rate on every rally
- A higher-range gyro (±4000 °/s) so smashes stop saturating
- A trained stroke classifier on our own recordings, replacing the rule-based labels on the racquet data
- A proper enclosure and mount for the pod
