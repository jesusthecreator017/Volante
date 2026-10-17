# Stroke dataset and classifier: design

Date: 2026-10-17. Status: design approved in conversation; written spec pending review.

## 1. Goal
After a normal match, a player gets a report of every stroke they played (type, forehand/backhand, speed, time) recognised from
the racquet pod's IMU alone.

To get there:
- collect labelled swings of every stroke type from many people (to cover different swing styles)
- clean and pseudonymise the data
- train a classifier
- ship one model that the laptop tools, the website and the iPhone app all use.

### User decisions
- **Labels:** drill blocks, plus synced phone video to verify outliers and to label real-match swings for the test set.
- **Sensor:** collect with the current MPU-6500. Speeds from clipped swings are lower bounds.
- **Reports:** after the match, not live.
- **Model location:** one ONNX model used by both the laptop/web tools and the phone app.
- **Classes:** the core 10, with forehand/backhand, plus "not a hit".
- **Privacy:** pseudonymous IDs with consent.

### Non-goals (this phase)
- live in-play recognition
- doubles-specific strokes
- a sensor upgrade (ICM-20649), which is a later phase and would trigger a re-check of the speed stats
- shuttle-based labels.

## 2. Data facts that shape the design (measured 2026-10-17)
- **Sampling:** 500 Hz; 6-axis; ±16 g / ±2000 °/s.
- **Clipping:** in a hard-smash session, **26 of 41 smashes clipped**. Clipping must be a feature, not noise.
- **Smash signature:**
  - ~500 ms build-up
  - a ~100 ms burst
  - a peak dominated by **shaft roll (pronation)**, about −1,200 °/s
  - a sharp reversal within 100 ms
  - softer swings peak at half the rate with little shaft roll.
- **Data integrity bug:** the pod can stream byte-misaligned data after a bad boot. Phase 16.0 fixes it in firmware, and the
  dataset QC rejects any such session anyway.
- **Mount:** the IMU sits flat in the pod. The calibrated mount (`~/.config/oso/mount.json`) is applied before any analysis.
  It's stored per pod in each session's metadata.

## 3. Classes
| id | class | sides |
|---|---|---|
| 0 | not_a_hit (waggle, pick-up, shadow move) | — |
| 1 | serve_short | FH/BH |
| 2 | serve_long | FH/BH |
| 3 | clear | FH/BH |
| 4 | smash | FH (BH rare: allowed) |
| 5 | drop | FH/BH |
| 6 | drive | FH/BH |
| 7 | lift | FH/BH |
| 8 | net_shot | FH/BH |
| 9 | block_push | FH/BH |
| 10 | net_kill | FH/BH |

- **Model outputs:** class (11-way) + side (2-way, ignored for not_a_hit).
- **Mapping:** a table to the Replay `STROKE_TYPES` list lives in the dataset README and code, so Replay reports and the IMU
  report share names.

## 4. Components

### 4.0 Firmware FIFO framing guard (Phase 16.0, prerequisite)
- A temperature sentinel in the FIFO detects misalignment, resets within 3 samples, and counts `framing_resets` in status.
- A host-side `implausible_rest` check warns in `oso-rec`.
- **No collection session starts until this is flashed.**

### 4.1 Collection tooling (Phase 16.1)
- **`oso-rec collect --player P07 --protocol drills-v1 [--session-dir DIR]`:** a guided session in the terminal, with the live
  racquet window optional.
  - It shows the current block ("forehand clear, 25 reps"), counts swings live (a simple gyro-peak detector), and glows the LED
    and beeps when the block's count is reached.
  - **Per block:**
    1. The pod gets a `0x06` marker at the start and the end.
    2. The block label is written to the session metadata.
    3. Keys: `n` next block, `r` redo block, `s` skip.
  - **Recording:** on-device for the whole session (lossless 500 Hz); the live stream is for the counter and view only. At the
    end: stop, upload, and verify the count (existing `0x07` rules).
