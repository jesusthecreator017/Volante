"""Phase 13 simulator tests. Run from anywhere: tools/.venv/bin/python arena/kit/oso_arena/test_badsim.py (plain asserts, prints numbers)."""
import hashlib
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the kit root
# ponytail: the Replay tools are reached by path until phase 14.2 vendors what the kit needs
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
import court as tools_court  # noqa: E402
import shuttle_physics as shp  # noqa: E402
import replay  # noqa: E402
import synth_motion as sm  # noqa: E402
from oso_arena import court as kit_court  # noqa: E402
from oso_arena import physics as kit_physics  # noqa: E402
from oso_arena import animate as pm, bot, evaluate, match, rewards, sim  # noqa: E402

# sha256 of synth_motion.generate(0) / generate(3, 30.0, 6.0) (joints, t, swing_player, swing_t0), recorded before the Phase 13 refactor
GOLDEN_SYNTH = ["f7629ba921112fba54f5c59e2a9bd1090cb273a7384ed49b13ec3e477b4f523b",
                "f7bcb2ca98be8e27268a9edfe11df710c100ddb64cd15894532aebc537b5f7e8"]
NOOP = np.full((1, 2, sim.ACT_DIM), -1.0)  # no movement command change, no swing


def one(seed=0, **kw):
    s = sim.BadSim(1, seed)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def run_substeps(s, k=1, act=NOOP, shaping=0.0, base_scale=0.0):
    """k decision steps; returns the last info and the accumulated reward: the simulator's win / loss reward plus the default reward
    terms (rewards.default_reward; the net-crossing `shaping` and `base_scale` are off unless a test asks for them)."""
    rew = 0
    for _ in range(k):
        _, r, d, info = s.step(act)
        rew = rew + rewards.total_reward(r, s.events(), None, shaping=shaping, base_scale=base_scale)
    return info, rew


def setup_flight(s, last_hitter, shots, p, v, server=None, score=(0, 0)):
    s.last_hitter[:] = last_hitter
    s.shots[:] = shots
    s.shp[:] = p
    s.shv[:] = v
    s.score[:] = score
    if server is not None:
        s.server[:] = server
    s.pos[0, 0] = [-4, 0]
    s.pos[0, 1] = [4, 0]
    s.vel[:] = 0
    s.land[:] = [1.0, 0.0]  # predicted landing short of the short line: no depth reward unless a test sets it


def test_kit_copies_match_tools():
    """oso_arena.court / physics are copies of tools/court.py / shuttle_physics.py: they must not drift."""
    for name in dir(tools_court):
        v = getattr(tools_court, name)
        if name.isupper() and not name.startswith("_"):
            assert repr(getattr(kit_court, name)) == repr(v) or np.array_equal(getattr(kit_court, name), v), name
    for name in ("G", "VT", "VT_RANGE", "H_TRUTH", "H_FIT"):
        assert getattr(kit_physics, name) == getattr(shp, name), name
    assert np.array_equal(kit_physics.GRAVITY, shp.GRAVITY)
    v = np.array([[22.0, 18.0, -2.0], [3.0, -5.0, 1.0]])
    assert np.array_equal(kit_physics.accel(v), shp.accel(v))
    p0, v0 = np.array([-5.0, 1.0, 0.5]), v[0]
    a, b = kit_physics.simulate(p0, v0, 0.0, [0.5, 1.0]), shp.simulate(p0, v0, 0.0, [0.5, 1.0])
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_drag_matches_reference():
    p0, v0 = np.array([-5.0, 1.0, 0.5]), np.array([22.0, 18.0, -2.0])
    ts = np.arange(1, 241) / 120  # 2 s clear
    ref, _ = shp.simulate(p0, v0, 0.0, ts)
    p, v, err = p0.copy(), v0.copy(), 0.0
    for i in range(240):
        p, v = sim.rk2(p, v)
        err = max(err, np.abs(p - ref[i]).max())
    print(f"drag RK2 vs shuttle_physics.simulate over a 2 s clear: max error {err * 100:.2f} cm")
    assert err < 0.02


