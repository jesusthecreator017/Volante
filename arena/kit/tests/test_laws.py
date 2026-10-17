"""Phase 14.2b: singles laws, net cord, turnover, physical racquet contact (CONTRACT.md sections 5, 6, 9)."""
import numpy as np

from helpers import run
from oso_arena import physics, sim

NOSWING = np.full((1, 2, sim.ACT_DIM), -1.0)


def fresh(seed=0, **kw):
    s = sim.BadSim(1, seed)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def rally(s, last_hitter, shots, p, v, pos0=(-4.0, 0.0), pos1=(4.0, 0.0), server=0):
    """A rally in progress: shuttle at p with velocity v, the given player last hit it, `shots` shots played."""
    s.server[:] = server
    s._start_rally(np.array([0]))
    s.last_hitter[:], s.shots[:] = last_hitter, shots
    s.shp[:], s.shv[:] = p, v
    s.tsh[:] = 1.0
    s.pos[0, 0], s.pos[0, 1] = pos0, pos1
    s.vel[:] = 0
    s.land[:] = [1.0, 0.0]


def swing_act(aim=(0.0, 0.0, 0.0)):
    a = np.zeros((1, 2, sim.ACT_DIM))
    a[0, :, 2] = -1
    a[0, 0, 2], a[0, 0, 3:] = 1, aim
    return a


def run_until_end(s, act=NOSWING, steps=400):
    for _ in range(steps):
        _, _, _, info = s.step(act)
        if info["rally_end"][0]:
            return info
    raise AssertionError("the rally did not end")


# ---------------------------------------------------------------- A: laws
def test_players_stand_still_until_the_serve_is_struck():
    s = fresh()
    srv = int(s.server[0])
    p0 = s.pos.copy()
    go = np.zeros((1, 2, sim.ACT_DIM))
    go[..., 0], go[..., 1], go[..., 2] = 1.0, 1.0, -1  # both run, nobody swings
    for _ in range(5):
        s.step(go)
    assert np.array_equal(s.pos, p0) and not s.vel.any(), "commanded velocity must be ignored before the serve"
    # the server may still swing: the serve is struck, and from then on the commands work
    go[0, srv, 2] = 1.0
    s.step(go)
    assert s.shots[0] == 1
    for _ in range(12):  # the receiver also waits out the 0.2 s reaction time
        s.step(go)
    assert np.linalg.norm(s.pos[0, 1 - srv] - p0[0, 1 - srv]) > 0.1 and np.linalg.norm(s.pos[0, srv] - p0[0, srv]) > 0.1


def test_contact_only_on_the_hitters_own_side_of_the_net():
    for x, want in ((-0.05, True), (0.05, False)):  # player 0, shuttle just before / just behind the net plane, in reach
        s = fresh()
        rally(s, 1, 2, [x, 1.5, 0.0], [0, 0, 0], pos0=(-0.9, 0.0))
        s.since_hit[:] = 1.0
        assert bool(s._can_hit(0)[1][0]) == want, x
    for x, want in ((0.05, True), (-0.05, False)):  # player 1 mirrored
        s = fresh()
        rally(s, 0, 2, [x, 1.5, 0.0], [0, 0, 0], pos1=(0.9, 0.0))
        s.since_hit[:] = 1.0
        assert bool(s._can_hit(1)[1][0]) == want, x


def test_net_touch_is_a_fault_for_a_lunge_at_the_net():
    def hit_at(x, pos0):
        s = fresh(noise_scale=0.0)
        rally(s, 1, 2, [x, 1.5, 0.0], [0, 0, 0], pos0=pos0)
        s.since_hit[:] = 1.0
        s._act = swing_act()
        s._swing = s._act[..., 2] > 0
        return s, s.step(s._act)[3]

    s, info = hit_at(-0.06, (-1.9, 0.0))  # 1.84 m away > reach 1.5: a lunge, contact 6 cm from the net plane
    assert info["rally_end"][0] and sim.REASONS[info["reason"][0]] == "net_touch" and info["winner"][0] == 1
    assert s.score[0].tolist() == [0, 1] and s.stats["faults"][sim.NET_TOUCH] == 1
    s, info = hit_at(-0.06, (-0.5, 0.0))  # normal reach: no touch
    assert not info["rally_end"][0] and s.shots[0] == 3
    s, info = hit_at(-0.3, (-2.0, 0.0))  # lunge, but 30 cm from the net: no touch
    assert not info["rally_end"][0] and s.shots[0] == 3


