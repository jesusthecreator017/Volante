# Stroke dataset: classes and definitions

Design: `docs/superpowers/specs/2026-10-17-stroke-dataset-design.md` (on the stroke-dataset branch). The data itself lives in
`~/Datasets/oso-strokes/`, never in this repo. Operator guide: [collection.md](collection.md). Consent: [consent.md](consent.md).

## Forehand and backhand
Side is defined **relative to the racquet face and the hitting hand**, not the court:
- **Forehand (FH):** the palm side of the hitting hand leads, so the string face that faces the shuttle is the one in
  front of the palm.
- **Backhand (BH):** the back of the hand leads, and the other face of the racquet hits the shuttle (the racquet crosses the body).

So a left-handed player's forehand is their forehand: it is the stroke hit on their left-hand side, and `session.json` records
`player.handed` only so analyses can check this. Nobody mirrors data by handedness. Overhead strokes on the non-racket side
(a "round-the-head" shot) are forehand by this rule, because the palm still leads.

## Classes
| id | class | sides | definition |
|---|---|---|---|
| 0 | `not_a_hit` | none | Any racquet motion with no shuttle contact: waggles, shadow swings, walking, picking up shuttles |
| 1 | `serve_short` | FH/BH | Low serve that lands near the front service line, a slow, short push |
| 2 | `serve_long` | FH/BH | High, deep serve to the back line (the high-serve or the flick-style long serve) |
| 3 | `clear` | FH/BH | Overhead shot, high and deep to the opponent's back line |
| 4 | `smash` | FH (BH is rare, optional) | Full-power overhead hit steeply downward |
| 5 | `drop` | FH/BH | Overhead shot with a clear-like preparation, soft, lands near the net |
| 6 | `drive` | FH/BH | Flat, fast shot at about shoulder height, travelling parallel to the floor, with a full-ish swing; **not** a push |
| 7 | `lift` | FH/BH | Underarm shot from low (near the net or the mid-court), high and deep |
| 8 | `net_shot` | FH/BH | Soft touch from the net, the shuttle barely clears the tape (a tumble or a lift-over) |
| 9 | `block_push` | FH/BH | Short, compact stroke at the net or mid-court that redirects the incoming shuttle: a **push** is flat, with a little wrist; a **block** absorbs a smash or fast drive |
| 10 | `net_kill` | FH/BH | Quick downward put-away from above tape height at the net |

**Drive vs push:** a drive uses a swing (the racquet accelerates over a distance) and the shuttle is struck hard at shoulder
height; a push is a short, controlled stroke with little backswing. The protocol blocks make the players do one or the other, so
the label comes from the block, not from judging each swing.

## Mapping to Replay `STROKE_TYPES`
Replay (`crates/oso-render/src/replay.rs`) uses `unknown, serve, clear, smash, drop, drive, lift, net, swing_only, block, push,
press, net_kill, flick_serve`. IMU report classes map as follows (side is kept separately):

| IMU class | `STROKE_TYPES` name |
|---|---|
| `not_a_hit` | `swing_only` |
| `serve_short` | `serve` |
| `serve_long` | `serve` (`flick_serve` for the flick-style long serve, when the model can tell) |
| `clear` | `clear` |
| `smash` | `smash` |
| `drop` | `drop` |
| `drive` | `drive` |
| `lift` | `lift` |
| `net_shot` | `net` |
| `block_push` | `block` (`push` when the model can tell; the dataset label covers both) |
| `net_kill` | `net_kill` |

`press` and `unknown` have no IMU class: `unknown` is what a swing below the model's confidence threshold is reported as.