def test_shot_solver():
    tab = sim.shot_table()
    rng = np.random.default_rng(1)
    K = 300
    D, h = rng.uniform(1.0, 13.0, K), rng.uniform(0.3, 3.1, K)
    tmin = tab.t_min(D, h)
    T = tmin + rng.uniform(0, 1, K) * np.clip(np.maximum(1 + 0.1 * D, tmin + 0.25) - tmin, 0, None)
    vh, vy, apex = tab.lookup(D, h, T)
    p = np.stack([np.zeros(K), h, np.zeros(K)], -1)
    v = np.stack([vh, vy, np.zeros(K)], -1)
    err = np.full(K, np.nan)
    mx = h.copy()
    for i in range(400):
        po = p
        p, v = sim.rk2(p, v, i * sim.DT)  # a struck shuttle: turnover drag from the hit
        mx = np.maximum(mx, p[:, 1])
        land = (p[:, 1] <= 0) & np.isnan(err)
        f = po[:, 1] / (po[:, 1] - p[:, 1])
        err = np.where(land, np.abs(po[:, 0] + f * (p[:, 0] - po[:, 0]) - D), err)
    print(f"shot table: landing error max {err.max() * 100:.2f} cm, mean {err.mean() * 100:.2f} cm, apex error max {np.abs(mx - apex).max() * 100:.1f} cm")
    assert err.max() < 0.05
    # against the reference solver (RK4 truth, no turnover) on a few cases: the turnover drag asks for a slightly faster launch (< 12 %, more for fast smashes)
    for i in range(5):
        v_ref = shp.solve_shot([0, h[i], 0], 0.0, [D[i], 0, 0], T[i])
        assert 0 <= vh[i] - v_ref[0] < 0.12 * abs(v_ref[0]) + 0.15 and abs(vy[i] - v_ref[1]) < 0.12 * np.hypot(*v_ref[:2]) + 0.15, (vh[i], vy[i], v_ref)
    # end to end through a hit: no noise, the flight lands on the aimed point (3D, 5 cm)
    s = one(noise_scale=0.0)
    s.shp[0], s.shv[0] = [-3.0, 1.9, 0.4], 0
    s.pos[0, 0] = [-3.3, 0.4]
    s.last_hitter[0], s.shots[0] = 1, 3
    a = np.array([[[0, 0, 1, 0.3, -0.4, 0.2], [0, 0, -1, 0, 0, 0]]])
    s._act = a
    s._hit(np.array([0]), 0)
    target = s.land[0].copy()
    p, v = s.shp[0].copy(), s.shv[0].copy()
    for i in range(600):
        po = p
        p, v = sim.rk2(p, v, i * sim.DT)
        if p[1] <= 0:
            f = po[1] / (po[1] - p[1])
            lx = po + f * (p - po)
            break
    e = np.hypot(lx[0] - target[0], lx[2] - target[1])
    print(f"hit -> landing {lx[[0, 2]]} aimed {target}: {e * 100:.2f} cm; expected aim ({3.35 + 3.35 * 0.3:.2f}, {2.59 * -0.4:.2f})")
    assert e < 0.05 and abs(target[0] - (3.35 + 3.35 * 0.3)) < 1e-9 and abs(target[1] - 2.59 * -0.4) < 1e-9


def test_smash_noise():
    """Aim scatter follows sigma = 0.15 + 0.25 v/90 + 0.35 (v/90)^2 (+ player speed term, here 0): a fast smash scatters more than a drop."""
    def spread(u, h, ax):
        s = sim.BadSim(4000, 1)
        s.shp[:] = [-3.0, h, 0.0]
        s.shv[:] = 0
        s.pos[:, 0] = [-3.45, -0.5]  # the shuttle is 0.45 m ahead and 0.5 m to the racquet side: contact quality 1
        s.vel[:] = 0
        s.shots[:] = 3
        s._act = np.tile(np.array([0, 0, 1, ax, 0.0, u]), (4000, 2, 1))
        s._hit(np.arange(4000), 0)
        v = np.linalg.norm(s.shv, axis=1)
        return s.land[:, 0].std(), v.mean()

    fast, vf = spread(-1.0, 2.6, -0.2)
    slow, vs = spread(1.0, 2.6, -0.2)
    want = lambda v: 0.15 + 0.25 * v / 90 + sim.SMASH_NOISE * (v / 90) ** 2  # noqa: E731
    print(f"aim scatter: fast {vf:.0f} m/s sigma {fast:.3f} (formula {want(vf):.3f}), slow {vs:.0f} m/s sigma {slow:.3f} (formula {want(vs):.3f})")
    assert fast > slow + 0.1 and abs(fast - want(vf)) < 0.08 and abs(slow - want(vs)) < 0.05


