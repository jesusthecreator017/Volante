"""Camera-to-camera time sync from the footage itself (no clap): the shuttle (fast, so timing-sensitive) and the players' wrists.
Model: camera B's timestamp t_b maps to camera A's clock by  t_common = rate_b * t_b + offset_b.
For a candidate (offset, rate) camera B's 2D tracks are resampled to camera A's frame times (linear interpolation in time, only
between two consecutive detected frames) and scored by how well A/B pairs satisfy the epipolar constraint, i.e. the median
Sampson distance in pixels, which is the first-order reprojection error of the best triangulation, without triangulating.
Search: coarse grid +-0.5 s in 1 ms steps at rate 1, then Nelder-Mead on (offset, rate); a second coarse minimum within 10 % of the
best cost (more than 3 frames away) is reported as ambiguity.
Run: python camsync.py --calib ../assets/calib/calib.json --shuttle2d a.npz b.npz [--pose2d a.npz b.npz] -o camsync.json
Output {offset_b, rate_b, cost, n_points, ambiguous, coarse_offset_b}; pose3d.py / shuttle3d.py take it with --camsync."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import minimize

MIN_SCORE = 0.3
MAX_JUMP_PX = 200.0  # shuttle only: two consecutive detections further apart than this are not one object
WRISTS = (9, 10)
COARSE_RANGE, COARSE_STEP = 0.5, 0.001
WRIST_GATE_PX = 6.0
MIN_RATE_OVERLAP_S = 120.0  # shorter clips: rate fixed to 1 (40 ppm over 10 s = 0.4 ms, below detection noise)
AMBIGUITY = 0.10
CAP_PX = 4.0


def resample(t_common, vals, score, t_dst, max_jump=None):
    """Linear interpolation of per-frame tracks to the times `t_dst` (same clock as `t_common`, increasing).
    vals [F, ..., C], score [F, ...]. A target is valid only between two consecutive source frames that both have
    score >= MIN_SCORE (no extrapolation, no bridging gaps). Returns (vals [Fd, ..., C], score [Fd, ...]); score is the
    smaller of the two neighbours, 0 where invalid."""
    # ponytail: linear interpolation cuts the corner of a curved track (~-0.26 ms offset bias on GT shuttle tracks); upgrade:
    # cubic/Hermite resampling
    F = len(t_common)
    k = np.clip(np.searchsorted(t_common, t_dst, side="right") - 1, 0, max(F - 2, 0))
    t0, t1 = t_common[k], t_common[np.minimum(k + 1, F - 1)]
    w = np.where(t1 > t0, (t_dst - t0) / np.where(t1 > t0, t1 - t0, 1), 0.0)
    inside = (t_dst >= t0) & (t_dst <= t1)
    k1 = np.minimum(k + 1, F - 1)
    s = np.minimum(score[k], score[k1])
    ok = inside.reshape((-1,) + (1,) * (score.ndim - 1)) & (score[k] >= MIN_SCORE) & (score[k1] >= MIN_SCORE)
    wv = w.reshape((-1,) + (1,) * (vals.ndim - 1))
    out = (1 - wv) * vals[k] + wv * vals[k1]
    if max_jump is not None:
        ok &= np.linalg.norm(vals[k1] - vals[k], axis=-1) <= max_jump
    return np.where(ok[..., None], out, 0.0), np.where(ok, s, 0.0)


def to_common(t_b, camsync):
    return camsync["rate_b"] * np.asarray(t_b, np.float64) + camsync["offset_b"]


def resample_shuttle(det_b, camsync, t_target):
    """shuttle2d dict (uv [F,2], score [F], t [F]) of camera B -> same dict on camera A's timestamps."""
    uv, score = resample(to_common(det_b["t"], camsync), det_b["uv"], det_b["score"], t_target, MAX_JUMP_PX)
    return {**det_b, "uv": uv, "score": score.astype(np.float32), "t": np.asarray(t_target)}


def resample_pose(p2d_b, camsync, t_target):
    """pose2d dict (kp [F,P,17,3], t [F]) of camera B -> same dict on camera A's timestamps (per joint)."""
    kp = p2d_b["kp"]
    uv, score = resample(to_common(p2d_b["t"], camsync), kp[..., :2], kp[..., 2], t_target)
    return {**p2d_b, "kp": np.concatenate([uv, score[..., None]], -1).astype(np.float32), "t": np.asarray(t_target),
            "frame": np.arange(len(t_target))}


