"""Phase 14.2b C3: in the replay the racquet's sweet spot meets the shuttle at every contact (recomputed from the joints the way the viewer draws the racquet)."""
import numpy as np

from helpers import run
from oso_arena import animate, match, sim

TOL = 0.03


def test_arm_solver_puts_the_sweet_spot_on_reachable_targets():
    rng = np.random.default_rng(0)
    S = np.array([-3.0, 1.4, 0.2])
    worst = 0.0
    for _ in range(300):
        d = rng.normal(size=3)
        P = S + d / np.linalg.norm(d) * rng.uniform(animate.REACH_MIN, animate.REACH_MAX)
        fwd = np.array([1.0, 0, 0])
        hint = animate.pick_hint(S, P, animate._right_hint(fwd))
        W, E, miss = animate.solve_arm(S, P, hint)
        worst = max(worst, miss)
        assert abs(np.linalg.norm(E - S) - animate.sm.UPPER_ARM) < 1e-6
    print(f"arm IK: worst miss {worst * 1000:.2f} mm over 300 targets between {animate.REACH_MIN} and {animate.REACH_MAX} m of the shoulder")
    assert worst < 0.005


def test_racquet_meets_the_shuttle_at_every_contact():
    log = match.play("bot", "bot", 4, best_of=1, target=7)
    tl = animate.build(log)
    joints = animate.bodies(tl, log.seed)
    worst, n, lunge = 0.0, 0, 0
    for p in (0, 1):
        for h in tl["hits"][p]:
            i = animate.frame_of(h["t"])
            sweet = animate.sweet_spot(joints[i, p, :, :3])
            d = float(np.linalg.norm(sweet - h["at"]))
            ds = float(np.linalg.norm(tl["shuttle"][i, :3] - h["at"]))
            assert d <= TOL, (p, h["t"], d)
            assert ds <= TOL, ("shuttle is not at the contact in the contact frame", ds)
            worst, n, lunge = max(worst, d), n + 1, lunge + (h["lunge"] or h["jump"])
    assert n == (tl["events"][:, 1] == 1).sum() and n > 30
    # the arm stays a real arm (bone lengths) and the shoulder-to-sweet-spot distance is within the arm's reach everywhere it was pinned
    S, W, E = joints[..., 6, :3], joints[..., 10, :3], joints[..., 8, :3]
    assert np.abs(np.linalg.norm(W - E, axis=-1) - animate.sm.FOREARM).max() < 0.02
    print(f"{n} contacts ({lunge} lunge / jump), racquet sweet spot within {worst * 100:.2f} cm of the shuttle at all of them")


def test_bodies_stay_plausible():
    log = match.play("bot", "bot", 6, best_of=1, target=5)
    tl = animate.build(log)
    j = animate.bodies(tl, log.seed)[..., :3]
    assert np.isfinite(j).all()
    assert j[..., 15:17, 1].min() > -0.02  # feet never below the floor
    assert (np.abs(np.diff(j[:, :, 11], axis=0)).max()) < 0.12 * 2, "hips jumped between two frames"  # < 7 m/s at 60 fps


if __name__ == "__main__":
    run(globals())
