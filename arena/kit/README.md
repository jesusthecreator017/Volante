# oso-arena kit

Python package `oso_arena` for contract **`oso-arena/1`** (`arena/contract/CONTRACT.md`, the source of truth). It was `tools/badsim` before the
Replay / Arena split. The real README (quickstart, training guide) arrives in phase 14.5.

| module | content |
|---|---|
| `sim`, `builds`, `bot`, `court`, `physics` | the simulator with per-player stat builds (37-float observation), the scripted bot |
| `contract`, `policy`, `check`, `export` | the submission file: one reader (`contract.read_submission`), `NumpyPolicy`, `check_file`, `export` |
| `match`, `log`, `animate`, `body` | `play(a, b, seed) -> MatchLog`, the versioned match log, `to_replay(log, out.bin)` (body code vendored from `tools/synth_motion.py`) |
| `train`, `evaluate` | PPO trainer (OBS 37, all-6 builds by default until 14.3), local evaluation |
| `cli` | `oso-arena check / export / eval / match / animate` (`python -m oso_arena` works too) |

Security: a submission is untrusted. Only `safetensors` ever opens it; pickle, `torch.load` and `allow_pickle` are not used on that path
(`tests/test_contract.py` checks the sources). `export` and `train` read our own `.pt` checkpoints with `weights_only=True`.

Tests (repo root): `tools/.venv/bin/python arena/kit/tests/run_all.py` (new, ~30 s) and `tools/.venv/bin/python arena/kit/oso_arena/test_badsim.py` (the
simulator tests). Checkpoints and matches stay in `out/badsim/`.
