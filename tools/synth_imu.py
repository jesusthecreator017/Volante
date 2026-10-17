"""Synthetic racquet IMU (MPU-9250 on the butt end) driven by the Phase 2 ground-truth motion, written as the exact
imu.bin the firmware will produce (packet format: docs/data-format.md).
Run: python synth_imu.py [--gt ../out/synth/pose_gt.npz] [--shuttle ../out/synth/shuttle_gt.npz] [--out ../out/synth] [--seed 0] [--player 0]
Writes imu.bin, imu_gt.npz and sync.json (the video-side clap annotation)."""
import argparse
import json
import struct
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline, PchipInterpolator
from scipy.ndimage import gaussian_filter1d
from scipy.spatial.transform import Rotation, RotationSpline

from synth import OUT
from synth_motion import SWING

RATE_HZ = 500
ACC_LSB, GYRO_LSB = 2048.0, 16.4  # +-16 g, +-2000 dps
G = 9.80665
BUTT_OFFSET = 0.06  # m from the wrist along the forearm (the model; src/imu/pose.rs mirrors it)
HEAD_CENTER = 0.53  # m from the butt along the handle to the string-bed center (src/scene/racquet.rs HEAD_CENTER)
# Device clock: t_dev = (t_video + OFFSET) * (1 + DRIFT), i.e. the device booted OFFSET s before video t = 0.
# (The spec writes t_video - offset; with a device that booted earlier the sign has to be +.)
OFFSET, DRIFT = 3.217, 40e-6
T_CLAP = 0.25  # video time of the first clap; the racquet is held still until the video starts (and a bit after)
T_CLAP_END = 9.6  # second clap near the end of the clip (the arm is at rest by then): fixes drift
SEQ_START = 65000  # wraps after 536 packets
JITTER_US = 30.0
MAGIC = b"OSOI"


def unit(v):
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def racquet_rotation(j, t, contact_t, snap):
    """Racquet rotation matrices [F, 3, 3] (columns = racquet x, y (handle, butt -> tip), z (string bed normal)) in the
    court frame, from the right wrist/elbow of joints j [F, 17, 3]: handle = forearm tilted by a wrist-cock angle in its vertical plane
    plus a slow wrist yaw deviation, plus pronation roll about the handle (snap = fraction of the forward swing the roll takes)."""
    b, f, ft, rt = SWING
    # Forearm direction band-limited to what an arm can do (the IK elbow whips it around when the arm passes vertical)
    fore = unit(gaussian_filter1d(unit(j[:, 10] - j[:, 8]), 0.05 / (t[1] - t[0]), axis=0, mode="nearest"))
    sh = unit(j[:, 5] - j[:, 6])
    up = np.array([0, 1.0, 0])
    ck_t, ck_v, rl_t, rl_v = [t[0]], [20.0], [t[0]], [0.0]
    for c, pf in zip(contact_t, snap):
        T = c - f / 2 - b  # swing start
        ck_t += [T, T + b, c, T + b + f + ft / 2, T + b + f + ft + rt]
        ck_v += [20.0, 60.0, 0.0, -20.0, 20.0]
        rl_t += [T + b + (1 - pf) * f, T + b + f, T + b + f + ft, T + b + f + ft + rt]
        rl_v += [0.0, 90.0, 60.0, 0.0]
    ck_t, ck_v, rl_t, rl_v = ck_t + [t[-1]], ck_v + [20.0], rl_t + [t[-1]], rl_v + [0.0]
    cock = np.radians(PchipInterpolator(ck_t, ck_v)(t))[:, None]
    roll = np.radians(PchipInterpolator(rl_t, rl_v)(t))[:, None]
    # Cock tilts the handle up/down in the vertical plane of the forearm (heading preserved); the wrist also wanders in
    # yaw by a slowly varying +-4 deg that the video forearm heading cannot see
    # The cock axis follows the forearm heading, smoothed: where the forearm is near vertical its heading whips around
    h = fore - (fore * up).sum(-1, keepdims=True) * up
    h = gaussian_filter1d(unit(h), 0.15 / (t[1] - t[0]), axis=0, mode="nearest")
    k = unit(np.cross(h, up))
    y = fore * np.cos(cock) + np.cross(k, fore) * np.sin(cock) + k * (k * fore).sum(-1, keepdims=True) * (1 - np.cos(cock))
    dev = np.radians(4.0 * np.sin(2 * np.pi * t / 6.5))[:, None]
    y = y * np.cos(dev) + np.cross(up, y) * np.sin(dev) + up * (up * y).sum(-1, keepdims=True) * (1 - np.cos(dev))
    z0 = unit(sh - (sh * y).sum(-1, keepdims=True) * y)
    z = z0 * np.cos(roll) + np.cross(y, z0) * np.sin(roll)
    x = np.cross(y, z)
    return np.stack([x, y, z], axis=-1), fore


