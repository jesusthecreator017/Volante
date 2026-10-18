# Session report `volante-report/1`

What one session was: the numbers behind the app's post-session Summary, the website's report page and `oso-rec report`
(Phase 16.5). Platform-neutral JSON; the schema is `report.schema.json`, examples are `report.example.json` (no strokes) and
`report.example-strokes.json` (with the stroke model's output). The reference implementation is `tools/strokes/report.py`;
the iPhone app (`apple/OsoKit/Sources/OsoKit/SessionReport.swift`) must reproduce its parity vector (section 5).

**Shareable by design:** no names, pod IDs, places or device times. The only timestamp is `session.started_at`.

## 1. Fields

| field | meaning |
|---|---|
| `schema` | exactly `volante-report/1` |
| `session.started_at` | `YYYY-MM-DDTHH:MM:SSZ`, when the recording started (UTC) |
| `session.duration_s` | recorded time: first to last sample of each recording part, summed |
| `session.active_s` | time in rallies: for each rally, its first to last swing onset plus 0.8 s; a lone swing counts 0.8 s |
| `session.source` | `pod` (racquet) or `watch` (wrist: speeds are hand speeds and lower) |
| `session.pod_fw` | firmware version, `unknown` when not known (same character set as the style profile) |
| `swings.count` | swings: the 300 dps swing counter of the style profile (`STYLE_PROFILE.md` section 2) |
| `swings.per_min_active` | `count / (active_s / 60)`; `null` when `active_s` is 0 |
| `speed.p50_kmh`, `p90_kmh`, `max_kmh` | head speed per swing (peak of hypot(wx, wz) in the racquet frame over 0.8 s x 0.53 m x 3.6); `null` with no swings |
| `speed.clipped_frac` | share of swings during which the gyro clipped (their speed is a lower bound) |
| `speed.estimated` | always `true`: every speed is an estimate. A UI must say so |
| `tempo.median_gap_s`, `p25_gap_s`, `p75_gap_s` | gaps between consecutive swing onsets of a part, gaps over 6 s dropped; `null` when there are none |
| `rallies.count` | runs of 2 or more swings with every gap at most 6 s (a lone swing is not a rally) |
| `rallies.median_shots`, `longest_shots` | swings per rally; `null` / `0` with no rallies |
| `timeline[]` | one entry per started minute from the first sample: `t_min` (0, 1, ...), `swings` with an onset in that minute, `p90_kmh` of them (`null` below 3 swings), `hr_bpm` (mean heart rate in that minute, `null` without heart rate) |
| `heart_rate` | `{"avg_bpm", "max_bpm"}` over the session, or `null` without a watch or Fitbit |
| `strokes` | `null` until a stroke model ran (section 3) |
| `insights[]` | 0-4 observations (section 4); empty from the reference implementation |
| `model` | `{"name", "version"}` of the stroke model, or `null` |

Percentiles interpolate linearly between order statistics (numpy's default). Every real number is rounded to **3 significant digits**
(as in the style profile); counts are integers. Keys appear in the order above.

## 2. Nullability rules
- No heart rate: `heart_rate` is `null` and every `timeline[].hr_bpm` is `null`. Never 0.
- No swings: `speed.*_kmh` and `tempo.*` are `null`, `clipped_frac` is 0, `rallies.count` is 0.
- `strokes` and `model` are both `null` or both present.

## 3. Strokes (when a model ran)

```json
"strokes": {
  "groups": {"power": 61, "mid": 140, "touch": 90, "unclassified": 7},
  "counts": {"smash": {"FH": 12, "BH": 1}, "drop": {"FH": 20, "BH": 9}},
  "speed_by_class": {"smash": {"p50_kmh": 96.0, "p90_kmh": 112.0}},
  "unclassified": 7,
  "experimental": true
}
```
- `groups`: the 4-group model (power = smash, clear; mid = drive, drop, lift, net_kill, serve_long; touch = net_shot, block_push,
  serve_short), the recommended v0 (`docs/strokes/model-card.md`). `counts` / `speed_by_class` hold class names of
  `docs/strokes/README.md`; FH/BH only when the side model is trusted (v0's is not: omit the side keys, use `{"all": n}`).
- `experimental: true` while the model card says so; a UI must label stroke numbers "experimental".

## 4. Insights
`{"id", "severity": "info" | "tip" | "praise", "title", "body", "evidence": {name: number}}`. Rules are deterministic, cite every
number they use in `evidence`, never give medical advice, never claim what the sensor cannot see (placement, court position,
"technique errors"), and never fire on fewer than 30 swings. The rule set lives with the implementation
(`SessionReport.swift`, `InsightRules`) and its thresholds are named constants there.

## 5. Parity
`arena/kit/tests/data/twins_vector.imu.bin` (identity mount, `started_at` 2026-10-17T18:00:00Z, source `pod`, fw `unknown`, no heart
rate) gives `tools/strokes/data/report_vector.json`. Implementations must match it exactly after the 3-significant-digit rounding
(`tools/strokes/test_report.py` checks the Python side, `StyleProfileTests`/`SessionReportTests` the Swift side).