def test_no_double_hit():
    s = fresh(noise_scale=0.0)
    rally(s, 1, 2, [-3.5, 1.5, 0.0], [0, 0, 0], pos0=(-3.9, -0.4))
    s.since_hit[:] = 1.0
    a = swing_act((0.0, 0.0, 1.0))
    s.step(a)
    assert s.shots[0] == 3 and s.last_hitter[0] == 0
    n = s.shots[0]
    s.shp[0], s.shv[0] = [-3.5, 1.5, 0.0], 0  # the shuttle is back in reach while the swing is still held
    s.step(a)
    assert s.shots[0] == n, "the same player must not hit twice in a row"
    s.last_hitter[:] = 1  # even when the opponent touched it, 0.35 s must pass since the own last hit
    s.since_hit[0, 0] = 0.1
    s.step(a)
    assert s.shots[0] == n


def test_ceiling_and_walls_are_the_hitters_fault():
    for p, v in (([-2.0, 11.45, 0.0], [5.0, 20.0, 0.0]), ([-2.0, 5.0, 0.0], [-100.0, 2.0, 0.0]), ([-2.0, 5.0, 0.0], [0.0, 2.0, 80.0])):
        s = fresh()
        rally(s, 0, 3, p, v)
        info = run_until_end(s)
        assert sim.REASONS[info["reason"][0]] == "ceiling" and info["winner"][0] == 1, (p, v)
    s = fresh()  # the opponent hit it: the ceiling fault is the opponent's
    rally(s, 1, 3, [2.0, 11.45, 0.0], [5.0, 20.0, 0.0])
    info = run_until_end(s)
    assert sim.REASONS[info["reason"][0]] == "ceiling" and info["winner"][0] == 0


def test_serve_legality():
    # struck too high: the whole shuttle must be below 1.15 m (we test the centre)
    s = fresh(noise_scale=0.0)
    srv = int(s.server[0])
    s.shp[0] = [s.pos[0, srv, 0] - sim.SIGN[srv] * 0.5, 1.3, s.pos[0, srv, 1]]  # 0.5 m ahead of the server (toward the net), at 1.3 m
    a = np.zeros((1, 2, sim.ACT_DIM))
    a[0, srv, 2] = 1
    a[0, 1 - srv, 2] = -1
    s._act, s._swing = a, a[..., 2] > 0
    s._tv = np.zeros((1, 2, 2))
    info = s.step(a)[3]
    assert info["rally_end"][0] and sim.REASONS[info["reason"][0]] == "serve" and info["winner"][0] == 1 - srv
    # the normal toss falls from 1.1 m and is always struck below the limit
    for seed in range(20):
        s = fresh(seed)
        s.shp[0, 1] = sim.TOSS_Y
        assert s.shp[0, 1] <= sim.SERVE_MAX_H
    # a serve that passes under the net tape is a net fault of the server; one landing outside the service court is a serve fault
    s = fresh()
    rally(s, 0, 1, [-0.05, 1.0, 0.0], [8.0, 0.0, 0.0])
    assert sim.REASONS[run_until_end(s)["reason"][0]] == "net"
    s = fresh()
    rally(s, 0, 1, [4.0, 0.02, 1.0], [0.0, -5.0, 0.0], server=0)  # server 0 at even score serves into z < 0: this is the wrong court
    assert sim.REASONS[run_until_end(s)["reason"][0]] == "serve"


def test_let_only_for_the_rally_cap():
    s = fresh()
    rally(s, 0, sim.RALLY_CAP_SHOTS, [-3.0, 4.0, 0.0], [0.0, 0.0, 0.0])
    info = run_until_end(s)
    assert info["reason"][0] == sim.LET and info["winner"][0] == -1 and s.score[0].tolist() == [0, 0]
    assert sim.REASONS == ["net", "out", "missed", "serve", "net_touch", "ceiling", "let"]


# ---------------------------------------------------------------- B: shuttle physics
def cord_run(y_off, vx, vy=0.0, seed=0, z=0.0):
    s = fresh(seed)
    ht = float(sim.tape_height(z))
    rally(s, 0, 2, [-0.3, ht + y_off - vx * 0.0, z], [vx, vy, 0.0])
    # start so the first contact happens within a few steps
    info = run_until_end(s)
    return s, info


