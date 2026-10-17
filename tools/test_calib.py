"""Plain asserts: python tools/test_calib.py"""
import json
import tempfile
from pathlib import Path

import numpy as np

from calibrate import make_calib, solve
from synth import AIRSPACE, CAMERA_A, CAMERA_B, clicks
from triangulate import triangulate

SEED = 0


def rot_err_deg(R1, R2):
    return np.degrees(np.arccos(np.clip((np.trace(R1 @ np.array(R2).T) - 1) / 2, -1, 1)))


def check(cam, cid, noise, seed, pos_tol, rot_tol, f_tol, rms_tol=None):
    est = solve(clicks(cam, noise, seed), cam.size, cid)
    pos = np.linalg.norm(np.array(est["position"]) - cam.eye)
    rot = rot_err_deg(cam.R, est["R"])
    ferr = abs(est["K"][0][0] - cam.f) / cam.f
    print(f"[{cid}] noise={noise}: rms={est['rms_px']:.3f}px pos={pos * 100:.2f}cm rot={rot:.3f}deg f={ferr * 100:.3f}%")
    assert pos < pos_tol and rot < rot_tol and ferr < f_tol, (pos, rot, ferr)
    if rms_tol:
        assert est["rms_px"] < rms_tol
    return est


def main():
    from court import LANDMARKS as LM
    for cam in (CAMERA_A, CAMERA_B):  # layout sanity: every landmark and airspace point in frame with >= 20 px margin
        uv = cam.project(list(LM.values()) + AIRSPACE)
        assert (uv >= 20).all() and (uv <= np.array(cam.size) - 20).all(), (uv.min(0), uv.max(0))
    ests = []
    for cam, cid, seed in ((CAMERA_A, "a", SEED), (CAMERA_B, "b", SEED + 1)):
        ests.append(check(cam, cid, 0.5, seed, 0.05, 0.3, 0.01, rms_tol=1.0))
        check(cam, cid, 0.0, seed, 0.001, 0.3, 0.01)  # conventions sanity: exact data, mm-level
    calib = make_calib(ests)

    # round trip
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "calib.json"
        p.write_text(json.dumps(calib))
        back = json.loads(p.read_text())
    assert back == calib and back["version"] == 1

    # triangulation, full pipeline over seeds 0..9 (cameras on one side near opposite baselines, rays crossing near the net)
    from court import LANDMARKS
    for seed in range(10):
        ea = solve(clicks(CAMERA_A, 0.5, seed), CAMERA_A.size, "a")
        eb = solve(clicks(CAMERA_B, 0.5, seed + 1), CAMERA_B.size, "b")
        cal = make_calib([ea, eb])
        ca, cb = clicks(CAMERA_A, 0.5, seed), clicks(CAMERA_B, 0.5, seed + 1)
        names = [n for n in ca if n in cb]
        truth = np.array([LANDMARKS[n] for n in names])
        err = np.linalg.norm(triangulate(cal, [ca[n] for n in names], [cb[n] for n in names]) - truth, axis=1)
        near = err[truth[:, 0] < 0]
        print(f"seed {seed}: n={len(names)} median={np.median(err) * 100:.2f}cm near-max={near.max() * 100:.2f}cm max={err.max() * 100:.2f}cm")
        assert np.median(err) < 0.02 and near.max() < 0.04 and err.max() < 0.04, seed
    print("ok")


if __name__ == "__main__":
    main()
