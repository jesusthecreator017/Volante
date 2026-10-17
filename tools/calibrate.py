"""Calibrate one camera from clicked court landmarks.

  python calibrate.py --image cam_a.png --points cam_a.points.json --camera-id a -o a.json --overlay a_overlay.png
  python calibrate.py --merge a.json b.json -o calib.json
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from court import LANDMARKS, court_polylines

FLAGS = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO
         | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3)


def _K(f, size):
    return np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1.0]])


def _rms(obj, img, rvec, t, K, dist):
    proj, _ = cv2.projectPoints(obj, rvec, t, K, dist)
    return np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1)))


def solve(points, size, camera_id="a", fps=60.0):
    """points: {landmark name: (u, v)}, size: (w, h). Returns a calib camera dict."""
    names = [n for n in points if n in LANDMARKS]
    if len(names) < 6:
        print(f"warning: only {len(names)} points (< 6)", file=sys.stderr)
    obj = np.array([LANDMARKS[n] for n in names], np.float64)
    img = np.array([points[n] for n in names], np.float64)

    # Step 1: initial focal. Scan f with solvePnP (undistorted) and keep the best fit. Cheap and
    # robust where a homography estimate is noisy; the planar floor points alone fix f poorly.
    best = None
    for f in np.geomspace(size[0] * 0.2, size[0] * 3, 120):
        ok, rv, tv = cv2.solvePnP(obj, img, _K(f, size), None, flags=cv2.SOLVEPNP_ITERATIVE)
        if ok:
            e = _rms(obj, img, rv, tv, _K(f, size), np.zeros(5))
            if best is None or e < best[0]:
                best = (e, f, rv, tv)
    _, f0, rv, tv = best
    # Step 2: PnP at that focal.
    ok, rv, tv = cv2.solvePnP(obj, img, _K(f0, size), None, rv, tv, True, cv2.SOLVEPNP_ITERATIVE)
    # Step 3: refine f, k1, pose with calibrateCamera on the single view.
    _, K, dist, rvs, tvs = cv2.calibrateCamera(
        [obj.astype(np.float32)], [img.astype(np.float32)], size, _K(f0, size), np.zeros(5), [rv], [tv],
        flags=FLAGS)
    rvec, t = rvs[0], tvs[0].reshape(3)
    R, _ = cv2.Rodrigues(rvec)
    proj, _ = cv2.projectPoints(obj, rvec, t, K, dist)
    err = np.linalg.norm(proj.reshape(-1, 2) - img, axis=1)
    rms = float(np.sqrt(np.mean(err**2)))
    for n, e in zip(names, err):
        print(f"  {n:40s} {e:6.2f} px")
    print(f"camera {camera_id}: rms {rms:.3f} px, {len(names)} points, f={K[0, 0]:.1f}")
    if rms > 2:
        print(f"warning: rms {rms:.2f} px > 2 px", file=sys.stderr)
    return {
        "id": camera_id, "image_size": list(size), "fps": fps,
        "K": K.tolist(), "dist": dist.reshape(-1)[:5].tolist(),
        "R": R.tolist(), "t": t.tolist(), "position": (-R.T @ t).tolist(),
        "rms_px": rms, "n_points": len(names),
    }


def make_calib(cameras):
    return {"version": 1, "coordinate_system": "court_m_yup_xlength", "cameras": cameras}


def overlay(img, cam, points):
    out = img.copy()
    K, dist = np.array(cam["K"]), np.array(cam["dist"])
    R, t = np.array(cam["R"]), np.array(cam["t"])
    rvec, _ = cv2.Rodrigues(R)
    pt = lambda p: tuple(int(round(v)) for v in p)  # noqa: E731
    for a, b in court_polylines():
        pts = a + (b - a) * np.linspace(0, 1, 60)[:, None]
        ok = (pts @ R.T + t)[:, 2] > 0.1
        uv, _ = cv2.projectPoints(pts, rvec, t, K, dist)
        uv = uv.reshape(-1, 2)
        for i in range(len(pts) - 1):
            if ok[i] and ok[i + 1]:
                cv2.line(out, pt(uv[i]), pt(uv[i + 1]), (0, 0, 255), 1, cv2.LINE_AA)
    for n, (u, v) in points.items():
        if n not in LANDMARKS:
            continue
        cv2.circle(out, pt((u, v)), 4, (0, 255, 0), -1, cv2.LINE_AA)
        p, _ = cv2.projectPoints(np.array([LANDMARKS[n]], float), rvec, t, K, dist)
        cv2.drawMarker(out, pt(p.reshape(2)), (0, 0, 255), cv2.MARKER_CROSS, 12, 1, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image")
    ap.add_argument("--points")
    ap.add_argument("--camera-id", default="a")
    ap.add_argument("--fps", type=float, default=60.0)
    ap.add_argument("--overlay")
    ap.add_argument("--merge", nargs="+", help="camera json files to merge into one calib.json")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()
    if args.merge:
        cams = [json.loads(Path(p).read_text()) for p in args.merge]
        Path(args.output).write_text(json.dumps(make_calib(cams), indent=2))
        return
    img = cv2.imread(args.image)
    points = json.loads(Path(args.points).read_text())
    h, w = img.shape[:2]
    cam = solve(points, (w, h), args.camera_id, args.fps)
    Path(args.output).write_text(json.dumps(cam, indent=2))
    if args.overlay:
        cv2.imwrite(args.overlay, overlay(img, cam, points))


if __name__ == "__main__":
    main()
