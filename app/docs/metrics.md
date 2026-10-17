# Metrics: definitions, formulas, units, sources

Abbreviations: **ω** = bias-corrected angular rate (sensor/racquet frame), **|ω|** its magnitude, **a** = specific
force from the accelerometer, **g** = 9.80665 m/s². "Port of X" means the TypeScript reproduces the Rust reference in
`context/imu-reference/` (tests in `test/` reproduce the Rust unit tests). **[est]** = heuristic, labelled
"estimate" in the UI.

Racquet frame: origin at the butt (the sensor), **+Y along the handle toward the tip, +Z = string-bed normal**,
+X across the head. Court/world frame: **+Y up**. The sensor axes are assumed to coincide with the racquet axes
(a ~3° mount error is ignored, as in the pipeline).

## Processing chain

| Step | What | Source |
|---|---|---|
| Decode | counts → a [m/s²] = c·g/2048, ω [rad/s] = c·(π/180)/16.4; `saturated` = a gyro axis at ±32767/−32768 | `core/packet.ts` ← `packet.rs parse` |
| Unwrap / sort / dedupe | seq and t_us unwrapped against the previous arrival with signed 16/32-bit differences; stable sort by seq; first arrival kept; gaps kept | `packet.rs` |
| Accel saturation (app addition) | an accel axis at ±32767/−32768 (±16 g) | `toSample` |
| Stationary window | first run of ≥ 10 blocks of 50 samples (1 s) where every axis has gyro std < 2 dps, \|mean gyro\| < 6 dps, accel std < 0.05 g, and \|mean a\| within 0.05 g of 1 g; the last block of the run is dropped | `fusion.ts findStationary` ← `fusion.rs` |
| Gyro bias | mean gyro over the stationary window | `gyroBias` ← `fusion.rs gyro_bias` |
| Orientation (saved sessions) | Madgwick 6-axis, β = 0.04, trapezoid rate; accel used only within ±0.1 g of 1 g; holes > 50 ms re-initialise tilt and keep heading; shorter holes integrate a cubic-Hermite rate in nominal sub-steps; **forward + backward** pass blended by `sigmoid(2(since−until)/1 s)` from disturbances (saturation, holes of 4+ packets) | `fusion.ts run` ← `fusion.rs run` |
| Orientation (live) | the same forward pass sample by sample. The only difference: after a hole the Hermite end slope is 0, because the next sample hasn't arrived yet | `LiveFusion` |
| Orientation confidence | 0.5 when both passes are within 0.5 s of a disturbance (live: within 0.5 s after one), else 1 | `fusion.rs` |
| Spikes | per-sample deviation of each accel axis from its 9-sample median, combined as a vector norm in g; 0 when the window spans > 19 ms. A spike = run above threshold ≤ 3 samples wide; time = strength-weighted centroid including one flank sample each side | `core/spikes.ts` ← `sync.rs spike_strength/spikes` |

## Connection & device (Device page)