def test_net_cord_slow_graze_falls_back_and_fast_clip_goes_over():
    back = over = 0
    for seed in range(40):
        s, info = cord_run(0.0, 3.0, seed=seed)  # slow, straight into the tape
        back += sim.REASONS[info["reason"][0]] == "net" and s.stats["cords"] > 0  # fell back: landed on the hitter's side
    assert back >= 38, back
    for seed in range(40):
        s = fresh(seed)
        rally(s, 0, 2, [-0.3, 1.564, 0.0], [25.0, 0.0, 0.0])  # fast, clips the top of the tape
        info = run_until_end(s)
        over += s.stats["cords"] > 0 and sim.REASONS[info["reason"][0]] in ("missed", "out")
    assert over >= 38, over
    # the tape is 2.6 cm higher at the posts
    assert abs(sim.tape_height(0.0) - 1.524) < 1e-12 and abs(sim.tape_height(3.05) - 1.55) < 1e-12 and sim.tape_height(-1.525) > 1.536
    # a shuttle that passes fully below the tape (no contact) is still a net fault
    s = fresh()
    rally(s, 0, 2, [-0.3, 1.2, 0.0], [10.0, 0.0, 0.0])
    assert sim.REASONS[run_until_end(s)["reason"][0]] == "net" and s.stats["cords"] == 0


def test_net_cord_is_deterministic_with_the_seed():
    def once(seed):
        s = sim.BadSim(1, seed, log_game=0)
        rally(s, 0, 2, [-0.3, 1.56, 0.0], [9.0, 0.0, 0.0])
        run_until_end(s)
        cords = [e for e in s.log["events"] if e["kind"] == "cord"]
        return cords, s.serialize_game(0)["shuttle"]
    a, b, c = once(3), once(3), once(4)
    assert len(a[0]) > 0 and a[0] == b[0] and np.array_equal(a[1], b[1])
    assert a[0][0]["v"] != c[0][0]["v"]  # a different seed deflects differently


def test_net_cord_reflection_numbers():
    """Straight into the tape (normal -x): v_x -> -0.15 v_x (+ noise sigma 0.2), v_z halves."""
    s = fresh(0)
    rally(s, 0, 2, [-0.0525, 1.524, 0.0], [10.0, 0.0, 2.0])
    old = s.shp.copy()
    s.shp[0] = [0.0, 1.524, 0.0]  # moved to the tape axis in this step: the segment enters the circle at x = -0.05
    s.shv[0] = [10.0, 0.0, 2.0]
    s._net_cord(old)
    assert s.stats["cords"] == 1 and abs(s.shp[0, 0] + sim.CORD_R) < 1e-3
    assert abs(s.shv[0, 0] + 1.5) < 1.0 and abs(s.shv[0, 2] - 1.0) < 1e-12
    xs = []
    for seed in range(300):
        s = fresh(seed)
        rally(s, 0, 2, [-0.0525, 1.524, 0.0], [10.0, 0.0, 0.0])
        o = s.shp.copy()
        s.shp[0] = [0.0, 1.524, 0.0]
        s._net_cord(o)
        xs.append(s.shv[0, 0])
    assert abs(np.mean(xs) + 1.5) < 0.05 and abs(np.std(xs) - sim.CORD_SIGMA) < 0.04, (np.mean(xs), np.std(xs))


