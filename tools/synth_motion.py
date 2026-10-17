"""Procedural 2-player motion (ground truth) projected to both synthetic cameras as noisy COCO-17 detections.
Run: python synth_motion.py [--calib ../assets/calib/calib.json] [--out ../out/synth] [--seed 0]
Writes pose_gt.npz and pose2d_a.npz / pose2d_b.npz (format: docs/data-format.md)."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline

from skeleton import CHAINS
from synth import CAMERA_A, CAMERA_B, OUT

# Fixed bone lengths for a ~1.75 m adult (m)
THIGH, SHIN, UPPER_ARM, FOREARM = 0.45, 0.43, 0.30, 0.27
HIP_Y, ANKLE_Y, TORSO, HIP_HALF, SHOULDER_HALF = 0.90, 0.08, 0.50, 0.10, 0.19
# Swing phases (s): backswing, forward swing, follow-through, return
SWING = (0.4, 0.12, 0.3, 0.3)
SWING_LEN = sum(SWING)
JUMP_S = 0.5  # airborne duration of a jump (s)
NOISE_PX = 2.0


def mj(u):
    """Minimum-jerk ease 0 -> 1."""
    u = np.clip(u, 0, 1)
    return 10 * u**3 - 15 * u**4 + 6 * u**5


def ik(a, b, l1, l2, hint):
    """Middle joint of a two-bone chain a -> b, bending toward hint. Reach is clamped to the chain length."""
    v = b - a
    d = min(np.linalg.norm(v), l1 + l2 - 1e-4)
    u = v / max(np.linalg.norm(v), 1e-9)
    h = hint - (hint @ u) * u
    h /= np.linalg.norm(h)
    x = (l1**2 - l2**2 + d**2) / (2 * d)
    return a + x * u + np.sqrt(max(l1**2 - x**2, 0.0)) * h


def root_path(rng, sign, t):
    """Smooth random walk inside the player's half: spline through waypoints ~0.8 s apart (<= 1.6 m apart)."""
    n = int(t[-1] / 0.8) + 2
    pts = [np.array([sign * rng.uniform(1.0, 5.5), rng.uniform(-2.6, 2.6)])]
    for _ in range(n):
        step = rng.normal(size=2)
        step *= rng.uniform(0.3, 1.6) / np.linalg.norm(step)
        p = pts[-1] + step
        p[0] = sign * np.clip(sign * p[0], 0.8, 5.8)
        p[1] = np.clip(p[1], -2.6, 2.6)
        pts.append(p)
    return CubicSpline(np.arange(n + 1) * 0.8, np.array(pts), bc_type="clamped")(t)


def swings_for(rng, t0, dur):
    """[(start time, forward-swing arc in degrees)] every ~2 s."""
    out, T = [], t0 + rng.uniform(0, 0.4)
    while T + SWING_LEN < dur - 0.1:
        out.append((T, rng.uniform(92, 112)))
        T += 2.0 + rng.uniform(-0.2, 0.2)
    return out


def arm_polar(t, swings):
    """Right-arm wrist as (theta deg from forward toward up, radius) at time t. A swing is (start, forward arc in degrees),
    optionally followed by its own phase timing (backswing, forward, follow-through, return seconds) and a pitch offset in
    degrees that tilts the whole swing plane up or down (faded in and out around the forward swing); both used by play_match.py."""
    r_rest, r_full = 0.45, UPPER_ARM + FOREARM - 2e-3
    for T, delta, *ph in swings:
        b, f, ft, rt = ph[0] if ph else SWING
        if not T <= t < T + (b + f + ft + rt if ph else SWING_LEN):
            continue
        th, r = _swing_polar(t, T, delta, b, f, ft, rt, r_rest, r_full)
        if len(ph) > 1:
            w = mj((t - T) / b) if t < T + b + f + ft else 1 - mj((t - T - b - f - ft) / rt)
            th += ph[1] * w
        return th, r
    return -90.0, r_rest


