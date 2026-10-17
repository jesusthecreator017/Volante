# Style profile `volante-style/1`

The Volante Twins profile: a few numbers about how a player swings, computed **locally** from their racquet-pod sessions
(`oso-rec profile`, or the phone app) and attached to an Arena model. The kit (`oso_arena.style`), the Pi and the site use the
same rules; `oso-rec` (`crates/oso-replay/src/imu/profile.rs`) has a twin validator. Design: `docs/superpowers/specs/2026-10-17-volante-twins-design.md`.
Schema: `style.schema.json`. The contract `oso-arena/1` is unchanged: the profile travels in the optional header key `style_profile`.

Stage 1 sees racquet motion only, so a twin copies **power, rhythm and (in 18b) stroke choice**, not court position or aim.

## 1. File

```json
{
  "schema": "volante-style/1",
  "created_at": "2026-10-17T18:00:00Z",
  "source": {"sessions": 3, "swings": 1240, "minutes": 52.4, "pod_fw": "0.1", "tool": "oso-rec 0.1.0"},
  "speed": {"p50_kmh": 58.0, "p90_kmh": 84.0, "cv": 0.27, "clipped_frac": 0.31},
  "tempo": {"median_gap_s": 1.9, "p25_gap_s": 1.4, "p75_gap_s": 2.6},
  "stroke_mix": null,
  "side_mix": null,
  "notes": "speeds are estimates (gyro x 0.53 m) and lower bounds when clipped"
}
```

| field | rule |
|---|---|
| `schema` | exactly `volante-style/1` |
| `created_at` | `YYYY-MM-DDTHH:MM:SSZ`, the creation time (the only timestamp) |
| `source.sessions`, `source.swings` | integers 1 to 10,000,000 |
| `source.minutes` | number above 0, at most 10,000,000: recorded time (first to last sample of each recording part) |
| `source.pod_fw` | 1-64 characters of `A-Z a-z 0-9 . _ - space` (the pod firmware version; `unknown` for a bare imu.bin). No pod ID |
| `source.tool` | same character set, e.g. `oso-rec 0.1.0` |
| `speed.p50_kmh`, `speed.p90_kmh` | 0-400, `p50 <= p90`. Estimated head speed per swing (section 2) |
| `speed.cv` | 0-2, std / mean of the swing speeds |
| `speed.clipped_frac` | 0-1, the share of swings during which the gyro clipped (the speed is then a lower bound) |
| `tempo.median_gap_s`, `p25_gap_s`, `p75_gap_s` | 0.2-10 s, `p25 <= median <= p75`. Time between consecutive swings inside a rally |
| `stroke_mix` | `null` until the Phase 16 classifier exists (18b); else an object class -> share (keys `[a-z_]`, at most 16), each 0-1, sum 1 +- 1e-3 |
| `side_mix` | `null` or `{"fh": x, "bh": y}`, each 0-1, sum 1 +- 1e-3 |
| `notes` | optional, `null` or printable ASCII, at most 280 characters |

Exactly these top-level keys (`schema`, `created_at`, `source`, `speed`, `tempo` required; no others), exactly these keys inside
`source`, `speed` and `tempo`. All numbers finite; counts are JSON integers. The file is at most **8192 bytes**.

**No personal data:** no names, no player or pod IDs, no timestamps other than `created_at`. The raw sessions never leave the player's computer.

## 2. How `oso-rec profile` computes it (v1; the phone app uses the same algorithm: `docs/twins-app.md`)

1. Rotate every sample into the racquet frame with the session's mount (a bare imu.bin: `--mount`, default flat).
2. Swings: the guided-collection swing counter (`swingdet`) with a **300 dps** threshold (soft strokes count; collection uses 400):
   the 20 ms-smoothed gyro norm rises through 300 dps, the counter re-arms 0.8 s later once the smoothed norm is below 150 dps.
