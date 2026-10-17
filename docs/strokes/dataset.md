# Building the stroke dataset (Phase 16.2)

The data lives in `~/Datasets/oso-strokes/` (or `$OSO_STROKES_ROOT`), **never in this repo**; the build refuses a root inside it.

```
raw/<session_id>/{session.json, imu.partN.bin | imu.bin, video.mp4?}   from oso-rec collect (16.1)
labels/<session_id>.csv        swing_id,t_peak_us,class,side,source,reviewer   (review/video labels, 16.3)
processed/swings_v<N>.npz      the windows + metadata          processed/qc_v<N>.html   the QC report
manifest.json                  sessions, player IDs, versions, QC results, counts
private/participants.csv       name <-> ID, consent (never shared)
```

```bash
tools/.venv/bin/python tools/strokes/build_dataset.py [--root DIR] [--version N]   # rebuild (deterministic)
tools/.venv/bin/python tools/strokes/withdraw.py P07 [--root DIR] [--yes]         # a participant withdraws
tools/.venv/bin/python tools/strokes/test_dataset.py                              # tests (synthetic sessions)
```

## What the build does
1. **Reads** each session (schema `oso-strokes-session/1` or `/2`; a `/2` session's parts are separate recordings, each on its own
   device clock) and every recording part with the same rules as the Rust parser (unwrap, sort, dedupe, gaps).
2. **Session QC** per part; a failing part is listed in the manifest and the report, and none of its swings is used:
   - measured rate within 3 % of 500 Hz, at most 5 % packets lost;
   - **misaligned data**: the Rust `implausible_rest` detector (exact port), plus a rest-gravity check: the median |a| of the stillest
     20 % of samples must be 0.8-1.2 g (real recordings rest between reps; shifted bytes never read 1 g; this catches noisy shifts
     the Rust detector misses).
3. **Mount**: `session.json` → `mount` (shaft and face-normal axes in the sensor frame) rotates every sample into the racquet frame.
4. **Swings**: the live counter of `oso-rec collect`, ported constant for constant (20 ms smoothing, 0.8 s refractory, re-arm below
   150 dps), run fresh in each block with the protocol's threshold for it (400 dps default, also between blocks). The peak is the
   largest raw |w| within 500 ms of the counter's onset. Two peaks closer than 250 ms are one swing (a swing across a block edge is
   seen twice): the larger is kept.
5. **Windows**: −600..+400 ms around the peak on the 2 ms grid, 10 channels: a (g) and w (dps) on the racquet axes, |w|, |a|, and
   the gyro/accel saturation flags (clipping is a feature: hard smashes clip).
6. **Labels**: the block a peak falls in gives class and side (`warmup` and `free` blocks, and anything outside blocks, stay
   unlabelled; a discarded block's swings are `discarded`). A `labels/<session>.csv` row with the swing's `swing_id` overrides it
   (`class` = `discard` removes the swing).
7. **Review queue** (status `review`, with the reasons): a block whose detected count differs from the live count, or whose live
   count differs from its target; a window with lost packets or past the recording's edge; a swing far from its class and side
   (robust z > 4 on the summary features, groups of 8+).

Side is relative to the racquet face, never mirrored by handedness (`README.md`). `features.TO_REPLAY` maps our 11 classes to
Replay's `STROKE_TYPES` names.

## swings_v<N>.npz
No pickle (`np.load(path, allow_pickle=False)`). One row per swing, sorted by session, part and peak time:
`x` float32 [n, 500, 10] (channels in `manifest.json`), `feat` float32 [n, 8] (`features.summary`), `swing_id`, `session`,
`player`, `handed`, `part`, `t_peak_us`, `block` (-1 outside blocks), `label`, `class_id` (-1 unlabelled), `side` (0 FH, 1 BH, -1),
`status` (`labelled` / `review` / `unlabelled` / `discarded`), `source` (`block` / `review` / `video`), `reasons`.

## Withdrawal
`withdraw.py P07` deletes the player's sessions, their label files, their row in `private/participants.csv` and every processed
version (older versions hold their swings too), then rebuilds the newest version. A model trained on the old data is retrained at the
next model version (16.4).
