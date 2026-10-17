# Calibration tools
Setup: `cd tools && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
Synthetic: `.venv/bin/python synth.py` (writes ../out/synth/cam_{a,b}.png + .points.json), then per camera
`.venv/bin/python calibrate.py --image ../out/synth/cam_a.png --points ../out/synth/cam_a.points.json --camera-id a -o ../out/synth/a.json --overlay ../out/synth/cam_a_overlay.png`
and merge: `.venv/bin/python calibrate.py --merge ../out/synth/a.json ../out/synth/b.json -o ../assets/calib/calib.json`
Real footage: `.venv/bin/python pick_points.py --video match.mp4 --frame 100 -o a.points.json`, then calibrate.py as above
(`--image` a frame of the same video; check the overlay: red lines must sit on the painted lines).
Test: `tools/.venv/bin/python tools/test_calib.py`
Format: ../docs/data-format.md

# Pose (Phase 2)
Synthetic 2D keypoints + ground truth, then 3D replay (run from the repo root):
`tools/.venv/bin/python tools/synth_motion.py` (writes out/synth/pose_gt.npz, pose2d_a.npz, pose2d_b.npz)
`tools/.venv/bin/python tools/pose3d.py --calib assets/calib/calib.json --pose2d out/synth/pose2d_a.npz out/synth/pose2d_b.npz -o assets/replay/replay.bin --min-score 0.15 --max-gap 30`
Real pose model later: write `pose2d_<cam>.npz` in the same format (docs/data-format.md) and skip synth_motion.
Test: `tools/.venv/bin/python tools/test_pose3d.py`

# Racquet IMU (Phase 4)
Synthetic IMU in the firmware's packet format, then fusion + time sync + racquet track (from the repo root, after the Pose steps):
`tools/.venv/bin/python tools/synth_imu.py` (writes out/synth/imu.bin, imu_gt.npz, sync.json; ground truth from pose_gt.npz, player 0; `--hole T N` adds an N-packet hole at video time T)
`cargo run --release --bin oso-imu -- --imu out/synth/imu.bin --replay assets/replay/replay.bin --sync out/synth/sync.json --player 0 --mount identity -o assets/replay/replay.bin`
Prints packet accounting, gyro bias, the sync table (start-clap-only offset, clap-fit offset and drift, per-impact residuals in ms: a check) and
yaw-correction stats. Flags: `--no-video-yaw` (IMU heading as fused), `--yaw-const` (constant alignment only), `--beta B`
(Madgwick gain, default 0.04), `--forward-only` (skip the backward fusion pass), `--report out.json`, `--mount identity|flat|FILE` (how the IMU sits in the racquet; without it `~/.config/oso/mount.json`, else `flat`, so **synthetic data needs `--mount identity`**; see ../firmware/README.md). Re-running replaces the `racquet` track (the other tracks stay byte-identical).
Real device: `firmware/` (README there) + `oso-rec` write the same imu.bin format; or write it yourself and a sync.json `{"claps_video": [start, end]}`: do a clap/LED event at the start AND the end of each session (still ~2 s before each); the two give offset and drift. `oso-rec record start` / `oso-rec live` show the pod's racquet live in a window (keys R, M, Esc): see firmware/README.md.
Test (runs cargo): `tools/.venv/bin/python tools/test_imu.py`; Rust unit tests: `cargo test`, benches: `cargo bench --bench imu`.
Format: ../docs/data-format.md

# Shuttle (Phase 5)
Pipeline order (from the repo root): synth_motion -> pose3d -> synth_shuttle -> shuttle3d -> synth_imu -> oso-imu
`tools/.venv/bin/python tools/synth_shuttle.py` (writes out/synth/shuttle_gt.npz, shuttle2d_a.npz, shuttle2d_b.npz; the clear insertion can move a contact time, synth_imu reads shuttle_gt.npz so its impacts agree)
`tools/.venv/bin/python tools/shuttle3d.py --calib assets/calib/calib.json --shuttle2d out/synth/shuttle2d_a.npz out/synth/shuttle2d_b.npz --replay assets/replay/replay.bin -o assets/replay/replay.bin`
(adds the `shuttle` and `events` tracks, other tracks byte-identical; run `oso-imu` after it so it matches impacts to the shuttle contacts)
A real TrackNet writes `shuttle2d_<cam>.npz` (docs/data-format.md) and replaces synth_shuttle. Test (runs cargo): `tools/.venv/bin/python tools/test_shuttle.py`.
Viewer: V (or the "Big shuttle" button) draws the shuttle at 3x; yellow trail = detected, orange = physics-filled.

# Swings and strokes (Phase 6)
`oso-imu` also writes the `swings` track (swing detection on the IMU gyro + shuttle contacts + rule-based stroke labels) and prints the stroke table with the rule inputs (h, v0, angle, apex, x) so the thresholds in `crates/oso-replay/src/imu/strokes.rs` can be tuned. Same pipeline as Phase 4/5; re-running replaces the track. Format and rules: ../docs/data-format.md.
`oso-imu --replay R --no-imu -o out.bin` (Phase 11) writes the same track without an IMU: one contact-only row per `events` contact, no racquet track.
Test (runs cargo): `tools/.venv/bin/python tools/test_swings.py`. Viewer: N / P (or the timeline buttons) jump to the next / previous stroke; the timeline shows stroke markers and the P0 head-speed sparkline.

# Real video (Phase 9)
Two videos in, `replay.bin` out: RTMPose (rtmlib) per camera, our-court player slots, classical shuttle detector, camera-to-camera
time sync from the footage, then the Phase 2/5 stages. Setup: `pip install --no-deps -r requirements.txt` (one command; every dependency is listed, so rtmlib's opencv-contrib does not overwrite cv2 4.x). The detector and pose models (YOLOX-m 90 MB, RTMPose-m 48 MB) download on first use into
`~/.cache/rtmlib/hub/checkpoints/` (network needed once).

Tested on rendered video (every stage against ground truth; nothing to record):
`tools/.venv/bin/python tools/render_videos.py` renders `out/video/cam_a.mp4`, `cam_b.mp4` (+ `truth.json`) from the two calibrated cameras:
distortion from calib.json applied, camera B 7.3 ms late and 40 ppm off, distractor players on the neighbouring court. The batch renderer
behind it is `cargo run --release -- --render-frames DIR --camera a --t0 0 --t1 10 --fps 60 [--rate 1.0] [--offset 0.0] [--size 1920x1080] [--distractors]`
(frame i at `offset + t0 + i / (fps * rate)`; a `--size` larger than the calibration's keeps the pixel scale and widens the view).
`tools/.venv/bin/python tools/run_video_pipeline.py --video-a out/video/cam_a.mp4 --video-b out/video/cam_b.mp4 --calib assets/calib/calib.json -o out/video/replay.bin`
(about 50 s for the 10 s clip on an M-series Mac; see "Pose speed" below). `cargo run --release -- --check-replay out/video/replay.bin` parses a
replay like the viewer does. Test: `tools/.venv/bin/python tools/test_video.py [--rerun]`.

Real footage:
1. Record both cameras (fixed, whole court in view, a rally in view of both, the court markings visible). Phone video is variable frame rate: the
   pipeline reads per-frame timestamps from the file. No clap is needed for camera sync (it uses the shuttle and wrists); the clap/LED events are for the IMU.
2. Calibrate each camera from a frame of its own video: `python pick_points.py --video match_a.mp4 --frame 100 -o a.points.json`, then
   `calibrate.py` and `--merge` as above into `calib.json` (camera ids `a`, `b` in that order).
3. `python run_video_pipeline.py --video-a match_a.mp4 --video-b match_b.mp4 --calib calib.json [--imu imu.bin --sync sync.json] -o replay.bin`
   (stages print their numbers; `camsync.json` and the per-camera `pose2d_*.npz` / `shuttle2d_*.npz` stay in `--work`, default `out/video`).
   Check the camsync line: "AMBIGUOUS" means the footage has too little shuttle motion in view of both cameras.
4. View: copy `replay.bin` to `assets/replay/` and `cargo run --release`.
`pose3d.py --min-score` (default 0.3) / `--max-gap` (frames, default 12), runner: `--pose-min-score` / `--pose-max-gap`. Real people score 0.6-0.9 on RTMPose, so the 0.3 default is right for real footage; the blocky renders score 0.1-0.4, so `test_video.py` uses 0.15 / 30 (hips and legs follow --min-score; arms and face stay at 0.3, their low-score keypoints are wild: 45 cm vs 15 cm error). pose3d prints joint and both-hips coverage per player.
Stage scripts also run alone: `pose2d_video.py`, `shuttle2d_video.py`, `camsync.py`, then `pose3d.py` / `shuttle3d.py` with `--camsync camsync.json`.
Pose speed (Phase 9.5): `pose2d_video.py` runs the person detector every N frames (`--detect-every N`, default 6) and tracks each player with the pose
network from the box of their previous keypoints in between; `--profile` prints the per-stage time (decode / track / tiling / detector / pose / assign).
`run_video_pipeline.py` passes `--detect-every` and `--profile` through and gives each camera process `cores // 2` ONNX threads (`pose2d_video.py --threads`).
Provider: both networks use ONNX Runtime's CoreML execution provider when `onnxruntime.get_available_providers()` lists it (Apple Silicon), else the CPU;
`--provider cpu` forces the CPU. The YOLOX session needs `RequireStaticInputShapes` for CoreML, the RTMPose one must not have it (it would silently run on
the CPU); `test_video.py` checks CoreML against the CPU outputs. ~15 frames/s per camera on an M-series Mac, two cameras side by side (Phase 9: 0.8).
Swap-ins: a better shuttle detector (TrackNetV3) writes `shuttle2d_<cam>.npz`; another pose model writes `pose2d_<cam>.npz`.

