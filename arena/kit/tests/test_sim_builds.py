"""Simulator with per-player builds: parity with Phase 13.2 at all-6, the 37-float observation, and what each stat changes."""
import hashlib

import numpy as np

from helpers import run
from oso_arena import bot, builds, sim

# sha256 of a fixed 900-step rollout (random actions + bot) of the all-6 simulator (obs[:29], rewards, positions, velocities, shuttle).
# Phase 14.2: 2aad0a5a... reproduced the Phase 13.2 simulator exactly, recorded before the builds existed.
# Phase 14.2b changed the physics on purpose (net cord, turnover drag, racquet contact quality, players still before the serve, new
# faults), so that hash is retired and this one, recorded from the 14.2b simulator, replaces it. It guards against accidental changes
# from here on (any later physics change must replace it and say why). The rollout draws random actions so most hits are poor contacts.
GOLDEN_14_2B = "8cada2d8290e69e51d02d670fe91b3d3ea4e58ebfb30bf0ab932e4f46a8e8512"


def fingerprint(builds_arr=None, n=8, steps=900, seed=11):
    s = sim.BadSim(n, seed, builds=builds_arr)
    s.shaping = 0.1
    rng = np.random.default_rng(5)
    obs = s._obs()
    h = hashlib.sha256()
    for t in range(steps):
        a = rng.uniform(-1, 1, (n, 2, 6))
        a[:, 0] = np.where((np.arange(n) % 2 == 0)[:, None], bot.bot_actions(obs[:, 0]), a[:, 0])
        a[:, 1, 2] = np.where(t % 3 == 0, 1.0, a[:, 1, 2])
        obs, rew, done, info = s.step(a)
        for x in (obs[..., :29], rew, s.pos, s.vel, s.shp, s.shv, s.score, s.land):
            h.update(np.ascontiguousarray(x).tobytes())
    return h.hexdigest(), s


def test_all6_matches_the_golden_rollout():
    fp, s = fingerprint()
    assert fp == GOLDEN_14_2B, "the all-6 simulator changed (obs[:29], rewards, positions, velocities, shuttle): see the note on GOLDEN_14_2B"
    assert s.stats["hits"] == 381 and s.stats["rallies"] == 159 and s.stats["cords"] == 15 and s.stats["mishits"] == 84
    fp2, _ = fingerprint(np.full((8, 2, 4), 6))  # explicit all-6 builds are the same thing
    assert fp2 == GOLDEN_14_2B


def test_obs_dim_and_build_columns():
    assert sim.OBS_DIM == 37 and sim.ACT_DIM == 6
    b = np.zeros((3, 2, 4), int)
    b[:, 0] = [9, 6, 5, 4]
    b[:, 1] = [3, 8, 8, 5]
    s = sim.BadSim(3, 0, builds=b)
    o = s._obs()
    assert o.shape == (3, 2, 37) and o.dtype == np.float32
    n0, n1 = (np.array([9, 6, 5, 4]) - 5.5) / 4.5, (np.array([3, 8, 8, 5]) - 5.5) / 4.5
    assert np.allclose(o[:, 0, 29:33], n0) and np.allclose(o[:, 0, 33:37], n1)  # own, then opponent
    assert np.allclose(o[:, 1, 29:33], n1) and np.allclose(o[:, 1, 33:37], n0)
    assert np.abs(o[..., 29:]).max() <= 1.0 and (sim.BadSim(1, 0)._obs()[..., 29:] == (6 - 5.5) / 4.5).all()