3. Head speed of a swing: the peak, over the 0.8 s from its onset, of the angular rate perpendicular to the shaft
   (`hypot(wx, wz)` in the racquet frame) times 0.53 m (head centre) times 3.6 = km/h. The gyro bias is not removed. A saturated
   sample (an axis at the int16 limit, +-2000 dps) in that window marks the swing clipped.
4. Tempo: gaps between consecutive swing onsets of one recording part; a gap over 6 s ends the rally and is dropped.
5. Percentiles: linear interpolation between order statistics (numpy default). cv: population std / mean.
6. Every number is rounded to 3 significant digits; keys are written in the order above, so equal input gives equal bytes.

## 3. Build mapping (`oso_arena.style.build_from_profile`, a v1 heuristic)

A deterministic map to a valid build (four integers 1-10, sum 24), rounding half up:

| stat | from | 1 at | 10 at |
|---|---|---|---|
| power | `speed.p90_kmh` | 40 | 120 |
| accuracy | `speed.cv` | 0.6 | 0.1 |
| speed | `tempo.median_gap_s` (shorter = faster) | 3.0 s | 1.0 s |
| reach | not measurable by the pod: the remainder `24 - power - speed - accuracy` | | |

Linear in between, clamped to 1-10. When the remainder is outside 1-10, reach is clamped to 1 or 10 and the difference is spread one
point at a time over power, speed and accuracy, never leaving 1-10: each point is taken from the stat with the largest current value
(too many points) or given to the one with the smallest (too few); ties go to the larger (smaller) unrounded value, then in the
order power, speed, accuracy. Monotone in each input while the remainder is in 1-10. Example vectors (p90, cv, median gap -> power, speed, reach, accuracy):

| p90 | cv | gap | build |
|---|---|---|---|
| 84 | 0.27 | 1.9 | 6, 6, 5, 7 |
| 80 | 0.35 | 2.0 | 6, 6, 6, 6 |
| 40 | 0.6 | 3.0 | 5, 5, 10, 4 (all minimums: reach 10, the surplus spread) |
| 120 | 0.1 | 1.0 | 7, 8, 1, 8 (all maximums: reach 1, the excess spread) |
| 60 | 0.3 | 1.5 | 3, 8, 7, 6 |
| 110 | 0.45 | 2.8 | 9, 2, 9, 4 |

## 4. Training and fidelity

- **Tempo reward** (`oso-arena train --style FILE [--style-weight W]`): each hit that follows the same player's previous hit in the
  rally pays `W * exp(-(gap - median)^2 / (2 sigma^2))` (sigma = IQR / 1.349, at least 0.3 s). Only the first 10 shots of a rally pay
  (5 per player): at most `5 W` per rally and player (0.25 at the default `W` = 0.05; `W` is limited to 0.1), added on top of the default reward
  inside the per-rally shaping budget (< 1).
- **Fidelity** (`oso_arena.style.fidelity(profile, match_logs, build)`), computed from the model's own matches:
  `build_ok` = the submission's build equals `build_from_profile(profile)`; `tempo_ratio` = the model's median gap between its own
  consecutive hits in a rally / `tempo.median_gap_s`, which passes within **0.75-1.33**. `fidelity_ok` = every applicable check
  passes. 18b adds `mix_js` (Jensen-Shannon distance of the stroke mix, <= 0.15).
- **Sim to profile classes (18b):** the sim produces clear, smash, drop, drive, lift, net and serves; `block_push` and `net_kill`
  have no distinct sim shot and map to the nearest class (`net` for both).

## 5. In a submission

`style_profile` in the safetensors header: the profile as compact JSON (no spaces), at most 8192 bytes, valid by section 1. `oso-arena check`
(and the Pi upload check) reject a file whose `style_profile` is present and invalid. `oso-arena export --style FILE` embeds it and
forces the build to `build_from_profile` (a different `--build` is refused). The Pi admits a model to the Twins ladder only when
`fidelity_ok` holds after at least 10 ladder matches, and re-checks weekly.
