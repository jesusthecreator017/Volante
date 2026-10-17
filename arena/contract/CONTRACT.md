# Contract `oso-arena/1`

Source of truth for the Arena kit (`arena/kit/oso_arena`) and the Pi site. **The kit loads its constants from code, not from this
file**; `arena/kit/tests/test_contract.py` asserts that the tables below (build mapping, observation indices) match the code. Any change
to the observation, the actions, the physics or the build mapping bumps the version (`oso-arena/2`) and starts a new season.

Version string: `oso-arena/1`.

## 1. Submission file

One `.safetensors` file of at most **1 MiB (1,048,576 bytes)**. A normal file is about 308 KB.

Tensors: exactly these six, all **float32** (`F32`), nothing extra, no other dtype.

| name | shape |
|---|---|
| `actor.0.weight` | [256, 37] |
| `actor.0.bias` | [256] |
| `actor.2.weight` | [256, 256] |
| `actor.2.bias` | [256] |
| `actor.4.weight` | [6, 256] |
| `actor.4.bias` | [6] |

Every value is finite with `|w| <= 1000`.

Header metadata (the safetensors `__metadata__` map, string to string). Unknown keys are ignored. Schema: `header.schema.json`.

| key | rule |
|---|---|
| `contract` | exactly `oso-arena/1` |
| `name` | 1-40 characters, all printable (no control, format or surrogate characters; a newline is rejected), and not only whitespace |
| `description` | optional, 0-280 characters; control characters other than newline and tab are rejected |
| `build` | a JSON object `{"power":p,"speed":s,"reach":r,"accuracy":a}` as a string. Exactly these 4 keys; each value a JSON **integer** 1-10 (not `6.0`, not `"6"`, not `true`); the sum is exactly **24** |

Security: a submission is untrusted input. It is parsed by hand (8-byte little-endian header length, a JSON header of at most 64 KiB,
offsets) and then by the `safetensors` library only. Nothing on this path may use pickle, `torch.load` or `numpy.load(allow_pickle=True)`.

## 2. The network

```
h1 = tanh(W0 . o + b0)          W0 = actor.0.weight, b0 = actor.0.bias
h2 = tanh(W2 . h1 + b2)         W2 = actor.2.weight, b2 = actor.2.bias
a  = clip(W4 . h2 + b4, -1, 1)  W4 = actor.4.weight, b4 = actor.4.bias
```

`o` is the 37-float observation of section 4, `a` the 6 actions of section 5. This is the deterministic mean action (no sampling).
`oso_arena.policy.NumpyPolicy` computes it in float64.

## 3. Stat builds (mapping table)

Four integer stats, each 1-10, sum 24. Piecewise linear 1 -> 6 -> 10. A 6 in every stat equals the Phase 13.2 constants. The simulator
holds the parameters per player; rules, shuttle physics, `REACTION_S`, jump height and freezes are the same for everyone.

| parameter | stat | s = 1 | s = 6 | s = 10 | meaning |
|---|---|---|---|---|---|
| `launch_hi` | power | 60 | 90 | 110 | max launch speed (m/s) at contact height >= 2.6 m |
| `launch_lo` | power | 20 | 25 | 30 | max launch speed (m/s) at contact height <= 1.0 m (linear in between) |
| `v_max` | speed | 4.0 | 5.0 | 6.0 | max running speed (m/s) |
| `a_max` | speed | 8 | 10 | 12 | max acceleration (m/s^2) |
| `reach` | reach | 1.2 | 1.5 | 1.8 | normal racquet reach (m) |
| `reach_lunge` | reach | 1.7 | 2.0 | 2.3 | extended reach with a lunge (m) |
| `noise_mult` | accuracy | 1.4 | 1.0 | 0.6 | multiplies the whole aim-noise sigma |

The fastest allowed flight time of a shot depends on the hitter's launch cap, so power changes which shots are possible, not only
how hard a clamped shot is.

## 4. Observation (37 floats)

Observation of player `p`, built every 30 Hz decision step. **Own view:** x and z are multiplied by `m` (+1 for player 0, who stands
on the -X half; -1 for player 1, who stands on the +X half), so every player sees the same picture: own half at x < 0, the net at
x = 0, the opponent at x > 0, +x toward the net. Heights (y) and scalars are not mirrored. Court: half length 6.70 m, singles half
width 2.59 m, net height 1.524 m. Velocities and times are in m/s and s.

