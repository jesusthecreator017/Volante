"""Scripted baseline: run to the predicted landing point (base position (-2, 0) between shots), always swing, return a clear to
the deep centre, serve to the short-line centre of the diagonal court. Works on the mirrored observation (sim.py) like a policy."""
import numpy as np

BASE = np.array([-2.0, 0.0])
CLEAR = dict(x=6.0, z=0.0, u=0.4)
SERVE = dict(x=2.5, z=1.3, u=0.4)


def bot_actions(obs):
    """obs [n, O] (one player's view) -> actions [n, 6]."""
    own = obs[:, 0:2] * [7, 3]
    land = obs[:, 15:17] * [7, 3]
    coming = obs[:, 14] > 0.5
    target = np.where(coming[:, None], land, BASE)
    target[:, 0] = np.clip(target[:, 0], -7.2, -0.7)
    to = target - own
    dist = np.linalg.norm(to, axis=1, keepdims=True)
    v = to / np.maximum(dist, 1e-9) * np.minimum(1.0, 3.0 * dist / 5.0)
    serving = obs[:, 22] > 0.5
    a = np.zeros((len(obs), 6))
    a[:, 0:2] = v
    a[:, 2] = 1.0
    # a serve's aim is normalised over the diagonal service court (sim.py `_hit`): x short line .. back line, z centre .. side line
    a[:, 3] = np.where(serving, 2 * (SERVE["x"] - 1.98) / (6.70 - 1.98) - 1, (CLEAR["x"] - 3.35) / 3.35)
    a[:, 4] = np.where(serving, 2 * SERVE["z"] / 2.59 - 1, CLEAR["z"] / 2.59)
    a[:, 5] = np.where(serving, SERVE["u"], CLEAR["u"])
    return a