def racquet_head_position(joints, t, contact_t, player, snap=None):
    """String-bed center [F, 3] of `player`'s racquet in the court frame: right wrist + BUTT_OFFSET along the forearm
    + HEAD_CENTER along the handle. contact_t = that player's swing contact times (they shape the wrist cock, which
    tilts the handle; the pronation roll `snap` only turns the face, so the head position does not depend on it)."""
    j = joints[:, player]
    contact_t = np.asarray(contact_t, float)
    R, fore = racquet_rotation(j, t, contact_t, np.ones(len(contact_t)) if snap is None else snap)
    return j[:, 10] + BUTT_OFFSET * fore + HEAD_CENTER * R[:, :, 1]


def _faults(rng, n, forced=()):
    """Arrival order (indices into 0..n-1), the dropped set, and the duplicate/reorder counts."""
    drop = set(forced)
    for _ in range(2):  # two bursts of 5-20 packets (never the first or last packet)
        s = int(rng.integers(10, n - 40))
        drop |= set(range(s, s + int(rng.integers(5, 21))))
    while len(drop) < round(0.01 * n):
        drop.add(int(rng.integers(1, n - 1)))
    keep = np.array([i for i in range(n) if i not in drop])
    order, i = keep.copy(), 0
    while i < len(order):  # reorder inside windows of up to 8 packets
        if rng.random() < 0.005:
            w = int(rng.integers(2, 9))
            order[i:i + w] = rng.permutation(order[i:i + w])
            i += w
        else:
            i += 1
    dups = set(rng.choice(len(order), round(0.002 * n), replace=False).tolist())
    arrival = np.array([k for p, k in enumerate(order) for _ in range(2 if p in dups else 1)])
    seen, top, n_reordered = set(), -1, 0
    for k in arrival:  # same rule as src/imu/packet.rs: late = lower than something already seen, not a repeat
        if k in seen:
            continue
        seen.add(k)
        n_reordered += k < top
        top = max(top, k)
    return arrival, len(drop), len(dups), n_reordered


