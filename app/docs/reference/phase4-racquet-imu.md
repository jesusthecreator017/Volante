# Phase 4: racquet IMU ingest, orientation fusion, time sync, racquet in the viewer

## Goal
The racquet-mounted IMU stream (MPU-9250 on a XIAO ESP32-S3, butt end of the handle) becomes a racquet pose track in
the replay: position from the video wrist, orientation from the IMU (Madgwick, 6-axis) with yaw drift corrected from
video, on the video clock via a clap + impact time sync whose residuals are reported in ms. The viewer draws a blocky
racquet in each tracked player's hand. Synthetic data first (no device recordings yet), in the exact packet format the
firmware will send.

## Scope
In: synthetic IMU generator (Python, ground truth from the Phase 2 motion), packet format spec, Rust IMU processing
(`src/imu/` lib module + `src/bin/oso-imu.rs` CLI): decode, de-duplicate/reorder/gap handling, unwrap counters,
calibration (gyro bias), Madgwick, time sync, video yaw correction, racquet pose, Rust replay writer; viewer racquet
mesh + track playback.
Out (do NOT build): firmware, live streaming/UDP receiver, magnetometer fusion, swing detection/classification (Phase 6),
shuttle (Phase 5), LED-flash detection in video (synthetic gives the clap time; real detection gets its own spec),
racquet-head trail, racquet-speed overlay (Phase 6).

## Design

### Packet format v1 (document in `docs/data-format.md`; firmware must match)
Little-endian, 18 bytes: `u16 seq`, `u32 t_us` (device `micros()`), `i16 ax, ay, az, gx, gy, gz` (raw MPU-9250 counts,
sensor axes). Scales: accel ±16 g -> 2048 LSB/g, gyro ±2000 dps -> 16.4 LSB/(deg/s). Session file `imu.bin` = a
16-byte header (`OSOI`, u16 version=1, u16 rate_hz, u32 mount_id/reserved, u32 reserved) + concatenated packets
in arrival order. Magnetometer omitted (optional later as packet v2).
- `seq` wraps every 65536 packets (131 s at 500 Hz) and `t_us` every ~71.6 min: unwrap both.
- Packets may be lost, duplicated, or reordered: sort by unwrapped seq, drop duplicates, keep gaps as gaps.

### Synthetic IMU — `tools/synth_imu.py`
- Ground truth racquet pose from `out/synth/pose_gt.npz` right wrist + forearm of the player with the IMU (P0 by default):
  butt position = wrist + 0.06 m along the forearm direction; handle axis = forearm direction rotated by a wrist-cock
  angle that goes from ~60 deg (backswing, cocked) to ~0 deg at the forward-swing peak (contact) and ~-20 deg in
  follow-through; plus pronation roll around the handle (~90 deg over the forward swing). Upsample to 500 Hz with cubic
  splines (positions and rotation vectors relative to frame 0 — keep it smooth; quaternion SLERP/SQUAD fine).
- 2 s stationary start (racquet held still), then a "clap": a sharp tap = accel spike (±12 g, 4 ms) at a known video
  time `t_clap_video`, written to `out/synth/sync.json` as the video-side annotation.
- Contact impacts at each forward-swing peak: accel spike (~8-16 g, 3 ms) + a short gyro ringing (~200 Hz decaying, small).
  Their true video times go in `pose_gt.npz` extra key `contact_t` (video clock).
- Sensor model: mounting rotation (small fixed rotation, ~3 deg, known in GT only), gyro bias (random ~1-3 deg/s per
  axis, constant), gyro noise 0.05 deg/s/√Hz-ish, accel noise ~0.01 g, accel = specific force (a - g) in sensor frame,
  **saturation**: clip at ±2000 dps and ±16 g, quantize to int16 counts.
- Device clock: `t_us = (t_video - offset) * (1 + drift) * 1e6 + jitter`, offset = 3.217 s (device booted earlier),
  drift = +40 ppm, jitter ±30 µs. Start seq at 65000 to exercise wrap.
- Transport faults: drop 1 % of packets (some in bursts of 5-20), duplicate 0.2 %, reorder within windows of up to 8.
- Writes `out/synth/imu.bin`, `out/synth/imu_gt.npz` (t_video, q_world_racquet, w, mount, bias, offset, drift),
  `out/synth/sync.json` (`{"t_clap_video": ...}`).
