# Volante Twins: the style profile on the phone (for the app developer)

The app computes the same `volante-style/1` profile as `oso-rec profile` (Rust, `crates/oso-replay/src/imu/profile.rs`) from the
racquet recordings it already holds, and exports `my_style.json` (the player trains a twin with it on their computer:
`oso-arena train --style my_style.json`). The file format and rules are `arena/contract/STYLE_PROFILE.md`; the schema is
`arena/contract/style.schema.json`. Raw sessions never leave the phone; only this file is shared.

## Input
Per recording (an imu.bin / one BLE download; docs/data-format.md): packets of `seq u16, t_us u32, ax ay az gx gy gz i16`, 500 Hz.
Unwrap `t_us` against the previous packet (32-bit wrap), sort by `seq`, drop duplicate `seq`. Gyro: `dps = raw / 16.4`; a sample is
**saturated** when any gyro raw value is 32767 or -32768. Several recordings (parts, sessions) are separate inputs: each has its own
clock, and nothing (no swing gap) spans two of them.

## Algorithm (the constants are the contract)
1. **Racquet frame.** Rotate the gyro vector into the racquet frame with the mount (rows = racquet X, Y, Z axes in the sensor frame; the default
   "flat" mount: racquet X = sensor Y, racquet Y (shaft) = sensor Z, racquet Z = sensor X). Only the two components perpendicular to the shaft are needed:
   racquet X and Z. With the flat mount `perp = hypot(sensor gx, sensor gy)`.
2. **Swing detection** (same as the pod's swing counter, `swingdet.rs`), threshold **300 dps**, on the gyro norm `n` of all 3 axes (any frame):
   keep the samples of the trailing **20 ms** (drop those with `t_now - t >= 0.020`; the new sample is included) and let `v` be their mean `n`.
   Armed at start. When armed and `v >= 300`: a swing starts at this sample's time, disarm. When disarmed: re-arm if `v < 150` and at least
   **0.8 s** passed since the last swing start.
3. **Head speed** of a swing starting at `t0`: `max` over samples with `t0 <= t < t0 + 0.8` (including the start sample) of
   `perp` (rad/s) `* 0.53 * 3.6` (km/h). The swing is **clipped** if any of those samples is saturated.
4. **Tempo.** For consecutive swing starts of one recording, `gap = t[i+1] - t[i]`; keep gaps `<= 6.0 s` (a longer gap ends the rally).
5. **Statistics.** Percentile = linear interpolation between sorted values (`pos = q * (n - 1)`, numpy default).
   `speed.p50_kmh`, `p90_kmh` = percentiles 0.5, 0.9 of the swing speeds; `speed.cv` = population standard deviation / mean of the **power swings** only, those with speed `>=` the unrounded p50 (0 when mean is 0);
   `speed.clipped_frac` = clipped swings / swings. `tempo.median_gap_s`, `p25_gap_s`, `p75_gap_s` = percentiles 0.5, 0.25, 0.75 of the kept gaps.
   `source.minutes` = sum over recordings of (last sample time - first sample time) / 60. `source.swings` = number of swings, `sessions` = number of sessions.
6. **Rounding.** Round every real number to **3 significant digits** (`x` -> round(x * 10^p) / 10^p, `p = 2 - floor(log10 |x|)`, half away from zero).
7. **Fields.** `schema` "volante-style/1"; `created_at` the creation time in UTC `YYYY-MM-DDTHH:MM:SSZ`; `pod_fw` the pod firmware version
   (letters, digits, `. _ -`; "unknown" if not known); `tool` e.g. "oso-app 1.2.0"; `stroke_mix` and `side_mix` null; `notes` the fixed text
   "speeds are estimates (gyro x 0.53 m) and lower bounds when clipped". Key order as in the spec's example.
8. **Refuse** (tell the player to play more) when there is no swing, or no two swings within 6 s. Validate the result with the rules of
   STYLE_PROFILE.md section 1 before saving; the file is at most 8192 bytes (it is about 600).

The gyro bias is not removed (it is a few dps against swings of hundreds). The head speed is an estimate; it is a lower bound where the sensor clips (+-2000 dps per axis).

## Test vectors
`arena/kit/tests/data/twins_vector.imu.bin` (8 swings, identity mount) must produce exactly `arena/kit/tests/data/twins_vector.profile.json`
with `created_at` "2026-10-17T18:00:00Z" and `tool` "oso-rec 0.1.0": `p50_kmh` 33.3, `p90_kmh` 60.3, `cv` 0.409, `clipped_frac` 0.125,
`median_gap_s` 2.0, `p25_gap_s` 1.81, `p75_gap_s` 2.34, 8 swings, 0.342 minutes (the 7.9 s gap between swing 5 and 6 starts a new rally, so there are 6 gaps).
With the **flat** mount, a recording with 1000 dps about the sensor X axis for a 100 ms half-sine has `perp` peak 1000 dps = 33.3 km/h.
Both validators (`oso_arena.style.validate_profile`, Rust `profile::validate`) accept the JSON; an implementation should also reject
the cases in `arena/kit/tests/test_twins.py::test_validate_edge_cases`.

## Build the player will get
`oso_arena.style.build_from_profile` maps the profile to the build (power from `p90_kmh`, accuracy from `cv` of the power swings (0.30 -> 1, 0.05 -> 10), speed from `median_gap_s`,
reach the remainder): the app can preview it with the table in STYLE_PROFILE.md section 3 ("You hit hard and steady: power 8, accuracy 8").