def test_obs_index_formulas():
    """Every index of CONTRACT.md section 4 against an independent formula from the raw state."""
    s = sim.BadSim(2, 4, builds=np.array([[[7, 5, 6, 6], [4, 6, 8, 6]]] * 2))
    s.pos[:] = [[[-3.0, 1.0], [2.5, -0.5]]] * 2
    s.vel[:] = [[[1.0, -2.0], [-3.0, 0.5]]] * 2
    s.shp[:] = [-2.0, 1.8, 0.7]
    s.shv[:] = [10.0, -5.0, 30.0]
    s.land[:] = [3.0, -1.0]
    s.land_t[:] = 1.25
    s.last_hitter[:], s.shots[:], s.server[:] = [1, 0], [4, 0], [1, 0]
    s.score[:] = [[3, 7], [3, 7]]
    s.since_hit[:] = [[0.2, 0.5], [0.2, 0.5]]
    s.freeze[:] = [[0.15, 0.0]] * 2
    s.react[:] = [[0.1, 0.0]] * 2
    o = s._obs()
    g, p = 0, 0  # game 0, player 0 (m = +1); game 0: player 1 last hit with 4 shots, server 1
    exp = [-3 / 7, 1 / 3, 1 / 5, -2 / 5, 2.5 / 7, -0.5 / 3, -3 / 5, 0.5 / 5, -2 / 7, 1.8 / 5, 0.7 / 3, 10 / 20, -5 / 20, 30 / 20,
           1.0, 3 / 7, -1 / 3, 1.25 / 3]
    assert np.allclose(o[g, p, :18], exp, atol=1e-6), (o[g, p, :18] - exp)
    # frozen and 0.2 s after an own hit: nothing can be hit yet (18 and 19 are 0)
    assert o[g, p, 18] == 0 and o[g, p, 19] == 0
    assert np.allclose(o[g, p, 20:29], [0.15 / 0.3, 0.2 / 0.35, 0, 0, 7 % 2, 3 / 21, 7 / 21, 4 / 20, 0.1 / 0.2], atol=1e-6)
    assert np.allclose(o[g, p, 29:], np.concatenate([(np.array([7, 5, 6, 6]) - 5.5) / 4.5, (np.array([4, 6, 8, 6]) - 5.5) / 4.5]), atol=1e-6)
    # player 1 sees the mirrored court: x and z negated, own/opponent swapped, own score first, is_server 1
    q = o[g, 1]
    assert np.allclose(q[:8], [-2.5 / 7, 0.5 / 3, 3 / 5, -0.5 / 5, 3.0 / 7, -1.0 / 3, -1 / 5, 2 / 5], atol=1e-6)
    assert np.allclose(q[8:11], [2 / 7, 1.8 / 5, -0.7 / 3], atol=1e-6) and q[14] == 0 and q[23] == 1 and q[22] == 0
    assert np.allclose(q[25:27], [7 / 21, 3 / 21]) and q[24] == 1  # server score 7 -> parity 1
    # reach flags: free to hit, shuttle 1.04 m away -> normal reach (18); 1.7 m away -> stretch only (19, reach 6 = 1.5 / 2.0 m); 2.3 m -> neither
    s.freeze[:], s.since_hit[:] = 0.0, 1.0
    for dist, want in ((1.04, (1, 0)), (1.7, (0, 1)), (2.3, (0, 0))):
        s.shp[:] = [-3.0 + dist, 1.8, 1.0]
        s.pos[:, 0] = [-3.0, 1.0]
        oo = s._obs()
        assert (oo[0, 0, 18], oo[0, 0, 19]) == want, (dist, oo[0, 0, 18:20])
    # game 1: a fresh rally (shots 0, server 0): obs 22 = "my serve is to be hit now"
    o = s._obs()
    assert o[1, 0, 22] == 1 and o[1, 0, 23] == 1 and o[1, 1, 22] == 0 and o[1, 1, 23] == 0 and o[1, 0, 14] == 0


