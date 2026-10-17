"""Training state -> submission file: exactly the 6 actor tensors as float32 plus the contract header."""
import json
from pathlib import Path

import numpy as np

from oso_arena import CONTRACT, builds, contract


def _np32(v):
    if hasattr(v, "detach"):  # torch tensor, without importing torch
        v = v.detach().cpu().numpy()
    return np.ascontiguousarray(v, dtype=np.float32)


def state_path(p):
    """runs/x/best -> runs/x/best.safetensors when only that file exists; any other path is returned as given."""
    p = Path(p)
    q = p.with_name(p.name + ".safetensors")
    return q if not p.exists() and q.exists() else p


def export(actor_state, out_path, name, description, build):
    """actor_state: {key: ndarray | torch tensor} holding the 6 `actor.*` tensors (other keys, e.g. critic / log_std, are dropped).
    Validates before writing and refuses invalid input with ValueError(reasons)."""
    from safetensors.numpy import save_file

    try:
        arrays = {k: _np32(actor_state[k]) for k in contract.TENSORS}
    except KeyError as e:
        raise ValueError(f"missing tensor {e.args[0]!r}") from None
    if isinstance(build, dict) and set(build) == set(builds.STATS):
        build = {k: build[k] for k in builds.STATS}  # canonical key order
    try:
        build_json = json.dumps(build, separators=(",", ":"))
    except TypeError:
        raise ValueError("build must be a dict of four integers") from None
    meta = {"contract": CONTRACT, "name": name, "description": description or "", "build": build_json}
    reasons = contract.check_tensors(arrays)
    if not isinstance(name, str) or not isinstance(description or "", str):
        reasons.append("name and description must be strings")
    else:
        reasons += contract.check_meta(meta)[0]
    if reasons:
        raise ValueError("; ".join(reasons))
    save_file(arrays, str(out_path), metadata=meta)
