"""Synthetic rally (ground truth) + per-camera 2D shuttle detections (TrackNet-style output).
Run: python synth_shuttle.py [--gt ../out/synth/pose_gt.npz] [--out ../out/synth] [--seed 0]
Writes shuttle_gt.npz and shuttle2d_a.npz / shuttle2d_b.npz (format: docs/data-format.md).

Rally: the players' swing contacts (pose_gt.npz) merged in time order; each consecutive pair is one physics shot from the
hitter's racquet head to the receiver's racquet head (shuttle_physics.solve_shot). Before the first contact the shuttle
is tossed from 0.3 s earlier (a short falling arc ending at the racquet head); after the last it flies 1.2 s to the
floor in the receiver's court. If no shot apexes above 6.5 m (out of the cameras' view, H_AIR = 6 m) the longest-gap shot
becomes a clear: apex CLEAR_APEX = 9 m, ending at the receiver's racquet head. Its flight time is then fixed by the physics, so the
receiver's contact moves to the arrival time (it no longer lines up with that player's swing animation, which is
acceptable for synthetic data) and the contacts that would fall inside the clear or within 0.5 s after it are dropped.
The contact times in shuttle_gt.npz are the ones synth_imu.py uses for its impacts.
The shuttle is only detected while in flight (it is hidden before the toss and after it lands)."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

import shuttle_physics as sp
from synth import CAMERA_A, CAMERA_B, OUT
from synth_imu import racquet_head_position

TOSS_S = 0.3
MIN_APEX = 6.5
# ponytail: spec says 7 m, but with the 108 deg cameras (H_AIR = 6 m framing margin) a 7 m apex still projects inside both images;
# 9 m leaves both frames for ~0.8 s. Real clears peak at 8-10 m.
CLEAR_APEX = 9.0
LAND_S = 1.2
MIN_GAP_AFTER_CLEAR = 0.5
MIN_CONTACT_GAP = 0.35  # real fastest exchanges are ~0.3 s; closer contacts are dropped (the later one)
MIN_LAND_S = 0.5
# Detection simulator
NOISE_ALONG_PX, NOISE_ALONG_PER_SPEED, NOISE_ACROSS_PX = 1.5, 0.04, 1.0
MISS_BASE, MISS_SPEED, MISS_OCCLUDED = 0.08, 0.30, 0.30
SPEED_FULL_PX = 60.0  # px/frame at which the speed-dependent miss probability reaches its full 30 %
OCCLUDE_PX = 40.0
FALSE_POSITIVE = 0.005


def merge_contacts(contact_t, swing_player):
    """[(t, player)] in time order; a second hit by the same player in a row, or one closer than MIN_CONTACT_GAP to the
    previous kept contact, is dropped (the earlier one stays)."""
    out = []
    for t, p in sorted(zip(contact_t, swing_player)):
        if not out or (out[-1][1] != p and t - out[-1][0] >= MIN_CONTACT_GAP):
            out.append((float(t), int(p)))
    return out


def plan_rally(contacts, heads, t_end, rng, vt=sp.VT):
    """contacts [(t, player)] -> (contacts after the clear insertion, list of flights, apexes, ...).
    A flight is dict(t0, p0, v0, t1, kind) with kind 'toss' | 'shot' | 'clear' | 'land'; it ends at the next flight's t0
    (at the floor for 'land'). Terminal velocity vt applies to every flight."""
    head = lambda p, t: heads[p](t)  # noqa: E731

    def shots(cs):
        return [sp.solve_shot(head(a[1], a[0]), a[0], head(b[1], b[0]), b[0], vt) for a, b in zip(cs[:-1], cs[1:])]

    # The landing flight needs room before the clip ends
    while len(contacts) > 2 and contacts[-1][0] > t_end - 0.03 - MIN_LAND_S:
        contacts = contacts[:-1]
    v0s = shots(contacts)
    kinds = ["shot"] * len(v0s)
    apexes = [sp.apex(head(c[1], c[0]), v, c[0], vt)[1][1] for c, v in zip(contacts, v0s)]
    t_arr = {}
    if max(apexes, default=0) <= MIN_APEX:
        # Longest-gap shot first; skip those whose clear would arrive too late for the landing flight
        done = False
        for i in np.argsort([a[0] - b[0] for a, b in zip(contacts[:-1], contacts[1:])]):
            i = int(i)
            (ti, pi), (_, pr) = contacts[i], contacts[i + 1]
            try:
                v0, T = sp.solve_clear(head(pi, ti), ti, lambda t: head(pr, t), CLEAR_APEX, vt)
            except AssertionError:
                continue
            arrive = ti + T
            if arrive + LAND_S + 0.03 > t_end:
                continue
            tail, last = [], pr
            for c in contacts[i + 2:]:
                if c[0] > arrive + MIN_GAP_AFTER_CLEAR and c[1] != last:
                    tail.append(c)
                    last = c[1]
            contacts = contacts[:i + 1] + [(arrive, pr)] + tail
            v0s = v0s[:i] + [v0] + shots(contacts[i + 1:])
            kinds = kinds[:i] + ["clear"] + ["shot"] * (len(contacts) - i - 2)
            t_arr = {i + 1: arrive}
            done = True
            break
        assert done, "no shot can become a clear inside the clip"
    flights = []
    c0 = contacts[0]
    p_hit = head(c0[1], c0[0])
    p_toss = p_hit + np.array([0.15, 0.6, 0.1])
    flights.append(dict(t0=c0[0] - TOSS_S, p0=p_toss, v0=sp.solve_shot(p_toss, c0[0] - TOSS_S, p_hit, c0[0], vt),
                        kind="toss"))
    for c, v0, kind in zip(contacts[:-1], v0s, kinds):
        flights.append(dict(t0=c[0], p0=head(c[1], c[0]), v0=v0, kind=kind))
    tl, pl = contacts[-1]
    # Lands in the receiver's court (player 0 stands at x < 0, player 1 at x > 0)
    land = np.array([(1 if pl == 0 else -1) * rng.uniform(2.0, 4.5), 0.0, rng.uniform(-2.0, 2.0)])
    T = min(LAND_S, t_end - tl - 0.03)
    assert T >= MIN_LAND_S, f"landing flight of {T:.2f} s"
    flights.append(dict(t0=tl, p0=head(pl, tl), v0=sp.solve_shot(head(pl, tl), tl, land, tl + T, vt), kind="land",
                        t1=tl + T))
    return contacts, flights, apexes, t_arr


def rally(joints, t, swing_player, contact_t, seed=0, vt=sp.VT):
    """Ground truth dict (shuttle_gt.npz contents). vt = the generator's terminal velocity (m/s)."""
    rng = np.random.default_rng(seed + 3000)
    heads = {}
    for p in sorted(set(swing_player.tolist())):
        track = racquet_head_position(joints, t, contact_t[swing_player == p], p)
        heads[p] = CubicSpline(t, track)
    contacts, flights, apexes, _ = plan_rally(merge_contacts(contact_t, swing_player), heads, t[-1], rng, vt)
    for a, b in zip(flights[:-1], flights[1:]):
        a["t1"] = b["t0"]
    F = len(t)
    pos, vel = np.full((F, 3), np.nan), np.full((F, 3), np.nan)
    in_flight, flight_id = np.zeros(F, bool), np.full(F, -1)
    for k, fl in enumerate(flights):
        m = (t >= fl["t0"]) & (t < fl["t1"])
        pos[m], vel[m] = sp.simulate(fl["p0"], fl["v0"], fl["t0"], t[m], vt)
        in_flight[m], flight_id[m] = True, k
    tl = flights[-1]["t1"]
    p_land = sp.simulate(flights[-1]["p0"], flights[-1]["v0"], flights[-1]["t0"], [tl], vt)[0][0]
    # ponytail: only the toss and the clear arc are checked for the floor; a shot dipping below y = 0 would be a generator bug
    assert np.nanmin(pos[:, 1]) > -1e-3, "shuttle below the floor"
    ap = max(sp.apex(f["p0"], f["v0"], f["t0"], vt)[1][1] for f in flights[1:-1])
    return dict(pos=pos, vel=vel, in_flight=in_flight, flight_id=flight_id, t=t,
                contacts=np.array([[c[0], c[1], *heads[c[1]](c[0])] for c in contacts]),
                landing=np.array([tl, *p_land]), max_apex=ap, vt=vt)