- The synthetic smash: if the GT angular velocity exceeds 2000 dps, saturation happens by design (real smashes reach
  ~1700-2500 dps at the handle). Print how many samples saturated.

### Rust processing — `src/imu/` (pure, unit tested; compiles for wasm but only used natively)
- `packet.rs`: parse header + packets, unwrap seq/t_us, sort/dedupe, produce `Vec<Sample { t_dev: f64, acc: [f32;3] (m/s²),
  gyro: [f32;3] (rad/s), saturated: bool }>` plus gap list. Reject malformed sizes with errors, not panics.
- `fusion.rs`: Madgwick 6-axis (beta tunable, default 0.04), quaternion integration with variable dt (gaps: if dt > 50 ms,
  re-initialize tilt from accel and mark orientation untrusted until video correction re-anchors yaw).
  Gyro bias = mean gyro over the first stationary window (detected: |gyro| < 3 deg/s and | |acc| - g | < 0.05 g for >= 1 s).
  During saturation the integration is wrong by an unknown amount; mark those samples and the following 0.5 s as low
  confidence (conf 0.5) — the video yaw correction pulls it back.
- `sync.rs`: model `t_video = t_dev * (1 + a) + b`.
  1. `b` from the clap: detect the first accel spike > 6 g after the stationary window; `b = t_clap_video - t_dev_spike`.
  2. Impact refinement: detect impact spikes (> 5 g, < 6 ms wide) in the IMU; video-side contact proxy = time of right-wrist
     speed peak per swing from the replay's joints3d (Phase 2; real shuttle-direction-change contact comes in Phase 5).
     Match each IMU impact to the nearest video contact within ±100 ms, robust linear fit (least squares, drop residuals
     > 3x MAD, refit) for `a` and `b`.
  3. Report: clap-only offset, fitted offset, drift ppm, per-impact residuals (ms), RMS and max residual. Print a table.
  - ponytail comment: the wrist-speed-peak proxy is biased vs true contact by a few ms; Phase 5 swaps in shuttle contact.
- `correct.rs`: video yaw correction. Accel already fixes tilt; only yaw drifts. Each video frame where forearm joints are
  conf 1: heading of the handle axis projected on the floor vs heading of the forearm projected on the floor; the
  difference contains the (unknown, slowly varying) wrist yaw + drift. Low-pass it with time constant 4 s and apply as
  a yaw-only correction. Ponytail comment: forearm heading is only a proxy for racquet heading (wrist deviation); fine
  for drift, wrong for fast wrist yaw, which the IMU carries.
  Mounting rotation: estimate as the constant part of the same comparison over the whole clip (yaw only); tilt mount
  errors are ignored (ponytail comment, ~3 deg).
- `pose.rs`: per video frame: position = right wrist + 0.06 m along the forearm (same model as the generator — note in a
  comment that this is the model, not a measurement), orientation = corrected IMU quaternion at the synced time (SLERP
  between samples), conf = min(video wrist conf, IMU conf). Racquet head speed (m/s) at the head center (0.55 m from butt
  along the handle) = |v_butt + w x r|, computed from IMU angular velocity + video butt velocity — stored for Phase 6.

### Replay
- New track `racquet`: `f32 [F, P, 9]` = `px, py, pz, qx, qy, qz, qw, conf, head_speed`. `P` = player slots; players without
  an IMU have conf 0. Racquet local frame: +Y along the handle from butt to tip, +Z normal to the string bed, origin at the
  butt (where the IMU sits). Document it.
- New track header field `sync`: `{ "offset_s": b, "drift_ppm": a*1e6, "residual_rms_ms": .., "residual_max_ms": .., "n_impacts": .. }`.
- Rust replay writer `Replay::write` / `write_tracks` (mirror `tools/replay.py`; 16-byte padding rules) used by
  `oso-imu` to rewrite `assets/replay/replay.bin` with the new track added (existing tracks copied byte-identical).
  Round-trip unit test Rust write -> Rust parse, and Python `tools/replay.py read` reads the Rust-written file in the
  Python test.

### CLI — `src/bin/oso-imu.rs` (std::env::args, no clap)
`cargo run --release --bin oso-imu -- --imu out/synth/imu.bin --replay assets/replay/replay.bin --sync out/synth/sync.json --player 0 -o assets/replay/replay.bin`
Prints: packets read / lost / duplicate / reordered, saturation count, bias, sync table + RMS/max ms, yaw correction
magnitude stats.

