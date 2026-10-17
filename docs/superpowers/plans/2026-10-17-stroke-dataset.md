# Stroke dataset and classifier: implementation plan

> **For agentic workers:** this repo runs phases through its orchestrator workflow:
> - a phase spec in `.claude/specs/`, then the `developer` agent, then the `qa` agent, then a user check
> - each phase is its own `feature/` branch and PR; the owner reviews and merges.
>
> This plan lists the phases, interfaces and acceptance criteria.

**Goal:** a post-match stroke report from the racquet pod's IMU, with a model trained on a pseudonymous, multi-player, labelled
swing dataset.

**Architecture:**
- **Python** (`tools/strokes/`) for collection metadata, the dataset pipeline and training.
- **Rust** (`oso-replay`) for collection UX (`oso-rec collect`) and inference (`tract-onnx`).
- **A local web review tool.**
- **One ONNX model** shared by the laptop, the web and iOS.

**Spec:** `docs/superpowers/specs/2026-10-17-stroke-dataset-design.md`

## Global constraints
- **No session data, video or participant names in the repo.** The dataset lives at `~/Datasets/oso-strokes/`. Add
  `.gitignore` rules plus a CI check that fails on `*.mp4`, `*.mov` or `imu.bin` under the repo, outside `assets/` and the test
  fixtures.
- **Mount:** every analysis applies the session's mount (`session.json` → mount). Synthetic fixtures use identity.
- **Sampling:** 500 Hz, racquet frame (+Y shaft, +Z face normal), windows −600..+400 ms around the gyro peak.
- **Classes:** the 11 classes + side as in spec §3. The order is fixed and versioned in `stroke_model_v1.json`.
- **Workflow:** one feature branch per phase, a PR into `main`, and CI (#22) must pass.

## Review focus (pinned by tests in the owning phase)
1. **A session recorded with misaligned IMU data:** QC rejects it with a clear reason (16.2), and `oso-rec` warns at
   upload (16.0).
2. **A block where the player did 23 reps but 25 were asked, or an extra waggle got counted:** the swings go to the review
   queue, not silently mislabelled (16.2).
3. **A left-handed player:** side labels stay correct. Forehand/backhand is defined relative to the racquet face, not the court
   (16.1 metadata and 16.4 features/tests).
4. **A participant withdraws:** every trace is removed and the rebuild excludes them (16.2 `withdraw.py` test).
5. **The same swing through the Rust, wasm and Python inference:** identical class and probabilities within 1e-4 (16.5 test
   vectors).

---

### Phase 16.0: firmware FIFO framing guard (prerequisite)
- **Spec:** `.claude/specs/phase16_0-imu-fifo-alignment.md`.
- A temperature sentinel in the FIFO (14-byte frames); a reset after 3 invalid samples; `framing_resets` in status; the
  host-side `implausible_rest` warning.
- **Acceptance:** a fault-injection build shows recovery; a still-desk stream is sane; it's flashed to the pod.

### Phase 16.1: collection tooling
- `oso-rec collect --player Pxx --protocol drills-v1`: guided blocks, a live swing counter, block markers, LED/beep on done,
  then the session folder (`imu.bin` + `session.json`). Optional live view.
- `arena/`-independent, under `crates/oso-replay/src/bin/oso-rec.rs` + `crates/oso-replay/src/collect/` (protocol, counter,
  session writer).
- Files: `tools/strokes/protocols/drills-v1.json`; `docs/strokes/collection.md`, `docs/strokes/consent.md`.
- **Tests:**
  - protocol parsing and the block state machine
  - the swing counter on the recorded smash sessions (counts within ±1 of the 41 and 18 found in the 2026-10-17 analysis)
  - `session.json` schema.
- **Pilot:** the owner runs one session; fix the UX issues.

### Phase 16.2: dataset pipeline
- `tools/strokes/build_dataset.py`, `qc.py`, `withdraw.py`, `features.py` (shared with 16.4).
- Dataset layout per spec §4.2. A QC HTML report.
- **Tests:**
  - misaligned-session reject (a fixture built by byte-shifting a clean recording)
  - block/count mismatch → review
  - the duplicate-peak rule
  - withdrawal
  - a deterministic rebuild (same inputs give byte-identical npz).

### Phase 16.3: review and labelling tool
- `tools/strokes/review/` (Vite + TS front end, small Python server). Local only.
- Synced video + IMU traces, accept/relabel/discard, the match labelling mode, keyboard shortcuts.
- **Tests:** the label CSV round trip, the sync-offset math, the server reads only inside the dataset root (path traversal
  test), and a UI smoke test with a fixture session.

### Phase 16.4: model
- `tools/strokes/train.py` (both candidates) and `evaluate.py` (leave-players-out k-fold + real-match test set) →
  `stroke_model_v1.onnx` + `.json`, plus `docs/strokes/model-card.md` (data, metrics, limits).
- **Gate:** macro-F1 ≥ 0.80 (leave-players-out, drills) and not_a_hit precision ≥ 0.90. Real-match accuracy reported.

### Phase 16.5: inference and report
- `tract-onnx` in `oso-replay`: `oso-rec report SESSION_DIR|imu.bin` → `report.json` + `report.html`. `oso-imu` labels the swings
  track.
- `docs/strokes/report-format.md` (the contract), plus test vectors (20 swings with expected outputs).
- An iOS handoff note for the app developer: the model, the contract, the test vectors.
- **Tests:** Rust == Python within 1e-4 on the test vectors, a wasm build, and a report snapshot test.

## Order
16.0 → 16.1 → **pilot** → 16.2 → 16.3 → **main collection (8+ players) + real-match recordings** → 16.4 → 16.5.

16.2 and 16.3 can overlap once 16.1's session format is fixed.
