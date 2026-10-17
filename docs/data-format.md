# Data formats

## Court coordinates
Meters. Origin: court center on the floor. +Y up, +X along the length (baselines at x = +-6.70),
+Z across the width (doubles sidelines at z = +-3.05). Net plane x = 0, posts at z = +-3.05.
Defined in `crates/oso-render/src/scene/court.rs`, mirrored in `tools/court.py`.

## Landmark names
Landmarks are intersections of line **centerlines** (a line at measurement 6.70 is centered at 6.68).
Name: `<xline>_<sx>__<zline>[_<sz>]` where
- `xline` (constant x): `baseline` (6.68), `long_service` (5.92), `short_service` (1.96); `sx` is `n` (x<0) or `p` (x>0)
- `zline` (constant z): `doubles_side` (3.03), `singles_side` (2.57), `center` (z=0, no sign); `sz` is `n`/`p`
- Plus `post_top_near` (0, 1.55, -3.05) and `post_top_far` (0, 1.55, +3.05) (not on the floor; they pin the focal length).
Example: `baseline_n__doubles_side_p` = (-6.68, 0, 3.03). 32 landmarks in total.

## Points file
`{ "<landmark name>": [u, v], ... }` pixel coordinates (origin top-left), written by `pick_points.py` / `synth.py`.

## calib.json
```json
{ "version": 1, "coordinate_system": "court_m_yup_xlength",
  "cameras": [ { "id": "a", "image_size": [1920, 1080], "fps": 60.0,
    "K": [[fx,0,cx],[0,fy,cy],[0,0,1]], "dist": [k1,k2,p1,p2,k3],
    "R": [[..],[..],[..]], "t": [tx,ty,tz], "position": [x,y,z], "rms_px": 0.6, "n_points": 22 } ] }
```
`R`, `t`: court -> camera, OpenCV convention (x right, y down, z forward): `X_cam = R X_court + t`.
`position = -R^T t`. The optical axis in court coordinates is the third row of `R`.

Triangulation accuracy with the default camera layout (both cameras on the same side, 2.5 m outside the sideline, one near each baseline, aimed diagonally across the court; 0.5 px click noise): median about 0.7-1.1 cm and worst case 3.1 cm over all 32 landmarks across seeds 0..9.

Camera framing assumption: 108 deg HFOV ultra-wide lens (k1 -0.18, k2 0.03), pitched about 18.5 deg down so the frame holds every court landmark plus airspace up to H_AIR = 6.0 m over the court centerline (`tools/synth.py`).

## Skeleton: COCO-17
Joint order (all per-joint arrays below use it): 0 nose, 1 l_eye, 2 r_eye, 3 l_ear, 4 r_ear, 5 l_shoulder, 6 r_shoulder,
7 l_elbow, 8 r_elbow, 9 l_wrist, 10 r_wrist, 11 l_hip, 12 r_hip, 13 l_knee, 14 r_knee, 15 l_ankle, 16 r_ankle.
Bones for drawing: `tools/skeleton.py` `BONES`, mirrored in `crates/oso-render/src/replay.rs` (change together).

## pose2d_<camera_id>.npz (swap-in point for a real pose model)
- `kp` float32 `[F, P, 17, 3]`: u px, v px (origin top-left, distorted image), score 0..1
- `t` float64 `[F]`: seconds from the start of that camera's video; `frame` `[F]`: frame index. From real video `t` is the per-frame container
  timestamp (OpenCV `CAP_PROP_POS_MSEC`, variable-frame-rate safe; frame / fps if the container has none). Cameras need not be synchronized when a
  `camsync.json` is given (below).
