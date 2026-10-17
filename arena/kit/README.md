# Volante Arena kit (`oso-arena`)

Train a small neural network to play singles badminton, check it, play it against the scripted bot, and watch the match in 3D.
Python package `oso_arena` for contract **`oso-arena/1`** (`oso_arena/CONTRACT.md`, a copy of `arena/contract/CONTRACT.md`, the
source of truth). MIT licensed (`LICENSE`). The model you export is one `.safetensors` file of numbers: no code, no pickle.

## 5-minute quickstart

You need Python 3.10 or newer. The kit is not on PyPI: install it from this repository or from a wheel.

```bash
# from the repo root: a virtual environment, then the kit with the training extra (adds PyTorch)
python3 -m venv .venv && source .venv/bin/activate
pip install "./arena/kit[train]"
#   or build a wheel (needs `pip install build`) and install that:
#   python -m build arena/kit            # -> arena/kit/dist/oso_arena-0.4.0-py3-none-any.whl (+ .tar.gz)
#   pip install "arena/kit/dist/oso_arena-0.4.0-py3-none-any.whl[train]"
#   without the [train] extra you can do everything below except `train` (PyTorch is only for training)
```

The first command that plays a game builds a shot table once (about 10 s, cached in `~/.cache/oso-arena`, or in `$OSO_ARENA_CACHE`).

```bash
# 1. train. A build is four stats 1-10 that sum to 24: power,speed,reach,accuracy. 5 minutes is a smoke run, not a good model.
oso-arena train --build 9,6,5,4 --minutes 5 --out runs/try1

# 2. turn the best checkpoint into the file you upload; the build comes from the run
oso-arena export runs/try1/best -o mymodel.safetensors --name "Drop Shot Dan"

# 3. the same check the site runs on upload (file format, build, a smoke game against the bot)
oso-arena check mymodel.safetensors

# 4. 100 games against the scripted bot: win rate, rally length, shot mix, faults
oso-arena eval mymodel.safetensors --vs bot --games 100

# 5. one full match (best of 3) and its animation
oso-arena match mymodel.safetensors bot -o match.log.gz --seed 1
oso-arena animate match.log.gz -o match.bin
```

Watch `match.bin` in the Arena viewer: drag it onto the viewer page (nothing is uploaded), or from a repo checkout
`cargo run --release -p oso-arena -- --replay match.bin`.

**The base model.** The kit is designed to start training from a base model (`--init base`) and to play against it
(`--vs base`, `match ... base`, `check base`). It is package data (`oso_arena/data/base_40min*.safetensors`): **House Base**, an all-6
build trained by self-play for about 4 hours. Against the scripted bot it won 200 of 200 games (rally mean 4.9 shots, 0.003 serve faults per
rally). `base_40min.safetensors` is the submission file (`check base`, `--vs base`); `base_40min_full.safetensors` is the full training state
(actor, critic, exploration noise) that `--init base` starts from. A run from scratch also works, but needs hours (see `TRAINING.md`).

## Commands

| command | what it does |
|---|---|
| `oso-arena train` | PPO self-play. `--build P,S,R,A` (your build), `--opp-builds random\|P,S,R,A`, `--reward FILE.py`, `--init base\|FILE.safetensors`, `--minutes`, `--seed`, `--resume`, `--out DIR` (default `runs/<timestamp>`) |
| `oso-arena export CKPT -o FILE --name NAME` | training checkpoint (`runs/x/best`) to submission file; `--build` only if the checkpoint does not record one |
| `oso-arena check FILE` | the upload check; exit 0 when the file is valid |
| `oso-arena eval FILE --vs bot\|base\|FILE2 --games N` | local games and metrics |
| `oso-arena match A B -o LOG --seed N` | one best-of-3 match (A, B: a file, `bot` or `base`) |
| `oso-arena animate LOG -o match.bin` | match log to a replay for the viewer |

