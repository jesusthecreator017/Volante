## Inspiration

- Badminton is one of the fastest racquet sports, but players who want to improve only have a coach or phone video to scrub through.
- Neither tells you how fast you swung, when in the swing you hit the shuttle, or how your strokes change over a match.
- **oso** turns a small racquet sensor, two cameras and the phone or watch you already own into a 3D replay with numbers for every stroke.

## What it does

- **Racquet pod:** a sensor in the handle records 500 motion samples a second and streams them over Bluetooth, or stores ~12.5 minutes to upload later.
- **Swing analysis:** every swing gets peak rotation, racquet-head speed, contact, impact timing, face angle, forehand/backhand and a stroke type.
- **3D replay:** two cameras beside the court become a 3D replay of the players, shuttle and racquet, on desktop or in the browser.
- **Stroke classifier:** trained on 11,301 labelled hits from pro matches, it labels 12 stroke types.
- **Mobile app:** one codebase for web, iOS and Android that connects to the pod, shows live swings and plays back the replay.
- **Tap to sync:** tapping the racquet at the start and end of a session lines the sensor clock up with the video.
- **In progress:** a native iPhone app, an Apple Watch app that records wrist motion and heart rate, and Fitbit heart rate on each swing.

## How we built it

- **Hardware:** Seeed XIAO ESP32-S3 with an MPU-9250 IMU, LiPo-powered, mounted in the racquet handle
- **Firmware:** async Rust (Embassy, esp-hal, trouble BLE) with a crash-safe flash log and deep sleep
- **Viewer:** Rust + wgpu 3D renderer, compiled to WebAssembly for the browser
- **Vision:** RTMPose + YOLOX on ONNX Runtime/CoreML, stereo triangulation, physics-based shuttle tracking
- **ML:** scikit-learn stroke classifier, PyTorch self-play agents
- **Mobile:** TypeScript, Vite and Capacitor with Web Bluetooth/CoreBluetooth; Node server on a Raspberry Pi 5
- **Apple:** Swift, SwiftUI, CoreMotion, HealthKit and WatchConnectivity; Google Health API for Fitbit

## Challenges we ran into

- **Gyro saturation:** smashes go past the IMU's 2000 °/s limit, so we flag those speeds as lower bounds.
- **Heading drift:** a 6-axis IMU can't sense heading, so we correct the racquet's yaw from the forearm direction in the video.
- **Lossy Bluetooth:** packets get lost, duplicated and reordered; our decoder recovers exact counts and the fusion bridges the gaps.
- **No power switch:** we built a sleep mode that wakes for a short Bluetooth advert every 30 s (~70 h standby).
- **Protocol drift:** the app was written before the firmware existed, so we ported it to the real protocol and made the mock pod behave like the hardware.
- **Fitbit:** current Fitbits run no third-party apps and the old API shuts down in October 2026, so Fitbit contributes heart rate, not motion.

## Accomplishments that we're proud of

- A full chain from a sensor on a racquet to a 3D replay with stats for every stroke
- Firmware, apps and tools sharing one protocol and one file format
- An ~18× faster pose pipeline (0.8 → 15 fps per camera) on a laptop
- Every stage tested against synthetic ground truth before real data existed

## What we learned

- Sensor fusion lives or dies on clocks: offsets, drift and lost packets
- How to design a Bluetooth protocol that never silently loses data
- Embedded async Rust on an ESP32
- What each wearable platform really exposes: raw motion on Apple Watch, heart-rate summaries from Fitbit

## What's next for oso

- Test end to end on the real pod
- Ship the iPhone and Apple Watch apps, recording wrist and racquet together
- Heart rate on every rally
- A ±4000 °/s gyro so smashes stop saturating
- A stroke classifier trained on our own recordings