def _project_rows(cam, pts):
    return cam.project(pts.reshape(-1, 3)).reshape(pts.shape[:-1] + (2,))


def detect(rng, cam, gt, joints, cid):
    """Project the truth to one camera with blur-dependent noise, misses (speed, occlusion by bodies), out-of-image and
    false positives (random place near a player's head/feet). Returns the shuttle2d_<id>.npz fields."""
    F = len(gt["t"])
    inf = gt["in_flight"]
    uv_true = np.full((F, 2), np.nan)
    uv_true[inf] = cam.project(gt["pos"][inf])
    # Image motion per frame (px/frame), central difference inside the flight
    vel_px = np.zeros((F, 2))
    nxt, prv = np.minimum(np.arange(F) + 1, F - 1), np.maximum(np.arange(F) - 1, 0)
    ok = inf & inf[nxt] & inf[prv]
    vel_px[ok] = (uv_true[nxt[ok]] - uv_true[prv[ok]]) / 2
    speed = np.linalg.norm(vel_px, axis=1)
    along = vel_px / np.maximum(speed, 1e-9)[:, None]
    across = np.stack([-along[:, 1], along[:, 0]], 1)
    sig_a = NOISE_ALONG_PX + NOISE_ALONG_PER_SPEED * speed
    uv = uv_true + rng.normal(size=(F, 1)) * sig_a[:, None] * along + rng.normal(size=(F, 1)) * NOISE_ACROSS_PX * across

    jt = _project_rows(cam, joints.reshape(F, -1, 3))  # [F, P*17, 2]
    occluded = (np.linalg.norm(jt - uv_true[:, None], axis=-1) < OCCLUDE_PX).any(1)
    p_miss = np.clip(MISS_BASE + MISS_SPEED * np.minimum(1, speed / SPEED_FULL_PX) + MISS_OCCLUDED * occluded, 0, 1)
    w, h = cam.size
    inside = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
    seen = inf & inside & (rng.random(F) >= p_miss)
    score = np.where(seen, rng.uniform(0.7, 1.0, F), 0.0)
    out_uv = np.where(seen[:, None], uv, 0.0)

    # False positives replace whatever the model would have output that frame
    P = joints.shape[1]
    for f in np.flatnonzero(rng.random(F) < FALSE_POSITIVE):
        p, j = int(rng.integers(P)), int(rng.choice([0, 15, 16]))  # nose or an ankle
        pt = _project_rows(cam, joints[f, p, j])[0] + rng.normal(0, 25, 2)
        if 0 <= pt[0] < w and 0 <= pt[1] < h:
            out_uv[f], score[f] = pt, rng.uniform(0.5, 0.9)
    return dict(uv=out_uv.astype(np.float32), score=score.astype(np.float32), t=gt["t"].astype(np.float64),
                camera_id=cid, model="synthetic")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", type=Path, default=OUT / "pose_gt.npz")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--vt", type=float, default=sp.VT, help="generator terminal velocity, m/s (the fitter is not told)")
    args = ap.parse_args()
    z = np.load(args.gt)
    gt = rally(z["joints"], z["t"], z["swing_player"], z["contact_t"], args.seed, args.vt)
    args.out.mkdir(parents=True, exist_ok=True)
    np.savez(args.out / "shuttle_gt.npz", **gt)
    rng = np.random.default_rng(args.seed + 4000)
    for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B)):
        d = detect(rng, cam, gt, z["joints"], cid)
        np.savez(args.out / f"shuttle2d_{cid}.npz", **d)
        print(f"cam {cid}: {(d['score'] > 0).sum()} detections over {int(gt['in_flight'].sum())} in-flight frames")
    c = gt["contacts"]
    print(f"wrote {len(c)} contacts (players {c[:, 1].astype(int).tolist()}), landing at t={gt['landing'][0]:.3f}, "
          f"max apex {gt['max_apex']:.2f} m to {args.out}")
    print(json.dumps({"contacts_t": np.round(c[:, 0], 3).tolist()}))


if __name__ == "__main__":
    main()
