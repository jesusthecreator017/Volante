"""Synthetic camera views with known ground truth. Run: python synth.py [--out DIR]"""
import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from court import LANDMARKS, court_polylines

OUT = Path(__file__).resolve().parent.parent / "out" / "synth"
HFOV = 108  # phone 0.5x ultra-wide
# ponytail: H_AIR is a guess at clear apex height; tune once real footage shows where clears peak.
H_AIR = 6.0  # 7.0 and 6.5 miss the 20 px margin when bisecting the elevation span; 6.0 is the largest that fits
AIRSPACE = [(x, H_AIR, 0.0) for x in (-3.35, 0.0, 3.35)]  # framing check only, not calibration landmarks


@dataclass
class CameraTruth:
    eye: tuple
    target: tuple
    hfov_deg: float
    size: tuple = (1920, 1080)
    k1: float = -0.18
    k2: float = 0.03

    @property
    def f(self):
        return self.size[0] / 2 / np.tan(np.radians(self.hfov_deg) / 2)

    @property
    def K(self):
        return np.array([[self.f, 0, self.size[0] / 2], [0, self.f, self.size[1] / 2], [0, 0, 1]])

    @property
    def dist(self):
        return np.array([self.k1, self.k2, 0, 0, 0], float)

    @property
    def R(self):
        eye, tgt = np.array(self.eye, float), np.array(self.target, float)
        z = tgt - eye
        z /= np.linalg.norm(z)
        x = np.cross(z, [0, 1, 0])  # camera right (image x); y_cam is down
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        return np.stack([x, y, z])

    @property
    def t(self):
        return -self.R @ np.array(self.eye, float)

    def project(self, pts):
        rvec, _ = cv2.Rodrigues(self.R)
        uv, _ = cv2.projectPoints(np.array(pts, float).reshape(-1, 1, 3), rvec, self.t, self.K, self.dist)
        return uv.reshape(-1, 2)


def aim(eye):
    """Floor target of a view direction whose yaw bisects the horizontal span of the four outer court
    corners and whose pitch bisects the elevation span of all landmarks plus the airspace points."""
    eye = np.array(eye, float)
    corners = np.array([(sx * 6.70, 0, sz * 3.05) for sx in (-1, 1) for sz in (-1, 1)]) - eye
    ang = np.arctan2(corners[:, 2], corners[:, 0])
    ref = ang[0]
    rel = (ang - ref + np.pi) % (2 * np.pi) - np.pi  # unwrap around the first bearing
    yaw = ref + (rel.min() + rel.max()) / 2
    fwd = np.array([np.cos(yaw), 0, np.sin(yaw)])
    d = np.array(list(LANDMARKS.values()) + AIRSPACE) - eye
    elev = np.arctan2(d[:, 1], d @ fwd)
    pitch = (elev.min() + elev.max()) / 2
    dirn = np.array([np.cos(pitch) * fwd[0], np.sin(pitch), np.cos(pitch) * fwd[2]])
    return tuple(eye + dirn * (-eye[1] / dirn[1]))  # where the optical axis hits the floor (pitch < 0)


# Real layout: same side (z < 0), 2.5 m outside the sideline, one near each baseline, 3.5 m up, aimed diagonally.
_EYE_A, _EYE_B = (7.2, 3.5, -5.55), (-7.2, 3.5, -5.55)
CAMERA_A = CameraTruth(eye=_EYE_A, target=aim(_EYE_A), hfov_deg=HFOV)
CAMERA_B = CameraTruth(eye=_EYE_B, target=aim(_EYE_B), hfov_deg=HFOV)


def _in_front(cam, pts):
    return (np.array(pts, float) @ cam.R.T + cam.t)[:, 2] > 0.1


def render(cam):
    w, h = cam.size
    img = np.full((h, w, 3), (30, 70, 30), np.uint8)  # dark green BGR
    for a, b in court_polylines():
        # sample the segment: lens distortion bends straight lines
        pts = a + (b - a) * np.linspace(0, 1, 60)[:, None]
        ok = _in_front(cam, pts)
        uv = cam.project(pts)
        for i in range(len(pts) - 1):
            if ok[i] and ok[i + 1]:
                cv2.line(img, tuple(np.round(uv[i]).astype(int)), tuple(np.round(uv[i + 1]).astype(int)),
                         (255, 255, 255), 2, cv2.LINE_AA)
    uv = cam.project(np.array([(0, y, 0) for y in np.linspace(0, H_AIR, 40)], float))
    cv2.polylines(img, [np.round(uv).astype(np.int32)], False, (110, 150, 110), 1, cv2.LINE_AA)
    for p in AIRSPACE:
        u, v = cam.project([p])[0]
        cv2.circle(img, (int(round(u)), int(round(v))), 4, (200, 200, 120), -1, cv2.LINE_AA)
    for name in ("post_top_near", "post_top_far"):
        u, v = cam.project([LANDMARKS[name]])[0]
        cv2.circle(img, (int(round(u)), int(round(v))), 6, (0, 200, 255), -1, cv2.LINE_AA)
    return img


def clicks(cam, noise_px=0.5, seed=0):
    rng = np.random.default_rng(seed)
    names = list(LANDMARKS)
    pts = np.array([LANDMARKS[n] for n in names])
    uv = cam.project(pts) + rng.normal(0, noise_px, (len(names), 2))
    w, h = cam.size
    return {n: [float(u), float(v)] for n, (u, v), ok in zip(names, uv, _in_front(cam, pts))
            if ok and 0 <= u < w and 0 <= v < h}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--noise", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B)):
        cv2.imwrite(str(args.out / f"cam_{cid}.png"), render(cam))
        pts = clicks(cam, args.noise, args.seed + (cid == "b"))
        (args.out / f"cam_{cid}.points.json").write_text(json.dumps(pts, indent=1))
        print(f"cam_{cid}: {len(pts)} points")


if __name__ == "__main__":
    main()
