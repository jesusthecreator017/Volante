"""All-rounder reward for `oso-arena train --reward allrounder.py --build 6,6,6,6`.

The balanced reward: the kit's default terms (depth and displacement of the shot at the legal net crossing, the base-position term), with the
shot cap that keeps a long rally from paying more than a point. No standout style: expect a balanced shot mix.

Shaping bound per rally and player (the kit warns above 1): 5 counted crossings x (0.04 + 0.08) + the base-position term (<= 0.3, only shots 1-10 count) = 0.90.
Win / loss and the serve-fault penalty are added by the trainer; this file only shapes. Copy it and change the numbers, keep the bound.
"""
import numpy as np
from oso_arena import court as C

BUILD = "6,6,6,6"  # power,speed,reach,accuracy: use it for --build
DEPTH, DISP, DISP_NORM = 0.04, 0.08, 4.0
MAX_SHOTS = 10  # only the first 10 shots of a rally pay
HL, HW, SHORT_X = C.HALF_LENGTH, C.SINGLES_HALF_WIDTH, C.SHORT_SERVICE_X


def base_position(ev, scale=0.001, max_shots=10, base_x=3.8, sigma=0.8):
    """While the opponent is the next to hit, pay a little for standing near the base position (keeps the player from camping at the net)."""
    r = np.zeros((ev.n_games, 2))
    wait = (ev.shots > 0) & (ev.shots <= max_shots)
    for p in (0, 1):
        d2 = (ev.pos[:, p, 0] - (-1.0 if p == 0 else 1.0) * base_x) ** 2 + ev.pos[:, p, 1] ** 2
        r[:, p] = scale * np.exp(-d2 / (2 * sigma**2)) * (wait & (ev.last_hitter == p))
    return r


def reward(ev):
    r = base_position(ev)
    c = ev.crossings  # legal crossings of the net, with the hit's predicted landing point and displacement
    for k in range(c.mask.shape[1]):
        idx = np.nonzero(c.mask[:, k] & (c.shots[:, k] <= MAX_SHOTS))[0]
        if len(idx) == 0:
            continue
        p = c.player[idx, k]
        lx, lz = c.land[idx, k, 0], c.land[idx, k, 1]
        inside = (np.abs(lx) <= HL) & (np.abs(lz) <= HW)
        depth = np.clip((np.abs(lx) - SHORT_X) / (HL - SHORT_X), 0.0, 1.0) * inside
        r[idx, p] += DEPTH * depth + DISP * np.clip(c.disp[idx, k] / DISP_NORM, 0.0, 1.0)
    return r
