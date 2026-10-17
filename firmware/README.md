# oso-fw: XIAO ESP32-S3 racquet pod

Samples a GY-9250 (MPU-9250/6500) at 500 Hz, streams packet v1 over BLE, records sessions to flash and uploads them. Contract: [docs/ble-profile.md](../docs/ble-profile.md).

Wiring: IMU on I2C0, SDA = D4 = GPIO5, SCL = D5 = GPIO6, 3V3, address 0x68 (AD0 and INT unconnected). LiPo straight to BAT+/- (no switch: use `oso-rec sleep`).

## Build and flash (esp toolchain, `~/export-esp.sh`)
```
cd firmware
source ~/export-esp.sh
cargo build --release          # also: cargo clippy --release
cargo run --release            # flash (partitions.csv) + serial monitor; the XIAO enumerates as /dev/cu.usbmodem*
cargo build --release --features sim   # synthetic sampler for bench tests when no IMU is wired
```
The firmware waits 3.5 s before the first log line (the USB serial port re-enumerates on reset).
Boot log: `WHO_AM_I`, the config registers read back, then `imu NNN Hz measured ... last [ax ay az gx gy gz]` every 5 s. On the desk |a| is about 2048 counts and the gyro near 0.

## Record and upload (from the repo root, where `cargo run --release --bin X` resolves to the `oso-replay` package, i.e. `-p oso-replay`; macOS asks to allow Bluetooth for the terminal the first time)
```
cargo run --release --bin oso-rec -- scan
cargo run --release --bin oso-rec -- status
cargo run --release --bin oso-rec -- stream -o out/hw/stream.bin --secs 60   # live, prints rate / loss
cargo run --release --bin oso-rec -- upload -o out/hw/imu.bin                # the session recorded since power-on
cargo run --release --bin oso-rec -- mark | record stop   # record stop ends the recording
cargo run --release --bin oso-rec -- record start [--force] [--no-view]   # --force overwrites an un-uploaded session; opens the live racquet window unless --no-view (then it starts, prints and exits)
cargo run --release --bin oso-rec -- live                                 # the same window without starting a recording
cargo run --release --bin oso-rec -- calibrate                            # mount calibration in the terminal (below)
cargo run --release --bin oso-imu -- --imu out/hw/imu.bin --replay assets/replay/replay.bin --sync sync.json --player 0 -o out.bin
```
Power on = recording starts (after a ~5-20 s flash erase). Tap the pod (or clap) after 1 s of stillness at the start and end of each session. Switch off, switch on near the laptop, `upload`; the upload marks the session as uploaded so the next power-on records again. A session that was not uploaded is never overwritten silently.
`imu.markers.json` next to the file lists the device-side tap times.

Live racquet view (`record start` and `live`): the racquet model turns with the pod (0x01 streaming, ~500 Hz, the offline forward Madgwick filter run incrementally; hold the pod still for the first second, it calibrates the gyro bias). The window title is the readout: recording/streaming state, packets per second, loss %. Keys: `C` calibrate the mount (below), `R` re-centre (current heading becomes forward, +X along the red grid line; tilt stays from gravity), `M` sync marker (0x06, the pod glows), `Esc` or close = stop streaming (0x02) and disconnect; **the recording keeps running on the pod** (`oso-rec record stop` ends it). If the link drops (or no data arrives for 3 s) the title says reconnecting and the window re-scans (up to 40 s), reconnects and re-sends 0x01 by itself, retrying every 5 s; the fusion, mount and re-centre carry on (the gap shows as loss). Each reconnect is logged to stderr with a UTC time. The pod itself rides out transient notify errors (drops the batch, counted in status `stream_drops`) and disconnects a link idle for 30 s so it advertises again.
**Mount** (how the MPU sits in the racquet): every accelerometer and gyro vector is rotated from the sensor frame into the racquet frame (+Y shaft to the tip, +Z string-face normal) before fusion, in the live view and in `oso-imu` alike. `--mount identity|flat|FILE` (on `live`, `record start`, `oso-imu`), else `~/.config/oso/mount.json`, else `flat` (the pod's board lies flat: sensor +Z = shaft, sensor +X = face normal; the normal is a guess). `identity` is for the synthetic data (`tools/test_*.py` pass it). To calibrate: press `C` in the window (or run `oso-rec calibrate`), hold the racquet upright with the tip up and still, press `C` (or Enter); then lay it flat with the strings up, still, press again. Each step averages the last second of gravity; it is rejected if the gyro exceeded 20 dps or the two poses are within 30 degrees. The result is written to `~/.config/oso/mount.json` (`{"shaft": [x,y,z], "normal": [x,y,z]}`, the racquet axes in the sensor frame) and applied at once (the view recalibrates its gyro bias for a second). `R` takes the heading from the shaft's horizontal direction, or from the face normal while the shaft is within 20 degrees of vertical (back to the shaft beyond 35 degrees).
Without a pod: `oso-rec live --mount identity --replay-file imu.bin [--time S --screenshot out.png]` plays a recorded file in the same window (or renders one frame at S seconds to a PNG).
LED (highest priority first): fast blink = IMU error; slow blink = erasing; **solid 1.2 s, then 0.3 s off = a sync tap or `mark` registered** (another one inside the glow restarts the 1.2 s); double blink every ~2 s = un-uploaded session waiting.

## Sleep and wake (the pod has no power switch)
```
cargo run --release --bin oso-rec -- sleep   # stops recording, parks the IMU, deep sleep; the pod then wakes every 30 s for a 2 s advert
cargo run --release --bin oso-rec -- wake    # scans up to 40 s for that advert, sends 0x09, waits for the normal boot (~7 s)
```
While sleeping, `status` shows `sleeping` (and 0 stored packets; the session is untouched) and any other command gets error 5. Flash or `upload` only after `wake`:
the USB port is only present during the 2 s windows. The un-uploaded session survives sleep and wake. Details and the power estimate: [docs/ble-profile.md](../docs/ble-profile.md#sleep-the-pod-has-no-power-switch).
