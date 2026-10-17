# House Base: the packaged base model

`oso_arena/data/base_40min.safetensors` (submission file, name "House Base", all-6 build) and
`oso_arena/data/base_40min_full.safetensors` (actor + critic + exploration noise, for `train --init base`). The file names keep the
`40min` of the original design; this model took longer.

## Training
- `oso-arena train`, default reward, the all-6 build against random opponent builds, 1024 games x 64-step horizon, seed 15, a
  300-minute schedule on an M-series Mac (MPS for the update), from random weights (the Phase 13.2 warm start was not available).
- A first run stopped at 118 minutes (disk full) and was resumed from its own checkpoint with the budget raised from 240 to 300 minutes.
- Shipped: the **254-minute checkpoint**, the first with 100 % wins against the bot in the trainer's full-game check.

## Gate (spec 4: at least 80 % wins against the scripted bot at the all-6 build, 200 games)
`oso-arena eval base_40min.safetensors --vs bot --games 200`:

| | |
|---|---|
| win rate | **1.00** (200 of 200 games; mean score 15.0 to 5.9) |
| rally shots | mean 4.94, max 27 |
| faults per rally | net 0.280, out 0.252, missed 0.376, **serve 0.003**, net_touch 0.088 |
| shot mix | serve 20.6 %, clear 18.0 %, drop 14.2 %, smash 7.5 %, lift 5.2 %, drive 0.0 %, net 0.0 %, unknown 34.4 % |
| displacement | mean 3.46 m (median 3.55 m); opponent run per shot 2.68 m |
| contact | quality 0.536, mishit rate 14.4 % |

Not compared against the Phase 13.2 40-minute model (the "no worse than" gate): that file is not in the repo.

## The same weights at the seven style builds (200 games each against the bot)
Re-exported with `--build`; a user's training changes the play, this only shows the starting point is sound for every preset.

| style | build P/S/R/A | win rate | rally mean | serve faults/rally | clear | smash | drop | lift |
|---|---|---|---|---|---|---|---|---|
| Smasher | 9/6/5/4 | 0.935 | 4.51 | 0.001 | 21.0 % | 9.3 % | 10.3 % | 7.1 % |
| Wall | 3/8/8/5 | 1.000 | 4.83 | 0.000 | 18.2 % | 6.3 % | 18.0 % | 5.8 % |
| Net Dominator | 3/6/7/8 | 1.000 | 4.66 | 0.000 | 17.6 % | 6.1 % | 18.1 % | 6.4 % |
| Controller | 4/6/5/9 | 1.000 | 4.38 | 0.000 | 17.7 % | 6.1 % | 16.1 % | 7.9 % |
| Speedster | 4/9/5/6 | 0.990 | 4.44 | 0.000 | 18.6 % | 6.0 % | 15.8 % | 7.4 % |
| Counter-attacker | 6/7/7/4 | 0.955 | 5.15 | 0.004 | 17.7 % | 8.1 % | 14.4 % | 4.3 % |
| All-rounder | 6/6/6/6 | 1.000 | 4.94 | 0.003 | 18.0 % | 7.5 % | 14.2 % | 5.2 % |

## Known weaknesses (what users can train away)
- Self-play rallies are short (2-3 shots in the trainer's self-play check; the trainer's 6-shot rally gate was never met, as in Phase 13).
- No drives or net shots; errors are mostly net and out; 14 % mishits.
- It beats the bot by placement and consistency rather than variety: a good first opponent, not a ceiling.