- **Session folder:**
  - `imu.bin` (OSOI v1)
  - `session.json`:
    - player ID, handedness, level, height range
    - protocol version
    - pod MAC
    - firmware version
    - mount
    - blocks [{label, side, t_start_us, t_end_us, target_reps, counted_reps}]
    - markers
    - operator notes.
  - Optional `video.mp4`, recorded on a phone at the court side.
- **Protocol `drills-v1`:**
  - Warm-up.
  - Then each class × side in a randomised order, so fatigue doesn't correlate with class: 20-25 reps, with ~2 s stillness
    between reps.
  - A "not_a_hit" block: 60 s of waggles, walking and shuttle pick-ups.
  - A "free rally" block, 3-5 min (unlabelled, video-labelled later), at the end.
  - The protocol is a JSON file in the repo.
- **Docs:** `docs/strokes/collection.md`, a one-page guide for operators: setup, mount calibration, phone placement, the tap
  sync, and what to say to participants.
- **Consent:** `docs/strokes/consent.md`, a short plain-language consent note.

### 4.2 Dataset store and pipeline (Phase 16.2)
**Location:** `~/Datasets/oso-strokes/`. **Never in the repo**; the repo only holds code and docs.

```
raw/<session_id>/{imu.bin, session.json, video.mp4?}
labels/<session_id>.csv          # swing_id, t_peak_us, class, side, source(block|review|video), reviewer
processed/swings_v<N>.npz        # windows + metadata, rebuilt by the pipeline
manifest.json                    # sessions, players (IDs only), versions, QC results
private/participants.csv         # name <-> ID, consent date (never shared, never in repo)
```

**Pipeline (`tools/strokes/build_dataset.py`), deterministic and re-runnable:**
1. Read and validate.
2. Apply the mount.
3. **QC per session:**
   - misalignment (`implausible_rest`), rejecting the session
   - sample-rate and loss check
   - clipping stats.
4. **Swing segmentation:** a gyro-peak detector (threshold plus refractory window). Every candidate is kept, then labelled by
   block membership: swings outside blocks get `unlabelled`.
5. **Windows:** −600..+400 ms around the peak at 500 Hz, in the racquet frame: 6 channels + |ω| + |a| + saturation flags.
6. **Per-swing QC:**
   - **Count mismatch:** a block with counted ≠ detected → its swings go to review.
   - **Outliers:** swings far from their class's centroid (feature space) → review.
   - **Duplicates:** two peaks < 250 ms apart → keep the larger one.
7. Write `processed/`, plus a QC report (HTML) with counts per player × class × side, clipping rate and rejected sessions.

### 4.3 Labelling and review tool (Phase 16.3)
A local web app (Vite + TS, like `site/`) with a tiny Python server over the dataset folder. It runs locally only and never
uploads.

**Views:**
- **Review queue:** per swing, synced video (±1.5 s around the peak; the offset comes from the tap markers matched to an audio
  or visual tap in the video, or a manual nudge), IMU traces, the current label and the model's guess (once a model exists).
  Actions: accept, relabel (class/side), discard, with keyboard shortcuts.
- **Match labelling:** step through every detected swing in a free-play or real-match session and label it. This builds the
  **test set** that is never used for training.

The output is `labels/*.csv`, with a reviewer and timestamp per row. Inter-rater checks are optional: two reviewers on a 10 %
sample, with agreement reported.

### 4.4 Model (Phase 16.4)
**Splits:**
- **Leave-players-out:** k-fold by player, so no player appears in both train and test.
- **The real-match test set** is held out entirely.

**Candidates:**
- **A. Features + gradient boosting** (like Phase 10): peak values per axis, timing (build-up, burst, follow-through),
  shaft-roll share, pre-swing shaft elevation from gravity, clipping counts, energy bands.