class Epipolar:
    """Essential matrix between the two calibrated cameras and pixel -> normalized coordinate conversion."""

    def __init__(self, calib):
        a, b = calib["cameras"][:2]
        self.cams = (a, b)
        Ra, Rb = np.array(a["R"]), np.array(b["R"])
        R = Rb @ Ra.T
        t = np.array(b["t"]) - R @ np.array(a["t"])
        tx = np.array([[0, -t[2], t[1]], [t[2], 0, -t[0]], [-t[1], t[0], 0]])
        self.E = tx @ R
        self.f = float(np.mean([a["K"][0][0], b["K"][0][0]]))

    def normalize(self, cam, uv):
        n = cv2.undistortPoints(np.asarray(uv, np.float64).reshape(-1, 1, 2), np.array(cam["K"]),
                                np.array(cam["dist"])).reshape(-1, 2)
        return n.reshape(np.shape(uv))

    def sampson_px(self, na, nb):
        """na, nb [N, 2] normalized coordinates of the same point seen by A and B -> distance in pixels."""
        xa = np.hstack([na, np.ones((len(na), 1))])
        xb = np.hstack([nb, np.ones((len(nb), 1))])
        Exa, Etxb = xa @ self.E.T, xb @ self.E
        num = np.sum(xb * Exa, axis=1) ** 2
        den = Exa[:, 0] ** 2 + Exa[:, 1] ** 2 + Etxb[:, 0] ** 2 + Etxb[:, 1] ** 2
        return np.sqrt(num / np.maximum(den, 1e-18)) * self.f


class Tracks:
    """Flattened A/B observations in normalized coordinates: shuttle [F] and wrists [F, K]; B is kept on its own clock."""

    def __init__(self, calib, sh_a, sh_b, p_a=None, p_b=None):
        self.ep = Epipolar(calib)
        self.fps = calib["cameras"][0]["fps"]
        ca, cb = self.ep.cams
        self.t_a, self.t_b = np.asarray(sh_a["t"], np.float64), np.asarray(sh_b["t"], np.float64)
        self.sh_a = (self.ep.normalize(ca, sh_a["uv"]), np.asarray(sh_a["score"]))
        self.sh_b = (self.ep.normalize(cb, sh_b["uv"]), np.asarray(sh_b["score"]))
        self.wr = None
        if p_a is not None and p_b is not None:
            ka, kb = (p["kp"][:, :, list(WRISTS)].reshape(len(p["kp"]), -1, 3) for p in (p_a, p_b))
            self.wr_t_a, self.wr_t_b = np.asarray(p_a["t"], np.float64), np.asarray(p_b["t"], np.float64)
            self.wr = ((self.ep.normalize(ca, ka[..., :2]), ka[..., 2]), (self.ep.normalize(cb, kb[..., :2]), kb[..., 2]))

    def errors(self, offset, rate):
        """Sampson distances (px) of every usable A/B pair when B's clock is mapped by (offset, rate)."""
        out = []
        sets = [(self.t_a, self.sh_a, self.t_b, self.sh_b, MAX_JUMP_PX / self.ep.f)]  # jump limit in normalized units
        if self.wr is not None:
            sets.append((self.wr_t_a, self.wr[0], self.wr_t_b, self.wr[1], None))
        for t_a, (na, sa), t_b, (nb, sb), jump in sets:
            vb, ssb = resample(rate * t_b + offset, nb, sb, t_a, jump)
            ok = (sa >= MIN_SCORE) & (ssb >= MIN_SCORE)
            if ok.any():
                out.append(self.ep.sampson_px(na[ok], vb[ok]))
        return np.concatenate(out) if out else np.zeros(0)

    def cost(self, offset, rate):
        e = self.errors(offset, rate)
        return float(np.sqrt(np.mean(np.minimum(e, CAP_PX) ** 2))) if len(e) >= 10 else np.inf


def estimate(calib, sh_a, sh_b, p_a=None, p_b=None):
    """Shuttle first; the wrists join only if they are consistent with that result (median epipolar error below
    WRIST_GATE_PX): a pose model that puts wrists a few cm off in each view (blocky renders, heavy occlusion) would only add
    noise the cap cannot remove."""
    res = _estimate(Tracks(calib, sh_a, sh_b))
    res.update(wrists_used=False, wrist_median_px=None)
    if p_a is not None and p_b is not None:
        with_w = Tracks(calib, sh_a, sh_b, p_a, p_b)
        e_all, e_sh = (with_w.errors(res["offset_b"], res["rate_b"]), Tracks(calib, sh_a, sh_b).errors(res["offset_b"], res["rate_b"]))
        e_w = e_all[len(e_sh):]
        if len(e_w):
            res["wrist_median_px"] = float(np.median(e_w))
            if res["wrist_median_px"] < WRIST_GATE_PX:
                res = _estimate(with_w) | dict(wrists_used=True, wrist_median_px=res["wrist_median_px"])
    return res


