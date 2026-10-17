# Training guide

What a run does, what to expect, how to spend the compute, and what went wrong in Phases 13-13.2 so it does not go wrong for you.
The numbers marked *measured* were measured on an Apple M4 (10 cores) with the default sizes; yours will differ.

## What `oso-arena train` does

PPO self-play with a small own implementation (PyTorch, two 256-unit tanh layers for the actor and for the critic, a learned exploration
`log_std`). 1024 games run side by side in numpy; the policy acts at 30 Hz.

- **Your slot and build.** In every game the learner plays one slot with `--build`; the other slot gets the opponent build from
  `--opp-builds`: `random` (the default; a uniformly random valid build per game, drawn again whenever a game restarts) or one fixed build.
  The observation contains both builds, so one network learns to play against many builds. Train against one fixed build
  (`--opp-builds 3,8,8,5`) to specialise against it.
- **Opponents.** 60 % of the games are self-play (the current policy on both slots), 30 % are against a frozen snapshot from a pool (one new snapshot
  every 5 M samples, 12 kept), 10 % are against the scripted bot. The assignment is redrawn every iteration. Only the learner's slot learns,
  unless both slots have the learner's build.
- **Reward.** +1 / -1 per rally and an extra -0.5 for a serve fault (from the simulator, always), plus the shaping in `oso_arena/rewards.py`:
  the default (0.1 per legal net crossing annealed to 0 over the first 30 % of the budget, a depth and a displacement term for the first 10 shots of a
  rally, a base-position term) or your own `--reward FILE.py`.
- **Schedules** all follow the time budget `--minutes`: learning rate 1e-4 to 3e-5, entropy bonus 6e-3 to 2e-4, and a reach curriculum (1.3x the real
  reach, ramping to 1.0x by 40 % of the budget).
- **Checkpoints** every 10 minutes (`--ckpt-minutes`) and at the end, in `--out` (default `runs/<timestamp>`):
  `latest.safetensors` (+ `latest.opt.pt`, for `--resume`), `best.safetensors`, `ckpt_NNNmin.safetensors`, `final.safetensors`,
  `train_log.jsonl` (every 2 minutes), `ckpt_eval.jsonl`, `run.json`. A `.safetensors` checkpoint holds the actor, the critic and `log_std`; its header
  holds the build, the elapsed time and the schedule. `best` is the checkpoint with the highest self-play displacement among those that beat the
  bot in at least 80 % of full games with rallies of at least 6 shots and few serve faults (else the highest with 80 % wins, else the highest
  displacement). `--early-stop` ends the run when those gates hold at two checkpoints in a row.
- **Resume** with `--resume --out runs/x` (the build comes from the run). `--minutes` is the budget of the whole run: resuming with a larger
  number stretches the remaining schedule.

## Compute

- **Rollouts and the update.** The rollout (numpy simulation and the policy on CPU) is most of the time; with 1024 games the PPO update is the
  smaller part. The update runs on `--device` (`auto`: Apple MPS when available, else CPU). In Phase 13.2 the update dominated at 256 games and
  moving it to MPS and raising the games to 1024 was the big speed-up. *Measured* on the M4: about 63 k learner samples per second with the update on
  MPS and about 56 k on CPU (`sps` in `train_log.jsonl`), a smaller gap than on weaker CPUs. Try both.
- **Threads.** `--threads` (default: cores minus 2) sets the torch threads for the rollout. The simulation itself is single-threaded numpy, so more
  games per iteration (`--games`) is how you use more of a big machine; run two trainings side by side on a many-core box rather than
  one with a huge thread count.
- **Smoke runs.** `--games 16 --horizon 16 --guard-games 8 --eval-games 4 --eval-steps 150 --minutes 0.05` is a few seconds: use it to test a
  reward file before spending an hour. The kit's own tests train like this.
- **The first game builds a shot table** (about 10 s, cached in `~/.cache/oso-arena` or `$OSO_ARENA_CACHE`).
- **No GPU needed.** The network is tiny (about 300 KB as an upload).

## Budgets and what to expect