def test_set_builds_validates_and_applies():
    s = sim.BadSim(4, 0)
    s.set_builds(np.array([1, 3]), np.array([[[10, 10, 2, 2], [1, 1, 10, 10]]] * 2))
    pt = builds.param_table()
    assert (s.builds[1, 0] == [10, 10, 2, 2]).all() and (s.builds[[0, 2]] == 6).all()
    assert s._p["v_max"][1, 0] == 6.0 and s._p["v_max"][0, 0] == 5.0 and s._p["noise_mult"][3, 1] == 0.6
    assert s._p["reach"][3, 0] == pt["reach"][2] and s._p["a_max"][3, 1] == 8.0
    assert np.isclose(s._build_obs[3, 0, 0], (10 - 5.5) / 4.5) and np.isclose(s._build_obs[3, 0, 4], (1 - 5.5) / 4.5)
    for bad in (np.full((4, 2, 4), 11), np.full((4, 2, 4), 0), np.full((4, 2, 4), 6.0), np.full((4, 4), 6)):
        try:
            s.set_builds(slice(None), bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_speed_stat_changes_running():
    b = np.array([[[6, 10, 4, 4], [6, 1, 9, 4]]])  # player 0 fast, player 1 slow
    s = sim.BadSim(1, 0, builds=b)
    s.pos[0, 0], s.pos[0, 1] = [-6.0, 0.0], [6.0, 0.0]
    s.shots[:], s.last_hitter[:] = 2, 1  # a rally in progress (before the serve nobody moves)
    s.shp[:], s.shv[:] = [0.0, 10.5, 0.0], 0.0  # the shuttle starts high (1.9 s to fall): nothing ends
    a = np.zeros((1, 2, 6))
    a[..., 0] = 1.0  # both run toward the net
    a[..., 2] = -1
    for _ in range(45):  # 1.5 s
        s.step(a)
    d0, d1 = abs(s.pos[0, 0, 0] + 6.0), abs(s.pos[0, 1, 0] - 6.0)
    assert d0 > d1 + 0.5 and np.linalg.norm(s.vel[0, 0]) <= 6.0 + 1e-9 and np.linalg.norm(s.vel[0, 1]) <= 4.0 + 1e-9, (d0, d1)


def test_reach_stat_changes_what_is_hittable():
    got = {}
    for stat in (1, 6, 10):
        b = np.array([[[6, 6, stat, 6], [6, 6, 6, 6]]])
        s = sim.BadSim(1, 0, builds=b)
        s.last_hitter[:], s.shots[:] = 1, 2
        s.shp[:] = [-1.0, 1.5, 0.0]
        s.pos[0, 0] = [-2.65, 0.0]  # 1.65 m away
        s.since_hit[:] = 1.0
        n, ext = s._can_hit(0)
        got[stat] = (bool(n[0]), bool(ext[0]))
    # reach 1: 1.2 / 1.7 m (the lunge reaches 1.65); 6: 1.5 / 2.0 (stretch only); 10: 1.8 / 2.3 (normal reach)
    assert got == {1: (False, True), 6: (False, True), 10: (True, True)}, got


def test_accuracy_stat_scales_the_aim_noise():
    sig = {}
    for acc in (1, 10):
        n = 4000
        b = np.zeros((n, 2, 4), int)
        b[:] = [6, 6, 6, 6]
        b[:, 0, 3] = acc
        s = sim.BadSim(n, 3, builds=b)
        s.server[:] = 0
        s._start_rally(np.arange(n))
        s.pos[:, 0] = [-2.6, 1.3]
        a = np.zeros((n, 2, 6))
        a[:, 0, 2], a[:, 0, 3], a[:, 0, 4], a[:, 0, 5] = 1, 0.0, 0.0, 0.2
        a[:, 1, 2] = -1
        s.shp[:, 0] = -2.6 + 0.0
        s.shp[:, 1], s.shp[:, 2] = 1.1, 1.3
        s.shots[:], s.last_hitter[:] = 0, 1
        s._act = a
        s._swing = a[..., 2] > 0
        s._hit(np.arange(n), 0)
        sig[acc] = s.land[:, 1].std()
    assert 1.4 / 0.6 * 0.8 < sig[1] / sig[10] < 1.4 / 0.6 * 1.2, sig  # the whole sigma scales: 1.4 / 0.6


def test_power_stat_changes_launch_cap_and_flight_time_floor():
    tab = sim.shot_table()
    D, h = np.array([9.0, 9.0]), np.array([2.6, 2.6])
    t1, t10 = tab.t_min(D, h, np.array([1, 10]))
    assert t10 < t1 - 0.05  # more power allows a faster flight
    t6 = tab.t_min(D[:1], h[:1], np.array([6]))[0]
    assert t10 < t6 < t1 and tab.t_min(D[:1], h[:1])[0] == t6  # default = all-6
    cap = sim.launch_cap(np.array([0.5, 1.8, 2.8] * 2), np.array([1] * 3 + [10] * 3))
    assert np.allclose(cap, [20, 40, 60, 30, 70, 110])  # 1.8 m: halfway between 1.0 and 2.6 -> mean of lo and hi
    # a hit at full power: the faster build launches the shuttle faster
    v = {}
    for pw in (1, 10):
        b = np.array([[[pw, 6, 6, 6], [6, 6, 6, 6]]])  # the simulator does not require sum 24 (check does)
        s = sim.BadSim(1, 0, builds=b)
        s.noise_scale = 0.0
        s._start_rally(np.array([0]))
        s.last_hitter[:], s.shots[:] = 1, 2
        s.shp[0] = [-1.0, 2.8, 0.0]
        s.pos[0, 0] = [-1.0, 0.0]
        a = np.zeros((1, 2, 6))
        a[0, 0] = [0, 0, 1, 1.0, 0.0, -1.0]  # flat out: deepest corner, fastest flight
        s._act, s._swing = a, a[..., 2] > 0
        s._hit(np.array([0]), 0)
        v[pw] = np.linalg.norm(s.shv[0])
    assert v[10] > v[1] + 20, v


if __name__ == "__main__":
    run(globals())
