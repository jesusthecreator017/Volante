# Stroke model v0 (personal, Phase 16.4a part 2)

**Status: trained and exported, but it does NOT meet the targets (macro-F1 >= 0.80, not_a_hit precision >= 0.90). Do not use its
output in reports yet.** Leave-one-session-out macro-F1 is 0.24 (A) and 0.22 (B). Random splits inside the same sessions score
0.82 accuracy, so the signal is there; what fails is carrying it across sessions (see "Why it fails").

## Data
- 1 player (P01), 3 drill sessions, 678 `labelled` swings (review/unlabelled/discarded swings excluded; `labels/*.csv` overrides apply
  through `build_dataset.py`). Windows: -600..+400 ms around the gyro peak, 500 Hz, 10 channels, racquet frame (`features.py`).
- Sessions are unbalanced: 155705 has 112 swings in 4 classes (block_push, net_kill, net_shot, not_a_hit), 190209 has 214 in 9 classes,
  220845 has 352 in 10 classes. A class absent from a test fold is left out of that fold's macro average.

| class | FH | BH |
|---|---|---|
| not_a_hit | 27 (no side) | |
| serve_short | 31 | 14 |
| serve_long | 13 | 11 |
| clear | 20 | 35 |
| smash | 44 | 23 |
| drop | 44 | 17 |
| drive | 22 | 42 |
| lift | 46 | 36 |
| net_shot | 34 | 44 |
| block_push | 20 | 33 |
| net_kill | 73 | 49 |

## Method
- **A**: `HistGradientBoostingClassifier` (200 iterations, balanced class weights) on `features.full_features` (versioned, documented in
  `features.py`: per-axis peaks and signs, |w| peak, build-up and burst durations, follow-through decay, shaft-roll share, pre-swing shaft
  elevation, accel peak, clipping counts, |w| band energies, accel/gyro peak lag), plus a second GBM for FH/BH on swings with a side.
- **B**: 1D-CNN (4 strided conv blocks, mean+max pooling, 46,941 parameters) on the window, normalisation inside the graph,
  class head + masked side head, balanced class-weighted loss. Augmentation: time warp +-10 %, gain +-15 %, mount rotation +-10 deg on the
  acc/gyro axes, simulated clipping at +-2000 dps / +-16 g with the flags set. Early stopping on a stratified 15 % validation split.
- Evaluation: leave-one-session-out (3 folds). `train.py --eval lopo` is the same code grouped by player for the >= 8-player gate.

## LOSO results (seed 0)
| | A (GBM) | B (CNN) |
|---|---|---|
| mean macro-F1 (present classes) | **0.235** | 0.216 |
| mean accuracy | 0.322 | 0.261 |
| mean side accuracy | 0.644 | 0.657 |
| mean not_a_hit precision | 0.25 | 0.11 |
| fold 155705 / 190209 / 220845 macro-F1 | 0.247 / 0.206 / 0.251 | 0.252 / 0.184 / 0.211 |

Pooled out-of-fold per-class F1 (support = labelled swings):

| class | A | B | support |
|---|---|---|---|
| not_a_hit | 0.12 | 0.08 | 27 |
| serve_short | 0.03 | 0.00 | 45 |
| serve_long | 0.00 | 0.00 | 24 |
| clear | 0.38 | 0.44 | 55 |
| smash | 0.15 | 0.34 | 67 |
| drop | 0.17 | 0.13 | 61 |
| drive | 0.00 | 0.00 | 64 |
| lift | 0.51 | 0.48 | 82 |
| net_shot | 0.20 | 0.01 | 78 |
| block_push | 0.29 | 0.46 | 53 |
| net_kill | 0.48 | 0.34 | 122 |

Winner: **A** (higher mean macro-F1, higher accuracy, exports as a plain tree graph). Full per-fold numbers, confusion matrices and
precision/recall per class: `~/Datasets/oso-strokes/reports/eval_v0.{json,html}` (not in the repo).

## Uncertainty threshold
Rule: report "unclassified" when the max class probability is below the threshold. Targets (overall accuracy >= 0.90 and not_a_hit
precision >= 0.90 on accepted swings, keeping >= 5 % coverage) are **not reachable**; the stored fallback is the best-accuracy threshold
with >= 20 % coverage: **0.947** (A), keeping 21 % of swings at 46 % accuracy. Coverage vs accuracy for A: 100 %/0.31, 85 %/0.33,
60 %/0.37, 32 %/0.44 (thresholds 0, 0.5, 0.7, 0.9). The threshold comes from out-of-fold probabilities; the exported model is trained on
all data and is more confident, so re-pick it once the model is useful.

## Why it fails (what the data shows)
- Sessions differ more than classes do. Median shaft-roll peak of the same class flips sign and size between sessions (smash FH: -755 dps
  in 190209 vs -1933 dps in 220845; net_kill FH: +823 vs -871 dps), and pre-swing shaft elevation of the same class differs by 30-45 deg.
  Either the grip/stance or the mount/racquet differed between sessions, or some side labels are off. Worth checking with the owner
  before collecting more.
- The classes are not spread across sessions (one session has 4 classes), so each fold trains on sessions with different class mixes.
- With one player and three sessions there is nothing to generalise over. More sessions per class (and players) is the fix, not a bigger model.

## Known limits
Single player; not_a_hit has 27 examples; drill vs match gap untested; clipped swings (speeds are lower bounds); no review swings used;
the exported model has never been tested on a session it was not trained on, apart from the numbers above.

## Intended use
Personal v0 plumbing: shows that training, ONNX export and parity checks work end to end. Not validated on other players. Not for reports.

## Files and retraining
- `models/strokes/stroke_model_v0.onnx` (inputs `features` [N, 37], outputs `probabilities` [N, 11], `side_probabilities` [N, 2] FH/BH),
  `stroke_model_v0.json` (classes, window, channels, feature definitions, mount convention, threshold, data hash, metrics),
  `test_vectors_v0.npz` (20 windows + features + expected outputs). Because candidate A won, `features.py` is part of the model contract.
- onnxruntime vs Python on all 678 labelled swings: max abs probability difference 3.2e-07 (class), 8.9e-08 (side).
- Retrain: `tools/.venv/bin/python tools/strokes/build_dataset.py`, then `tools/.venv/bin/python tools/strokes/train.py --final`
  (evaluates both candidates, picks the winner, exports it). ~45 s on an M-series Mac.
