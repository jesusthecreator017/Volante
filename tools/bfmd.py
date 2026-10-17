"""BFMD loaders and court geometry (Phase 10). BFMD is non-commercial research data: read-only at ~/Datasets/BFMD, never
redistributed; every derived file goes to ~/Datasets/BFMD-work, never into the repo. JSON is parsed as data only (the dataset's
own .py files are never run or imported). Run anything that reads it with `python -I`.

Court: the 4 annotated corners are the DOUBLES outer lines (13.40 x 6.10 m). Evidence (`python -I bfmd.py`): the homography
from those corners puts the annotated net bases at x = 0.0..0.3 m (net at the middle of the length) and z = +-3.03..3.10 m
(measured against corner width, so independent of the assumed width: the posts stand on the doubles sidelines, 3.05 m).
Court coordinates are tools/court.py's (m, +X length, +Z width, origin center). The far ("top") baseline is +X, the near
("bottom") one -X; image right is +Z (camera at -X looking +X, +Y up). The bottom player is therefore on the -X half.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from court import HALF_LENGTH, HALF_WIDTH, NET_TOP_POST  # noqa: E402

ROOT = Path.home() / "Datasets" / "BFMD"
WORK = Path.home() / "Datasets" / "BFMD-work"
W, H = 1280, 720

# Virtual broadcast camera: median of the 12 per-match fits (`python -I tools/bfmd.py`); look-at form, 1280x720, no distortion.
VIRTUAL_CAM = {"eye": (-28.14, 6.69, 0.0), "target": (6.16, 0.0, 0.01), "focal": 2830.0}


def matches():
    return sorted(p.stem for p in (ROOT / "annotations" / "hit_inferred").glob("*.json"))


def _json(path):
    with open(path) as f:
        return json.load(f)


def load_court(m):
    d = _json(ROOT / "annotations" / "court" / f"{m}_court.json")
    c, n = d["court"], d["net"]
    img = np.float64([c["top_left"], c["top_right"], c["bottom_right"], c["bottom_left"],
                      n["left_base"], n["right_base"], n["left_top"], n["right_top"]])
    return img


# corners then net posts, matching load_court's order: top = far = +X, image left = -Z
WORLD = np.float64([
    [HALF_LENGTH, 0, -HALF_WIDTH], [HALF_LENGTH, 0, HALF_WIDTH], [-HALF_LENGTH, 0, HALF_WIDTH], [-HALF_LENGTH, 0, -HALF_WIDTH],
    [0, 0, -HALF_WIDTH], [0, 0, HALF_WIDTH], [0, NET_TOP_POST, -HALF_WIDTH], [0, NET_TOP_POST, HALF_WIDTH]])


def homography(img8):
    """Image px -> court floor (x, z) m from the 4 corners."""
    return cv2.getPerspectiveTransform(img8[:4].astype(np.float32), WORLD[:4, [0, 2]].astype(np.float32)).astype(np.float64)


def to_court(Hm, px):
    """px [..., 2] -> court (x, z) [..., 2]."""
    p = np.asarray(px, np.float64).reshape(-1, 2)
    q = np.c_[p, np.ones(len(p))] @ Hm.T
    return (q[:, :2] / q[:, 2:3]).reshape(np.shape(px))


def K_of(f):
    return np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])


def _reproj(img8, f):
    K = K_of(f)
    ok, rv, tv = cv2.solvePnP(WORLD[:4], img8[:4], K, None, flags=cv2.SOLVEPNP_IPPE)
    ok, rv, tv = cv2.solvePnP(WORLD, img8, K, None, rv, tv, True, cv2.SOLVEPNP_ITERATIVE)
    pr = cv2.projectPoints(WORLD, rv, tv, K, None)[0].reshape(-1, 2)
    return float(np.sqrt(((pr - img8) ** 2).sum(1).mean())), rv, tv


def fit_camera(img8):
    """PnP over a focal grid (coarse then fine); returns dict focal, eye, R, t, rms (px)."""
    fs = np.arange(500, 6001, 100.0)
    best = min(fs, key=lambda f: _reproj(img8, f)[0])
    fs = np.arange(best - 100, best + 101, 5.0)
    best = min(fs, key=lambda f: _reproj(img8, f)[0])
    rms, rv, tv = _reproj(img8, best)
    R = cv2.Rodrigues(rv)[0]
    return {"focal": float(best), "eye": (-R.T @ tv).ravel(), "R": R, "t": tv.ravel(), "rms": rms}


def lookat(eye, target):
    """Court -> camera rotation (OpenCV axes) for a camera at eye looking at target, +Y up."""
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    fwd = target - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 1.0, 0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)  # = -(right x fwd) = -cam up
    return np.stack([right, down, fwd])


def virtual_camera():
    c = VIRTUAL_CAM
    R = lookat(c["eye"], c["target"])
    return K_of(c["focal"]), R, -R @ np.asarray(c["eye"], float)


def project(pts):
    """Court points [..., 3] -> virtual-camera px [..., 2] (NaN where behind the camera)."""
    K, R, t = virtual_camera()
    p = np.asarray(pts, np.float64)
    c = p.reshape(-1, 3) @ R.T + t
    z = np.where(c[:, 2] > 0.1, c[:, 2], np.nan)
    uv = np.stack([K[0, 0] * c[:, 0] / z + K[0, 2], K[1, 1] * c[:, 1] / z + K[1, 2]], 1)
    return uv.reshape(p.shape[:-1] + (2,))


def load_hits(m):
    """Singles hits [(frame, shot_type, side)] in order."""
    d = _json(ROOT / "annotations" / "hit_inferred" / f"{m}.json")
    return [(h["frame"], h["shot_type"], h["side"]) for h in d["hits"]]


def load_shuttle(m):
    """{frame: (x_px, y_px)} for enabled detections (centre of the TrackNet box)."""
    d = _json(ROOT / "annotations" / "shuttle" / f"{m}.json")
    out = {}
    for s in d["predictions"][0]["result"][0]["value"]["sequence"]:
        if s.get("enabled", True) and s.get("x") is not None:
            out[s["frame"]] = ((s["x"] + s.get("width", 0) / 2) * W / 100, (s["y"] + s.get("height", 0) / 2) * H / 100)
    return out


def load_pose(m):
    """[(start, end, {frame: [entry, ...]})] per rally; entries are {bbox, keypoints} as in the file."""
    d = _json(ROOT / "annotations" / "pose" / f"{m}.json")["videos"]
    out = []
    for k, v in d.items():
        _, _, a, b = k.split("_")
        out.append((int(a), int(b), {int(f): e for f, e in v["frames"].items()}))
    return sorted(out, key=lambda r: r[0])


def main():
    """Per-match geometry report (and the numbers behind VIRTUAL_CAM)."""
    eyes, tgts, fs = [], [], []
    for m in matches():
        img = load_court(m)
        Hm = homography(img)
        err = np.abs(to_court(Hm, img[:4]) - WORLD[:4, [0, 2]]).max()
        net = to_court(Hm, img[4:6])
        cam = fit_camera(img)
        # optical axis (third row of R) hits the floor at target
        ax, e = cam["R"][2], cam["eye"]
        tgt = e - ax * e[1] / ax[1]
        eyes.append(e)
        tgts.append(tgt)
        fs.append(cam["focal"])
        print(f"{m[:48]:48s} f={cam['focal']:6.0f} eye=({e[0]:6.2f},{e[1]:5.2f},{e[2]:5.2f}) "
              f"tgt=({tgt[0]:5.2f},{tgt[2]:5.2f}) rms={cam['rms']:.2f}px corner_err={err * 100:.1e}cm "
              f"net_base=({net[0, 0]:.2f},{net[0, 1]:.2f}),({net[1, 0]:.2f},{net[1, 1]:.2f})")
    e, t, f = np.median(eyes, 0), np.median(tgts, 0), float(np.median(fs))
    print(f'VIRTUAL_CAM = {{"eye": ({e[0]:.2f}, {e[1]:.2f}, {e[2]:.2f}), "target": ({t[0]:.2f}, 0.0, {t[2]:.2f}), "focal": {f:.0f}.0}}')


if __name__ == "__main__":
    main()
