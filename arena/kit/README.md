# oso-arena kit

Python package `oso_arena`: the badminton simulator (`sim`), the scripted bot (`bot`), the PPO trainer (`train`), the evaluator
(`evaluate`) and the match exporter (`play_match`, writes a `replay.bin` the viewers play). It was `tools/badsim` before the
Replay / Arena split. The real README (quickstart, contract, training guide) arrives in phase 14.5.

Tests: `tools/.venv/bin/python arena/kit/oso_arena/test_badsim.py` from the repo root. Checkpoints and matches stay in `out/badsim/`.
