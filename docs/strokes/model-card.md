# Stroke model v0 (personal, Phase 16.4a part 2, revised)

**Recommended v0 for reports: the stroke intensity group model (`stroke_group_v0`)**, which says whether a swing was a *power*
(smash, clear), *mid* (drive, drop, lift, net_kill, serve_long) or *touch* (net_shot, block_push, serve_short) stroke. Orientation-free GBM
(variant a_of), block 5-fold macro-F1 0.75 / accuracy 0.78 (within-session, optimistic; see below).

**The 11-class CNN (`stroke_model_v0`) is EXPERIMENTAL: do not use it in reports.** Reason: it was trained on one drill session with only
1-2 blocks per class (one per side), so it cannot separate strokes beyond the block it saw (block 5-fold macro-F1 0.23; its apparent
winner status over the other 11-class variants is noise). Neither model meets the targets (macro-F1 >= 0.80, not_a_hit precision >= 0.90)
or has a generalisation estimate: the only clean data is one drill session (352 swings).

## Data
- Player P01, three sessions on 2026-10-17. Their context (`docs/strokes/dataset.md`):
  - `20261017-220845-P01`: **drill** sets. 352 labelled swings, 17 blocks. This is the only training data.
  - `20261017-155705-P01` and `20261017-190209-P01`: **rally** with a partner (override in `meta/sessions.json`). Block labels are only
    approximate (other strokes happen inside a block, no stillness), so their 326 labelled swings are **not used for training**.
- Real-play test set: rally swings with a review label (`labels/*.csv`, `source=review`). **Currently 0.** Review rally swings in the
  review tool (`docs/strokes/review.md`), rebuild, and `train.py` evaluates every variant on them automatically.
- The drill session has no `not_a_hit` swings, so the model has no `not_a_hit` class (the ONNX class output omits it; see
  `output_class_ids` in the json) and not_a_hit precision cannot be measured. Serve_long (BH only), block_push (BH only) and net_kill
  (FH only) have a single block each.

| class | FH | BH | blocks |
|---|---|---|---|
| serve_short | 16 | 14 | 2 |
| serve_long | 0 | 11 | 1 |
| clear | 8 | 25 | 2 |
| smash | 23 | 23 | 2 |
| drop | 22 | 17 | 2 |
| drive | 22 | 25 | 2 |
| lift | 24 | 25 | 2 |
| net_shot | 26 | 23 | 2 |
| block_push | 0 | 23 | 1 |
| net_kill | 25 | 0 | 1 |

## Method
- Variants: **a** HistGradientBoosting (class + side heads) on all 44 features (`features.py`, v2); **a_of** the same on the 24
  orientation-free features only (norms, timing, clipping, spectra; no axis-wise values, signs, shaft-roll share or gravity direction);
  **b** 1D-CNN (46,941 parameters) with mount-rotation augmentation +-10 deg; **b30** the same with +-30 deg. CNN augmentation also: time
  warp +-10 %, gain +-15 %, simulated clipping. Each variant is also trained as a 4-way model: not_a_hit / power (smash, clear) /
  mid (drive, drop, lift, net_kill, serve_long) / touch (net_shot, block_push, serve_short).
- Evaluation, chosen automatically: leave-one-session-out when there are >= 2 drill sessions (`--eval loso`), else **grouped 5-fold over
  drill blocks** (`StratifiedGroupKFold` on (session, block), seed 0; a block is never split between train and test). Today only the
  block K-fold applies.
- **How to read the block K-fold.** It is within one session (same day, same mount, same player), so it is optimistic for generalisation
  to new sessions. It is also harsh in one way: most classes have 1-2 blocks (one per side), so a held-out block is often a class x side
  the model never saw in that fold (a class with a single block is entirely unseen). For reference, a swing-level random 5-fold on the
  same drill data (leaky: neighbouring reps of one block are near-duplicates) gives 0.92 accuracy / macro-F1 for 11 classes with variant a,
  so the features separate blocks easily; they do not yet carry a class across sides or blocks.