- attributes `camera_id` (matches calib.json), `model` ("synthetic", later e.g. "rtmpose-m"), `keypoint_format` "coco17"
- P is the player slot: the same slot is the same person in every camera (ID association is the producer's job).
  A missing person in a frame has all scores 0.
Written by `tools/synth_motion.py` (with ground truth `pose_gt.npz`: `joints [F, P, 17, 3]`, `t`, `swing_player`, `swing_t0`).

## replay.bin v1 (written by `tools/pose3d.py` / `tools/replay.py`, read by `crates/oso-render/src/replay.rs`)
All numbers little-endian.
```
"OSOR" (4) | u32 header_len N | N bytes UTF-8 JSON, zero-padded so that (8 + N) % 16 == 0 | data blob
```
Header: `{"version":1, "fps":60.0, "frame_count":F, "start_time":0.0, "coordinate_system":"court_m_yup_xlength",
"tracks":[{"name":"joints3d", "dtype":"f32", "shape":[F,P,17,4], "offset":0, "layout":"x,y,z,conf",
"keypoint_format":"coco17", "players":["p0","p1"]}]}`
- `frame_count` is the first dimension of a **per-frame** track (`joints3d`, `racquet`, `shuttle`). A track whose header has `"per_frame": false` (`events`) has its own length: readers must not take frame_count from it, and writers must not take it from the first track blindly (`tools/replay.py` skips `per_frame: false` tracks).
- `offset` is in bytes from the start of the data blob and a multiple of 16 (tracks are zero-padded to 16).
- Readers must ignore unknown tracks (`shuttle` and `events` came in Phase 5; later phases add more). Written by `tools/replay.py` and, for `oso-imu`, by `Container` in `crates/oso-render/src/replay.rs` (same padding rules; all existing tracks are copied byte-identical).
- `match` (optional top-level header object, Phase 14.4, written by `arena/kit/oso_arena/animate.py` for an Arena match, read by `Replay::match_info()`; files without it stay valid and unknown header keys are ignored):
  `"match":{"a":{"name":"Model A","build":{"power":9,"speed":6,"reach":5,"accuracy":4}}, "b":{...}, "ends":[[0.0,0],[412.3,1]]}`.
  A and B are the two submissions of the match. The `joints3d` player slots (and `events` player, `point` score order) follow the **world sides**
  (slot 0 plays on -X), so after a change of ends slot 0 holds the other model. `ends` is the list of change points `[t_seconds, s]`, ascending,
  first at t = 0: from `t` on, slot 0 is model A (`s` = 0) or model B (`s` = 1), so `model = slot XOR s` for the last entry with `t <= playback time`.
  A change takes effect one frame after the last point event of the rally before it. At most 4 changes per match. Viewers that know `match` (Arena) colour
  by model and show the score as `[A, B]`; without it (the Replay app, old files) everything stays by slot.
- `joints3d`: court meters; conf 1.0 triangulated, 0.5 gap-filled, 0.0 missing (position written as 0).
- Pipeline: both scores >= 0.3 -> triangulate; reject reprojection error > 12 px in either view, implausible points,
  and temporal outliers (> 15 cm from the +-3 frame median); fill gaps <= 12 frames with a cubic spline; zero-phase
  order-2 Butterworth per contiguous run of >= 15 frames (6 Hz body, 16 Hz elbows/wrists).

## racquet track (Phase 4, added by `oso-imu`)
`{"name":"racquet", "dtype":"f32", "shape":[F,P,9], "offset":..., "layout":"px,py,pz,qx,qy,qz,qw,conf,head_speed",
"players":["p0","p1"], "sync":{"offset_s":b, "drift_ppm":a*1e6, "drift_known":true, "residual_rms_ms":.., "residual_max_ms":.., "n_impacts":..}}`
- Per frame and player slot: position of the **butt** of the handle in court meters, orientation quaternion **x, y, z, w**
  (the racquet frame -> court frame; cgmath's `Quaternion::new` takes w first), `conf` (0 missing, 0.5 low: within 0.5 s of both a gyro saturation / lost-packet
  hole in the forward and the backward fusion pass, 1.0), `head_speed` (m/s at the head center, 0.53 m from the butt, from IMU angular rate plus video butt velocity).
  Players without an IMU are all zero (conf 0).
- Racquet local frame: origin at the butt (where the IMU sits), **+Y along the handle from butt to tip, +Z normal to the
  string bed**, +X across the head (right-handed). The IMU's sensor axes are taken to coincide with it (mount error ~3 deg ignored).
- Position is a model, not a measurement: right wrist + 0.06 m along the forearm (elbow -> wrist).
- `sync`: the fit `t_video = t_dev * (1 + a) + b` used to place IMU samples on the video clock (`offset_s` = b, in seconds;
  negative when the device booted before the video started). `drift_known` is false with a single clap. Residuals are of the IMU-impact to video-contact-proxy matches against the clap fit (a check, not part of the fit).

## swings track (Phase 6, added by `oso-imu`)
`{"name":"swings", "dtype":"f32", "shape":[N,16], "per_frame":false, "layout":"t_start,t_peak,t_end,t_contact,player,stroke_type,peak_omega_dps,head_speed_peak,head_speed_rot,impact_g,timing_ms,contact_h,launch_speed,launch_angle_deg,saturated,confidence", "stroke_types":["unknown","serve","clear","smash","drop","drive","lift","net","swing_only","block","push","press","net_kill","flick_serve"]}`
One row per stroke, sorted by `t_peak`; times in video seconds. `stroke_type` is an index into `stroke_types`. Not per frame: N has nothing to do with `frame_count`.
- Rows with an IMU swing: `t_start, t_peak, t_end` are the swing window, `peak_omega_dps` is |omega| (bias removed) at the peak, `head_speed_rot` = peak |omega| (rad/s) x 0.53 m,
  `head_speed_peak` the max `racquet` track `head_speed` (IMU rotation + video translation) over the window (NaN if conf 0 throughout), `impact_g` / `timing_ms` (t_impact - t_peak) NaN when no impact spike,
  `saturated` 0/1, `confidence` 1.0 or 0.5 (saturation, or a lost-packet gap of >= 4 packets in the window).
- Rows with a shuttle contact: `t_contact`, `player`, `contact_h` (m), `launch_speed` (m/s), `launch_angle_deg` (+ up) from the outgoing flight (NaN if the flight was not found).
  A contact with no IMU swing (the other player, or an IMU gap) is **contact-only**: `t_start/t_peak/t_end` = `t_contact` -0.15 / 0 / +0.15 s placeholders, IMU columns NaN, `saturated` 0, `confidence` 0.
- Air swing (IMU swing with no contact within its window +- 60 ms): `stroke_type` = `swing_only`, `t_contact`, contact and launch columns NaN.
- Swing detection (`crates/oso-replay/src/imu/swings.rs`, same contract as the BLE app spec): start when |omega| > 300 dps; end when |omega| < 120 dps for 80 ms; swings shorter than 80 ms are discarded.
  Impact = a narrow accel spike (> 5 g from the 9-sample median, <= 3 samples) inside the window or within 60 ms after its end (the strongest one).
  **Peak rule**: a window can hold backswing, forward swing and follow-through when |omega| never dips below 120 dps between them, so the plain maximum can be the backswing. Runs = contiguous regions with |omega| >= 50 % of the window maximum.
  With an impact: the forward swing is the run containing the impact time, else the last run that ends before it (else the first run). Without an impact (air swing): the latest run whose maximum is >= 75 % of the window maximum.
  The peak is the highest |omega| in that run, parabola-refined between samples; the forward swing starts where that run starts; `timing_ms` = t_impact - t_peak. A single-lobe swing gives the plain maximum.
  Known ambiguity: with no impact, a follow-through lobe at >= 75 % of the window maximum (e.g. lobes 900 / 800 / 700) is picked over the forward swing.
- Stroke label rules (`crates/oso-replay/src/imu/strokes.rs`; hand-picked, to be replaced by a trained classifier; first match wins). Inputs: contact height h, |x| of the contact, launch speed v0, elevation angle, apex, |x_end| of the outgoing flight (its next contact or landing):

| # | type | rule |
|---|---|---|
| 1 | serve | first contact of a rally (none in the previous 3 s) and h < 1.15 m |
| 2 | smash | h > 2.0 m, angle < -5 deg, v0 > 25 m/s |
| 3 | net | \|x_contact\| < 2.2 m, apex < 2.3 m, \|x_end\| < 2.5 m |
| 4 | drop | h > 1.8 m, \|x_end\| < 3.2 m, v0 < 20 m/s |
| 5 | clear | h > 1.8 m, apex > 4.5 m, \|x_end\| > 4.5 m |
| 6 | lift | h < 1.2 m, apex > 3.5 m |
| 7 | drive | 0.6 < h < 1.9 m, apex < h + 0.6 m, v0 > 12 m/s |
| 8 | unknown | otherwise |

- Rust: `Replay::strokes()`, `next_stroke(t)` / `prev_stroke(t)` (seek time: 0.4 s before the contact, or the swing start for an air swing). Web: `oso_strokes()`, `oso_stroke_types()`, `oso_head_speed(player)`.
- Without an IMU (Phase 11): `oso-imu --replay R --no-imu -o out.bin` runs the same builder with no swings, so every `events` contact becomes a contact-only row
  (placeholders around the contact, IMU columns NaN, confidence 0) with the rule label; no `racquet` track is written. `classify_strokes.py` then relabels it.

## Match stats (Phase 11, `crates/oso-replay/src/stats.rs`)
Pure function of a replay's `swings` rows with a contact; `oso --stats replay.bin` prints it (pretty JSON), the web page reads it from `oso_stats()`.
`{"players":[{"hits", "points" (rallies won from `point` events, else null), "types":{name:count}, "launch_speed_max/mean", "head_speed_max/mean" (null without IMU rows), "contact_height_mean",
"fastest":{"speed","t","type"} | null, "positions":[{"t","x","z","type"}]}], "rallies":{"count","shots_mean","shots_max","duration_mean","duration_max",
"longest_start","list":[{"start","end","shots","duration"}]}, "fastest_by_type":{name:{"speed","t","type"}}}`.
Speeds m/s (launch speed = the swings `launch_speed` column), `t` s, `x`/`z` court m. Position = ankle midpoint (conf > 0) at the nearest frame, else hip center,
else the hit has no position. A new rally starts when two consecutive contacts are more than `RALLY_GAP_S` = 3.0 s apart (same as `classify_strokes.py`).
Means/maxes skip NaN; players with no data give 0 / null.

## imu.bin (racquet IMU session; firmware must match)
All numbers little-endian. 16-byte header, then packets in **arrival order**:
```
"OSOI" (4) | u16 version = 1 | u16 rate_hz | u32 mount_id (reserved, 0) | u32 reserved (0)
packet, 18 bytes: u16 seq | u32 t_us | i16 ax | i16 ay | i16 az | i16 gx | i16 gy | i16 gz
```
- `t_us` = device `micros()` at the sample; `seq` increments by 1 per sample. Raw MPU-9250 counts in the **sensor axes**
  (the board is mounted so sensor +Y runs along the handle toward the tip and +Z is the string-bed normal).
- Scales: accel +-16 g -> 2048 LSB/g, gyro +-2000 dps -> 16.4 LSB/(deg/s). A gyro axis at -32768 or +32767 is saturated.
- `seq` wraps every 65536 packets (131 s at 500 Hz) and `t_us` every 2^32 us (~71.6 min): readers unwrap both against the
  previous packet (signed 16/32-bit difference). The first packet's values are taken as-is.
- Packets may be lost, duplicated or reordered (reorder within a few packets): readers sort by unwrapped `seq`, drop repeats,
  keep gaps as gaps. Accounting: `lost` = seqs missing between the first and last received, `duplicate` = a seq seen before,
  `reordered` = a first-seen seq lower than one already seen.
- A file whose body is not a whole number of 18-byte packets is rejected. No magnetometer (would be packet v2).
- The racquet-pod firmware (`firmware/`) and `oso-rec` produce this file; the BLE contract, status struct and flash log are in
  [ble-profile.md](ble-profile.md). The packet layout is defined once in `proto/` (`oso-proto`), which `crates/oso-replay/src/imu/packet.rs` decodes with.
- `imu.markers.json` (optional, written next to `imu.bin` by `oso-rec` when the device reported sync markers):
  `{"markers":[{"t_us": <device clock, u32>, "source": "tap"|"command"}, ...]}`. `t_us` is on the same clock as the packets. They
  help to find the claps; `sync.json` stays video-side.

## Time sync inputs (`sync.json`)
`{"claps_video": [t_start, t_end]}`: video times (s) of a clap (a sharp tap on the racquet, visible in video; an LED flash
works the same) at the **start and at the end** of every session, with the racquet still for about 2 s before the first and
before the second. The two claps fix the device-clock offset and drift exactly (a line through the two points); with only one
(`{"claps_video": [t]}` or the older `{"t_clap_video": t}`) the drift is unknown and reported as such (a = 0).
Drift precision is about (clap timing error) / (session length): a clap located to one 60 fps frame (16.7 ms) in a 10 min
session gives about 28 ppm; the IMU side of a clap is located to ~0.1 ms by the sample amplitudes, so video timing dominates.
Impacts (racquet-shuttle contacts) are only a check: matched IMU spikes vs the video contacts (the `events` track if present, else the wrist-speed proxy), residuals reported.
Real clap detection from the video (LED flash / audio) is a later spec. Written by `tools/synth_imu.py`.

## Synthetic IMU ground truth (`imu_gt.npz`, from `tools/synth_imu.py`)
`t_video [N]`, `q_world_racquet [N,4]` (x,y,z,w), `w [N,3]` (racquet-frame rad/s), `mount [3,3]` (sensor = racquet * mount),
`bias [3]` dps, `offset`, `drift` (device clock `t_dev = (t_video + offset) * (1 + drift)`), `a_true`, `b_true`
(`t_video = t_dev * (1 + a_true) + b_true`), `t_clap_video`, `claps_video [2]`, `impact_t_video`, `sat_t_video`, packet fault counts. `pose_gt.npz` gains
`contact_t` (video time of each forward-swing wrist-speed peak, aligned with `swing_player`). `impact_t_video` is the shuttle contacts of that player (`shuttle_gt.npz`) when it exists, else `contact_t`.

## shuttle2d_<camera_id>.npz (swap-in point for TrackNet; Phase 5)
- `uv` float32 `[F, 2]`: u px, v px (origin top-left, distorted image) of the single shuttle detection per frame
- `score` float32 `[F]`: 0..1, 0 = not detected (`uv` ignored)
- `t` float64 `[F]`: seconds from the start of that camera's video (cameras must be frame-synchronized, as for pose2d, unless a `camsync.json` is given)
- attributes `camera_id` (matches calib.json), `model` ("synthetic" now, e.g. "tracknetv3" later)
Written by `tools/synth_shuttle.py` (with ground truth `shuttle_gt.npz`: `pos [F,3]`, `vel [F,3]` (NaN outside flight), `in_flight [F]`,
`flight_id [F]`, `contacts [N,5]` = t, player, x, y, z, `landing` = t, x, y, z, `max_apex`). The simulator: noise sigma 1.5 px + 0.04 px per
(px/frame) of image speed along the motion direction, 1 px across; miss 8 % + up to 30 % with image speed (full at 60 px/frame) + 30 % within
40 px of a projected body joint; out of image = missed; 0.5 % false positives per frame (near a head/ankle, score 0.5-0.9). The shuttle is only
detected while in flight. Contact times in `shuttle_gt.npz` are what `synth_imu.py` uses for its impacts. The clear apex is 9 m (a 7 m apex still
projects inside both 108 deg views; see the ponytail note in `synth_shuttle.py`).

## shuttle and events tracks (Phase 5, added by `tools/shuttle3d.py`)
- `shuttle`: f32 `[F, 8]` = `x, y, z, vx, vy, vz, conf, flight_id` (court m, m/s). conf 1.0: a detection in at least one camera agrees with
  the fitted flight (< 6 px); 0.5: filled by the physics inside a flight; 0: outside flights (all zero, flight_id -1). Frames of one flight are
  evaluated from one drag-physics model (`tools/shuttle_physics.py`, terminal velocity 6.8 m/s), so values are exact physics between contacts.
- `events`: f32 `[N, 6]` = `t, type, player, x, y, z`; type 1 = racquet contact (player = nearest racquet head / right wrist), 2 = floor landing
  (player -1), 3 = `point` (Phase 13, written by `arena/kit/oso_arena/animate.py`): rally end, player = winner, x = winner's new score, y = the other player's score (the winner serves next), z = 0. Readers that do not know a type ignore it; `Replay::score_at(t)` gives the score at `t`; `oso --stats` and the stats panel show points won. The Arena viewer (`oso-arena`, `oso_score(t)` on its web page) draws, for a replay with point events, a 3D scoreboard in the hall (native and web); the Replay app (`oso`) parses the events but shows no score: a board hung on cables behind the far baseline (x = 9.7, z = 0, bottom edge 3.5 m) facing -x toward the default camera, orange P0 score on the left, a colon, cyan P1 score, updated from `score_at`; in Arena the board (and `oso_score`) is by **model** when the header has a `match` object (`Replay::model_score_at`: A left in orange, B right in cyan, no swap at a change of ends; each point event's slot is mapped with `ends` at its own time), by slot otherwise; Arena draws no scoreboard for replays without point events. Header `"per_frame": false`. Contact time is the closest approach of the extrapolated neighbouring flights (sub-frame).
- Rust: `Replay::sample_shuttle(t)` (linear inside a flight, never across a flight_id change), `Replay::events()`.
- `oso-imu` matches IMU impacts to the contacts of the `events` track when the replay has any for that player, else to the wrist-speed proxy.
- Pipeline order: synth_motion -> pose3d -> synth_shuttle -> shuttle3d -> synth_imu -> oso-imu (see `tools/README.md`).

## Video pipeline (Phase 9, `tools/run_video_pipeline.py`)
- `pose2d_video.py` writes the pose2d format above (`model` "rtmpose-m", `t` from the container) plus diagnostics: `foot_xz [F,P,2]` (court x, z of the
  slot's person, NaN when none), `boxes_all [F,8,4]` (every person box the detector found, NaN padded; the shuttle detector masks them),
  `detected` / `coasted` `[P]` (frames from a detection / from the tracked previous box), `rejected` (people off our court), `people`, `seconds`.
  Slots: foot point (ankles, else hips at 0.9 m, else the box bottom) back-projected to the floor; kept inside the court +-1.0 m; P0 = x < 0, P1 = x > 0.
- `shuttle2d_video.py` writes the shuttle2d format (`model` "classical-framediff"); TrackNet-style detectors write the same file.
- `camsync.json` (`tools/camsync.py`): `{"offset_b", "rate_b", "cost", "n_points", "ambiguous", "coarse_offset_b", "coarse_cost", "second_cost"}`.
  Camera B's timestamp maps to camera A's clock by `t_common = rate_b * t_b + offset_b` (seconds; `rate_b` ~ 1, 0.99996 = B's clock runs 40 ppm fast).
  `pose3d.py` / `shuttle3d.py --camsync` resample B's 2D tracks linearly to A's frame times (only between two consecutive detected frames) and the
  replay timeline is camera A's clock. `cost` is the capped (4 px) rms Sampson distance of the A/B pairs; `ambiguous` = a second minimum within 10 %
  more than 3 frames away. The IMU sync (claps) is against camera A's clock, unchanged.
- Rendered test videos: `out/video/truth.json` = `{"fps", "cameras": {id: {offset, rate, n_frames, t_render}}, "estimator": {offset_b, rate_b}}`; frame i of a
  camera is rendered at `offset + i / (fps * rate)` and stamped `i / fps`, so `estimator.rate_b = 1 / rate` (camsync's convention).
