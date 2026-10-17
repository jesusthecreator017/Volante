"""Argument parser of `oso-arena train`, kept free of torch so `oso-arena train --help` works without it."""
import argparse
import os
from pathlib import Path

N_GAMES, HORIZON = 1024, 64
CKPT_EVERY_S = 600


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="oso-arena train", description="PPO self-play training of a badminton model.",
                                 epilog="--reward FILE.py EXECUTES that file (it is your own code): only use reward files you wrote or read.")
    ap.add_argument("--build", help="the learner's build power,speed,reach,accuracy (integers 1-10, sum 24); default 6,6,6,6; with --resume: the run's")
    ap.add_argument("--opp-builds", default=None, help="random (default; a uniformly random valid build per game, re-drawn at game reset) or P,S,R,A")
    ap.add_argument("--reward", type=Path, metavar="FILE.py", help="a file defining reward(ev) -> array [n_games, 2] (see oso_arena.rewards); default: the default reward; with --resume: the run's")
    ap.add_argument("--init", help="start from 'base' (the packaged base model) or a .safetensors file")
    ap.add_argument("--init-elapsed", type=float, default=0.0, help="with --init: begin the schedules (lr, entropy, reach) at this many minutes")
    ap.add_argument("--out", type=Path, help="run folder (default runs/<timestamp>)")
    ap.add_argument("--minutes", type=float, default=60.0, help="wall-clock budget (of the whole run when resuming)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="continue the run in --out from latest.safetensors")
    ap.add_argument("--threads", type=int, default=max((os.cpu_count() or 2) - 2, 1))
    ap.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto", help="where the PPO update runs (rollouts always use the CPU)")
    ap.add_argument("--early-stop", action="store_true", help="stop when the gates pass at 2 consecutive checkpoints")
    ap.add_argument("--ckpt-minutes", type=float, default=CKPT_EVERY_S / 60, help="minutes between checkpoints and their evaluation")
    g = ap.add_argument_group("size (the defaults are the tuned Phase 13.2 values; smaller values are for tests)")
    g.add_argument("--games", type=int, default=N_GAMES, help="parallel games")
    g.add_argument("--horizon", type=int, default=HORIZON, help="decision steps per iteration")
    g.add_argument("--eval-games", type=int, default=64, help="games of each checkpoint evaluation")
    g.add_argument("--eval-steps", type=int, default=20000, help="step limit of a checkpoint evaluation (quick stats use at most 1500)")
    g.add_argument("--guard-games", type=int, default=64, help="games of the shaping-guard rollout (0 skips the guard)")
    g.add_argument("--guard-steps", type=int, default=600, help="decision steps of the shaping-guard rollout")
    return ap.parse_args(argv)
