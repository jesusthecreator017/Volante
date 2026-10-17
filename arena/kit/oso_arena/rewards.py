"""Training rewards: the `RewardFn` interface, the default reward, and the shaping guard.

A reward plug-in is one function in a Python file you pass as `oso-arena train --reward my_reward.py`::

    def reward(ev) -> np.ndarray   # shape [ev.n_games, 2], float: one number per game and player slot

It is called once per decision step (30 Hz) with `ev`, a read-only `oso_arena.events.StepEvents`: arrays of what happened in that
step for every game at once (hits with their contact point, launch speed, angle and predicted landing, legal net crossings,
floor landings, rally ends, both players' positions, the builds, who hit last, the rally's shot count). Look at the dataclasses in
`oso_arena/events.py` for every field. `oso_arena.rewards.default_reward` is the reward training uses when you pass none; copy it.

What it can change: training only. The +1 / -1 for winning / losing a rally and the extra serve-fault penalty are added by the
trainer outside your function and always apply, and the simulator and the contract cannot be touched.

The rule that keeps the model honest: **keep the total your function pays per rally and player below 1 point**, far less than a
point is won or lost. A reward that pays a little for every shot teaches both players to keep the rally going (cooperation)
instead of winning it. Cap the number of shots that pay, as the default does (first 10 shots of a rally). Before training starts
the trainer plays a short rollout, estimates your per-rally total and warns when it exceeds 1 (`shaping_guard`).

`--reward FILE.py` EXECUTES that file (importlib), like any Python script you run: only use reward files you wrote or read.
"""
import importlib.util
from pathlib import Path

import numpy as np

from oso_arena import court as C

SIGN = np.array([-1.0, 1.0])  # x side of player p (player 0 on -X)
SHORT_X, HL, HW = C.SHORT_SERVICE_X, C.HALF_LENGTH, C.SINGLES_HALF_WIDTH

# The terms of the default reward (they moved here from sim.py in phase 14.5; the values are the Phase 13.2 ones).
SHAPING = 0.1  # per legal crossing of the net; the trainer anneals it to 0 over the first 30 % of the run
SHOT_REWARD, SHOT_REWARD_MAX_SHOTS = 0.0, 10  # Phase 13.2: off (it paid both sides for a cooperative rally). Cap: first 10 shots of a rally
# Depth, paid at the legal net crossing from the hit's predicted landing point: DEPTH_REWARD * clip((|x| - SHORT_X) / (HL - SHORT_X), 0, 1),
# 0 when that point is out. Same first-10-shots cap (10 rally shots = 5 per player), so (SHOT + DEPTH + DISP) * 5 = 0.75 < 1 point per player.
DEPTH_REWARD = 0.05
# Displacement at the same point: DISP_REWARD * clip(d / DISP_NORM, 0, 1), d = distance from the opponent at the hit to the landing point.
DISP_REWARD, DISP_NORM = 0.10, 4.0
# Base position: while the opponent is the next hitter (the player hit last), each decision step pays
# base_scale * BASE_REWARD * exp(-d^2 / (2 * 0.8^2)), d = distance to (|x| = BASE_X, z = 0) on the own half (~0.07 per rally).
BASE_REWARD, BASE_X, BASE_SIGMA = 0.001, 3.8, 0.8
SHAPING_LIMIT = 1.0  # points per rally and player (the guard warns above it)


def default_reward(ev, shaping=SHAPING, base_scale=1.0):
    """The Phase 13.2 shaping terms, numerically identical to what the simulator used to add itself: `shaping` per legal net
    crossing, the depth and displacement terms (first 10 shots of a rally), and the annealed base-position term (`base_scale`).
    Returns [n_games, 2]. Win / loss and the serve-fault penalty are not in it."""
    r = np.zeros((ev.n_games, 2))
    cr = ev.crossings
    for k in range(cr.mask.shape[1]):  # in simulator-step order, as the simulator used to add them
        idx = np.nonzero(cr.mask[:, k])[0]
        if len(idx) == 0:
            continue
        p = cr.player[idx, k]
        if shaping:
            r[idx, p] += shaping
        counted = cr.shots[idx, k] <= SHOT_REWARD_MAX_SHOTS
        gi, gp = idx[counted], p[counted]
        if SHOT_REWARD:
            r[gi, gp] += SHOT_REWARD
        if DEPTH_REWARD:
            lx, lz = cr.land[idx, k, 0], cr.land[idx, k, 1]
            inside = (np.abs(lx) <= HL) & (np.abs(lz) <= HW)
            depth = np.clip((np.abs(lx) - SHORT_X) / (HL - SHORT_X), 0.0, 1.0) * inside
            r[gi, gp] += DEPTH_REWARD * depth[counted]
        if DISP_REWARD:
            r[gi, gp] += DISP_REWARD * np.clip(cr.disp[idx, k][counted] / DISP_NORM, 0.0, 1.0)
    if BASE_REWARD and base_scale:
        wait = ev.shots > 0
        for p in (0, 1):
            d2 = (ev.pos[:, p, 0] - SIGN[p] * BASE_X) ** 2 + ev.pos[:, p, 1] ** 2
            r[:, p] += base_scale * BASE_REWARD * np.exp(-d2 / (2 * BASE_SIGMA**2)) * (wait & (ev.last_hitter == p))
    return r


