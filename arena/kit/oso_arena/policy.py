"""NumpyPolicy: the submitted network, run with numpy only (no torch; the Pi uses this class).
a = clip(W4 . tanh(W2 . tanh(W0 . o + b0) + b2) + b4, -1, 1), the deterministic mean action of the trainer's ActorCritic plus the
simulator's clip. Math is float64 (weights are widened from the float32 file), so it matches the float32 torch actor to ~1e-6."""
import numpy as np

from oso_arena import contract


class NumpyPolicy:
    def __init__(self, arrays, meta):
        self.meta = dict(meta)
        self.w = [np.asarray(arrays[f"actor.{i}.{k}"], np.float64) for i in (0, 2, 4) for k in ("weight", "bias")]

    @classmethod
    def load(cls, path):
        """A .safetensors submission file -> policy. Runs every file check except the smoke test; ValueError lists the reasons."""
        sub = contract.read_submission(path)
        if sub.reasons:
            raise ValueError("; ".join(sub.reasons))
        return cls(sub.arrays, sub.meta)

    @classmethod
    def from_arrays(cls, arrays, meta):
        """Training-time use: arrays {actor.*: ndarray}, meta {name, description, build, sha256}."""
        reasons = contract.check_tensors(arrays)
        if reasons:
            raise ValueError("; ".join(reasons))
        return cls(arrays, dict(name="", description="", build=None, sha256=None) | dict(meta))

    def act(self, obs):
        """obs [n, 37] -> actions [n, 6] in [-1, 1] (float64)."""
        o = np.asarray(obs, np.float64)
        w0, b0, w2, b2, w4, b4 = self.w
        return np.clip(np.tanh(np.tanh(o @ w0.T + b0) @ w2.T + b2) @ w4.T + b4, -1.0, 1.0)
