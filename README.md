# Volante

Volante is the product name; command and package names keep the `oso` prefix.

Badminton, measured. A sensor in the racquet handle, two cameras and the phone or watch you already own turn a session into
a 3D replay with numbers for every stroke: racquet-head speed, impact timing, stroke type, rallies and heart rate. Next to it,
**Volante Arena**: a public ladder where people train small neural networks to play badminton and watch their matches in 3D.

## What's in this repo

| Folder | What it is | Start here |
|---|---|---|
| `firmware/` | Racquet pod firmware (XIAO ESP32-S3 + MPU-9250, async Rust: Embassy, esp-hal, trouble BLE). 500 Hz IMU, BLE streaming, flash recording, deep sleep. | [firmware/README.md](firmware/README.md) |
| `proto/` | The pod's BLE protocol constants, shared by firmware and tools. | [docs/ble-profile.md](docs/ble-profile.md) |
| `crates/oso-render` | Shared Rust + wgpu 3D renderer (native and WebAssembly). | |
| `crates/oso-replay` | **Replay** app: recorded matches in 3D, plus the `oso-rec` pod recorder and the `oso-imu` analysis tool. | `cargo run --release` |
| `crates/oso-arena` | **Arena** viewer: the renderer plus a scoreboard, for simulated matches. | [web/arena/README.md](web/arena/README.md) |
| `web/replay`, `web/arena` | The two viewers built for the browser (wasm). | `web/arena/build.sh` |
| `tools/` | Python: camera calibration, pose (RTMPose/YOLOX), stereo triangulation, shuttle tracking, stroke classifier. | [tools/README.md](tools/README.md) |
| `app/` | Cross-platform app (TypeScript + Vite + Capacitor): web, iOS, Android. Pod over Bluetooth, live swings, replay. Also the LAN server for the Pi. | [app/README.md](app/README.md) |
| `apple/` | Native **iPhone** and **Apple Watch** apps (SwiftUI) and **OsoKit**, the shared Swift analysis core. Watch records wrist motion + heart rate in a badminton workout. | [apple/README.md](apple/README.md) |
| `arena/kit` | `oso-arena` Python kit: the official simulator, PPO trainer, reward plug-ins, `check`/`export`/`eval`/`match`/`animate`. | [arena/kit/README.md](arena/kit/README.md) |
| `arena/contract` | Contract `oso-arena/1`: the submission format, network, builds, observation, actions and match rules. | [arena/contract/CONTRACT.md](arena/contract/CONTRACT.md) |
| `skills/oso-arena-coach` | A Claude skill that coaches you through training an Arena model in a play style. | [SKILL.md](skills/oso-arena-coach/SKILL.md) |
| `site/` | The public Arena website (TypeScript + Vite, strict CSP). | [site/README.md](site/README.md) |
| `arena/handoff` | The bundle and brief for the Raspberry Pi server that hosts Arena. | [arena/handoff/README.md](arena/handoff/README.md) |
| `deploy/pi` | Deploying the LAN Replay server to a Raspberry Pi 5. | [deploy/pi/README.md](deploy/pi/README.md) |
| `docs/` | File formats (`data-format.md`), the BLE profile, design specs and plans. | [docs/data-format.md](docs/data-format.md) |

## Quick starts

```bash
# 3D replay viewer (native), on the bundled sample match
cargo run --release

# Viewers in the browser
web/replay/build.sh && python3 -m http.server -d web/replay 8080
web/arena/build.sh  && python3 -m http.server -d web/arena 8080   # open /?match=assets/replay/replay.bin

# Arena: simulate a match between two scripted bots and watch it (Python 3.10+, e.g. python3.12)
python3.12 -m venv .venv && .venv/bin/pip install "./arena/kit[train]"
.venv/bin/oso-arena match bot bot -o match.log --seed 1 && .venv/bin/oso-arena animate match.log -o match.bin

# Website against a fake API
cd site && npm install && npm run dev:mock

# Cross-platform app
cd app && npm install && npm run dev

# iPhone + Watch apps (Xcode 26+): see apple/README.md for signing
brew install xcodegen && cd apple && xcodegen && open Oso.xcodeproj
```

## Tests

| Area | Command |
|---|---|
| Rust | `cargo test --workspace` |
| Arena kit | `python arena/kit/tests/run_all.py` (Python 3.10+, numpy, safetensors; torch for training tests) |
| Website | `cd site && npm test` |
| App | `cd app && npm test` |
| Viewer JS | `node --test web/arena/*.test.mjs` |
| OsoKit (Swift) | `cd apple/OsoKit && swift test` (needs Xcode) |

## Credits

Third-party assets and their licences are listed in [CREDITS.md](CREDITS.md). The Arena kit is MIT licensed
([arena/kit/LICENSE](arena/kit/LICENSE)).