| idx | name | meaning | scaling |
|---|---|---|---|
| 0 | own_x | own court x | x * m / 7 |
| 1 | own_z | own court z | z * m / 3 |
| 2 | own_vx | own velocity x | vx * m / 5 |
| 3 | own_vz | own velocity z | vz * m / 5 |
| 4 | opp_x | opponent court x | x * m / 7 |
| 5 | opp_z | opponent court z | z * m / 3 |
| 6 | opp_vx | opponent velocity x | vx * m / 5 |
| 7 | opp_vz | opponent velocity z | vz * m / 5 |
| 8 | shuttle_x | shuttle x | x * m / 7 |
| 9 | shuttle_y | shuttle height | y / 5 |
| 10 | shuttle_z | shuttle z | z * m / 3 |
| 11 | shuttle_vx | shuttle velocity x | clip(vx * m / 20, -4.5, 4.5) |
| 12 | shuttle_vy | shuttle velocity y | clip(vy / 20, -4.5, 4.5) |
| 13 | shuttle_vz | shuttle velocity z | clip(vz * m / 20, -4.5, 4.5) |
| 14 | incoming | 1 when the opponent made the last hit of a rally in progress (a shot is coming), else 0 | 0 / 1 |
| 15 | land_x | predicted landing x of the last shot (its aim point including the aim noise; at a rally start, the toss's x) | x * m / 7 |
| 16 | land_z | predicted landing z, same shot | z * m / 3 |
| 17 | land_t | time until that landing (s) | clip(t, 0, 4) / 3 |
| 18 | can_hit | 1 when a swing now would hit at normal reach (shuttle on own half, within `reach` horizontally, 0.15 <= y <= 2.9 m, not the last hitter, 0.35 s since own last hit, not frozen) | 0 / 1 |
| 19 | can_hit_stretch | 1 when a swing now would hit only with a lunge or jump (within `reach_lunge`, y <= 3.2 m) and not at normal reach | 0 / 1 |
| 20 | freeze | remaining freeze after a lunge (0.25 s) or jump (0.3 s) hit, during which the player cannot move | freeze / 0.3 |
| 21 | hit_gap | time since own last hit | min(t / 0.35, 1) |
| 22 | serve_now | 1 when own serve is to be hit now (own serve, toss not yet hit) | 0 / 1 |
| 23 | is_server | 1 when own side serves this rally | 0 / 1 |
| 24 | server_parity | the server's score mod 2 (even: serves from the right court); the same value for both players | 0 / 1 |
| 25 | own_score | own points | score / 21 |
| 26 | opp_score | opponent points | score / 21 |
| 27 | rally_shots | shots hit in this rally so far | min(n, 40) / 20 |
| 28 | reaction | remaining reaction time after the opponent's hit (0.2 s, commands ignored, velocity held) | t / 0.2 |
| 29 | own_power | own build, power | (s - 5.5) / 4.5 |
| 30 | own_speed | own build, speed | (s - 5.5) / 4.5 |
| 31 | own_reach | own build, reach | (s - 5.5) / 4.5 |
| 32 | own_accuracy | own build, accuracy | (s - 5.5) / 4.5 |
| 33 | opp_power | opponent build, power | (s - 5.5) / 4.5 |
| 34 | opp_speed | opponent build, speed | (s - 5.5) / 4.5 |
| 35 | opp_reach | opponent build, reach | (s - 5.5) / 4.5 |
| 36 | opp_accuracy | opponent build, accuracy | (s - 5.5) / 4.5 |

## 5. Actions (6 floats, 30 Hz, clipped to [-1, 1])

The action is held for the 4 simulation steps (1/120 s each) of a decision step. All coordinates are in the own view of section 4.

| idx | name | meaning |
|---|---|---|
| 0 | move_x | target velocity x = a0 * `v_max` (m/s); +x is toward the net |
| 1 | move_z | target velocity z = a1 * `v_max` (m/s). The target vector is capped at `v_max` in length; velocity changes by at most `a_max` per second. Ignored while frozen or reacting |
| 2 | swing | > 0: swing. The shuttle is hit when it is within reach (obs 18 or 19) and the hit rules hold; otherwise nothing happens |
| 3 | aim_x | target landing x = 3.35 * (1 + a3): -1 is the net, +1 the opponent's back line (6.70 m). For a **serve** the range is the short service line (1.98 m) to the back line instead |
| 4 | aim_z | target landing z = 2.59 * a4 (singles half width). For a **serve**: the distance from the centre line (a4 = -1) to the side line (a4 = +1), on the diagonal service court |
| 5 | flight_time | u in [-1, 1]: flight time T = t_min + (u + 1)/2 * (t_max - t_min) seconds, with D the horizontal distance to the target, t_min the fastest flight within the hitter's launch cap at that contact height, t_max = clip(max(1 + 0.1 D, t_min + 0.25), 0.3, 3.0) |

Execution noise: the aim point gets Gaussian noise (x and z, independent) with sigma = (0.15 + 0.25 v/90 + 0.35 (v/90)^2 + 0.3 [lunge or
jump] + 0.2 s/`v_max`) * `noise_mult` * (1 + 1.5 (1 - q)), with v the launch speed (m/s), s the hitter's speed and q the contact quality
(section 9). A shot faster than the launch cap x (0.6 + 0.4 q) is slowed to that cap (it then falls short).

## 6. Match rules

A match is **best of 3 games** (BWF laws with 15-point games).

- **Each game:** 15-point rally scoring, win by 2, capped at 21 (at 20-20 the 21st point wins). The winner of a rally serves next. The server serves from the right court at an even own score, the left at an odd score, into the diagonal service court (short service line to back line, centre line to side line).
- **Serving order:** the first server of game 1 is random from the seed (the first draw of `numpy.random.default_rng(seed).integers(0, 2, 1)`). **The winner of a game serves first in the next game.**
- **Laws enforced by the simulator (singles).** Each fault loses the rally for the player named; the `reason` is the name in the match log (`rallies_detail`, the `point` event) and in `evaluate` faults.

  | reason | fault | law |
  |---|---|---|
  | `serve` | the toss is not hit; the serve lands outside the diagonal service court; the serve is struck with the shuttle centre above 1.15 m (the toss starts at 1.1 m, so this cannot happen in normal play) | 9.1, 9.1.3 |
  | `net` | the shuttle passes under the net tape (also a serve), or ends on the hitter's own side (falls back from the net cord) | 13.4 |
  | `net_touch` | a hit whose contact point is within 0.10 m of the net plane (`|x| < 0.10`) with the player extended (a lunge or jump), at once, hitter loses. Approximation of "touches the net" | 13.4.3 |
  | `out` | a shot lands outside the singles court (lines are in) | 13.1 |
  | `ceiling` | the shuttle goes above 11.5 m or beyond 12 m in x or 9.5 m in z (the hall), hitter loses | ceiling or obstacle |
  | `missed` | the shuttle lands in bounds on the opponent's side: the hitter wins the rally (the opponent did not return it) | |
  | `let` | the rally exceeds 150 shots: replayed, nobody scores. The only let | 14 |

  Other laws: server and receiver **stand still until the serve is struck** (their commanded velocity is ignored, the server may swing; 9.1); a player may hit the shuttle only on their own side of the net (13.4.1); no player hits twice in a row and 0.35 s must pass since their last hit (13.4.5); the shuttle is hit by the racquet only.
- A shuttle that lands in bounds on the hitter's opponent's half is won by the hitter.
- **Intervals** (time only, no effect on play): 60 s when the leading score first reaches 8 in a game, 120 s between games. They count in `wall_duration_s` and appear as pauses in the replay (shown at most 8 s long).
- **Change of ends:** after game 1, after game 2, and in game 3 when the leading score first reaches 8. The simulator is mirror-symmetric, so this only flips which world side each player occupies; every rally in the log records it (`ends`: 0 = player A on the -X half, 1 = on +X). Game 1 starts with A on -X.
- A rally of more than **150 shots** is a let: replayed, nobody scores.
- A match over **150 simulated minutes** of play (intervals excluded) is void (no winner).
- Players: A (match argument `a`) and B (`b`). `score` in the match log is games won; each game's points are in `games` and `rallies_detail`.
- The check's smoke test and nothing else overrides the game points (`target`) and the number of games.

## 7. Determinism

The only randomness is the simulator's execution noise (section 5), driven by the match seed. The same models, builds and seed give an
identical match, an identical match log and identical log bytes (gzip header time 0, no pickle). The log records the contract, the kit
version, the seed, both builds and both model hashes.

## 8. Check list (`oso-arena check`, identical to the Pi upload check)

In order; reasons are collected up to the first fatal one, and a broken file never reaches step 6.

1. The file is at most 1 MiB.
2. It is a safetensors file: 8-byte header length at most 64 KiB, a JSON header, then the `safetensors` loader. Never pickle.
3. Exactly the six tensors of section 1, all F32, with the listed shapes.
4. All values finite, `|w| <= 1000`.
5. Header: `contract == oso-arena/1`; name 1-40 printable characters, not only whitespace; description at most 280 characters; build valid (section 1).
6. Smoke test.

Result: `ok`, or the list of human-readable reasons; `meta` = name, description, build, sha256 of the file bytes.

### Smoke test

Two matches against the scripted bot (all-6 build), each **one game to 5 points** (a smoke test, not a full match; win by 2 still applies):

| game | seed | model is |
|---|---|---|
| 1 | 1 | player 0 |
| 2 | 2 | player 1 |

Passes when both games run with no exception and every action is finite. A game still running after 10 simulated minutes is stopped
and counts as run (not a failure).

## 9. Shuttle physics and racquet contact

All of this is in the simulator; none of it changes the observation or action sizes.

- **Flight:** quadratic drag, terminal velocity 6.8 m/s (the fitted model in `physics.py`), midpoint RK2 at 1/120 s. **Turnover:** for 0.03 s after every hit the drag is multiplied by 1.6, decaying linearly to 1. The shot solver (`shot_table.npz`, table version 3) uses the same flight, so an intended landing point is reached (within 1 cm without noise).
- **Net cord:** the tape is a horizontal cylinder of radius 0.02 m along z, centred at 1.524 m at the middle of the net rising linearly to 1.55 m at the posts (|z| = 3.05 m). When the shuttle centre comes within 0.05 m (tape plus shuttle radius 0.03 m) of the tape axis, the velocity component normal to the tape is reflected with restitution 0.15, the tangential part (also z) is multiplied by 0.5, and the x velocity gets Gaussian noise (sigma 0.2 m/s) from the match seed. The shuttle then flies on under the drag model: it may roll over (play on) or fall back (`net`). A `cord` event is logged.
- **Floor:** the shuttle stops at the floor, no bounce.
- **Contact quality q in [0, 1].** The contact point is the shuttle position at the hit, in the hitter's body frame: forward f (toward the net), lateral l (+ on the racquet side, the right hand: +z for player 0, -z for player 1, in court coordinates), height y. Ideal zone: f 0.2-0.7 m, l 0.2-0.9 m, y 0.9-2.9 m; the lower f limit relaxes from 0.2 to -0.1 as y goes from 2.0 to 2.6 m (an overhead stroke). `q = 1 - (zone + cramped + late + backhand + stretch)`, clipped to [0, 1], with
  - zone = 0.6 * min(1, d / (0.8 * reach)), d = distance from the contact point to the ideal zone
  - cramped = 0.25 * clip(1 - r / 0.35, 0, 1), r = horizontal distance from the body
  - late = 0.4 * clip(excess / 0.5, 0, 1), excess = how far behind the (relaxed) lower f limit
  - backhand = 0.2 * clip(-l / 0.4, 0, 1)
  - stretch = 0.15 when the hit is a lunge or a jump.
- **Effect of q:** the launch cap is multiplied by (0.6 + 0.4 q); the aim noise sigma by (1 + 1.5 (1 - q)). **Mishit** when q < 0.15: the shuttle leaves at 40 % of the intended launch speed with the elevation and the heading each off by a Gaussian error of sigma 25 degrees.
- **Replay:** `animate` moves the body so the contact is 0.78-1.08 m from the right shoulder (a step, hop or crouch) and solves the right arm so that the racquet's sweet spot (0.55 m from the grip along the forearm, as the viewer draws the racquet) is on the shuttle at every contact frame.
- The match log records `q` (and the body-frame contact point, `mishit`) on every `hit` event and `contact_q` per rally in `rallies_detail`.