def test_turnover_drag_and_solver():
    assert sim.drag_factor(0.0) == sim.TURNOVER_DRAG and sim.drag_factor(sim.TURNOVER_S) == 1.0 and sim.drag_factor(1.0) == 1.0
    assert abs(sim.drag_factor(sim.TURNOVER_S / 2) - (1 + (sim.TURNOVER_DRAG - 1) / 2)) < 1e-12
    # a turnover flight is slower than the plain one after 0.1 s, and the two agree if the turnover is over
    p0, v0 = np.array([-5.0, 1.0, 0.0]), np.array([60.0, 10.0, 0.0])
    a, b, c = (p0, v0), (p0, v0), (p0, v0)
    for i in range(12):
        a, b, c = sim.rk2(*a), sim.rk2(*b, i * sim.DT), sim.rk2(*c, 5.0)
    assert b[0][0] < a[0][0] - 0.01 and np.array_equal(a[0], c[0]) and np.array_equal(a[1], c[1])
    # the table solves the same flights: noise-free shots through _hit land on the aim within 5 cm (every build's power, both servers)
    rng = np.random.default_rng(2)
    worst, n_ok = 0.0, 0
    for trial in range(80):
        pw = int(rng.integers(1, 11))
        s = sim.BadSim(1, trial, builds=np.array([[[pw, 6, 6, 6], [6, 6, 6, 6]]]))
        s.noise_scale = 0.0
        rally(s, 1, 3, [-2.5 + rng.uniform(-1, 1), rng.uniform(0.95, 2.8), rng.uniform(-1, 1)], [0, 0, 0])
        s.pos[0, 0] = [s.shp[0, 0] - 0.45, s.shp[0, 2] - 0.5]  # ideal contact: quality 1
        a_ = np.zeros((1, 2, sim.ACT_DIM))
        a_[0, 0] = [0, 0, 1, rng.uniform(-0.5, 0.8), rng.uniform(-0.8, 0.8), rng.uniform(0.0, 1.0)]
        s._act, s._swing = a_, a_[..., 2] > 0
        s._hit(np.array([0]), 0)
        if np.linalg.norm(s.shv[0]) > sim.launch_cap(s.shp[:1, 1], np.array([pw]))[0] * 0.999:
            continue  # clamped to the launch cap: it falls short on purpose
        pp, vv, skip = s.shp[0].copy(), s.shv[0].copy(), False
        for i in range(700):
            po = pp
            pp, vv = sim.rk2(pp, vv, i * sim.DT)
            if po[0] * pp[0] < 0 and abs(po[1] - float(sim.tape_height(po[2]))) < 0.15:
                skip = True  # grazes the tape: the net-cord model (not the table) decides this one
            if pp[1] <= 0:
                f = po[1] / (po[1] - pp[1])
                lnd = po + f * (pp - po)
                break
        if not skip:
            worst = max(worst, float(np.hypot(lnd[0] - s.land[0, 0], lnd[2] - s.land[0, 1])))
            n_ok += 1
    print(f"turnover solver: worst landing error {worst * 100:.2f} cm over {n_ok} unclamped shots")
    assert n_ok > 30 and worst < 0.05


def test_drag_model_parity_with_physics_simulate_without_turnover():
    """The kit's plain drag (tau = None, or a hit long ago) is the model of physics.simulate: RK2 at 1/120 s is within 1 cm over 2 s."""
    for p0, v0 in (([-5.0, 1.0, 0.5], [22.0, 18.0, -2.0]), ([-3.0, 2.8, 0.0], [45.0, -12.0, 3.0]), ([2.0, 0.5, -1.0], [-6.0, 14.0, 1.0])):
        p0, v0 = np.array(p0), np.array(v0)
        ts = np.arange(1, 241) / 120
        ref, refv = physics.simulate(p0, v0, 0.0, ts)
        for tau in (None, 9.0):
            p, v, err = p0.copy(), v0.copy(), 0.0
            for i in range(240):
                p, v = sim.rk2(p, v, tau)
                err = max(err, np.abs(p - ref[i]).max())
            assert err < 0.01, (tau, err)
        assert np.abs(v - refv[-1]).max() < 0.02


# ---------------------------------------------------------------- C: racquet contact
def q_of(f, l, y, reach=1.5, ext=False):
    return float(sim.contact_quality(np.array([f]), np.array([l]), np.array([y]), reach, np.array([ext]))[0])


def test_contact_quality_zone_and_penalties():
    assert q_of(0.45, 0.5, 1.6) == 1.0 and q_of(0.3, 0.3, 0.9) == 1.0 and q_of(0.7, 0.9, 2.9) == 1.0  # the ideal zone (its corner at 0.2, 0.2 is within the 0.35 m cramped radius)
    assert q_of(-0.1, 0.5, 2.7) == 1.0  # overhead: a hit above and slightly behind the head is ideal at 2.6 m and higher
    assert q_of(-0.1, 0.5, 1.6) < 0.9  # the same place at shoulder height is late
    cramped = q_of(0.0, 0.0, 1.6)
    assert cramped < 0.7 and q_of(0.0, 0.0, 1.6) < q_of(0.2, 0.2, 1.6)  # right on the body
    late = q_of(-0.5, 0.5, 1.6)
    assert late < 0.5 and late < q_of(-0.2, 0.5, 1.6) < 1.0  # behind the body, worse the further behind
    back = q_of(0.45, -0.4, 1.6)
    assert back < 0.7 and back < q_of(0.45, -0.1, 1.6)  # backhand side
    assert q_of(1.4, 0.5, 1.6) < q_of(1.0, 0.5, 1.6) < 1.0  # stretched forward: worse with the distance
    assert q_of(1.4, 0.5, 1.6, ext=True) < q_of(1.4, 0.5, 1.6)  # a lunge costs extra
    assert q_of(1.4, 0.5, 1.6, reach=1.8) > q_of(1.4, 0.5, 1.6, reach=1.2)  # long arms stretch less
    assert q_of(0.45, 0.5, 0.2) < 0.7  # a ground-level shuttle is outside the zone
    assert 0.0 <= q_of(-3.0, -3.0, 0.1, ext=True) <= 0.15  # unreachable garbage is a mishit
    r = np.random.default_rng(0)
    q = sim.contact_quality(r.uniform(-2, 2, 500), r.uniform(-2, 2, 500), r.uniform(0, 3.3, 500), 1.5, r.random(500) < 0.5)
    assert q.min() >= 0 and q.max() <= 1


