"""Test data: N distinct valid submissions derived from a smoke model (weights slightly perturbed, new name and build), so a
tournament can be tested with 8+ different models. Usage: python tests/make_models.py <valid smoke file> <out dir> [n]"""
import json
import sys
from pathlib import Path

import numpy as np
from safetensors.numpy import load_file, save_file

BUILDS = [(9, 6, 5, 4), (3, 8, 8, 5), (4, 6, 5, 9), (6, 6, 6, 6), (5, 7, 6, 6), (7, 5, 6, 6), (4, 9, 5, 6), (6, 7, 7, 4),
          (8, 5, 5, 6), (5, 5, 7, 7)]


def main(src, out, n=10):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    base = load_file(src)
    rng = np.random.default_rng(42)
    for i in range(int(n)):
        arr = {k: (v + rng.normal(0, 0.02, v.shape)).astype(np.float32) for k, v in base.items()}
        p, s, r, a = BUILDS[i % len(BUILDS)]
        meta = {"contract": "oso-arena/1", "name": f"Test Player {i + 1}", "description": f"tournament test model {i + 1}",
                "build": json.dumps({"power": p, "speed": s, "reach": r, "accuracy": a})}
        save_file(arr, str(out / f"test_{i + 1:02d}.safetensors"), metadata=meta)
    print(f"wrote {n} models to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