### Viewer
- Blocky racquet mesh (`src/scene/racquet.rs`): grip box 0.028 x 0.20 x 0.028 from the butt, thin shaft 0.008 x 0.25,
  head = 12-segment oval loop of thin boxes (0.23 m wide x 0.29 m long, centered 0.55 m from butt), all one mesh, origin at
  the butt, +Y along the handle. Color: dark gray frame, grip white; no strings (or one very thin dark quad — optional).
- Per frame: sample the `racquet` track (interpolate position linearly, orientation SLERP), set the racquet entity's
  Transform; hidden when conf 0. Parse the track in `Replay` (unknown-track tolerance stays).
- Racquet is rigid, uniform scale 1 — no normal-matrix issue.

### Python
- `tools/test_imu.py`: runs synth_imu -> oso-imu (via subprocess `cargo run --release --bin oso-imu`) -> reads replay ->
  compares to `imu_gt.npz`.

## Files
- `tools/synth_imu.py`, `tools/test_imu.py` — new; `tools/synth_motion.py` — add `contact_t`; `tools/README.md`.
- `docs/data-format.md` — packet v1, imu.bin header, racquet track, sync header, racquet local frame.
- `src/imu/{mod,packet,fusion,sync,correct,pose}.rs` — new; `src/lib.rs` — `pub mod imu` (doc(hidden) ok, like replay).
- `src/bin/oso-imu.rs` — new.
- `src/replay.rs` — writer, racquet track parse, `racquet(frame, player)` / sample.
- `src/scene/racquet.rs` — new mesh; `src/state.rs` — spawn + update racquet; `src/scene/mod.rs`.
- `assets/replay/replay.bin` — regenerated with the racquet track.

## Acceptance
Python `tools/test_imu.py` (seed 0):
- Packet accounting matches the injected faults exactly (lost/dup/reordered counts) and seq wrap is handled.
- Sync: fitted offset within 2 ms of truth, drift within 10 ppm of truth; residual RMS < 5 ms (report it; the wrist-speed
  proxy limits this).
- Orientation error (angle between estimated and true racquet orientation, conf 1 frames): median < 5 deg, p95 < 12 deg;
  during the 0.5 s after saturation report separately (no threshold, just print).
- Without the video yaw correction (flag `--no-video-yaw`), yaw error after 10 s is larger than with it (shows correction works).
- Gyro bias estimate within 0.2 deg/s per axis.
Rust: build, wasm build/clippy --lib, `./web/build.sh`, clippy --all-targets -D warnings, `cargo test`. Unit tests: packet
parse/unwrap/dedupe/reorder (hand-built byte vectors incl. seq wrap 65535->0 and t_us wrap), malformed input errors,
Madgwick: stationary level sensor stays level; constant rotation about Z integrates to the right angle; sync fit recovers a
known a/b from synthetic pairs with one outlier; replay write/parse round-trip; racquet mesh bounds (length 0.675 m ± 1 cm).

## Benchmarks
- Packet decode + reorder for 10 min at 500 Hz (300k packets).
- Madgwick update (per sample) and full fusion over 300k samples.
- Viewer: racquet track sample per frame.

## Visual check
1. Run the commands in `tools/README.md` (synth_imu, then oso-imu). Read the printed sync table: offset, drift ppm, per-impact
   residuals in ms.
2. `cargo run --release`: P0 (orange) holds a blocky racquet in the right hand. On each swing the racquet cocks back, whips
   through and rolls (pronation) at contact. It never detaches from the hand or spins wildly.
3. Slow to 0.1x (`[`) through a swing: the racquet face turns over through contact.
4. Toggle `K`: the racquet butt sits at the wrist cross.
5. Web: same after `./web/build.sh`.

## Accuracy notes (tell the user)
- Gyro saturates at 2000 dps; smashes exceed it at the handle. Orientation during and right after a saturated smash is
  degraded until video yaw correction and gravity pull it back. A gyro with a ±4000 dps range would remove this
  (check current part datasheets before choosing one).
- Yaw is unobservable from a 6-axis IMU; video forearm heading is only a proxy. Roll about the handle comes from the
  gyro only (the video can't see it).
- Racquet position is a wrist-offset model, not measured: grip changes (short grip on drops) shift the racquet by cm.
- Sync is limited by video frame time (16.7 ms at 60 fps) and the contact proxy until Phase 5 provides shuttle contact.