- **B. A small 1D-CNN** on the 9-channel window (about 50k parameters), with augmentation: time warp ±10 %, amplitude ±15 %,
  small mount rotations ±10° (pods get remounted), simulated clipping.

Pick the better one on leave-players-out macro-F1. Both must handle clipped inputs (the flags are inputs).

**Outputs:** class probs + side probs + an "uncertain" threshold. Swings below the threshold are reported as "unclassified".

**Gate:**
- leave-players-out **macro-F1 ≥ 0.80** on drill swings
- not_a_hit precision ≥ 0.90 (no phantom strokes in reports)
- real-match accuracy reported (no gate in v1).

**Export:** `stroke_model_v1.onnx` + `stroke_model_v1.json` (classes, window spec, preprocessing constants, mount convention,
version, training data hash). Only these artefacts live in the repo (small; no raw data).

**Speed:** head speed from |ω_perp| × 0.53 m, flagged as a lower bound when clipped. A swing-speed regression is a later phase.

### 4.5 Integration and post-match report (Phase 16.5)
- **Rust inference:** `tract-onnx` (pure Rust, also builds for wasm) in `oso-replay`.
  - `oso-imu`/`oso-rec report SESSION.bin` → detect swings → classify → `report.json` + `report.html`.
  - Report contents: counts per class × side, speed stats per class, timeline, longest rally segment if inferable, and
    comparisons to the player's previous reports when `--history DIR` is given.
- **Replay:** the swings track gets the model's labels (the existing swings track; additive).
- **iPhone app:** Core ML via `coremltools` conversion of the same ONNX, or ONNX Runtime Mobile.
  - `report.json` is a documented contract (`docs/strokes/report-format.md`) so the app and the website render the same thing.
  - The app work goes to the app developer (the friend's agent) via a handoff note that includes the model, the contract and
    test vectors.
- **Test vectors:** 20 recorded swings with expected outputs, so every implementation (Rust, wasm, iOS) is verified to match
  within 1e-4.

## 5. Privacy and data handling
- **Before the first recording:** consent ("motion data from a racquet sensor, optional video; used to train a stroke
  recogniser; pseudonymous; you can withdraw any time").
- **Identity:**
  - IDs `P01…` are the only identifiers in data.
  - Names live in `private/participants.csv`.
  - Metadata is limited to handedness, level (beginner/club/advanced), height range (5 cm bins) and age range (optional).
- **Storage:** videos stay local in `raw/`, are never committed or uploaded, and are deleted after labelling if the participant
  prefers.
- **Withdrawal:** `tools/strokes/withdraw.py P07` deletes the raw data, labels and processed entries, and rebuilds; the model is
  retrained at the next version.
- **Public release:** the model artefacts contain no raw data. Anyone wanting to publish the dataset needs separate consent.
- **Repo safety:** a check (like the BFMD rule) makes sure no `*.bin` session data or video is committed. Add the patterns to
  `.gitignore` and a CI check.

## 6. Collection targets
- **Pilot (the owner):** one full `drills-v1` session to shake out the tools. **Not** used for the gate on its own.
- **Main collection:** ≥ 8 players, ideally a mix of handedness (≥ 2 left-handed) and level, giving about 11 classes ×
  2 sides × 25 reps ≈ 500 swings per player, about 4,000-5,000 swings in total.
- **Real-match test set:** ≥ 4 matches from ≥ 3 players, every swing video-labelled.

## 7. Risks
- **Clipping hides power differences** (smash vs hard clear). Mitigation: timing and shaft-roll features; report speed as a
  lower bound.
- **Drill swings are cleaner than match swings.** The real-match test set measures the gap. If it's big, add free-play
  sessions to training.
- **Mount differences between pods** (and remounting). Mitigation: per-session mount calibration plus rotation augmentation.
- **Data integrity** (FIFO bug). Mitigation: the 16.0 firmware guard plus a QC session reject.
- **Class imbalance in real play** (lots of drives and nets). Mitigation: class weights plus calibrated thresholds.
