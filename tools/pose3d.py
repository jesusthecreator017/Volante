"""Triangulate per-camera 2D keypoints into smoothed 3D joints -> replay.bin.
Run: python pose3d.py --calib ../assets/calib/calib.json --pose2d ../out/synth/pose2d_a.npz ../out/synth/pose2d_b.npz -o ../assets/replay/replay.bin
Cameras that are not frame-synchronized (real video): add --camsync camsync.json (tools/camsync.py); camera B's tracks are then
resampled to camera A's timestamps and the replay timeline is camera A's clock."""
import argparse
import json
import warnings
from pathlib import Path

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import butter, filtfilt

import replay
from camsync import resample_pose
from court import HALF_LENGTH, HALF_WIDTH
from skeleton import FAST, NAMES
from triangulate import reprojection_error, triangulate

MIN_SCORE = 0.3
LOWER_BODY = 11  # COCO joints >= 11 (hips, knees, ankles) follow --min-score; the rest never go below MIN_SCORE
MAX_REPROJ_PX = 12.0
MAX_GAP = 12
MIN_SEGMENT = 15
GUIDE_MIN_SEGMENT = 6  # gap-fill guide only: also smooth short runs next to a gap
CUTOFF_HZ, CUTOFF_FAST_HZ = 6.0, 16.0
HAMPEL_WINDOW, HAMPEL_M = 7, 0.15


def load_pose2d(path):
    z = np.load(path)
    return {k: z[k] for k in ("kp", "t", "frame")} | {k: str(z[k]) for k in ("camera_id", "model", "keypoint_format")}