def generate_imu(joints, t, contact_t, seed=0, player=0, hole=None, impact_t=None):
    """joints [F, P, 17, 3] ground truth, t [F]. contact_t = the player's swing timing (shapes the racquet motion).
    impact_t = when the racquet actually hits the shuttle (synth_shuttle's contacts; may differ from the swings
    after the clear insertion), default contact_t. hole = (video time, packets): drop that many consecutive packets
    centered there (on top of the random faults). Returns dict(packets=bytes, gt=dict, sync=dict)."""
    rng = np.random.default_rng(seed + 2000)
    j = joints[:, player]
    contact_t = np.asarray(contact_t, float)
    impact_t = contact_t if impact_t is None else np.asarray(impact_t, float)
    # Pronation takes the last 60-100 % of the forward swing (a snap): fast smashes push the gyro past 2000 dps
    R_wr, fore = racquet_rotation(j, t, contact_t, rng.uniform(0.6, 1.0, len(contact_t)))
    rot = RotationSpline(t, Rotation.from_matrix(R_wr))
    butt = CubicSpline(t, j[:, 10] + BUTT_OFFSET * fore)

    # Samples on the device clock; the pose before video t = 0 is held (racquet still)
    k = np.arange(1, int((t[-1] + OFFSET) * (1 + DRIFT) * RATE_HZ) + 1)
    t_dev = k / RATE_HZ
    tv = t_dev / (1 + DRIFT) - OFFSET
    tc = np.clip(tv, t[0], t[-1])
    still = (tv < t[0])[:, None]
    Rq = rot(tc)
    # rotation-vector rate of the spline is in the body frame (checked in tools/test_imu.py)
    w_r = np.where(still, 0.0, rot(tc, 1))
    a_w = np.where(still, 0.0, butt(tc, 2))
    f_r = Rq.inv().apply(a_w + G * np.array([0, 1.0, 0]))

    # Sensor frame = racquet frame rotated by the mount (about 3 deg, known here only)
    axis = unit(rng.normal(size=3))
    M = Rotation.from_rotvec(axis * np.radians(3.0))
    w_s = M.inv().apply(w_r)
    acc = M.inv().apply(f_r) / G  # g
    bias = rng.uniform(1, 3, 3) * rng.choice([-1, 1], 3)  # dps
    gyro = np.degrees(w_s) + bias

    # Clap (+14 g half sine, 4 ms) and contact impacts (8-16 g, 3 ms + 200 Hz gyro ringing), along the face normal
    def pulse(center, width, amp, smooth=False):
        # A clap is a smooth mechanical pulse (half sine), which is what lets the sample amplitudes locate it to a
        # fraction of the 2 ms sample period; impacts are flat so every sample of one is above threshold
        m = np.abs(tv - center) < width / 2
        shape = np.cos(np.pi * (tv[m] - center) / width) if smooth else 1.0
        acc[m] += np.outer(amp * shape, [0.0, 0.0, 1.0])
        return m.sum()

    pulse(T_CLAP, 0.004, 14.0, True)
    pulse(T_CLAP_END, 0.004, 14.0, True)
    for c in impact_t:
        pulse(c, 0.003, rng.uniform(8, 16))
        d = tv - c
        ring = np.where(d >= 0, 150.0 * np.exp(-np.maximum(d, 0) / 0.008) * np.sin(2 * np.pi * 200 * d), 0.0)
        gyro += ring[:, None] * np.array([1.0, 0.5, 0.3])
    gyro += rng.normal(0, 0.05 * np.sqrt(RATE_HZ / 2), gyro.shape)  # 0.05 dps/sqrt(Hz)
    acc += rng.normal(0, 0.01, acc.shape)

    g_counts, a_counts = np.round(gyro * GYRO_LSB), np.round(acc * ACC_LSB)
    sat_g = (np.abs(g_counts) >= 32767).any(1)
    sat_a = (np.abs(a_counts) >= 32767).any(1)
    raw = np.clip(np.concatenate([a_counts, g_counts], 1), -32768, 32767).astype("<i2")

    seq = (SEQ_START + np.arange(len(k))) % 65536
    t_us = np.round(t_dev * 1e6 + rng.uniform(-JITTER_US, JITTER_US, len(k))).astype(np.int64) % 2**32
    forced = ()
    if hole is not None:
        c = int(np.argmin(np.abs(tv - hole[0])))
        forced = range(c - hole[1] // 2, c - hole[1] // 2 + hole[1])
    arrival, n_lost, n_dup, n_reord = _faults(rng, len(k), forced)
    body = b"".join(struct.pack("<HI6h", int(seq[i]), int(t_us[i]), *raw[i].tolist()) for i in arrival)
    data = MAGIC + struct.pack("<HHII", 1, RATE_HZ, 0, 0) + body

    q_gt = rot(tc).as_quat()  # x, y, z, w
    gt = dict(t_video=tv, q_world_racquet=q_gt, w=w_r, mount=M.as_matrix(), bias=bias, offset=OFFSET, drift=DRIFT,
              a_true=1 / (1 + DRIFT) - 1, b_true=-OFFSET, t_clap_video=T_CLAP, claps_video=np.array([T_CLAP, T_CLAP_END]),
              t_dev_clap=(T_CLAP + OFFSET) * (1 + DRIFT), impact_t_video=impact_t,
              n_packets=len(k), n_lost=n_lost, n_duplicate=n_dup, n_reordered=n_reord,
              n_arrivals=len(arrival), n_saturated_gyro=int(sat_g.sum()), n_saturated_acc=int(sat_a.sum()),
              sat_t_video=tv[sat_g], seq=seq)
    return dict(packets=data, gt=gt, sync={"claps_video": [T_CLAP, T_CLAP_END]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", type=Path, default=OUT / "pose_gt.npz")
    ap.add_argument("--shuttle", type=Path, default=OUT / "shuttle_gt.npz", help="impact times (if the file exists)")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--player", type=int, default=0)
    ap.add_argument("--hole", type=float, nargs=2, metavar=("T_VIDEO", "PACKETS"), help="extra lost-packet hole")
    args = ap.parse_args()
    z = np.load(args.gt)
    mine = z["swing_player"] == args.player
    # Impacts happen where the shuttle contacts are (synth_shuttle.py); without shuttle ground truth, at the swings
    impacts = None
    if args.shuttle.exists():
        c = np.load(args.shuttle)["contacts"]
        impacts = c[c[:, 1] == args.player, 0]
    r = generate_imu(z["joints"], z["t"], z["contact_t"][mine], args.seed, args.player,
                     None if args.hole is None else (args.hole[0], int(args.hole[1])), impacts)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "imu.bin").write_bytes(r["packets"])
    np.savez(args.out / "imu_gt.npz", **r["gt"])
    (args.out / "sync.json").write_text(json.dumps(r["sync"]))
    g = r["gt"]
    print(f"wrote {g['n_arrivals']} packets ({g['n_packets']} sampled, {g['n_lost']} dropped, {g['n_duplicate']} duplicated, "
          f"{g['n_reordered']} reordered) to {args.out}")
    print(f"saturated samples: gyro {g['n_saturated_gyro']}, accel {g['n_saturated_acc']}; "
          f"truth: claps {T_CLAP} and {T_CLAP_END} s, offset {-OFFSET} s, drift {g['a_true'] * 1e6:.1f} ppm, bias {np.round(g['bias'], 2)} dps")


if __name__ == "__main__":
    main()