def check_output(r, n_games):
    r = np.asarray(r, dtype=float)
    if r.shape != (n_games, 2):
        raise ValueError(f"your reward must return an array of shape [n_games, 2] = {(n_games, 2)}, got {r.shape}")
    if not np.isfinite(r).all():
        raise ValueError("your reward returned NaN or infinity")
    return r


def total_reward(terminal, ev, fn=None, extra=None, **default_kw):
    """What the trainer feeds PPO: the simulator's win / loss / serve-fault reward `terminal` plus the reward function (`fn(ev)`, or
    `default_reward(ev, **default_kw)` when fn is None), plus `extra(ev)` when given (a small term on top of either, e.g. the Twins
    tempo reward `style.TempoReward`)."""
    shaped = default_reward(ev, **default_kw) if fn is None else check_output(fn(ev), ev.n_games)
    return terminal + shaped + (0.0 if extra is None else check_output(extra(ev), ev.n_games))


def load_reward_file(path):
    """Loads `reward` from a Python file. This EXECUTES the file."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"reward file not found: {p}")
    spec = importlib.util.spec_from_file_location("oso_user_reward", p)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise ValueError(f"could not load {p}: {type(e).__name__}: {e}") from e
    fn = getattr(mod, "reward", None)
    if not callable(fn):
        raise ValueError(f"{p} must define a function reward(ev) -> array [n_games, 2]")
    return fn


def shaping_guard(fn, build, opp_build=None, policy="bot", n=64, steps=600, seed=0, extra=None):
    """Estimates what reward function `fn` (None = the default) pays per rally and player, on a short rollout (n games x steps
    decision steps) of `policy` against itself: "bot" (the scripted bot, long cooperative rallies), "random" (random actions), or a
    callable obs [m, 37] -> actions [m, 6]. `extra`: a stateful term added on top (reset first, see total_reward). Finished rallies only. Returns dict(rallies, mean, p95, max, per_rally) with the
    per-rally per-player totals of the reward (win / loss excluded)."""
    from oso_arena import bot, builds as B, sim

    build = B.to_array(build)
    opp = build if opp_build is None else B.to_array(opp_build)
    s = sim.BadSim(n, seed, builds=np.broadcast_to(np.stack([build, opp])[None], (n, 2, 4)).copy())
    rng = np.random.default_rng(seed)
    if policy == "bot":
        policy = bot.bot_actions
    elif policy == "random":
        policy = None
    if extra is not None and hasattr(extra, "reset"):
        extra.reset()
    obs = s._obs()
    acc = np.zeros((n, 2))
    tot = []
    for _ in range(steps):
        if policy is None:
            act = rng.uniform(-1, 1, (n, 2, sim.ACT_DIM))
            act[..., 2] = np.where(rng.random((n, 2)) < 0.3, 1.0, -1.0)
        else:
            act = np.stack([policy(obs[:, 0]), policy(obs[:, 1])], 1)
        obs, _, _, info = s.step(act)
        ev = s.events()
        acc += (default_reward(ev) if fn is None else check_output(fn(ev), n))
        if extra is not None:
            acc += check_output(extra(ev), n)
        end = ev.rally_end.mask
        if end.any():
            tot.append(acc[end].ravel())
            acc[end] = 0.0
    per = np.concatenate(tot) if tot else np.zeros(0)
    return dict(rallies=len(per) // 2, mean=float(per.mean()) if len(per) else 0.0, p95=float(np.percentile(per, 95)) if len(per) else 0.0,
                max=float(per.max()) if len(per) else 0.0, per_rally=per)