`python -m oso_arena <command>` works the same. Exit codes: 0 ok, 1 failed check or usage error.

## Training your own reward

Training pays +1 / -1 per rally (and a serve-fault penalty) plus a small default shaping. You can replace the shaping with your own
function, in any Python file:

```python
# my_reward.py
import numpy as np

def reward(ev):                          # ev: oso_arena.events.StepEvents, arrays for all games at once
    r = np.zeros((ev.n_games, 2))        # one number per game and player slot
    h = ev.hits                          # racquet hits, axes [n_games, 4 simulator steps]
    for k in range(h.mask.shape[1]):
        idx = np.nonzero(h.mask[:, k] & (h.shot_no[:, k] >= 2) & (h.shot_no[:, k] <= 10))[0]
        r[idx, h.player[idx, k]] += 0.05 * np.clip(h.disp[idx, k] / 6.0, 0, 1)    # reward making the opponent run
    return r
```

```bash
oso-arena train --reward my_reward.py --build 4,6,5,9 --minutes 30 --out runs/mine
```

`--reward` **executes your file** like any Python script: only use reward files you wrote or read. The interface is documented in
`oso_arena/rewards.py` (`RewardFn`, `default_reward`) and `oso_arena/events.py` (every field of `ev`). Rule: **keep the total paid per
rally and player below 1 point**, or the model learns to keep rallies going instead of winning them. Before training, the kit plays a short
rollout, estimates your per-rally total and warns when it is above 1. A reward cannot change the simulator or the contract.

Seven ready-made rewards, one per play style, live in the repo's `skills/oso-arena-coach/rewards/`; the **oso-arena-coach** Claude Code
skill in `skills/oso-arena-coach/` runs the whole loop (pick a style, train, diagnose, export) for you.

## Modules

| module | content |
|---|---|
| `sim`, `builds`, `bot`, `court`, `physics` | the simulator with per-player stat builds (37-float observation), the scripted bot |
| `events`, `rewards` | what happened in a step (`StepEvents`); `default_reward`, the `RewardFn` interface, `load_reward_file`, `shaping_guard` |
| `contract`, `policy`, `check`, `export` | the submission file: one reader (`contract.read_submission`), `NumpyPolicy`, `check_file`, `export` |
| `match`, `log`, `animate`, `body` | `play(a, b, seed) -> MatchLog`, the versioned match log, `to_replay(log, out.bin)` |
| `train`, `evaluate` | the PPO trainer (needs torch), local evaluation |
| `cli` | the `oso-arena` command |

## Security

A submission is untrusted: only `safetensors` ever opens it. Pickle, `torch.load` and `allow_pickle` are not used on that path, and
`tests/test_contract.py` checks the sources. The trainer's checkpoints are safetensors; its optimizer file is a torch file the trainer
wrote and reads back with `weights_only=True`. The only code the kit runs from you is the `--reward` file you pass.

## Tests (repo checkout)

```bash
python arena/kit/tests/run_all.py            # contract, builds, rules, match, rewards, trainer, packaging (a few minutes)
python arena/kit/oso_arena/test_badsim.py    # the simulator tests (needs scipy and the repo's tools/ for the reference physics)
```

`tests/test_package.py` builds the wheel and installs it in a clean virtual environment without torch; `tests/test_train.py` trains tiny
runs (a few games, a short horizon) and skips itself without torch.

## Volante Twins: play like me

`oso-rec profile <session folders or imu.bin> -o my_style.json` turns your racquet sessions into a style profile (a few numbers; the
raw data stays with you). Then `oso-arena train --style my_style.json` (the build comes from the profile, a small tempo reward pulls your
model's rhythm toward yours), `oso-arena export <run>/best -o twin.safetensors --name "My twin" --style my_style.json` (embeds the
profile), and `oso-arena eval twin.safetensors --style` prints the fidelity report the Pi uses to admit twins to the Twins ladder.
The format and rules: `oso_arena/STYLE_PROFILE.md`.
