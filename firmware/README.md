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
cargo run --release --bin oso-rec -- mark | record stop | record start [--force]   # --force overwrites an un-uploaded session
cargo run --release --bin oso-imu -- --imu out/hw/imu.bin --replay assets/replay/replay.bin --sync sync.json --player 0 -o out.bin
```
Power on = recording starts (after a ~5-20 s flash erase). Tap the pod (or clap) after 1 s of stillness at the start and end of each session. Switch off, switch on near the laptop, `upload`; the upload marks the session as uploaded so the next power-on records again. A session that was not uploaded is never overwritten silently.
`imu.markers.json` next to the file lists the device-side tap times.

## Sleep and wake (the pod has no power switch)
```
cargo run --release --bin oso-rec -- sleep   # stops recording, parks the IMU, deep sleep; the pod then wakes every 30 s for a 2 s advert
cargo run --release --bin oso-rec -- wake    # scans up to 40 s for that advert, sends 0x09, waits for the normal boot (~7 s)
```
While sleeping, `status` shows `sleeping` (and 0 stored packets; the session is untouched) and any other command gets error 5. Flash or `upload` only after `wake`:
the USB port is only present during the 2 s windows. The un-uploaded session survives sleep and wake. Details and the power estimate: [docs/ble-profile.md](../docs/ble-profile.md#sleep-the-pod-has-no-power-switch).
