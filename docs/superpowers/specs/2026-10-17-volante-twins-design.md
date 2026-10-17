# Volante Twins ("play like me"): design

Date: 2026-10-17. Status: approved in conversation.

## 1. Goal
A player's real games, recorded by the Volante pod, shape an Arena AI that plays in their style: their "twin". Twins compete on
a separate **Twins ladder**, and the Pi admits a twin only if it really plays like its profile.

### User decisions
- **Purpose:** play like me (style imitation), not data donation.
- **Data:** pod only, staged. Stage 1 uses racquet data only. Stage 2 (later, optional) adds video for positioning and
  placement.
- **Training:** on the user's computer with the kit. The Pi trains nothing.
- **Privacy:** raw sessions stay local, and only the style profile (a few numbers) is uploaded, attached to a model.
- **Competition:** a separate Twins ladder with its own Elo and weekly tournament.
- **Verification:** a fidelity check on the Pi (measured play vs the profile). The profile itself is trusted and shown publicly
  with its data volume.
- **Brand:** Volante is the user-facing name; internal identifiers keep the `oso` prefix.

### Honest limit
The pod sees racquet motion, not court position or shot placement. Stage-1 twins copy **stroke choice, power and rhythm**, not
where the player stands or aims.

## 2. Style profile `volante-style/1` (contract)
A JSON file computed locally from one or more pod sessions. It is the source of truth in `arena/contract/STYLE_PROFILE.md` plus
`style.schema.json`.

```json
{
  "schema": "volante-style/1",
  "created_at": "2026-10-17T18:00:00Z",
  "source": {"sessions": 3, "swings": 1240, "minutes": 52.4, "pod_fw": "0.1", "tool": "oso-rec 0.x"},
  "speed": {"p50_kmh": 58.0, "p90_kmh": 84.0, "cv": 0.27, "clipped_frac": 0.31},
  "tempo": {"median_gap_s": 1.9, "p25_gap_s": 1.4, "p75_gap_s": 2.6},
  "stroke_mix": null,
  "side_mix": null,
  "notes": "speeds are estimates (gyro × 0.53 m) and lower bounds when clipped"
}
```

**Fields:**
- `stroke_mix`: 11 classes summing to 1. `side_mix`: `{"fh": 0.7, "bh": 0.3}`. Both are **null until Phase 16's classifier**
  exists (18b).
- `speed`: per-swing estimated head speed over detected swings (≥ the hit threshold).
- `tempo`: the time between consecutive swings within rallies. Swings > 6 s apart start a new rally and don't count.

**No personal data:** no names, no pod IDs (only the firmware version), no timestamps finer than the creation time.

**Validation** (the kit, the Pi and the site use the same rules):
- all numbers finite and in sane ranges (speed 0-400 km/h, gaps 0.2-10 s, cv 0-2)
- source counts ≥ 1 and ≤ 10⁷
- mixes non-negative and summing to 1 ± 1e-3
- the file ≤ 8 KB.

## 3. Build from profile (deterministic, `oso_arena.style.build_from_profile`)
The heuristic v1 must always produce a valid build (1-10 each, sum 24):
- **power:** from `speed.p90_kmh`, linear 40 → 1, 120 → 10.
- **accuracy:** from consistency `1 - cv`, with cv 0.6 → 1 and cv 0.1 → 10.
- **speed:** from tempo, a shorter `median_gap_s` meaning faster. 3.0 s → 1, 1.0 s → 10.
- **reach:** not measurable by the pod. It takes the remainder.

Then rebalance to sum 24, keeping each value in 1..10 by moving points from or to `reach` first, then from the largest or
smallest raw stat. Ties are broken in the fixed order power, speed, accuracy, reach.

Document it as a v1 heuristic, and test that it's monotonic and always valid.

## 4. Training (`oso-arena train --style my_style.json`)
- **18a:** sets `--build` from the profile, and adds a **tempo reward**: a small shaping term pulling the gap between the
  model's own consecutive hits toward the profile's median (Gaussian, bounded so it never outweighs winning). Plus
  `--style-weight` (default 0.05 per shot, within the existing per-rally shaping budget < 1).
- **18b:** adds a **stroke-mix reward**: per-shot credit for strokes under-represented versus the profile (the sim shot labels
  are mapped to profile classes). Plus a KL penalty on the evaluated mix.

  **Sim ↔ profile class mapping:** `arena/contract/STYLE_PROFILE.md` documents which profile classes the sim can produce. The
  sim has clear, smash, drop, drive, lift, net, and serves; there's no distinct block_push or net_kill, which map onto the
  nearest classes.

## 5. Fidelity (`oso_arena.style.fidelity(profile, match_logs) -> FidelityReport`)
Computed from the model's own matches.

- **18a:**
  - `build_ok`: the submission's build equals `build_from_profile(profile)`, exactly.
  - `tempo_ratio`: model median gap / profile median gap. It passes if within 0.75-1.33.
- **18b:** adds `mix_js`, the Jensen-Shannon distance between the model's stroke mix and the profile's. It passes if ≤ 0.15.

`fidelity_ok` = all applicable checks pass. `oso-arena eval --style` prints the report. The Pi runs the same function on the
model's ladder matches (after ≥ 10 matches) to admit it to the Twins ladder, and re-checks weekly.

## 6. Submission
- The optional header key `style_profile`: compact JSON (≤ 8 KB) in the safetensors metadata. Contract `oso-arena/1` already
  ignores unknown keys, so this is additive. `check_file` validates it when present.
- `oso-arena export --style my_style.json` embeds it and **forces the build from the profile** (it refuses a mismatched
  `--build`).

## 7. Components and owners
| Part | Phase | Owner |
|---|---|---|
| `STYLE_PROFILE.md` + schema; kit `oso_arena.style` (validate, build_from_profile, fidelity, tempo reward); `export/check/eval/train --style` | 18a | us |
| `oso-rec profile <session dirs or imu.bin…> -o my_style.json` (swing detection = 16.1 `swingdet`, speed = 2026-10-17 analysis method, tempo) | 18a | us |
| Pi: Twins ladder (own Elo, weekly tournament), fidelity gate (≥ 10 matches, weekly recheck), twin card API | 18a doc → Pi agent | Pi agent (prompt addition) |
| Site: Twins ladder page, twin card (profile + data volume + fidelity), upload shows the attached profile | 18a doc → site | site owner |
| App: compute and export the profile on the phone | 18a doc → app | friend (handoff note) |
| Stroke mix + side mix in the profile; mix reward; JS fidelity | 18b | us, after Phase 16 |

## 8. API additions (for the Pi agent and the site, additive)
- `ModelSummary` gains:
  - `twin: {"profile": StyleProfile, "fidelity": {"ok": bool, "tempo_ratio": f, "mix_js": f|null, "checked_at": "..."}} | null`
  - `ladder: "open" | "twins"`.
- `GET /api/ladder?season=&ladder=twins`
- `GET /api/tournaments?ladder=twins`

Upload is unchanged; the Pi reads `style_profile` from the header.

## 9. Testing
- Profile: schema round trip, validation edge cases, determinism (same sessions → byte-identical profile).
- `oso-rec profile` on the real 2026-10-17 recordings: the speed p50/p90 match the analysis (mean ~69 km/h, max ~95 km/h on
  `smashes_live1`) within 5 %.
- Build mapping: always valid, monotonic, a fixed vector table.
- Tempo reward: bounded, and the shaping budget stays < 1 per rally per player.
- Fidelity: a synthetic match log gives the expected tempo ratio; a mismatched build fails.
- Export and check: profile embedded, an oversize/invalid profile rejected, the build forced.
