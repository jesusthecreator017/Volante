# Volante Twins: what the site shows (for the site owner)

Additive to the Arena pages; the API is in `arena/handoff/PROMPT.md` section 14 (`ModelSummary.twin`, `ModelSummary.ladder`,
`GET /api/ladder?ladder=twins`, `GET /api/tournaments?ladder=twins`). The profile file is `arena/contract/STYLE_PROFILE.md`; the types
below are TypeScript for `site/src/types.ts`. Nothing here needs the stroke classifier (Phase 16): the stroke mix shows
"coming with stroke recognition".

```ts
type StyleProfile = {
  schema: "volante-style/1"; created_at: string;
  source: { sessions: number; swings: number; minutes: number; pod_fw: string; tool: string };
  speed: { p50_kmh: number; p90_kmh: number; cv: number; clipped_frac: number };
  tempo: { median_gap_s: number; p25_gap_s: number; p75_gap_s: number };
  stroke_mix: Record<string, number> | null; side_mix: { fh: number; bh: number } | null; notes?: string | null;
};
type Twin = { profile: StyleProfile; fidelity: { ok: boolean; tempo_ratio: number | null; mix_js: number | null; checked_at: string | null } };
// ModelSummary gains:  ladder: "open" | "twins";  twin: Twin | null
```

## Twins ladder page (`/arena/twins`)
- The Ladder page with `ladder=twins`: same table (rank, model, rating, games, W-L), plus a column with the twin's data volume
  ("1,240 swings, 52 min") and a fidelity badge. A tab or toggle "Open | Twins" on the Ladder, Tournaments and Home previews.
- The text explains: a twin is a model trained from a real player's racquet sessions; it joins this ladder only after it has played at
  least 10 matches and passed the fidelity check (build matches the profile, tempo within 0.75-1.33 of the player's); the check
  repeats weekly. Twins play twins and the house players. The weekly Twins tournament follows the open one's rules.
- Empty state (no twins yet): "No twins yet. Record with the Volante pod, make your style profile, train a twin."

## Twin card (on the model page of a model with `twin != null`)
- Header: the model name, a "Twin" chip, the fidelity badge: green "Plays like its profile" when `fidelity.ok`, amber "Checking" when
  `checked_at` is null, red "Does not match its profile yet" when not ok (with the reasons the API gives, or the numbers below).
- **Profile display** (all from `twin.profile`, shown as given: it is trusted, self-reported data, say so in one line):
  - Data volume: `source.swings` swings, `source.sessions` sessions, `source.minutes` minutes (round to whole minutes).
  - Power: head speed `p50_kmh` typical, `p90_kmh` hard; if `speed.clipped_frac` > 0.2 add "(lower bounds: the sensor clips on hard swings)".
  - Consistency: `cv` as "steady" (< 0.1), "mixed" (0.1-0.2), "wild" (> 0.2); cv is over the power swings (at or above the median speed), with the number in a tooltip.
  - Tempo: `median_gap_s` seconds between shots, with the 25-75 % range `p25_gap_s`-`p75_gap_s`.
  - Stroke mix: `stroke_mix` is `null` for now: show "Stroke mix: coming with stroke recognition". When it is an object (18b), draw bars sorted by share. Same for `side_mix` (forehand / backhand).
- Fidelity details: `tempo_ratio` as "plays 1.1x as slow as its profile" (ratio > 1 slower, < 1 faster, with the pass band 0.75-1.33);
  `mix_js` only when not null; `checked_at` as a date.
- The model's build (power/speed/reach/accuracy) is shown as for every model; for a twin add "Build derived from the profile" (it is forced, `STYLE_PROFILE.md` section 3).

## Upload page
- After choosing a file, if the API echoes `twin` (or the page reads the header client-side, optional), show "This file carries a style
  profile: N swings, M min" before the user confirms. A rejected file shows the `header style_profile:` reasons verbatim.
- Add one line to Get started: "Make your twin: `oso-rec profile <session folder> -o my_style.json`, `oso-arena train --style my_style.json`,
  `oso-arena export ... --style my_style.json`" (the kit docs have the steps).

## Privacy copy (Terms/Privacy additions)
- Raw pod sessions stay on the player's computer or phone. Only the style profile (a few numbers: speeds, tempo, counts) is uploaded, inside the model file.
- The profile contains no name, no pod ID, no timestamps except its creation time. It is shown publicly on the model page. Deleting the model or the account deletes it.
