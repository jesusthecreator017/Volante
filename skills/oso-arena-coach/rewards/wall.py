"""Wall / Defender reward for `oso-arena train --reward wall.py --build 3,8,8,5`.

Pays high, deep lifts and clears that land near the back line, and returns made while stretched (a lunge or a jump). Expect long rallies and
few winners; the price is little finishing power.

Shaping bound per rally and player (the kit warns above 1): at most 5 paid hits x 0.11 + the base-position term (<= 0.3, only shots 1-10 count) = 0.85.
Only shots 2-10 of a rally pay (never the serve), and only when the predicted landing is inside the singles court on the opponent's half.
Win / loss and the serve-fault penalty are added by the trainer; this file only shapes. Copy it and change the numbers, keep the bound.
"""
import numpy as np
from oso_arena import court as C

BUILD = "3,8,8,5"  # power,speed,reach,accuracy: use it for --build
W = 0.11  # most one paid hit can earn
FIRST, LAST = 2, 10  # rally shots that can pay (1 is the serve)
HL, HW = C.HALF_LENGTH, C.SINGLES_HALF_WIDTH
SHORT_X = C.SHORT_SERVICE_X


def clip01(x):
    return np.clip(x, 0.0, 1.0)


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
    h = ev.hits
    for k in range(h.mask.shape[1]):  # the simulator steps of this decision step
        idx = np.nonzero(h.mask[:, k] & (h.shot_no[:, k] >= FIRST) & (h.shot_no[:, k] <= LAST))[0]
        if len(idx) == 0:
            continue
        p = h.player[idx, k]
        lx, lz = h.land[idx, k, 0], h.land[idx, k, 1]
        ok = np.where(p == 0, lx > 0, lx < 0) & (np.abs(lx) <= HL) & (np.abs(lz) <= HW)
        speed, angle, disp = h.speed[idx, k], h.angle[idx, k], h.disp[idx, k]
        height, contact_x, extended = h.contact[idx, k, 1], np.abs(h.contact[idx, k, 0]), h.extended[idx, k]
        depth = clip01((np.abs(lx) - SHORT_X) / (HL - SHORT_X))  # 0 at the short service line, 1 at the back line
        side = np.abs(lz) / HW  # 0 down the middle, 1 on the sideline
        score = clip01((angle - 15.0) / 30.0) * (0.3 + 0.7 * depth) + 0.4 * extended * clip01(depth + 0.3)
        r[idx, p] += W * ok * np.clip(score, 0.0, 1.0)
    return r
