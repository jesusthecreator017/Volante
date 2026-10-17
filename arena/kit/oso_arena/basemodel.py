"""Where the packaged base model lives. The two files are package data (`oso_arena/data/`):
  base_40min.safetensors       the submission file (all-6 build), for `eval --vs base`, `match ... base`, `check base`
  base_40min_full.safetensors  the full training state (actor, critic, log_std), for `train --init base`
They are not part of every kit build; a build without them says so and everything else keeps working."""
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
NOT_BUNDLED = "the base model is not bundled in this version of the kit"


def path(full=False):
    """Path of the base model file; ValueError (NOT_BUNDLED) when this kit build does not carry it."""
    p = DATA / ("base_40min_full.safetensors" if full else "base_40min.safetensors")
    if not p.is_file():
        raise ValueError(f"{NOT_BUNDLED} (expected {p.name} in oso_arena/data/)")
    return p