# Stroke classifier (Phase 10)
Labels every contact (serve, clear, smash, drop, net, lift, drive, block, push, press, net_kill, flick_serve) with a model trained on BFMD
(12 singles matches, 11,301 labelled hits). **BFMD is non-commercial research data: it stays read-only in `~/Datasets/BFMD`, is never
redistributed, and everything derived from it (features, model, report) lives in `~/Datasets/BFMD-work/`, never in the repo.** Its own
`vis.py` / `download_youtube.py` are never run; the tools below parse the JSON as data and are run with `python -I`.
- `bfmd.py` loaders + court geometry (doubles outer lines; per-match homography and PnP camera; `python -I tools/bfmd.py` prints the per-match
  focal / eye / reprojection and the median = the virtual broadcast camera constants).
- `stroke_features.py` one feature function for both domains (hitter court position, pose before/at/after contact, shuttle after contact, rally context).
- `python -I tools/train_strokes.py [--rebuild]` leave-matches-out evaluation (4-fold by match, vs majority and context-only baselines), final model
  `~/Datasets/BFMD-work/stroke_hgb.joblib`, report `report.md`. Needs `pip install --no-deps -r requirements.txt` (scikit-learn, joblib, ...).
- `classify_strokes.py --replay replay.bin --model ~/Datasets/BFMD-work/stroke_hgb.joblib [-o out.bin]` re-projects the 3D joints and shuttle through the
  virtual broadcast camera at 30 fps and writes `stroke_type` into the swings rows (probability < 0.35 -> unknown). Chains after oso-imu, e.g. the synthetic one:
  `... oso-imu ... -o replay.bin && python tools/classify_strokes.py --replay replay.bin`; `run_video_pipeline.py --stroke-model PATH` does it as the last stage, with or without `--imu` (without, `oso-imu --no-imu` runs first).