## Results (grouped block 5-fold, seed 0, drill only, macro-F1 over classes present in the fold)
11 classes:

| variant | mean macro-F1 | mean acc | mean side acc | per-fold macro-F1 |
|---|---|---|---|---|
| a (all features) | 0.201 | 0.218 | 0.583 | 0.00 / 0.48 / 0.20 / 0.23 / 0.10 |
| a_of (orientation-free) | 0.220 | 0.216 | 0.430 | 0.07 / 0.21 / 0.12 / 0.36 / 0.34 |
| **b (CNN, +-10 deg)** | **0.233** | 0.250 | 0.540 | 0.00 / 0.51 / 0.24 / 0.16 / 0.25 |
| b30 (CNN, +-30 deg) | 0.216 | 0.245 | 0.582 | 0.00 / 0.54 / 0.22 / 0.29 / 0.04 |

4 groups (not_a_hit absent in drill, so it never counts):

| variant | mean macro-F1 | mean acc | per-fold macro-F1 |
|---|---|---|---|
| a | 0.574 | 0.631 | 0.68 / 0.66 / 0.39 / 0.73 / 0.42 |
| **a_of** | **0.748** | **0.779** | 0.82 / 0.86 / 0.40 / 0.80 / 0.85 |
| b | 0.488 | 0.512 | 0.46 / 0.50 / 0.26 / 0.37 / 0.84 |
| b30 | 0.439 | 0.500 | 0.28 / 0.52 / 0.27 / 0.31 / 0.81 |

Pooled per-class F1, 11 classes (a / a_of / b / b30): drive 0.68 / 0.27 / 0.71 / 0.66, net_shot 0.52 / 0.44 / 0.57 / 0.70,
drop 0.13 / 0.50 / 0.30 / 0.18, lift 0 / 0.36 / 0 / 0, smash 0 / 0.03 / 0 / 0; every other class is 0 for all four.
Pooled group F1 (a / a_of / b / b30): power 0.27 / 0.51 / 0.07 / 0.01, mid 0.63 / 0.79 / 0.45 / 0.48, touch 0.91 / 0.95 / 0.97 / 0.92.

What this says, with the caveats above:
- The 11-class differences between variants (0.20 to 0.23) are noise at this data size; it is not a result that the CNN is better. The
  exported winner (b) was picked by the rule (highest 11-class mean macro-F1), not because the margin means anything.
- **Orientation-free features help the coarse task** (a_of 4-group 0.75 vs a 0.57): dropping axis-wise features generalises better
  across blocks and sides. Stronger rotation augmentation (b30) did not help the CNN.
- Effort groups (power / mid / touch) are recognisable (touch F1 0.9+, mid ~0.8, power ~0.5 with a_of); individual strokes are not.
- Side accuracy is 0.43-0.58 (near chance for two classes): the side head does not generalise across blocks.

## Group model (`stroke_group_v0.{onnx,json}`)
- Variant a_of trained on all 352 drill swings with 4 group labels. The ONNX input is the 24 orientation-free features listed in the json;
  outputs `probabilities` [batch, 3] over `output_class_ids` = [1, 2, 3] = power, mid, touch, and `side_probabilities` (not reliable, see
  above). **not_a_hit has no drill data, so the model never outputs it** (`groups_not_modelled`). The json lists the groups and the
  `class_to_group` mapping from the 11 classes.
- Block 5-fold: macro-F1 0.748, accuracy 0.779; pooled F1 power 0.51, mid 0.79, touch 0.95. Stored threshold 0.999 (fallback, targets not
  met): keeps 21 % of swings at 82 % accuracy.
- onnxruntime parity on all 352 swings: max abs difference 1.2e-07 (group), 8.3e-08 (side). 20 test vectors in `test_vectors_group_v0.npz`
  (features and expected outputs; the windows are the same 20 swings as `test_vectors_v0.npz`, which holds them).