def triangulate_all(calib, kp_a, kp_b, min_score=MIN_SCORE):
    """kp_*: [F, P, 17, 3]. Returns xyz [F, P, 17, 3] with NaN where rejected."""
    shape = kp_a.shape[:-1]
    a, b = kp_a.reshape(-1, 3), kp_b.reshape(-1, 3)
    # Hips and legs are reliable at low scores (14-25 cm on the blocky renders), arms and face are not (45 cm): they keep
    # the default threshold, so lowering min_score recovers the body without adding wild arms
    thr = np.tile(np.where(np.arange(17) >= LOWER_BODY, min_score, max(min_score, MIN_SCORE)), len(a) // 17)
    ok = (a[:, 2] >= thr) & (b[:, 2] >= thr)
    xyz = np.full((len(a), 3), np.nan)
    if ok.any():
        p = triangulate(calib, a[ok, :2], b[ok, :2])
        ea, eb = reprojection_error(calib, p, a[ok, :2], b[ok, :2])
        good = (ea <= MAX_REPROJ_PX) & (eb <= MAX_REPROJ_PX)
        good &= (p[:, 1] >= -0.2) & (p[:, 1] <= 3.0)
        good &= (np.abs(p[:, 0]) <= HALF_LENGTH + 4) & (np.abs(p[:, 2]) <= HALF_WIDTH + 4)
        p[~good] = np.nan
        xyz[ok] = p
    return xyz.reshape(*shape, 3)


def hampel(x):
    """x [F, 3] with NaN gaps: drop samples > HAMPEL_M from the per-axis median of their +-3 neighbours.
    Two views cannot catch an outlier along the epipolar line by reprojection error; time can.
    A monotone motion (even a 25 cm/frame swing) keeps its median at the center sample."""
    F, h = len(x), HAMPEL_WINDOW // 2
    pad = np.concatenate([np.full((h, 3), np.nan), x, np.full((h, 3), np.nan)])
    win = np.stack([pad[i:i + F] for i in range(HAMPEL_WINDOW)])  # [W, F, 3]
    enough = np.isfinite(win[..., 0]).sum(0) >= 4
    with warnings.catch_warnings():  # all-NaN windows are fine, `enough` masks them
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(win, axis=0)
    bad = enough & (np.linalg.norm(x - med, axis=1) > HAMPEL_M)
    out = x.copy()
    out[bad] = np.nan
    return out


def fill_gaps(x, guide, max_gap=MAX_GAP):
    """Cubic-spline gaps <= max_gap frames that have valid samples on both sides. The spline runs through `guide`
    (a smoothed copy of x): interpolating raw samples turns their noise into wild end slopes across the gap.
    Returns (x, filled mask)."""
    valid = np.isfinite(x[:, 0])
    filled = np.zeros(len(x), bool)
    if valid.sum() < 4:
        return x, filled
    idx = np.flatnonzero(valid)
    spl = CubicSpline(idx, guide[idx])
    out = x.copy()
    for lo, hi in zip(idx[:-1], idx[1:]):
        if 1 < hi - lo <= max_gap + 1:
            gap = np.arange(lo + 1, hi)
            out[gap], filled[gap] = spl(gap), True
    return out, filled


def smooth(x, fps, cutoff, min_len=MIN_SEGMENT):
    """Zero-phase order-2 Butterworth over each contiguous finite run of >= MIN_SEGMENT frames."""
    b, a = butter(2, cutoff / (fps / 2))
    out = x.copy()
    valid = np.isfinite(x[:, 0])
    edges = np.flatnonzero(np.diff(np.concatenate([[0], valid.astype(int), [0]])))
    for s, e in zip(edges[::2], edges[1::2]):
        if e - s >= min_len:
            out[s:e] = filtfilt(b, a, x[s:e], axis=0, padlen=min(9, e - s - 1))
    return out


def pose3d(calib, p2d_a, p2d_b, camsync=None, min_score=MIN_SCORE, max_gap=MAX_GAP):
    """Returns joints [F, P, 17, 4] float32 (x, y, z, conf); missing joints are 0,0,0,conf 0.
    camsync (dict from camsync.py): camera B is resampled to camera A's timestamps first; without it the cameras must be
    frame-synchronized."""
    if camsync is None:
        assert p2d_a["kp"].shape == p2d_b["kp"].shape
        assert np.abs(p2d_a["t"] - p2d_b["t"]).max() < 1e-3, "cameras not frame-synchronized (pass --camsync)"
    else:
        p2d_b = resample_pose(p2d_b, camsync, p2d_a["t"])
    fps = calib["cameras"][0]["fps"]
    xyz = triangulate_all(calib, p2d_a["kp"], p2d_b["kp"], min_score)
    F, P = xyz.shape[:2]
    out = np.zeros((F, P, 17, 4), np.float32)
    for p in range(P):
        for j in range(17):
            cutoff = CUTOFF_FAST_HZ if j in FAST else CUTOFF_HZ
            x = hampel(xyz[:, p, j])
            x, filled = fill_gaps(x, smooth(x, fps, cutoff, GUIDE_MIN_SEGMENT), max_gap)
            conf = np.where(np.isfinite(x[:, 0]), np.where(filled, 0.5, 1.0), 0.0)
            x = smooth(x, fps, cutoff)
            out[:, p, j, :3] = np.nan_to_num(x)
            out[:, p, j, 3] = conf
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=Path, required=True)
    ap.add_argument("--pose2d", type=Path, nargs=2, required=True)
    ap.add_argument("--camsync", type=Path, help="camsync.json: resample camera B to camera A's clock")
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--min-score", type=float, default=MIN_SCORE, help="drop 2D keypoints scoring below this (real people score 0.6-0.9; blocky renders 0.1-0.4)")
    ap.add_argument("--max-gap", type=int, default=MAX_GAP, help="spline over 3D gaps up to this many frames")
    args = ap.parse_args()
    calib = json.loads(args.calib.read_text())
    a, b = (load_pose2d(p) for p in args.pose2d)
    assert [a["camera_id"], b["camera_id"]] == [c["id"] for c in calib["cameras"][:2]], "camera order"
    joints = pose3d(calib, a, b, json.loads(args.camsync.read_text()) if args.camsync else None,
                    args.min_score, args.max_gap)
    P = joints.shape[1]
    replay.write(args.out, calib["cameras"][0]["fps"], [(
        {"name": "joints3d", "layout": "x,y,z,conf", "keypoint_format": "coco17",
         "players": [f"p{i}" for i in range(P)]}, joints)])
    hips = (joints[:, :, 11:13, 3] > 0).all(-1)  # both hips tracked
    print(f"wrote {args.out}: {joints.shape[0]} frames, coverage {(joints[..., 3] > 0).mean():.3f}; per player joints "
          f"{[round(float(x), 3) for x in (joints[..., 3] > 0).mean((0, 2))]}, both hips {[round(float(x), 3) for x in hips.mean(0)]}")


if __name__ == "__main__":
    main()