def _swing_polar(t, T, delta, b, f, ft, rt, r_rest, r_full):
    """(theta, radius) at absolute time t of the swing that started at T."""
    if t < T + b:
        s = mj((t - T) / b)
        return -90 - 120 * s, r_rest + (r_full - r_rest) * s
    if t < T + b + f:
        return 150 - delta * mj((t - T - b) / f), r_full
    t2 = T + b + f
    if t < t2 + ft:
        return (150 - delta) + (-60 - (150 - delta)) * mj((t - t2) / ft), r_full
    s = mj((t - t2 - ft) / rt)
    return -60 - 30 * s, r_full + (r_rest - r_full) * s


def player_pose(xz, sign, t, fps, swings, lunges, jumps=(), facing=None):
    """FK/IK joints [F, 17, 3] for one player along the root path xz [F, 2] (court x, z), facing the net (+x for sign -1, -x for
    sign +1) unless `facing` [F, 3] (unit horizontal directions) is given. A lunge (start times) drops the hips and plants the
    right foot forward; a jump (start times) lifts the hips and both feet off the floor."""
    F = len(t)
    vel = np.gradient(xz, 1 / fps, axis=0)
    speed = np.linalg.norm(vel, axis=1)
    phase = 2 * np.pi * np.cumsum(speed / fps) / 1.5
    up = np.array([0, 1.0, 0])
    fwd = np.array([-sign, 0, 0.0])
    left = np.cross(up, fwd)
    out = np.zeros((F, 17, 3))
    for i in range(F):
        if facing is not None:
            fwd = facing[i]
            left = np.cross(up, fwd)
        lunge = sum(float(np.sin(np.pi * np.clip((t[i] - T) / 0.6, 0, 1)) ** 2) for T in lunges)
        air = 0.3 * sum(float(np.sin(np.pi * np.clip((t[i] - T) / JUMP_S, 0, 1)) ** 2) for T in jumps) if len(jumps) else 0.0
        sp = speed[i]
        vdir = vel[i] / sp if sp > 1e-3 else np.zeros(2)
        vd = np.array([vdir[0], 0, vdir[1]])
        stride = min(0.25, 0.1 * sp)
        root_g = np.array([xz[i, 0], 0, xz[i, 1]]) + 0.25 * lunge * fwd
        root = root_g + up * (HIP_Y - 0.3 * lunge + air)
        c, s = np.cos(phase[i]), np.sin(phase[i])
        ank = {}
        for side, sg in (("l", 1), ("r", -1)):
            lat = left if side == "l" else -left
            a = root_g + lat * HIP_HALF + vd * stride * s * sg + up * (ANKLE_Y + 0.1 * max(0.0, c * sg) * min(1, sp) + air)
            if side == "r":  # lunge: right foot steps forward and stays planted
                a = a + (0.55 * fwd + 0.0) * lunge
                a[1] = ANKLE_Y + (a[1] - ANKLE_Y) * (1 - lunge)
            ank[side] = a
        J = out[i]
        J[11], J[12] = root + left * HIP_HALF, root - left * HIP_HALF
        J[15], J[16] = ank["l"], ank["r"]
        J[13] = ik(J[11], J[15], THIGH, SHIN, fwd)
        J[14] = ik(J[12], J[16], THIGH, SHIN, fwd)
        sc = root + up * TORSO
        J[5], J[6] = sc + left * SHOULDER_HALF, sc - left * SHOULDER_HALF
        neck = sc + up * 0.08
        J[0] = neck + up * 0.12 + fwd * 0.10
        J[1], J[2] = J[0] + up * 0.04 - fwd * 0.03 + left * 0.03, J[0] + up * 0.04 - fwd * 0.03 - left * 0.03
        J[3], J[4] = neck + up * 0.1 - fwd * 0.02 + left * 0.08, neck + up * 0.1 - fwd * 0.02 - left * 0.08
        # right arm: swing; left arm: relaxed, counter-swinging with the gait
        th, r = arm_polar(t[i], swings)
        th, ph = np.radians(th), 0.3
        right = -left
        J[10] = J[6] + r * (np.cos(th) * fwd + np.sin(th) * (np.cos(ph) * up + np.sin(ph) * right))
        thl = np.radians(-90 + 20 * min(1, sp) * s)
        J[9] = J[5] + 0.45 * (np.cos(thl) * fwd + np.sin(thl) * up)
        hint = -up - 0.3 * fwd
        J[7] = ik(J[5], J[9], UPPER_ARM, FOREARM, hint + 0.5 * left)
        J[8] = ik(J[6], J[10], UPPER_ARM, FOREARM, hint + 0.5 * right)
    return out