def _estimate(tr):
    offsets = np.arange(-COARSE_RANGE, COARSE_RANGE + COARSE_STEP / 2, COARSE_STEP)
    costs = np.array([tr.cost(o, 1.0) for o in offsets])
    best = int(np.argmin(costs))
    # Ambiguity: the lowest cost more than 3 frames away from the best one
    fps = tr.fps
    far = np.abs(offsets - offsets[best]) > 3.0 / fps
    second = float(costs[far].min()) if far.any() else np.inf
    ambiguous = bool(second <= costs[best] * (1 + AMBIGUITY))
    # Fine search around the coarse minimum, then Nelder-Mead from the best grid point. The offset is parametrized at the middle
    # of the clip (t_c = rate * (t_b - tm) + tm + o_mid) so it barely correlates with the rate. A cost evaluation takes ~0.2 ms,
    # so the grid (41 x 41) costs about a second and keeps Nelder-Mead off the kinks of the piecewise-linear resampling.
    tm = float(np.mean(tr.t_b))

    def cost_mid(o_mid, rate):
        return tr.cost(o_mid + tm * (1.0 - rate), rate)

    o0 = offsets[best] - tm * 0.0
    og = o0 + np.arange(-2e-3, 2e-3 + 1e-9, 1e-4)
    overlap = float(min(tr.t_a[-1], tr.t_b[-1]) - max(tr.t_a[0], tr.t_b[0]))
    rate_fixed = overlap < MIN_RATE_OVERLAP_S
    rg = np.array([1.0]) if rate_fixed else 1.0 + np.arange(-200e-6, 200e-6 + 1e-9, 10e-6)
    grid = np.array([[cost_mid(o, r) for r in rg] for o in og])
    i, j = np.unravel_index(np.argmin(grid), grid.shape)
    start = np.array([og[i] * 1e3, (rg[j] - 1.0) * 1e4])
    if rate_fixed:
        res = type("R", (), {"x": start, "fun": grid[i, j]})  # grid minimum only: nothing to polish in one dimension at 0.1 ms
    else:
        res = minimize(lambda x: cost_mid(x[0] * 1e-3, 1.0 + x[1] * 1e-4), start, method="Nelder-Mead",
                       options=dict(initial_simplex=[start, start + [0.1, 0], start + [0, 0.1]], xatol=1e-3, fatol=1e-7, maxiter=300))
    x = res.x if res.fun <= grid[i, j] else start
    rate = 1.0 + x[1] * 1e-4
    offset = x[0] * 1e-3 + tm * (1.0 - rate)
    n = len(tr.errors(offset, rate))
    return {"offset_b": float(offset), "rate_b": float(rate), "cost": float(min(res.fun, grid[i, j])), "n_points": int(n),
            "ambiguous": ambiguous, "coarse_offset_b": float(offsets[best]), "coarse_cost": float(costs[best]),
            "second_cost": second, "rate_fixed": rate_fixed, "overlap_s": overlap}


def load(path):
    z = np.load(path)
    return {k: z[k] for k in z.files}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", type=Path, required=True)
    ap.add_argument("--shuttle2d", type=Path, nargs=2, required=True)
    ap.add_argument("--pose2d", type=Path, nargs=2)
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()
    calib = json.loads(args.calib.read_text())
    sh = [load(p) for p in args.shuttle2d]
    p2d = [load(p) for p in args.pose2d] if args.pose2d else [None, None]
    res = estimate(calib, sh[0], sh[1], *p2d)
    args.out.write_text(json.dumps(res, indent=1))
    if res["rate_fixed"]:
        print(f"rate fixed to 1 (overlap {res['overlap_s']:.1f} s < {MIN_RATE_OVERLAP_S:.0f} s)")
    print(f"camsync: offset_b {res['offset_b'] * 1e3:.3f} ms, rate_b {res['rate_b']:.7f} ({(res['rate_b'] - 1) * 1e6:+.1f} ppm), "
          f"capped rms {res['cost']:.2f} px over {res['n_points']} points{' AMBIGUOUS' if res['ambiguous'] else ''}; wrists "
          f"{'used' if res['wrists_used'] else 'not used'} (median {res['wrist_median_px'] if res['wrist_median_px'] is None else round(res['wrist_median_px'], 1)} px)")


if __name__ == "__main__":
    main()