def test_shot_rate():
    s = sim.BadSim(256, 0)
    s.shp[:, 1] = 1.5
    s.shp[:, 0] = -3.0
    s.pos[:, 0, 0] = -3.2
    s._act = np.tile(np.array([0, 0, 1, 0.5, 0.2, 0.1]), (256, 2, 1))
    idx = np.arange(256)
    t = time.perf_counter()
    for _ in range(20):
        s._hit(idx, 0)
    rate = 20 * 256 / (time.perf_counter() - t)
    print(f"shots/s through _hit (solve + noise + bookkeeping): {rate:,.0f}")
    assert rate > 50_000


def test_step_rate():
    s = sim.BadSim(256, 0)
    obs = s._obs()
    n = 300
    t = time.perf_counter()
    for _ in range(n):
        obs, *_ = s.step(np.stack([bot.bot_actions(obs[:, 0]), bot.bot_actions(obs[:, 1])], 1))
    dt = time.perf_counter() - t
    print(f"N=256: {n * sim.DECISION * 256 / dt:,.0f} game sim-steps/s ({n * sim.DECISION / dt:,.0f} sim-steps/s per game), "
          f"{n / dt:,.0f} decision steps/s incl. bot")
    assert n * sim.DECISION * 256 / dt >= 20_000


def test_net_fault():
    s = one()
    setup_flight(s, 0, 2, [-0.05, 1.0, 0.0], [8.0, 0.0, 0.0], server=0)  # crosses at ~1.0 m, tape is 1.524
    info, rew = run_substeps(s)
    assert info["rally_end"][0] and info["reason"][0] == sim.NET and info["winner"][0] == 1
    assert s.score[0].tolist() == [0, 1] and s.server[0] == 1 and rew[0].tolist() == [-1, 1]
    # over the tape: no fault, shaping reward to the hitter
    s = one()
    setup_flight(s, 0, 2, [-0.05, 2.5, 0.0], [8.0, 0.0, 0.0], server=0)
    info, rew = run_substeps(s, shaping=0.1)
    assert not info["rally_end"][0] and abs(rew[0, 0] - (0.1 + rewards.SHOT_REWARD)) < 1e-9
    # the per-shot reward stops after 10 shots of a rally; the annealed shaping does not
    s = one()
    setup_flight(s, 0, 11, [-0.05, 2.5, 0.0], [8.0, 0.0, 0.0], server=0)
    info, rew = run_substeps(s, shaping=0.1)
    assert abs(rew[0, 0] - 0.1) < 1e-9
    print("net fault / legal crossing ok")


