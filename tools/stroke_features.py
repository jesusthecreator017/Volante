"""Stroke features (Phase 10): one function for both domains (BFMD broadcast annotations and our re-projected 3D replays).

A `Rally` holds 2D inputs at 30 fps in broadcast-camera pixels: keypoints and bbox per side ("top" = far player, "bottom" = near),
shuttle px (NaN missing), hits [(frame, side)] and the image -> court homography. `hit_features` returns one row per hit.
Everything is mirrored so the hitter is on the -X half facing +X (top player: x -> -x, z -> -z on the court, left/right keypoints
swapped and image x flipped). Run anything that reads BFMD with `python -I`.
"""
from dataclasses import dataclass, field

import numpy as np

from bfmd import to_court

FPS = 30
MIN_SCORE = 0.3
POSE_OFFSETS = (-6, -3, 0, 3)
SHUTTLE_FRAMES = tuple(range(1, 9))
# COCO left/right swap (nose stays)
FLIP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
# BFMD boxes are detector boxes, taller than the keypoint extent (nose..ankles); classify_strokes pads its boxes by this factor
# (median over BFMD, measured by `train_strokes.py`).
BBOX_PAD = 1.30


@dataclass
class Rally:
    kp: dict  # side -> [F, 17, 3] px, px, score (NaN / 0 where missing)
    bbox: dict  # side -> [F, 4] x0, y0, x1, y1 px (NaN missing)
    shuttle: np.ndarray  # [F, 2] px
    hits: list  # [(frame, side)] in time order
    H: np.ndarray  # image -> court (x, z)
    names: list = field(default_factory=list)


def _names():
    n = ["hit_x", "hit_z", "hit_net"]
    for o in POSE_OFFSETS:
        n += [f"pose{o:+d}_{j}_{a}" for j in range(17) for a in "xy"] + [f"wrist_up{o:+d}"]
    n += [f"shuttle+{k}_{a}" for k in SHUTTLE_FRAMES for a in "xy"] + ["shuttle_vx", "shuttle_vy"]
    n += ["ctx_dt_next", "ctx_next_x", "ctx_next_z", "ctx_dt_prev", "ctx_idx", "ctx_prev_x", "ctx_prev_z"]
    return n


FEATURE_NAMES = _names()
CONTEXT = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("ctx_")]
NF = len(FEATURE_NAMES)


def _sign(side):
    return -1.0 if side == "top" else 1.0


def _at(a, f):
    return a[f] if 0 <= f < len(a) else None


def _foot_pos(r, side, f):
    """Hitter feet on the court, mirrored to the -X half; None if unknown."""
    k, b = _at(r.kp[side], f), _at(r.bbox[side], f)
    if k is not None and (k[15:17, 2] >= MIN_SCORE).all() and np.isfinite(k[15:17, :2]).all():
        px = k[15:17, :2].mean(0)
    elif b is not None and np.isfinite(b).all():
        px = np.array([(b[0] + b[2]) / 2, b[3]])
    else:
        return None
    x, z = to_court(r.H, px)
    s = _sign(side)
    return s * x, s * z


def _pose(r, side, f):
    """(17x2 relative to hips / bbox height, mirrored; wrist-above-head), or NaNs."""
    nan = np.full((17, 2), np.nan)
    k, b = _at(r.kp[side], f), _at(r.bbox[side], f)
    if k is None or b is None or not np.isfinite(b).all():
        return nan, np.nan
    hb = b[3] - b[1]
    ok = (k[:, 2] >= MIN_SCORE) & np.isfinite(k[:, 0])
    if hb <= 1 or not ok[11:13].all():
        return nan, np.nan
    hip = k[11:13, :2].mean(0)
    rel = np.where(ok[:, None], (k[:, :2] - hip) / hb, np.nan)
    wr = k[9:11][ok[9:11]]
    up = (k[0, 1] - wr[:, 1].min()) / hb if ok[0] and len(wr) else np.nan
    if side == "top":
        rel = rel[FLIP] * [-1, 1]
    return rel, up


def _shuttle(r, side, f):
    k, b = _at(r.kp[side], f), _at(r.bbox[side], f)
    out = np.full(len(SHUTTLE_FRAMES) * 2 + 2, np.nan)
    if b is None or not np.isfinite(b).all():
        return out
    hb = b[3] - b[1]
    head = k[0, :2] if k[0, 2] >= MIN_SCORE and np.isfinite(k[0, 0]) else np.array([(b[0] + b[2]) / 2, b[1]])
    s = np.array([r.shuttle[f + j] if f + j < len(r.shuttle) else (np.nan, np.nan) for j in SHUTTLE_FRAMES])
    m = np.array([_sign(side), 1.0])  # image x flips for the top player, like the pose
    out[:-2] = (((s - head) / hb) * m).ravel()
    out[-2:] = (s[3] - s[0]) / 3 / hb * m
    return out


def hit_features(r):
    """[n_hits, NF] float32."""
    X = np.full((len(r.hits), NF), np.nan, np.float32)
    pos = [_foot_pos(r, s, f) for f, s in r.hits]
    for i, (f, side) in enumerate(r.hits):
        row = []
        p = pos[i]
        row += [p[0], p[1], -p[0]] if p else [np.nan] * 3
        for o in POSE_OFFSETS:
            rel, up = _pose(r, side, f + o)
            row += list(rel.ravel()) + [up]
        row += list(_shuttle(r, side, f))
        nxt = pos[i + 1] if i + 1 < len(r.hits) else None
        prv = pos[i - 1] if i > 0 else None
        row += [r.hits[i + 1][0] - f if i + 1 < len(r.hits) else np.nan,
                nxt[0] if nxt else np.nan, nxt[1] if nxt else np.nan,
                f - r.hits[i - 1][0] if i > 0 else np.nan, i,
                prv[0] if prv else np.nan, prv[1] if prv else np.nan]
        X[i] = row
    return X
