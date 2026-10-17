# Building the stroke dataset (Phase 16.2)

The data lives in `~/Datasets/oso-strokes/` (or `$OSO_STROKES_ROOT`), **never in this repo**; the build refuses a root inside it.

```
raw/<session_id>/{session.json, imu.partN.bin | imu.bin, video.mp4?}   from oso-rec collect (16.1)
labels/<session_id>.csv        swing_id,t_peak_us,class,side,source,reviewer   (review/video labels, 16.3)
processed/swings_v<N>.npz      the windows + metadata          processed/qc_v<N>.html   the QC report
meta/sessions.json             optional per-session overrides, e.g. {"<session_id>": {"context": "rally"}} (raw/ stays read-only)
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
7. **Review queue** (status `review`, with the reasons). The drill blocks are trusted: a count mismatch (detected vs live, or live vs
   target) keeps the swings `labelled` (`source=block`) and is kept as an informational **note** (`notes` in the npz, listed apart in
   the QC report). Only a *large* mismatch sends the block to review (`qc.LARGE_OVER_TARGET`/`LARGE_UNDER`): detected > 1.5 x
   target (extra detections may be resets or shadow moves; the live counter is the same algorithm, so only the target is an
   independent reference) or detected < 0.5 x target (a failed block); timed
   blocks (target 0) are never large. Also in review: a window with lost packets or past the recording's edge, and a swing far from
   its class and side (robust z > 4 on the summary features, groups of 8+). Files in a session folder that `session.json` does not
   list (e.g. an `orphan.partN.bin`) are ignored. Review them with `tools/strokes/review/` (`docs/strokes/review.md`).

Side is relative to the racquet face, never mirrored by handedness (`README.md`). `features.TO_REPLAY` maps our 11 classes to
Replay's `STROKE_TYPES` names.

## Session context: drill or rally
Every session has a `context`: `drill` (clean sets: one stroke per block, ~2 s stillness between reps) or `rally` (play with a partner:
block labels are only approximate, other strokes happen inside a block). New sessions get it from `oso-rec collect --context drill|rally`
(default drill; stored in `session.json`; a missing field means drill). For sessions recorded before the flag, put an override in
`meta/sessions.json`; it wins over `session.json`. The build writes it to the npz (`context`) and the manifest. The model trains on
`drill` swings only; `rally` swings count as test data only where a reviewer labelled them (`labels/*.csv`, `source=review`), see
`model-card.md`.

## swings_v<N>.npz
No pickle (`np.load(path, allow_pickle=False)`). One row per swing, sorted by session, part and peak time:
`x` float32 [n, 500, 10] (channels in `manifest.json`), `feat` float32 [n, 8] (`features.summary`), `swing_id`, `session`,
`player`, `handed`, `context` (`drill` / `rally`), `part`, `t_peak_us`, `block` (-1 outside blocks), `label`, `class_id` (-1 unlabelled), `side` (0 FH, 1 BH, -1),
`status` (`labelled` / `review` / `unlabelled` / `discarded`), `source` (`block` / `review` / `video`), `reasons` (why in review), `notes` (count mismatch, informational).

## Withdrawal
`withdraw.py P07` deletes the player's sessions, their label files, their row in `private/participants.csv` and every processed
version (older versions hold their swings too), then rebuilds the newest version. A model trained on the old data is retrained at the
next model version (16.4).