| Metric | Unit | Definition |
|---|---|---|
| Device name/id, connection state | — | from the transport |
| RSSI | dBm | `readRssi` every 2 s (native only; Web Bluetooth: "—") |
| MTU | bytes | negotiated ATT MTU (native only); packets/notification = ⌊(MTU−3)/18⌋ |
| Firmware, sensor ranges, configured rate, recording state, buffered bytes, upload progress | — | `status` characteristic (docs/ble-profile.md) |
| Battery | % | Battery Service 0x2A19, else `status.battery_pct` |
| Measured sample rate | Hz | (unique + lost − 1) / (t_last − t_first) on the device clock |
| Packets received | count | arrivals (incl. duplicates), unique, notifications |
| Lost | count, % | seqs missing between the first and the highest seen so far; % = lost / (unique + lost). A late packet fills its hole back in |
| Duplicated | count | a seq seen before |
| Reordered | count | a first-seen seq lower than one already seen (+ how many arrived too late for the live 8-packet reorder buffer; they're still in the saved file) |
| Current gap | ms, packets | time since the last notification; size of the most recent hole |
| Link latency jitter | ms | std of notification inter-arrival times (last 200); the Session page also shows the std of arrival latency around the clock fit |

## Sensor health / calibration

| Metric | Unit | Definition |
|---|---|---|
| Gyro bias x/y/z | dps | mean gyro over the stationary window (live: the first 10 still blocks; saved: the full first still run, as in `fusion.rs`) |
| When estimated | time | host time at estimation (live) + device-time range of the window |
| Stationary indicator | — | `blockIsStill` on the latest 50 samples (the same rule as calibration) |
| \|a\| at rest | g | \|mean a\| over the stationary window (should be ≈ 1 g) |
| Saturation counters | count | samples with a gyro / accel axis at the int16 rail |
| Noise at rest | dps, g | per-axis std of bias-removed gyro and of accel over the stationary window |
| Mounting check | ° | "hold racquet flat, face up" → mean accel over 1 s; handle tilt = asin(a_y/\|a\|), face tilt = 90° − asin(a_z/\|a\|); fails if any sample deviates > 0.1 g. The Session page shows the same tilt for the stationary window |
| Clock offset / drift | s, ppm | per 1 s of device time keep the notification with the smallest (host − t_dev) (lower envelope = least queuing), least-squares line host = t_dev(1 + a) + b; drift = a·10⁶. Only for live-streamed sessions (uploads/imports have no arrival times) |

## Live view

| Metric | Unit | Definition |
|---|---|---|
| a x/y/z, \|a\| | g | latest sample / g |
| ω x/y/z, \|ω\| | dps | latest sample minus bias (bias = 0 until calibrated, marked) |
| Rolling charts | — | last 5 s of in-order samples, min/max decimated per pixel, redrawn at ≤ 30 fps |
| Quaternion | x, y, z, w | sensor → court (+Y up), live forward Madgwick |
| Euler | ° | yaw = atan2(h_x, h_z) of the handle h = q·(0,1,0) (**drifts**); pitch = asin(h_y); roll = angle of the face normal about the handle measured from the "up-most" perpendicular |
| Orientation widget | — | wireframe racquet rotated by q (2D canvas; not the replay renderer) |
| Swing indicator | — | swing state machine active + elapsed ms |
| Last swing card | — | the per-swing metrics below, computed live when the swing ends |

## Per swing (Session table + Swing page)

Swing detection (`core/swings.ts SwingDetector`, the PROMPT contract): start when |ω| > **300 dps**; end at the start
of the first run where |ω| < **120 dps** for **80 ms**; discard swings shorter than **80 ms**. Between 120 and
300 dps nothing changes state (hysteresis). All thresholds are in Settings.

| Metric | Unit | Formula / rule |
|---|---|---|
| index, start time | —, s | 1-based; start relative to the first sample (device time also shown) |
| duration | ms | t_end − t_start |
| peak \|ω\| | dps, rad/s | max \|ω\| in the swing |
| forward-swing peak | dps | max \|ω\| in the 150 ms before the impact (no impact: = peak) |
| backswing / forward / follow-through | ms | **Deviation from the PROMPT, on purpose:** in the sample data the overall \|ω\| peak falls in the *backswing* (≈ 1600 dps, then a valley, then the forward peak ≈ 1400 dps at contact), so splitting at the overall peak would put the whole forward swing in the "backswing". The split points are therefore (1) the **\|ω\| valley** between the backswing lobe and the forward-swing peak (needs a dip below 60 % of both peaks) and (2) the **impact** (no impact: the forward peak). No valley → backswing "—" and the start-to-impact time counts as forward swing |
| racquet-head speed **[est]** | m/s, km/h (unit setting) | peak \|ω\| [rad/s] × 0.53 m (head-center distance setting). **Rotation-only, excludes arm translation, underestimates.** Lower bound on saturated swings |
| peak acceleration | g | max \|a\| / g in the swing (accel-saturated swings flagged) |
| impact detected | y/n | an impact spike (> **5 g**, ≤ 3 samples, from `find_spikes`) inside [t_start, t_end + 20 ms]; claps excluded; strongest one wins |
| impact strength | g | peak spike strength (deviation from the 9-sample median) |
| impact vs peak **[est]** | ms | t_impact − t_forward-peak; early < −10 ms ≤ on peak ≤ +10 ms < late |
| handle tilt | ° | asin((q·ŷ)_y) at impact (no impact: at the forward peak); + = tip above the butt |
| face angle | ° | asin((q·ẑ)_y): 0 = face vertical, 90 = face pointing up |
| overhead / underarm | — | handle tilt > 0 → overhead |
| forehand / backhand **[est]** | — | sign of ω_z (about the face normal) at the forward peak × handedness (right: + = forehand); \|ω_z\| < 50 dps → "—". **The sign convention was set from the synthetic data (all forehands) and must be checked on real recordings** |
| stroke type **[est]** | — | no impact → "no contact". Overhead: smash if saturated or ≥ 15 m/s, clear ≥ 10 m/s, else drop. Not overhead: tilt > −25° → drive (≥ 9 m/s) or net; tilt ≤ −25° → lift (≥ 8 m/s) or net. Speeds are rotation-only (they top out near 18.5 m/s at gyro saturation, so thresholds are on that scale). "heuristic, classifier comes later" |
| saturated | y/n, count | gyro-saturated samples in the swing (accel-saturated counted separately) |
| confidence | 0–1 | 1 − min(0.4, 0.02 × lost packets in the swing) − 0.2 if a hole of 4+ packets touches the forward peak/impact (±30 ms) − 0.2 if saturated − 0.1 if orientation confidence < 1 at impact; floor 0.1. high ≥ 0.8, medium ≥ 0.5, low |

## Session summary

| Metric | Unit | Formula |
|---|---|---|
| duration | s | t_last − t_first |
| active / idle | s | active = union of [t_start − 0.5 s, t_end + 0.5 s] over swings; idle = duration − active |
| swings, hits | count | hits = swings with an impact |
| hits / minute | 1/min | hits / (duration / 60) |
| rallies | count, hits | consecutive hits < **4 s** apart (setting); a rally needs ≥ 2 hits; avg and longest in hits |
| rest periods | count, s | gaps ≥ 4 s between consecutive hits; avg, longest |
| head-speed avg / p90 / max **[est]** | m/s | over all swings; p90 by linear interpolation; 1 m/s histogram |
| peak \|ω\|, peak g | dps, g | max over swings |
| stroke breakdown **[est]** | count | per stroke type |
| forehand/backhand ratio **[est]** | count, % | swings with a side |
| overhead ratio | % | overhead hits / hits with an orientation |
| timing consistency **[est]** | ms | sample std of (impact − forward peak) over hits |
| fastest swing | link | max head speed |
| fatigue trend | chart, (m/s)/min | head speed per swing vs time; least-squares slope × 60 (≥ 3 swings) |
| load / intensity | rad per minute | Σ ½(\|ω\|ᵢ + \|ω\|ᵢ₋₁)·Δt per minute of session; not integrated across holes > 50 ms |
| data quality | %, count, ms | loss %, gaps, longest gap, gyro/accel saturations |

## Sync & replay

| Item | Definition | Source |
|---|---|---|
| Sync event (clap) | spike > **6 g**, ≤ 6 ms wide, with the racquet still in the 0.3 s before it (mean raw \|ω\| < 30 dps). sync.rs applies the stillness rule only to the end clap because it knows the video times; the app applies it to every candidate because it doesn't | `findClaps` ← `sync.rs find_clap/find_end_clap` |
| Sync event (button) | marker packet from control 0x06 | docs/ble-profile.md |
| Start / end | first / last event; the video pipeline fits `t_video = t_dev(1 + a) + b` through them | `fitClaps` ← `sync.rs fit_claps` (drift beyond 200 ppm is rejected) |

## Accuracy limits (also shown in the UI next to the values)

- The gyro saturates at 2000 dps, so peak |ω| and head speed are **lower bounds** on saturated swings ("+" in tables).
- Head speed from rotation alone **underestimates** (no arm translation; the video pipeline adds it).
- Yaw drifts with a 6-axis IMU; only tilt is absolute.
- Stroke type, forehand/backhand and timing are heuristics until a trained classifier exists.
- Packet loss during impacts is common, so swings overlapping gaps get lower confidence and may miss their impact.

## Sample-session reference values (`test/session.test.ts`, shown on the Session page)

6547 packets read, 6534 unique, 66 lost, 13 duplicate, 99 reordered, 44 gaps, 12 saturated gyro samples. Bias
(−1.820, −2.075, −1.108) dps. **4 swings, 3 impacts, max |ω| 2056.6 dps, 12 saturated samples** (all in swing 3).
3 claps detected at device 3.467 / 11.514 / 12.817 s. The first and last, fitted against `sync.json`
`[0.25, 9.6]`, give b = −3.2170 s and about −32 ppm. The generator used −3.217 s and about −40 ppm; the difference
comes from the video clap times being quantized to 60 fps frames.
