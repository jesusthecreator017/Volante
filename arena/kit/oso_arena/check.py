"""check_file: the submission check, identical for `oso-arena check` and the Pi upload (spec 3.5). In order: size, safetensors
structure (hand-parsed capped header, then the safetensors loader; never pickle / torch.load), exact tensors / shapes / F32, finite
values with |w| <= 1000, header (contract, name, description, build), then the smoke test. Reasons are collected up to the first
fatal one; a broken file never reaches the smoke test."""
from dataclasses import dataclass

from oso_arena import contract, match
from oso_arena.policy import NumpyPolicy

SMOKE_SEEDS = (1, 2)  # game 1: the model is player 0 (seed 1), game 2: player 1 (seed 2); both against the bot
SMOKE_TARGET = 5  # points
SMOKE_MAX_SIM_S = 600.0  # a smoke game still running after 10 simulated minutes is stopped (void): that is not a failure


@dataclass
class CheckResult:
    ok: bool
    reasons: list
    meta: dict  # name, description, build, sha256


def check_file(path):
    sub = contract.read_submission(path)
    reasons = list(sub.reasons)
    if not reasons:
        pol = NumpyPolicy(sub.arrays, sub.meta)
        for i, seed in enumerate(SMOKE_SEEDS):
            a, b = (pol, "bot") if i == 0 else ("bot", pol)
            try:
                match.play(a, b, seed, target=SMOKE_TARGET, best_of=1, max_sim_s=SMOKE_MAX_SIM_S)
            except Exception as e:  # any failure, incl. FloatingPointError for a non-finite action
                reasons.append(f"smoke test game {i + 1} (seed {seed}) failed: {type(e).__name__}: {e}")
    return CheckResult(not reasons, reasons, dict(sub.meta))
