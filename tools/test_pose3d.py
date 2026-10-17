"""Plain asserts: python tools/test_pose3d.py  (seed 0, full pipeline synth -> pose2d -> pose3d -> replay.bin -> read back)"""
import json
import tempfile
from pathlib import Path

import numpy as np

import replay
from pose3d import pose3d
from synth import CAMERA_A, CAMERA_B
from synth_motion import SWING, detect, generate

SEED = 0
CALIB = Path(__file__).resolve().parent.parent / "assets/calib/calib.json"


def main():
    calib = json.loads(CALIB.read_text())
    fps = calib["cameras"][0]["fps"]
    gt, t, sp, st = generate(SEED, fps)
    rng = np.random.default_rng(SEED + 1000)
    p2d = [detect(rng, cam, gt, fps, cid) for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B))]
    out = pose3d(calib, *p2d)

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "replay.bin"
        replay.write(path, fps, [({"name": "joints3d", "layout": "x,y,z,conf", "keypoint_format": "coco17",
                                   "players": ["p0", "p1"]}, out)])
        header, tracks = replay.read(path)
    tr = header["tracks"][0]
    assert header["version"] == 1 and header["frame_count"] == len(t) and header["fps"] == fps
    assert tr["shape"] == [len(t), 2, 17, 4] and tr["offset"] % 16 == 0 and tr["name"] == "joints3d"
    back = tracks["joints3d"]
    assert np.array_equal(back, out.astype("<f4"))

    conf = back[..., 3]
    err = np.linalg.norm(back[..., :3] - gt, axis=-1)
    full, filled = conf == 1.0, conf == 0.5
    cov = (conf > 0).mean()
    print(f"MPJPE {err[full].mean() * 100:.2f} cm  p95 {np.percentile(err[full], 95) * 100:.2f} cm  "
          f"max(conf>0) {err[conf > 0].max() * 100:.2f} cm")
    print(f"gap-filled: n={filled.sum()} MPJPE {err[filled].mean() * 100:.2f} cm   coverage {cov * 100:.2f} %")
    assert err[full].mean() < 0.03 and np.percentile(err[full], 95) < 0.06
    assert err[conf > 0].max() < 0.20
    assert err[filled].mean() < 0.06
    assert cov >= 0.97

    # No fake speed spikes: wrist/elbow speed between consecutive conf>0 frames (conf-0 frames are zero-filled, skip them)
    both = (conf[1:] > 0) & (conf[:-1] > 0)
    step = np.linalg.norm(np.diff(back[..., :3], axis=0), axis=-1) * fps
    for j in (7, 8, 9, 10):
        assert np.where(both[:, :, j], step[:, :, j], 0).max() < 20, j

    # Swing peak speed: right wrist, fully tracked swings (a swing with a filled gap measures gap fill, not smoothing)
    def speed(x, sl, c=None):
        x = x[sl] if c is None else np.where((c[sl] > 0)[:, None], x[sl], np.nan)  # conf-0 frames are zero-filled
        return np.nanmax(np.linalg.norm(np.gradient(x, 1 / fps, axis=0), axis=1))

    checked = 0
    for p, t0 in zip(sp, st):
        sl = slice(int((t0 - 0.1) * fps), int((t0 + SWING[1] + 0.1) * fps) + 1)
        ratio = speed(back[:, p, 10, :3], sl, conf[:, p, 10]) / speed(gt[:, p, 10], sl)
        tracked = (conf[sl, p, 10] > 0).all()
        print(f"swing p{p} t={t0:.2f}: peak {speed(gt[:, p, 10], sl):.1f} m/s ratio {ratio:.3f}"
              + ("" if tracked else "  (gap in window, not asserted)"))
        if tracked:
            assert abs(ratio - 1) < 0.15, (p, t0, ratio)
            checked += 1
    assert checked >= len(sp) // 2, checked
    print(f"ok ({checked}/{len(sp)} swings asserted)")


if __name__ == "__main__":
    main()