- Repo size of `models/strokes/`: about 1.26 MB in total.

## Uncertainty threshold (11-class CNN)
Rule: report "unclassified" when the max class probability is below the threshold. The targets are not reachable; the stored fallback
(best accuracy keeping >= 20 % coverage) for the exported model is **0.990**, which keeps 20 % of swings at 45 % accuracy. It comes
from out-of-fold probabilities and must be re-picked once the model is useful. Both stored thresholds (this one and the group
model's) are tuned on the same out-of-fold predictions their coverage/accuracy figures are reported on, as are the variant choice
and winner, so those figures are in-sample for the selection and optimistic; use nested selection once there are >= 2 drill sessions.

## Known limits
One player, one clean session; no not_a_hit class; rally swings unused until reviewed; drill vs match gap untested; clipped swings
(speeds are lower bounds). The exported model has never been tested on an unseen session.

## Intended use
Personal v0: the group model for rough intensity breakdowns of the owner's own swings; the 11-class model is plumbing only. Neither is validated on anyone else.

## Files and retraining
- Experimental 11-class: `models/strokes/stroke_model_v0.onnx` (input `window` [batch, 500, 10], outputs `probabilities`, `side_probabilities`),
  `stroke_model_v0.json`, `test_vectors_v0.npz` (20 windows with expected outputs, 615 KB). If a GBM variant wins, the input is the
  listed features of `features.py` instead and `output_class_ids` names the modelled classes.
- onnxruntime vs Python on all 352 training swings: max abs probability difference 4.8e-07 (class), 3.0e-07 (side).
- Retrain: `tools/strokes/build_dataset.py`, then `tools/strokes/train.py --final` (evaluates all four variants x two tasks, exports the
  winner on the 11-class mean macro-F1; ~2 min). Reports go to `~/Datasets/oso-strokes/reports/`.
- What would change the picture: more drill sessions (>= 2 switches the evaluation to leave-one-session-out), more players, drill
  blocks that cover both sides of each class, and reviewed rally swings as the real-play test set.

## On-device trees (iPhone app, Phase 22)
The phone does not run the ONNX models. `tools/strokes/export_trees.py` (also called by `train.py --final`) retrains the **a_of** GBMs
on all drill swings and writes `models/strokes/stroke_group_v0.trees.json` (power/mid/touch + the FH/BH head),
`stroke_class_v0.trees.json` (10 classes; the 11-class a_of GBM, macro-F1 0.220 vs the CNN's 0.233: noise, so one tree evaluator serves
both) and `test_vectors_trees_v0.json` (20 swings: |w|, |a|, saturation, expected features and probabilities, plus a raw-packet window
fixture). Schema `volante-trees/1`: preorder trees (`f` feature, `v` threshold or leaf, `r` right child, `m` missing-left), float32
thresholds rounded down, softmax or sigmoid. The pure-Python evaluator equals sklearn to 6e-8 on all 352 drill swings; Swift equals the
vectors to 5e-7 relative (features) and 2e-16 (probabilities).
The side head is shared (same labels, features and seed for both models) and lives in the group file only.

**Display thresholds** (below: "Unclassified"): lowest max-probability threshold whose out-of-fold (block 5-fold) accuracy on accepted
swings is >= 0.80 (group) / >= 0.50 (10-stroke), keeping >= 5 % coverage. Group: **0.696, coverage 94 %, accuracy 80.1 %** (the
model is rarely unsure, so this mostly marks the weakest 6 %). 10-stroke: **target not reachable** (confidence does not predict
correctness; accuracy is 22-25 % at every threshold), fallback = best accuracy keeping >= 20 % coverage: **0.968, coverage 27 %,
accuracy 27 %**. Hence the app labels it "Beta guess" and "often wrong". The side head is near chance (see above); the app shows it only
when p > 0.6. Swings whose window leaves the recording or spans a gap > 10 ms are Unclassified.
