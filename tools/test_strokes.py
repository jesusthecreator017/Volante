"""Phase 10 tests: BFMD geometry, feature round trip and mirroring, leave-matches-out evaluation, applying the model to the synthetic
replay. BFMD parts skip with a message when ~/Datasets/BFMD is missing. Run with `python -I tools/test_strokes.py`
(-I because it parses BFMD files; nothing from the dataset is imported).  Nothing BFMD-derived is written into the repo."""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bfmd  # noqa: E402
import classify_strokes as cs  # noqa: E402
import replay  # noqa: E402
import stroke_features as sf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SYNTH = ROOT / "assets" / "replay" / "replay.synthetic.bin"
HAVE_BFMD = bfmd.ROOT.is_dir()


def test_geometry():
    for m in bfmd.matches():
        img = bfmd.load_court(m)
        Hm = bfmd.homography(img)
        err = np.abs(bfmd.to_court(Hm, img[:4]) - bfmd.WORLD[:4, [0, 2]]).max()
        cam = bfmd.fit_camera(img)
        assert err < 0.01, (m, err)
        assert cam["rms"] < 5, (m, cam["rms"])
    print(f"geometry: {len(bfmd.matches())} matches, corners < 1 cm, PnP < 5 px")


def _synth_rallies():
    h, t = replay.read(SYNTH)
    sw = t["swings"]
    hits = sorted((float(r[3]), int(r[4])) for r in sw if np.isfinite(r[3]))
    return h, t, cs.to_rallies(t["joints3d"], t["shuttle"], h["fps"], hits)


def test_roundtrip():
    """Floor points through the virtual camera and back through the homography. Ankle keypoints sit up to ~20 cm above the floor,
    which a floor homography turns into up to ~1 m of depth error at the far baseline (grazing view); BFMD ankles carry the same
    bias, so the features keep it (printed, not asserted)."""
    h, t, rs = _synth_rallies()
    r = rs[0][0]
    ank = t["joints3d"][::4, :, 15:17, :]
    ank = ank[(ank[..., 3] > 0).all(-1)]  # [n, 2 ankles, xyzc]
    floor = ank[..., :3] * [1, 0, 1]
    err = np.abs(bfmd.to_court(r.H, bfmd.project(floor)) - floor[..., [0, 2]]).max()
    raw = np.abs(bfmd.to_court(r.H, bfmd.project(ank[..., :3])) - ank[..., [0, 2]]).max()
    assert err < 0.15, err
    print(f"round trip: floor points {err * 100:.2f} cm; raw ankles (height parallax) up to {raw * 100:.0f} cm")


def test_mirroring():
    _, _, rs = _synth_rallies()
    r = rs[0][0]
    flip_px = np.array([[-1, 0, bfmd.W], [0, 1, 0], [0, 0, 1.0]])
    kp_t = r.kp["bottom"][:, sf.FLIP].copy()
    kp_t[..., 0] = bfmd.W - kp_t[..., 0]
    bb = r.bbox["bottom"]
    bb_t = np.stack([bfmd.W - bb[:, 2], bb[:, 1], bfmd.W - bb[:, 0], bb[:, 3]], 1)
    sh_t = r.shuttle.copy()
    sh_t[:, 0] = bfmd.W - sh_t[:, 0]
    top = sf.Rally({"top": kp_t, "bottom": kp_t}, {"top": bb_t, "bottom": bb_t}, sh_t,
                   [(f, "top") for f, _ in r.hits], np.diag([-1, -1, 1.0]) @ r.H @ flip_px)
    bot = sf.Rally(r.kp, r.bbox, r.shuttle, [(f, "bottom") for f, _ in r.hits], r.H)
    a, b = sf.hit_features(bot), sf.hit_features(top)
    assert np.isfinite(a).sum() > 100
    assert np.array_equal(np.isnan(a), np.isnan(b)) and np.allclose(a, b, atol=1e-4, equal_nan=True), np.nanmax(np.abs(a - b))
    print("mirroring: top sample == bottom sample")


def test_train_eval():
    import train_strokes as ts
    X, y, g, _ = ts.load_data()
    keep = np.isin(y, ts.CLASSES)
    X, y, g = X[keep], y[keep], g[keep]
    from sklearn.metrics import accuracy_score, f1_score
    pred = ts.evaluate(X, y, g)
    f1 = {k: f1_score(y, p, average="macro", labels=ts.CLASSES, zero_division=0) for k, p in pred.items()}
    acc = accuracy_score(y, pred["full"])
    print(f"leave-matches-out: macro-F1 {f1}, accuracy {acc:.3f} (target 0.60)")
    assert f1["full"] > f1["majority"] and f1["full"] > f1["context"]
    if acc < 0.60:
        print("WARNING: accuracy below the 60 % target")


def test_apply():
    import train_strokes as ts
    if not ts.MODEL.exists():
        print(f"apply: skipped, no model at {ts.MODEL} (run train_strokes.py)")
        return
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.bin"
        cs.classify(SYNTH, ts.MODEL, out, quiet=True)
        h, t = replay.read(out)
        sw = t["swings"]
        types = [m for m in h["tracks"] if m["name"] == "swings"][0]["stroke_types"]
        assert types == cs.STROKE_TYPES
        for r in sw:
            assert 0 <= int(r[5]) < len(types)
            assert (types[int(r[5])] == "swing_only") == bool(np.isnan(r[3])), r
        assert any(types[int(r[5])] not in ("unknown", "swing_only") for r in sw)
        run = subprocess.run(["cargo", "run", "-q", "--release", "--", "--check-replay", str(out)], cwd=ROOT,
                             capture_output=True, text=True)
        assert run.returncode == 0 and "ok" in run.stdout + run.stderr, run.stderr
    print("apply: every swings row has a valid stroke index, replay parses")


if __name__ == "__main__":
    if HAVE_BFMD:
        test_geometry()
    else:
        print("skipping BFMD parts: ~/Datasets/BFMD not found")
    test_roundtrip()
    test_mirroring()
    if HAVE_BFMD:
        test_train_eval()
        test_apply()
    print("OK")