def _shot(f, l, y, aim=(0.5, 0.0, 0.0), n=3000, seed=1):
    """n shots from a hitter (player 0) with the shuttle at (f, l, y) in his body frame."""
    s = sim.BadSim(n, seed)
    s.shp[:] = [-3.0, y, 0.0]
    s.pos[:, 0] = [-3.0 - f, -l]  # player 0 faces +x; his racquet side is +z
    s.shots[:] = 3
    s.last_hitter[:] = 1
    s._act = np.tile(np.array([0, 0, 1, *aim]), (n, 2, 1))
    s._hit(np.arange(n), 0)
    return s


def test_quality_scales_launch_cap_and_aim_noise():
    good, poor = _shot(0.45, 0.5, 2.6, (0.9, 0.0, -1.0)), _shot(-0.3, -0.1, 2.6, (0.9, 0.0, -1.0))
    qp = q_of(-0.3, -0.1, 2.6)
    assert 0.15 < qp < 0.6, qp
    vg, vp = np.linalg.norm(good.shv, axis=1), np.linalg.norm(poor.shv, axis=1)
    assert vg.max() <= 90 * 1.0001 and vp.max() <= 90 * (0.6 + 0.4 * qp) * 1.0001  # the cap scales with q (a flat-out smash is clamped)
    assert vg.mean() > vp.mean() * 1.1
    gs, ps = _shot(0.45, 0.5, 2.0, (0.0, 0.0, 1.0)), _shot(-0.3, -0.1, 2.0, (0.0, 0.0, 1.0))
    qp = q_of(-0.3, -0.1, 2.0)
    ratio = ps.land[:, 1].std() / gs.land[:, 1].std()
    print(f"aim noise ratio poor/good {ratio:.2f}, expected {1 + sim.Q_NOISE * (1 - qp):.2f}")
    assert abs(ratio - (1 + sim.Q_NOISE * (1 - qp))) < 0.1 * ratio
    assert gs.stats["mishits"] == 0 and abs(gs.stats["q_sum"] / 3000 - 1.0) < 1e-9


def test_mishit_is_slow_and_scattered():
    n = 4000
    ok = _shot(0.45, 0.5, 1.6, (0.5, 0.0, 0.0), n)
    mis = _shot(-1.2, -1.0, 1.6, (0.5, 0.0, 0.0), n)  # far behind and on the backhand side: q = 0
    assert q_of(-1.2, -1.0, 1.6) < sim.MISHIT_Q and mis.stats["mishits"] == n and ok.stats["mishits"] == 0
    vo, vm = np.linalg.norm(ok.shv, axis=1), np.linalg.norm(mis.shv, axis=1)
    # 40 % of the intended speed (the intended shot of the mishit sits at the reduced cap, so compare with the aimed speed of a clean one)
    assert 0.3 < vm.mean() / vo.mean() < 0.5, vm.mean() / vo.mean()
    yaw = np.degrees(np.arctan2(mis.shv[:, 2], mis.shv[:, 0]))
    yaw0 = np.degrees(np.arctan2(ok.shv[:, 2], ok.shv[:, 0])).mean()
    assert 0.6 * 25 < np.std(yaw - yaw0) < 1.6 * 25, np.std(yaw - yaw0)  # direction error sigma 25 degrees (plus the aim noise)


def test_hit_event_and_log_carry_the_contact():
    s = sim.BadSim(1, 0, log_game=0)
    obs = s._obs()
    from oso_arena import bot

    for _ in range(300):
        obs, *_ = s.step(np.stack([bot.bot_actions(obs[:, 0]), bot.bot_actions(obs[:, 1])], 1))
        if any(e["kind"] == "hit" for e in s.log["events"]):
            break
    h = next(e for e in s.log["events"] if e["kind"] == "hit")
    assert 0 <= h["q"] <= 1 and len(h["body"]) == 3 and isinstance(h["mishit"], bool)
    assert abs(h["q"] - q_of(h["body"][0], h["body"][1], h["body"][2], 1.5, h["lunge"] or h["jump"])) < 1e-9


if __name__ == "__main__":
    run(globals())