- From scratch, Phase 13 needed about 3.75 hours (four resumed parts) to win 100 % of its games against the scripted bot, but its self-play rallies
  averaged 4.8 shots and the policy only served, smashed and dropped; clears and lifts never appeared. Phase 13.1 (the reach of 1.5 m with a 2.0 m lunge that
  the all-6 build now has, wider initial exploration on the aim and flight-time dimensions, more entropy) raised the rallies to 10.9 shots.
- Warm-started from a trained model the Phase 13.2 model took 40.5 minutes. The packaged base model (`--init base`) is meant for exactly this: start
  from it and a run begins at a level that beats the scripted bot. Without `--init` a run starts from random weights and **a 5-minute run is a smoke test,
  not a model**: the House Base itself took about 4 hours from scratch (it crossed 80 % wins against the bot after ~3.5 h). Judge by `eval`, not by hope.
- Look at `train_log.jsonl` every few minutes: `train_bot_point_wr` (point win rate against the bot while training), `train_selfplay_rally`
  (shots per rally), `eval.*.faults` (the serve fault share must go to ~0 first), `std` (exploration; it should fall slowly).
- Stop when the checkpoint evaluation stops improving for a long time, not when the clock says; a short run on a good reward beats a long run
  on a bad one.

## Lessons from Phases 13-13.2

1. **Annealing the base-position reward to zero made the players camp at the net.** The base term (a little for standing near the base position
   while the opponent is to hit) is constant in the default now. Keep a small one in a custom reward.
2. **A reward for every shot teaches cooperation.** The per-shot reward (`SHOT_REWARD`) paid both sides for a long friendly rally; it is off. Any shaping
   that totals more than a point per rally will do the same: cap how many shots pay (the default pays the first 10) and keep the total below 1. The trainer
   estimates it before it starts and warns.
3. **Serve faults are a local optimum.** A policy that always aimed its serve into the wrong court could not get out. The serve's aim is normalised over the
   diagonal service court so any aim lands in the right court, and the serve-fault penalty (-0.5 extra) stays. If `eval` shows serve faults per rally above a few
   percent, nothing else matters yet.
4. **Changing the rules in the middle of a run collapses it.** After a change to the reward or the simulator the old value estimates are wrong. Lower the
   learning rate, resume from the last good checkpoint (`--resume` with the run folder) and expect a dip. Better: change the reward only between runs and
   start the new run `--init` from the old best.
5. **Smash spam with low noise.** When shots are accurate regardless of speed, hitting everything at full power is optimal. The simulator adds aim noise that
   grows with the square of the launch speed, so power costs accuracy; watch the shot mix in `eval`.
6. **Hitting straight at the opponent.** Nothing in +-1 asks a policy to move its opponent. Phase 13.2 made the displacement term (distance from the
   opponent to the predicted landing point) the main shaping term for this; a custom reward that drops it needs another way to pay for placement.
7. **Pick the checkpoint from full games.** In Phase 13 the shipped model was the 165-minute checkpoint of a 225-minute run, chosen by self-play rally length among those that beat the bot every time; `best` is chosen from full games, not from the training reward.
8. **Players are point masses with a reach envelope.** What is learned is where to stand, when to hit, and which shot goes where. The numbers (speed, reach,
   noise) come from the build and are fixed by the contract; do not look for a reward that changes them.

## Rewards

`oso_arena/rewards.py` documents the interface and the default. Habits that work: pay at the hit or the legal crossing, never at the rally end (the +-1
is already there); weight so that five paid shots stay near 0.5; use the build's strength (a 9-power build smashes, a 9-accuracy build places); test with the smoke
settings above and look at the shaping guard's numbers. The seven style rewards in `skills/oso-arena-coach/rewards/` are worked examples, and the
coach skill's `diagnose.md` maps symptoms to fixes.

## After training

```bash
oso-arena export runs/x/best -o model.safetensors --name "My Model"
oso-arena check model.safetensors
oso-arena eval model.safetensors --vs bot --games 200
```

`eval` prints the win rate, the rally length, faults per rally, the shot mix (`clear`, `smash`, `drop`, `drive`, `lift`, `net`, `serve`), the mean
displacement and the contact quality. Compare against the bot and, when available, against `base`.