- Test: `tools/.venv/bin/python -I tools/test_strokes.py` (BFMD parts skip when the dataset is missing; the apply step runs cargo `--check-replay`).
Accuracy is measured on held-out BFMD matches (elite players, broadcast view); players repeat across matches. On our own replays labels are a plausibility
check until a few dozen strokes are hand-tagged; the synthetic motion has no real technique, so context features dominate there.

# Match stats (Phase 11)
`cargo run --release -- --stats replay.bin` prints per-player stroke counts, speeds, hit positions and rallies as JSON (format: ../docs/data-format.md);
the web viewer's Stats button shows the same numbers (`oso_stats()`).

# Badminton self-play (Phase 13, side project: two AIs play a 15-point game, the viewer replays it)
`arena/kit/oso_arena/`: a vectorised singles simulator (`sim.py`, numpy, N games at once, shuttle drag from `physics.py`, a numpy-only copy of `shuttle_physics.py`; `court.py` is a copy of `tools/court.py`, and `test_badsim.py` checks both copies, net / lines / service rules,
players as point masses with a reach envelope), a scripted baseline bot (`bot.py`), PPO self-play training (`train.py`, PyTorch), evaluation
(`evaluate.py`), and since Phase 14.2 the contract kit (`builds`, `policy`, `export`, `check`, `match`, `log`, `animate`, CLI `oso-arena`; see `arena/contract/CONTRACT.md`). Setup: `.venv/bin/pip install torch` (not part of the `--no-deps` list; see requirements.txt).
All commands from the repo root; outputs go to `out/badsim/` (gitignored).
```
tools/.venv/bin/python arena/kit/oso_arena/test_badsim.py                       # simulator, rules, solver, mirroring, export tests
PYTHONPATH=arena/kit tools/.venv/bin/python -m oso_arena train --minutes 225 --out out/badsim   # PPO self-play (safetensors checkpoints + train_log.jsonl; --resume continues); see arena/kit/README.md
tools/.venv/bin/python arena/kit/oso_arena/evaluate.py                          # 200 games vs bot + 200 policy-vs-policy -> out/badsim/eval.json
tools/.venv/bin/python arena/kit/tests/run_all.py                                # Phase 14.2 kit tests (contract, builds, check, match, animate, CLI)
PYTHONPATH=arena/kit tools/.venv/bin/python -m oso_arena match A.safetensors bot -o out/badsim/m.log.gz --seed 0   # one game -> match log (A / B: file or `bot`)
PYTHONPATH=arena/kit tools/.venv/bin/python -m oso_arena animate out/badsim/m.log.gz -o out/badsim/match.bin   # -> replay.bin
cargo run --release -p oso-arena -- --screenshot out/badsim/shot.png --time 12 --replay out/badsim/match.bin
```
- **What is learned:** where to stand, when to hit, which shot and where (target landing point + flight time). The swing is animation: players are
  point masses (5 m/s, 10 m/s^2) with a 1.5 m reach (2.0 m lunge; Phase 13 had 1.0 / 1.4, 0.25 s freeze; height 0.15-2.9 m, 3.2 m jump, 0.3 s freeze); the racquet face is not simulated.
  The numbers (speeds, reach, execution noise, 25 -> 90 m/s launch cap by contact height) are hand-set, so the emergent style depends on them.
