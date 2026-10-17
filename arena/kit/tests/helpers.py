"""Shared test helpers. Plain-assert tests: run a file directly (tools/.venv/bin/python arena/kit/tests/test_x.py) or all with run_all.py."""
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parents[1]
ROOT = KIT.parents[1]
for p in (KIT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from oso_arena import contract  # noqa: E402

GOOD_BUILD = {"power": 9, "speed": 6, "reach": 5, "accuracy": 4}


def random_actor(seed=0, scale=0.2):
    """Random float32 actor tensors of the contract shapes."""
    rng = np.random.default_rng(seed)
    return {k: (rng.normal(size=sh) * scale).astype(np.float32) for k, sh in contract.TENSORS.items()}


def meta_for(name="Test Model", description="a test", build=GOOD_BUILD, contract_id="oso-arena/1"):
    m = {"contract": contract_id, "name": name, "description": description}
    if build is not None:
        m["build"] = build if isinstance(build, str) else json.dumps(build, separators=(",", ":"))
    return m


def write_st(path, tensors, meta):
    from safetensors.numpy import save_file

    save_file(tensors, str(path), metadata=meta)
    return path


def raw_safetensors(path, header, data=b""):
    """A safetensors-shaped file with an arbitrary JSON header (for malformed-file tests)."""
    h = json.dumps(header).encode()
    Path(path).write_bytes(struct.pack("<Q", len(h)) + h + data)
    return path


def run(ns):
    tests = [(k, v) for k, v in sorted(ns.items()) if k.startswith("test_") and callable(v)]
    for k, t in tests:
        t0 = time.time()
        t()
        print(f"ok  {k} ({time.time() - t0:.1f}s)")
    print(f"all {len(tests)} tests ok")
