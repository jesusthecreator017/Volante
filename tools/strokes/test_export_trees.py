"""Phase 22 tests (synthetic data, CPU only): the JSON tree evaluator equals sklearn, display-threshold rule, schema of the committed
models/strokes/*.trees.json, their test vectors reproduce from the stored windows, and the iPhone's bundled copies are identical.
Run from anywhere: tools/.venv/bin/python tools/strokes/test_export_trees.py"""
import json
import sys
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_trees as X  # noqa: E402
import features as F  # noqa: E402
import train as T  # noqa: E402
from test_train import synth  # noqa: E402

MODELS = T.REPO / "models" / "strokes"
BUNDLE = T.REPO / "apple" / "OsoKit" / "Sources" / "OsoKit" / "Resources"
FILES = ["stroke_group_v0.trees.json", "stroke_class_v0.trees.json", "test_vectors_trees_v0.json"]


def test_json_evaluator_equals_sklearn():
    x, y, s = synth(20, classes=range(1, T.NC))
    for nc, yy in ((T.NC, y), (len(T.GROUP_NAMES), T.GROUP_OF[y])):
        m = T.GBM(max_iter=25, cols=F.ORIENTATION_FREE_IDX, nc=nc).fit(x, yy, s, 0)
        feats = m._f(x)
        head = json.loads(json.dumps(X.head_json(m.clf, list(range(len(m.clf.classes_))))))  # through JSON text, like the app
        pc, ps = m.predict(x)
        ids = list(m.clf.classes_)
        for i, f in enumerate(feats):
            assert np.abs(np.array(X.evaluate(head, f)) - pc[i, ids]).max() <= 1e-6
        side = json.loads(json.dumps(X.head_json(m.side_clf, ["FH", "BH"])))
        assert side["link"] == "sigmoid" and side["per_iter"] == 1
        for i, f in enumerate(feats):
            assert np.abs(np.array(X.evaluate(side, f)) - ps[i]).max() <= 1e-6


def test_display_threshold_rule():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 3, 600)
    pc = np.full((600, 3), 0.05)
    right = rng.random(600) < 0.8
    conf = np.where(right, rng.uniform(0.7, 1.0, 600), rng.uniform(0.4, 0.69, 600))
    pc[np.arange(600), np.where(right, y, (y + 1) % 3)] = conf
    r = X.display_threshold(y, pc, 0.9)
    assert r["met"] and r["accuracy"] >= 0.9 and 0.05 <= r["coverage"] <= 1 and 0.4 <= r["threshold"] <= 0.9, r
    lo = X.display_threshold(y, pc, 1.01)  # unreachable: fallback, flagged
    assert not lo["met"] and lo["coverage"] >= X.FALLBACK_COVERAGE


def test_committed_models():
    g, c, v = (json.loads((MODELS / f).read_text()) for f in FILES)
    names = [F.FEATURE_NAMES[i] for i in F.ORIENTATION_FREE_IDX]
    for m in (g, c):
        assert m["schema"] == X.SCHEMA and m["features_version"] == F.FEATURES_VERSION and m["feature_names"] == names
        assert m["link"] == "softmax" and len(m["baseline"]) == m["per_iter"] == len(m["classes"])
        assert len(m["trees"]) % m["per_iter"] == 0 and 0 < m["display_threshold"]["threshold"] <= 1.0001
        assert m["display_threshold"]["rule"] and "min_accuracy" in m["display_threshold"]
        for t in m["trees"]:
            n = len(t["f"])
            assert len(t["v"]) == len(t["r"]) == len(t["m"]) == n and t["f"][-1] == -1 and max(t["f"]) < len(names)
    # not_a_hit is a class once the training data has a not_a_hit block (model card, 2-drill retrain)
    assert [k for k in g["classes"] if k != "not_a_hit"] == ["power", "mid", "touch"]
    assert g["side"]["classes"] == F.SIDES and g["side"]["link"] == "sigmoid"
    assert len([k for k in c["classes"] if k != "not_a_hit"]) == 10
    assert v["features_version"] == F.FEATURES_VERSION and len(v["vectors"]) == 20


def test_vectors_reproduce():
    g, c, v = (json.loads((MODELS / f).read_text()) for f in FILES)
    for e in v["vectors"]:
        w = np.zeros((F.WINDOW_N, 10), np.float32)
        w[:, 6], w[:, 7] = e["w"], e["a"]
        w[e["sat_gyro"], 8], w[e["sat_acc"], 9] = 1, 1
        f = F.full_features(w)[F.ORIENTATION_FREE_IDX]
        assert np.allclose(f, e["features"], rtol=1e-5, atol=1e-5)
        for m, k in ((g, "group"), (c, "cls")):
            assert np.abs(np.array(X.evaluate(m, e["features"])) - e[k]).max() <= 1e-9
        assert np.abs(np.array(X.evaluate(g["side"], e["features"])) - e["side"]).max() <= 1e-9
    fx = v["window_fixture"]  # the Python window builder on the stored packets still gives the stored window
    again = X.window_fixture()
    assert again["w"] == fx["w"] and again["a"] == fx["a"] and again["t_peak_us"] == fx["t_peak_us"]


def test_bundled_copies_are_identical():
    # apple/scripts/sync-models.sh copies the two model files into the OsoKit resources; a stale copy fails here and in `swift test`
    for f in FILES[:2]:
        assert (BUNDLE / f).read_bytes() == (MODELS / f).read_bytes(), f"{f}: run apple/scripts/sync-models.sh"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("ok  ", name)
            except Exception:
                fails += 1
                print("FAIL", name)
                traceback.print_exc()
    sys.exit(1 if fails else 0)
