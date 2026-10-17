"""Training state -> submission file: exactly the 6 actor tensors as float32 plus the contract header."""
import json
from pathlib import Path

import numpy as np

from oso_arena import CONTRACT, builds, contract, style


def _np32(v):
    if hasattr(v, "detach"):  # torch tensor, without importing torch
        v = v.detach().cpu().numpy()
    return np.ascontiguousarray(v, dtype=np.float32)


def state_path(p):
    """runs/x/best -> runs/x/best.safetensors when only that file exists; any other path is returned as given."""
    p = Path(p)
    q = p.with_name(p.name + ".safetensors")
    return q if not p.exists() and q.exists() else p


def export(actor_state, out_path, name, description, build, style_profile=None):
    """actor_state: {key: ndarray | torch tensor} holding the 6 `actor.*` tensors (other keys, e.g. critic / log_std, are dropped).
    style_profile (a validated profile dict): embedded as the header key `style_profile`; the build is then the profile's (`build` None,
    or equal to it; anything else is refused). Validates before writing and refuses invalid input with ValueError(reasons)."""
    from safetensors.numpy import save_file

    try:
        arrays = {k: _np32(actor_state[k]) for k in contract.TENSORS}
    except KeyError as e:
        raise ValueError(f"missing tensor {e.args[0]!r}") from None
    extra = {}
    if style_profile is not None:
        want = style.build_from_profile(style_profile)  # ValueError when the profile is invalid
        if build is not None and build != want:
            raise ValueError(f"build {json.dumps(build, separators=(',', ':'))} conflicts with the style profile, which maps to {json.dumps(want, separators=(',', ':'))}")
        build, extra = want, {"style_profile": style.compact(style_profile)}
    if isinstance(build, dict) and set(build) == set(builds.STATS):
        build = {k: build[k] for k in builds.STATS}  # canonical key order
    try:
        build_json = json.dumps(build, separators=(",", ":"))
    except TypeError:
        raise ValueError("build must be a dict of four integers") from None
    meta = {"contract": CONTRACT, "name": name, "description": description or "", "build": build_json, **extra}
    reasons = contract.check_tensors(arrays)
    if not isinstance(name, str) or not isinstance(description or "", str):
        reasons.append("name and description must be strings")
    else:
        reasons += contract.check_meta(meta)[0]
    if reasons:
        raise ValueError("; ".join(reasons))
    save_file(arrays, str(out_path), metadata=meta)
