# Data formats

## Court coordinates
Meters. Origin: court center on the floor. +Y up, +X along the length (baselines at x = +-6.70),
+Z across the width (doubles sidelines at z = +-3.05). Net plane x = 0, posts at z = +-3.05.
Defined in `src/scene/court.rs`, mirrored in `tools/court.py`.

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
Bones for drawing: `tools/skeleton.py` `BONES`, mirrored in `src/replay.rs` (change together).

## pose2d_<camera_id>.npz (swap-in point for a real pose model)
- `kp` float32 `[F, P, 17, 3]`: u px, v px (origin top-left, distorted image), score 0..1
- `t` float64 `[F]`: seconds from the start of that camera's video; `frame` `[F]`: frame index
- attributes `camera_id` (matches calib.json), `model` ("synthetic", later e.g. "rtmpose-m"), `keypoint_format` "coco17"
- P is the player slot: the same slot is the same person in every camera (ID association is the producer's job).
  A missing person in a frame has all scores 0.
Written by `tools/synth_motion.py` (with ground truth `pose_gt.npz`: `joints [F, P, 17, 3]`, `t`, `swing_player`, `swing_t0`).

## replay.bin v1 (written by `tools/pose3d.py` / `tools/replay.py`, read by `src/replay.rs`)
All numbers little-endian.
```
"OSOR" (4) | u32 header_len N | N bytes UTF-8 JSON, zero-padded so that (8 + N) % 16 == 0 | data blob
```
Header: `{"version":1, "fps":60.0, "frame_count":F, "start_time":0.0, "coordinate_system":"court_m_yup_xlength",
"tracks":[{"name":"joints3d", "dtype":"f32", "shape":[F,P,17,4], "offset":0, "layout":"x,y,z,conf",
"keypoint_format":"coco17", "players":["p0","p1"]}]}`
- `frame_count` is the first dimension of a **per-frame** track (`joints3d`, `racquet`, `shuttle`). A track whose header has `"per_frame": false` (`events`) has its own length: readers must not take frame_count from it, and writers must not take it from the first track blindly (`tools/replay.py` skips `per_frame: false` tracks).
- `offset` is in bytes from the start of the data blob and a multiple of 16 (tracks are zero-padded to 16).
- Readers must ignore unknown tracks (`shuttle` and `events` came in Phase 5; later phases add more). Written by `tools/replay.py` and, for `oso-imu`, by `Container` in `src/replay.rs` (same padding rules; all existing tracks are copied byte-identical).
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
- `t` float64 `[F]`: seconds from the start of that camera's video (cameras must be frame-synchronized, as for pose2d)
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
  (player -1). Header `"per_frame": false`. Contact time is the closest approach of the extrapolated neighbouring flights (sub-frame).
- Rust: `Replay::sample_shuttle(t)` (linear inside a flight, never across a flight_id change), `Replay::events()`.
- `oso-imu` matches IMU impacts to the contacts of the `events` track when the replay has any for that player, else to the wrist-speed proxy.
- Pipeline order: synth_motion -> pose3d -> synth_shuttle -> shuttle3d -> synth_imu -> oso-imu (see `tools/README.md`).