def test_depth_reward():
    """Paid at the legal net crossing: 0 at the short line, DEPTH_REWARD at the back line, 0 out of bounds and for a net fault."""
    def crossing(land, y=2.5):
        s = one()
        setup_flight(s, 0, 2, [-0.05, y, 0.0], [8.0, 0.0, 0.0], server=0)
        s.land[:] = land
        return run_substeps(s)[1][0, 0] - rewards.SHOT_REWARD
    d = rewards.DEPTH_REWARD
    assert abs(crossing([sim.SHORT_X, 0.0])) < 1e-9
    assert abs(crossing([sim.HL, 0.0]) - d) < 1e-9
    assert abs(crossing([(sim.SHORT_X + sim.HL) / 2, 0.0]) - d / 2) < 1e-9
    assert abs(crossing([sim.HL + 0.2, 0.0])) < 1e-9 and abs(crossing([sim.HL, sim.HW + 0.2])) < 1e-9  # out
    s = one()
    setup_flight(s, 0, 2, [-0.05, 1.0, 0.0], [8.0, 0.0, 0.0], server=0)
    s.land[:] = [sim.HL, 0.0]
    assert run_substeps(s)[1][0, 0] == -1.0  # net fault: only the lost point
    # shaping bound: the cap counts rally shots, so one player earns on at most ceil(10 / 2) = 5 of them: below one point
    assert -(-rewards.SHOT_REWARD_MAX_SHOTS // 2) * (rewards.SHOT_REWARD + rewards.DEPTH_REWARD + rewards.DISP_REWARD) < 1.0
    print("depth reward ok")


def test_displacement_reward():
    """0 when the shot lands on the opponent, DISP_REWARD at >= DISP_NORM, linear between, none after the cap."""
    def crossing(d, shots=2):
        s = one()
        setup_flight(s, 0, shots, [-0.05, 2.5, 0.0], [8.0, 0.0, 0.0], server=0)
        s.disp[:] = d
        return run_substeps(s)[1][0, 0]
    assert crossing(0.0) == 0.0
    assert abs(crossing(rewards.DISP_NORM) - rewards.DISP_REWARD) < 1e-9 and abs(crossing(9.0) - rewards.DISP_REWARD) < 1e-9
    assert abs(crossing(rewards.DISP_NORM / 2) - rewards.DISP_REWARD / 2) < 1e-9
    assert crossing(9.0, shots=11) == 0.0
    # _hit stores d = distance from the opponent to the landing point, and starts the opponent's reaction
    s = one(noise_scale=0.0)
    setup_flight(s, 1, 2, [-4.0, 1.0, 0.0], [0.0, 0.0, 0.0], server=1)
    s.pos[0, 0] = [-4.0, 0.0]
    s.pos[0, 1] = [4.0, 0.0]
    act = np.zeros((1, 2, sim.ACT_DIM))
    act[0, 0, 2], act[0, 0, 3], act[0, 0, 4] = 1, 0.0, 0.0  # aim (HL/2, 0) on the far half
    s._act, s._swing = act, act[..., 2] > 0
    s._hit(np.array([0]), 0)
    assert abs(s.disp[0] - np.hypot(sim.HL / 2 - 4.0, 0)) < 1e-6 and s.react[0, 1] == sim.REACTION_S and s.react[0, 0] == 0
    print("displacement reward ok")


def test_reaction_delay():
    """For REACTION_S after the opponent's hit the commanded velocity is ignored (velocity held), then the player responds."""
    s = one()
    setup_flight(s, 0, 3, [-6.0, 3.0, 0.0], [0.0, 0.0, 0.0])
    s.vel[0, 1] = [-2.0, 0.0]
    s.react[0, 1] = sim.REACTION_S
    act = np.zeros((1, 2, sim.ACT_DIM))
    act[0, 1, 0], act[..., 2] = 1.0, -1  # player 1 commands full speed in +x of own view
    t = 0.0
    while t < sim.REACTION_S - 2 * sim.DECISION * sim.DT:
        s.step(act)
        t += sim.DECISION * sim.DT
        assert abs(s.vel[0, 1, 0] + 2.0) < 1e-9 and s.vel[0, 1, 1] == 0
    for _ in range(3):
        s.step(act)
    assert s.vel[0, 1, 0] != -2.0 and s.react[0, 1] == 0
    print("reaction delay ok")


def test_serve_fault_penalty():
    s = one()
    setup_flight(s, 0, 0, [-4.0, 0.02, 0.0], [0.0, -5.0, 0.0], server=0)  # toss dropped: serve fault by the server
    info, rew = run_substeps(s)
    assert info["reason"][0] == sim.SERVE and rew[0].tolist() == [-1 - sim.SERVE_FAULT_PENALTY, 1]
    print("serve fault penalty ok")


def test_base_reward():
    """While the opponent is the next hitter the player earns BASE_REWARD at the base, less away from it, 0 for the hitter-to-be, annealed."""
    def r(pos, hitter=0, scale=1.0):
        s = one()
        setup_flight(s, hitter, 3, [-6.0, 3.0, 0.0], [0.0, 0.0, 0.0])  # high, slow, far from both: nothing ends in one step
        s.shv[:] = 0
        s.pos[0, 0] = pos
        s.pos[0, 1] = [rewards.BASE_X, 0.0]
        act = np.zeros((1, 2, sim.ACT_DIM))
        act[..., 2] = -1  # stand still, no swing
        return run_substeps(s, act=act, base_scale=scale)[1][0]
    at = r([-rewards.BASE_X, 0.0])
    assert abs(at[0] - rewards.BASE_REWARD) < 1e-5 and at[1] == 0.0  # player 1 is the next hitter
    assert r([-rewards.BASE_X + 1.6, 0.0])[0] < rewards.BASE_REWARD * np.exp(-2.0) * 1.01  # 2 sigma away
    assert r([-rewards.BASE_X, 0.0], scale=0.5)[0] == at[0] / 2 and r([-rewards.BASE_X, 0.0], scale=0.0)[0] == 0.0
    assert r([-rewards.BASE_X, 0.0], hitter=1)[0] == 0.0
    print("base reward ok")


def land_test(x, z, hitter=0, shots=2, server=0, score=(0, 0)):
    """Shuttle falling onto (x, z) after a hit by `hitter`; returns (reason, winner)."""
    s = one()
    setup_flight(s, hitter, shots, [x, 0.02, z], [0.0, -5.0, 0.0], server=server, score=score)
    info, _ = run_substeps(s)
    assert info["rally_end"][0]
    return sim.REASONS[info["reason"][0]], int(info["winner"][0])


def test_in_out_lines():
    assert land_test(5.0, 0.0) == ("missed", 0)  # in the receiver's court: the hitter scores
    assert land_test(6.70, 2.59) == ("missed", 0)  # on the corner: in
    assert land_test(6.72, 0.0) == ("out", 1)  # long
    assert land_test(5.0, 2.62) == ("out", 1)  # wide (singles)
    assert land_test(5.0, 2.80) == ("out", 1)  # in the doubles alley: out in singles
    assert land_test(-3.0, 0.0) == ("net", 1)  # fell on the hitter's own side
    assert land_test(-5.0, 0.0, hitter=1) == ("missed", 1)  # player 1 hits to the -X side
    print("in / out / line calls ok")


def test_serve_legality():
    # server 0 on an even score stands at z > 0 (his right) and must serve into x > 1.98, z < 0
    s = one()
    assert s.pos[0, s.server[0], 1] != 0
    for sv in (0, 1):
        for score in ((0, 0), (1, 0), (2, 5), (3, 5)):
            s = one(seed=sv)
            s.score[:] = score
            s.server[:] = sv
            s._start_rally(np.array([0]))
            own = score[sv]
            z_expect = (1 if sv == 0 else -1) * (1 if own % 2 == 0 else -1)
            assert np.sign(s.pos[0, sv, 1]) == z_expect, (sv, score, s.pos[0])
            assert np.sign(s.pos[0, 1 - sv, 1]) == -z_expect  # receiver stands diagonally
            assert sim.in_service_court(s.pos[0, 1 - sv, 0], s.pos[0, 1 - sv, 1], -z_expect)
    # serve (shots == 1) landing: server 0 at z > 0 -> target court z < 0, x in [1.98, 6.7]
    def serve(x, z):
        s = one()
        s.score[:] = 0
        s.server[:] = 0
        setup_flight(s, 0, 1, [x, 0.02, z], [0, -5.0, 0], server=0)
        info, _ = run_substeps(s)
        return sim.REASONS[info["reason"][0]], int(info["winner"][0])

    assert serve(4.0, -1.0) == ("missed", 0)  # legal serve, the receiver failed
    assert serve(1.98, -0.01) == ("missed", 0)  # short line and centre line are in
    assert serve(1.9, -1.0) == ("serve", 1)  # short
    assert serve(4.0, 1.0) == ("serve", 1)  # wrong (non-diagonal) court
    assert serve(6.8, -1.0) == ("serve", 1)  # long
    assert serve(4.0, -2.7) == ("serve", 1)  # wide
    print("service court side by parity and legality ok")


def test_serve_aim_is_normalised_over_the_service_court():
    """Whatever the aim, a noise-free serve with u in the safe range lands in the diagonal service court (both courts, both servers)."""
    for sv in (0, 1):
        for score in (0, 1):
            for ax, az in ((-0.9, -0.9), (0.9, 0.9), (0.0, 0.0), (-0.9, 0.9), (0.9, -0.9)):
                s = one(noise_scale=0.0)
                s.server[:] = sv
                s.score[:] = score
                s._start_rally(np.array([0]))
                s._act = np.zeros((1, 2, sim.ACT_DIM))
                s._act[0, sv] = [0, 0, 1, ax, az, 0.5]
                s._hit(np.array([0]), sv)
                p, v = s.shp[0].copy(), s.shv[0].copy()
                for i in range(600):
                    po = p
                    p, v = sim.rk2(p, v, i * sim.DT)
                    if p[1] <= 0:
                        f = po[1] / (po[1] - p[1])
                        break
                lx = po + f * (p - po)
                zsgn = -sim.service_z_sign(sv, score)
                assert sim.in_service_court(lx[0], lx[2], zsgn), (sv, score, ax, az, lx)
    print("serve aims map into the diagonal service court ok")


def test_serve_must_be_hit():
    s = one()
    srv = int(s.server[0])
    info = None
    for _ in range(40):  # nobody swings: the toss falls
        info, _ = run_substeps(s)
        if info["rally_end"][0]:
            break
    assert info["rally_end"][0] and info["reason"][0] == sim.SERVE and info["winner"][0] == 1 - srv
    print("an unhit toss is a serve fault ok")


def test_scoring():
    over = sim.is_over
    assert over(15, 0) and over(15, 13) and not over(15, 14) and over(16, 14) and not over(16, 15)
    assert not over(14, 0) and not over(20, 20) and over(21, 20) and over(21, 19) and not over(19, 18) and over(17, 15)
    s = one()
    s.score[:] = [14, 3]
    s.server[:] = 0
    s._start_rally(np.array([0]))
    setup_flight(s, 0, 2, [5.0, 0.02, 0.0], [0, -5.0, 0], server=0, score=(14, 3))
    info, _ = run_substeps(s)
    assert info["rally_end"][0] and s.stats["games"] == 1 and info["final_score"][0].tolist() == [15, 3]
    assert s.score[0].tolist() == [0, 0]  # new game
    # 14-14, then 15-14 is not over and 16-14 is
    s = one()
    setup_flight(s, 0, 2, [5.0, 0.02, 0.0], [0, -5.0, 0], server=0, score=(14, 14))
    info, _ = run_substeps(s)
    assert s.stats["games"] == 0 and s.score[0].tolist() == [15, 14]
    print("rally scoring: 15 / win by 2 / cap 21 ok")


def test_hit_rules():
    s = one(noise_scale=0.0)
    swing = np.array([[[0, 0, 1, 0, 0, 0], [0, 0, 1, 0, 0, 0]]], float)
    # the receiver cannot hit the toss; the server can (at 0.9 m)
    obs = s._obs()
    assert obs[0, s.server[0], 18] == 1 and obs[0, 1 - s.server[0], 18] == 0
    # the player may not reach over the net: shuttle on the other side, in range
    s = one()
    setup_flight(s, 1, 2, [0.3, 1.5, 0.0], [0, 0, 0])
    s.pos[0, 0] = [-0.5, 0.0]
    s._act = np.clip(swing, -1, 1)
    n_, ext = s._can_hit(0)
    assert not ext[0]
    # lunge: 1.75 m away (REACH 1.5 < d <= REACH_LUNGE 2.0) is only reachable with the extended reach and costs a freeze
    s = one()
    setup_flight(s, 1, 2, [-1.65, 1.5, 0.0], [0, 0, 0])
    s.pos[0, 0] = [-3.4, 0.0]
    n_, ext = s._can_hit(0)
    assert not n_[0] and ext[0]
    s._swing = np.array([[True, False]])
    s._act = np.tile(np.array([0, 0, 1, 0, 0, 0.0]), (1, 2, 1))
    s._tv = np.zeros((1, 2, 2))
    s._substep(np.zeros((1, 2)), np.zeros(1, bool))
    assert s.last_hitter[0] == 0 and s.freeze[0, 0] > 0.2
    # no double hit: the same player cannot hit again at once
    n_, ext = s._can_hit(0)
    assert not ext[0]
    print("hit rules ok")


def mirror_state(src, dst, g_src, g_dst):
    m = np.array([-1.0, -1.0])
    for p in (0, 1):
        dst.pos[g_dst, 1 - p] = src.pos[g_src, p] * m
        dst.vel[g_dst, 1 - p] = src.vel[g_src, p] * m
        dst.since_hit[g_dst, 1 - p] = src.since_hit[g_src, p]
        dst.freeze[g_dst, 1 - p] = src.freeze[g_src, p]
        dst.score[g_dst, 1 - p] = src.score[g_src, p]
    dst.shp[g_dst] = src.shp[g_src] * [-1, 1, -1]
    dst.shv[g_dst] = src.shv[g_src] * [-1, 1, -1]
    dst.land[g_dst] = src.land[g_src] * m
    dst.land_t[g_dst] = src.land_t[g_src]
    dst.last_hitter[g_dst] = 1 - src.last_hitter[g_src]
    dst.server[g_dst] = 1 - src.server[g_src]
    dst.shots[g_dst] = src.shots[g_src]


def test_mirroring():
    s = sim.BadSim(2, 0)
    rng = np.random.default_rng(5)
    for trial in range(5):
        s.pos[0, 0] = [-rng.uniform(1, 6), rng.uniform(-2, 2)]
        s.pos[0, 1] = [rng.uniform(1, 6), rng.uniform(-2, 2)]
        s.vel[0] = rng.normal(size=(2, 2))
        s.shp[0] = [rng.uniform(-5, 5), rng.uniform(0.3, 3), rng.uniform(-2, 2)]
        s.shv[0] = rng.normal(size=3) * 10
        s.land[0] = [rng.uniform(-6, 6), rng.uniform(-2, 2)]
        s.land_t[0] = rng.uniform(0, 2)
        s.since_hit[0] = rng.uniform(0, 1, 2)
        s.freeze[0] = [0.1, 0.0]
        s.score[0] = [rng.integers(0, 10), rng.integers(0, 10)]
        s.last_hitter[0], s.server[0], s.shots[0] = trial % 2, (trial + 1) % 2, trial
        mirror_state(s, s, 0, 1)
        o = s._obs()
        assert np.allclose(o[0, 0], o[1, 1], atol=1e-6) and np.allclose(o[0, 1], o[1, 0], atol=1e-6), (o[0, 0] - o[1, 1])
    print("mirrored states give identical observations ok")


def test_mirrored_play_is_symmetric():
    """The same action in each player's own view moves/hits the same way on either side."""
    s = sim.BadSim(2, 0)
    s.noise_scale = 0.0
    s.server[:] = [0, 1]
    s._start_rally(np.array([0, 1]))
    a = np.zeros((2, 2, 6))
    a[:, :, 0] = 1.0  # "forward" in own view
    a[:, :, 2] = -1
    s.step(a)
    assert np.allclose(s.pos[0, 0] * -1, s.pos[1, 1], atol=1e-9)  # both run toward the net in their own view
    # server hits an identical serve aim; mirrored landing points
    s.reset()
    s.server[:] = [0, 1]
    s._start_rally(np.array([0, 1]))
    a = np.zeros((2, 2, 6))
    a[:, :, 2] = 1
    a[:, :, 3], a[:, :, 4], a[:, :, 5] = -0.3, 0.0, 0.4
    s.step(a)
    assert np.allclose(s.land[0] * -1, s.land[1], atol=1e-9)
    print("mirrored serve aims land on mirrored points ok")


def test_bot_game_is_legal():
    n = 16
    # The bot never misses at the Phase 13.1 reach (rallies run into the 150-shot let), so it plays with the Phase 13 reach here
    s = sim.BadSim(n, 3)
    s._p["reach"][:], s._p["reach_lunge"][:] = 1.0, 1.4
    obs = s._obs()
    done_at = np.full(n, -1)
    finals = np.zeros((n, 2), int)
    for step in range(250000):  
        obs, _, d, info = s.step(np.stack([bot.bot_actions(obs[:, 0]), bot.bot_actions(obs[:, 1])], 1))
        new = d & (done_at < 0)
        done_at[new] = step
        finals[new] = info["final_score"][new]
        if (done_at >= 0).all():
            break
    assert (done_at >= 0).all(), "bot games did not finish"
    hi, lo = finals.max(1), finals.min(1)
    assert ((hi >= 15) & (hi <= 21) & ((hi - lo >= 2) | (hi == 21))).all(), finals
    print(f"{n} bot-vs-bot games finished in {step + 1} decision steps; scores legal (e.g. {finals[:4].tolist()}); "
          f"mean rally {s.stats['rally_shots'] / s.stats['rallies']:.1f} shots")


def test_log_and_serialize():
    s = sim.BadSim(1, 0, log_game=0)
    obs = s._obs()
    steps = 0
    while not any(e["kind"] == "point" for e in s.log["events"]) and steps < 20000:
        obs, *_ = s.step(np.stack([bot.bot_actions(obs[:, 0]), bot.bot_actions(obs[:, 1])], 1))
        steps += 1
    g = s.serialize_game(0)
    kinds = [e["kind"] for e in g["events"]]
    assert g["pos"].shape == (1 + steps * sim.DECISION, 2, 2) and g["shuttle"].shape == (len(g["pos"]), 6)
    assert kinds[0] == "start" and "hit" in kinds and "land" in kinds and "point" in kinds
    print(f"serialize_game: {len(g['pos'])} steps, events {dict((k, kinds.count(k)) for k in set(kinds))}")


def test_shot_classifier_rules():
    c = evaluate.classify
    assert c(True, 0.9, 5.0, 14, 30, 3.0, 4.0) == "serve"
    assert c(False, 2.6, 3.0, 55, -20, 2.6, 4.0) == "smash"
    assert c(False, 2.4, 3.5, 28, 40, 7.0, 5.5) == "clear"
    assert c(False, 0.6, 2.5, 18, 55, 5.0, 5.5) == "lift"
    assert c(False, 2.4, 3.5, 14, 5, 2.8, 2.0) == "drop"
    assert c(False, 1.0, 1.0, 5, 40, 1.9, 1.2) == "net"
    assert c(False, 1.3, 3.5, 25, 3, 1.6, 5.0) == "drive"


def test_match_export_timeline():
    """A bot-vs-bot game becomes consistent replay tracks: legal final score, point events, flights, contacts, round trip."""
    tl = pm.build(match.play("bot", "bot", 2, best_of=1))
    ev = tl["events"]
    pts = ev[ev[:, 1] == 3]
    w = pts[-1]
    final = sorted([int(w[3]), int(w[4])])
    assert final[1] >= 15 and (final[1] - final[0] >= 2 or final[1] == 21) and final[1] <= 21, final
    assert len(pts) == final[0] + final[1]  # one point event per point, scores add up
    assert (np.diff(ev[:, 0]) >= 0).all() and (np.diff(pts[:, 0]) > 3.0).all()  # sorted; rallies > the stats' 3 s gap apart
    n_contacts = (ev[:, 1] == 1).sum()
    assert n_contacts == sum(r["shots"] for r in tl["sheet"]) and (ev[:, 1] == 2).sum() == len(pts)
    sh = tl["shuttle"]
    fid = sh[sh[:, 6] > 0, 7]
    assert (np.diff(fid) >= 0).all() and len(np.unique(fid)) >= n_contacts  # every shot (+ toss) is its own flight
    assert (sh[sh[:, 6] > 0, 1] >= -1e-9).all() and np.abs(tl["pos"][..., 1]).max() <= 3.7
    # contact events carry the shuttle position and the hitter's side
    c = ev[ev[:, 1] == 1]
    assert (np.sign(c[:, 3]) == np.where(c[:, 2] == 0, -1, 1)).mean() > 0.97  # contacts happen on the hitter's own side
    # a short written replay reads back through tools/replay.py
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        k = int(80 * pm.FPS)
        replay.write(Path(d) / "m.bin", pm.FPS, [({"name": "joints3d", "layout": "x,y,z,conf"}, np.zeros((k, 2, 17, 4))),
                                              ({"name": "shuttle", "layout": "x,y,z,vx,vy,vz,conf,flight_id"}, sh[:k]),
                                              ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, ev)])
        hd, tr = replay.read(Path(d) / "m.bin")
        assert tr["events"].shape == ev.shape and hd["frame_count"] == k
    print(f"match export: {len(tl['pos']) / pm.FPS / 60:.1f} min, {len(pts)} points, final {w[3]:.0f}-{w[4]:.0f} (winner's score first), "
          f"{n_contacts} contacts")