def generate(seed=0, fps=60.0, dur=10.0):
    rng = np.random.default_rng(seed)
    t = np.arange(round(dur * fps)) / fps
    joints, swing_player, swing_t0 = [], [], []
    for p, sign in enumerate((-1, 1)):
        sw = swings_for(rng, 0.6 + 0.7 * p, dur)
        lunges = np.sort(rng.uniform(1.0, dur - 1.0, 2))
        joints.append(player_pose(root_path(rng, sign, t), sign, t, fps, sw, lunges))
        swing_player += [p] * len(sw)
        swing_t0 += [T + SWING[0] for T, _ in sw]  # start of the forward swing
    return np.stack(joints, 1), t, np.array(swing_player), np.array(swing_t0)


def detect(rng, cam, joints, fps, cid):
    """Project truth to one camera: noise, occlusion bursts, outliers, out-of-image."""
    F, P = joints.shape[:2]
    uv = cam.project(joints.reshape(-1, 3)).reshape(F, P, 17, 2)
    kp = np.zeros((F, P, 17, 3), np.float32)
    kp[..., :2] = uv + rng.normal(0, NOISE_PX, uv.shape)
    kp[..., 2] = np.clip(rng.normal(0.9, 0.05, (F, P, 17)), 0, 1)
    chains = list(CHAINS.values())
    for p in range(P):  # bursts of 3-20 frames hiding one limb chain, about one every 2.5 s
        f = rng.exponential(2.5 * fps)
        while f < F:
            n = int(rng.integers(3, 21))
            sl = slice(int(f), int(f) + n)
            idx = list(chains[rng.integers(len(chains))])
            kp[sl, p, idx, :2] += rng.normal(0, 60, (len(kp[sl]), 3, 2))  # garbage position
            kp[sl, p, idx, 2] = rng.uniform(0.05, 0.2, (len(kp[sl]), 3))
            f += n + rng.exponential(2.5 * fps)
    out = rng.random((F, P, 17)) < 0.01  # outliers keep their score
    ang, mag = rng.uniform(0, 2 * np.pi, out.sum()), rng.uniform(30, 80, out.sum())
    kp[out, 0] += mag * np.cos(ang)
    kp[out, 1] += mag * np.sin(ang)
    w, h = cam.size
    off = (kp[..., 0] < 0) | (kp[..., 0] >= w) | (kp[..., 1] < 0) | (kp[..., 1] >= h)
    kp[off, 2] = 0
    return dict(kp=kp, t=np.arange(F) / fps, frame=np.arange(F), camera_id=cid, model="synthetic",
                keypoint_format="coco17")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=Path, default=Path(__file__).resolve().parent.parent / "assets/calib/calib.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    fps = json.loads(args.calib.read_text())["cameras"][0]["fps"]
    joints, t, sp, st = generate(args.seed, fps)
    args.out.mkdir(parents=True, exist_ok=True)
    # contact_t (video clock): the wrist-speed peak of each forward swing = middle of the minimum-jerk arc
    np.savez(args.out / "pose_gt.npz", joints=joints, t=t, swing_player=sp, swing_t0=st,
             contact_t=st + SWING[1] / 2)
    rng = np.random.default_rng(args.seed + 1000)
    for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B)):
        np.savez(args.out / f"pose2d_{cid}.npz", **detect(rng, cam, joints, fps, cid))
    print(f"wrote {len(t)} frames, {len(sp)} swings to {args.out}")


if __name__ == "__main__":
    main()
