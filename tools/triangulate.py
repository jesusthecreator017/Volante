"""Two-view triangulation from a calib.json dict (calib["cameras"][0] and [1])."""
import cv2
import numpy as np


def _P(cam):
    return np.hstack([np.array(cam["R"]), np.array(cam["t"]).reshape(3, 1)])


def _norm(cam, uv):
    """Pixels -> undistorted normalized camera coordinates, shape (2, N)."""
    pts = np.asarray(uv, np.float64).reshape(-1, 1, 2)
    return cv2.undistortPoints(pts, np.array(cam["K"]), np.array(cam["dist"])).reshape(-1, 2).T


def triangulate(calib, uv_a, uv_b):
    """uv_a, uv_b: (N, 2) pixels (or one (2,) pair). Returns (N, 3) court xyz (or (3,))."""
    ca, cb = calib["cameras"][:2]
    single = np.asarray(uv_a).ndim == 1
    X = cv2.triangulatePoints(_P(ca), _P(cb), _norm(ca, uv_a), _norm(cb, uv_b))
    xyz = (X[:3] / X[3]).T
    return xyz[0] if single else xyz


def reprojection_error(calib, xyz, uv_a, uv_b):
    """Per-point pixel error of xyz in each view: returns (err_a, err_b)."""
    out = []
    for cam, uv in zip(calib["cameras"][:2], (uv_a, uv_b)):
        rvec, _ = cv2.Rodrigues(np.array(cam["R"]))
        p, _ = cv2.projectPoints(np.asarray(xyz, np.float64).reshape(-1, 1, 3), rvec,
                                 np.array(cam["t"]), np.array(cam["K"]), np.array(cam["dist"]))
        out.append(np.linalg.norm(p.reshape(-1, 2) - np.asarray(uv, np.float64).reshape(-1, 2), axis=1))
    return tuple(out)