def test_swing_reaches_the_shuttle():
    """Body animation: at the contact the racquet head (wrist + 0.53 m along the forearm) is near the shuttle, from low to overhead."""
    fps = 60.0
    t = np.arange(int(3 * fps)) / fps
    root = np.tile([-3.0, 0.0], (len(t), 1))
    worst = 0.0
    for h in (0.5, 1.0, 1.6, 2.2, 2.6):
        hit = dict(t=1.5, h=h, lunge=False, jump=False, at=np.array([-2.6, h, 0.3]))
        sw = pm.swings_for([hit], np.random.default_rng(0))
        j = sm.player_pose(root, -1, t, fps, sw, [], [], pm.facing_for(root, -1, t, [hit], fps))
        i = int(round(1.5 * fps))
        fore = j[i, 10] - j[i, 8]
        head = j[i, 10] + 0.53 * fore / np.linalg.norm(fore)
        d = np.linalg.norm(head - hit["at"])
        worst = max(worst, d)
        assert d < 0.9, (h, d, head)
    print(f"swing: racquet head within {worst:.2f} m of the shuttle at the contact (0.5-2.6 m contact heights)")


def test_synth_motion_unchanged():
    import synth_motion

    for args, want in (((0,), GOLDEN_SYNTH[0]), ((3, 30.0, 6.0), GOLDEN_SYNTH[1])):
        h = hashlib.sha256()
        for a in synth_motion.generate(*args):
            h.update(np.ascontiguousarray(a).tobytes())
        assert h.hexdigest() == want, f"synth_motion.generate{args} changed"
    print("synth_motion default output unchanged ok")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"all {len(tests)} tests ok")


if __name__ == "__main__":
    main()
