# Diagnosing a training run

## How to read `oso-arena eval FILE --vs bot --games 100`

Read in this order:

1. **faults per rally** (`net  out  missed  serve  net_touch  ceiling  let`; they sum to 1). `net`, `out`, `serve`, `net_touch`, `ceiling` are errors by the hitter;
   `missed` is the other player failing to return a shot, so it is the "winner" share; `let` is a rally that reached 150 shots and was replayed (nobody scored).
   **Serve faults above 0.05 come first**: nothing else matters until the serve works.
2. **win rate of A** against the bot and **mean score A / B** (games finished out of games played; unfinished games mean the policy stalls). A 100-game win rate is only good to about +-10 points.
3. **rally shots** (mean and max). Under 3 means errors or serve faults; a mean above 20 with many `let` means a cooperative rally (below).
4. **shot mix** (`serve clear smash drop drive lift net unknown`): does it match `styles.md`? One class above 60 % is a degenerate policy.
5. **displacement d** (mean distance from the opponent to the landing point of non-serve shots) and **opponent run per shot**: how much the policy moves the opponent.
6. **contact quality** (mean, and the share of mishits): low quality means the player hits from bad positions (cramped, late, backhand, stretched); mishit rate above a few percent is a bug-sized problem.
7. **mean launch speed**, **net-cord events** (information only).

In `train_log.jsonl`: `train_bot_point_wr` (rises above 0.5 when it beats the bot), `train_selfplay_rally`, `eval.self` / `eval.bot` (the same numbers on short deterministic games),
`std` (mean exploration noise; if it collapses early the policy stops exploring), `pg` / `vl` (losses; spikes mean instability), `sps` (speed).

## Symptom, cause, fix

| symptom | likely cause | fix |
|---|---|---|
| Players stand at the net; short rallies; many `net`, `net_touch` or `out` faults; win rate falls late in the run | the base-position term was annealed away or left out (the Phase 13.2 run collapsed into net camping when it was annealed to 0 at 25 %) | keep a small constant base-position term in the reward (`base_position()` in the style files); do not anneal it |
| Very long rallies, many `let`, win rate near 50 % even against the bot, rally mean climbing past 20 | cooperative rallies: the reward pays per shot, so both sides keep the shuttle in play (Phase 13's per-shot reward) | cap the number of paid shots (first 10), cut the per-shot weight so a rally pays less than 1 point, check the shaping guard's numbers; train again from the last good checkpoint of a run without it |
| `serve` faults above 0.05 | the serve is a local optimum (aiming into the wrong court was never corrected); or a reward that pays serves | do not pay the serve (shots from shot 2); do not change the serve-fault penalty; train longer; if the run is old, restart from `--init` a checkpoint with fewer serve faults |
| Win rate and rally length collapse suddenly after a change (reward, rule, `--opp-builds`) | the value estimates no longer fit the new rewards; the learning rate is too high to recover | lower the learning rate by starting a new run from the last good checkpoint late in the schedule: `--init runs/x/ckpt_NNNmin.safetensors --init-elapsed <minutes of that checkpoint> --minutes <total>` (the lr follows the schedule position); change rewards only between runs |
| Smash share above 60 %, many `out` / `net` faults, short rallies | smash spam: fast shots pay too much relative to their error rate (the simulator adds noise growing with speed squared, so the policy can still overuse them) | lower the weight of the speed term, pay only smashes that land in court, raise the accuracy stat, or accept it for the Smasher and check the win rate |
| Displacement `d` near 0, shots land at the opponent's feet | nothing pays for placement | add or raise a displacement term (see `rewards/controller.py`: `disp` and `corner`) |
| Win rate against the bot below 50 % after hours, `std` very small (below 0.2) | exploration collapsed early; the entropy bonus is too low for this reward | start a new run from `--init` an earlier checkpoint with a lower `--init-elapsed` (earlier in the schedule: more entropy and learning rate); make rewards denser |
| `train_bot_point_wr` rises but `eval` against the bot loses | the checkpoint choice: `best` is a full-game pick, the training win rate counts points | trust `eval`; export `best`, not `final` |
| Mishit share above 5 % or contact quality below 0.6 | hitting from stretched, late or cramped positions; slow speed stat for the style | raise the speed or reach stat, add a small reward for the base-position term, pay shots hit early (`rewards/tempo.py`) |
| Win rate does not improve and `vl` is large or spiking | rewards too large or noisy; learning rate too high | scale the reward down (keep the bound), restart from `--init` the last good checkpoint |
| The shaping guard prints `WARNING` | the reward can pay more than 1 point per rally and player | cap paid shots, lower weights, run the guard again (`--guard-games 64`) |
| One style does not show its shot mix | the build does not support it (a 3-power build cannot smash; a 3-speed build cannot reach drives) | change the build one stat at a time (the sum stays 24); a reward cannot create a capability the build lacks |

## Habits

- One change per run; keep every run folder; compare `ckpt_eval.jsonl` across runs (`wr`, `rally_self`, `serve_fault`, `disp`).
- Judge by `eval` with at least 100 games and fixed seeds (`--seed`); compare two models with `eval A --vs B`.
- When in doubt, go back to the All-rounder reward and add one term at a time.