- **Shot model:** the action's aim point and flight time are turned into a launch velocity with a lookup table (distance, contact height, time) built with a
  Newton solve on the sim's own integrator (cached in `out/badsim/shot_table.npz` in a checkout, else `$OSO_ARENA_CACHE` or `~/.cache/oso-arena`; first build ~10 s); landing error without noise < 1 cm. Gaussian noise on the aim point
  grows with launch speed, lunges / jumps and player speed.
- **Opponents in training:** 60 % self-play, 30 % frozen snapshots, 10 % the bot. Reward: +-1 per rally, plus 0.1 for a legal net crossing annealed to 0 over the first 30 % of the time budget; Phase 13.1 adds a depth reward (0.15 x how far past the short service line the predicted landing is, paid at the net crossing, first 10 rally shots) and a base-position reward (0.001 per decision step while the opponent is to hit, annealed over 25 %). `train.py` writes `best.pt` only for checkpoints with bot win rate >= 80 % and clear + lift >= 10 % (per-checkpoint numbers in `ckpt_eval.jsonl`).
- **Match export:** the 120 Hz sim log is resampled to 60 fps; bodies come from `synth_motion.player_pose` along the sim paths (swing peak = contact, pitch follows
  the contact height, facing the shuttle, lunge = hip drop, jump = hip rise); events: contacts (type 1), landings (2) and the new `point` (3, docs/data-format.md).
  `oso-imu --no-imu` labels the strokes by the Phase 6 rules, and the BFMD classifier relabels them if `~/Datasets/BFMD-work/stroke_hgb.joblib` exists (`--no-classify` skips it).
  A 4 s pause between rallies keeps them apart for the stats' 3 s rally gap. The timeline shows "P1 7 - 5 P2" and the Stats panel the points won.
- **Training notes (what actually happened):** ~3.75 h total in four resumed parts. The serve aim is normalised over the diagonal service court (a policy that always
  aimed into the wrong court could not escape that local optimum); the amendment added `SHOT_REWARD` (+0.03 per legal return, first 10 shots, not annealed) and `SMASH_NOISE`
  (sigma += 0.35 m (v0/90)^2) in `sim.py`. Result: wins 100 % of games vs the bot, but self-play rallies average ~4.8 shots (gate 6 missed) and the policy favours serve /
  smash / drop; clears and lifts never appear. The checkpoint used (`best.pt` = `ckpt_165min.pt`) was picked by self-play rally length among those that beat the bot 100 %.
- **Phase 13.1 (longer rallies, 3D scoreboard):** Phase 13 outputs are in `out/badsim_v1/`, the first 13.1 attempt (reach 1.3 / 1.8, depth 0.05) in `out/badsim/run1/`.
  The tuning round (reach 1.5 / 2.0, depth 0.15, no aim-noise halving, ENT0 6e-3, initial log_std 0 on aim / flight-time) raised self-play rallies from 4.8 to 10.9 shots
  but no checkpoint met the clear + lift >= 10 % gate (best 3.7 %); the shipped checkpoint is `ckpt_196min.pt` (see the report). The viewer draws a 7-segment scoreboard
  (`crates/oso-arena/src/scoreboard.rs`) for replays with point events; since the Replay / Arena split it is drawn by the Arena viewer only.
