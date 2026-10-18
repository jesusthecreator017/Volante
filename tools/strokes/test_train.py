"""Phase 16.4a part 2 tests (synthetic windows, CPU only, no real data): feature determinism and shape, augmentation shape and
flag consistency, tiny CNN and GBM learn separable classes, leave-one-group-out never shares a group, ONNX export + onnxruntime
parity for both candidates, model json validation, threshold choice.
Run from anywhere: tools/.venv/bin/python tools/strokes/test_train.py"""
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as E  # noqa: E402
import features as F  # noqa: E402
import train as T  # noqa: E402


def synth(n_per=24, seed=0, classes=(1, 3, 4)):
    """Windows whose class sets the burst axis and amplitude; side from parity of the sample index; class 0 = no side."""
    rng = np.random.default_rng(seed)
    t = (np.arange(F.WINDOW_N) - T.PEAK) * F.DT
    xs, ys, ss = [], [], []
    for c in classes:
        for i in range(n_per):
            x = rng.normal(0, 0.02, (F.WINDOW_N, 10)).astype(np.float32)
            x[:, 1] += 1.0
            x[:, 8:] = 0
            x[:, 3:6] += rng.normal(0, 2.0, (F.WINDOW_N, 3))  # broadband noise: no degenerate (all-zero) band energies
            amp = (300 + 250 * c) * rng.uniform(0.85, 1.15) * (1 if i % 2 == 0 else -1)
            x[:, 3 + c % 3] += amp * np.exp(-0.5 * (t / (0.02 + 0.01 * c)) ** 2)
            x[:, 7] = np.linalg.norm(x[:, :3], axis=1)
            x[:, 6] = np.linalg.norm(x[:, 3:6], axis=1)
            xs.append(x), ys.append(c), ss.append(i % 2)
    return np.stack(xs), np.array(ys), np.array(ss)


def test_features():
    x, _, _ = synth()
    a, b = F.full_features_batch(x), F.full_features_batch(x.copy())
    assert a.shape == (len(x), F.N_FEATURES) and a.dtype == np.float32 and len(F.FEATURE_NAMES) == F.N_FEATURES
    assert np.array_equal(a, b) and np.isfinite(a).all()
    k = F.FEATURE_NAMES.index("w_peak_dps")
    assert abs(a[0, k] - x[0, :, 6].max()) < 1e-3
    assert a[0, F.FEATURE_NAMES.index("burst_s")] > 0


def test_augment():
    x, _, _ = synth(6)
    x[:, 300:310, 3] = 1990  # near the limit: gain pushes some into clipping
    rng = np.random.default_rng(1)
    for _ in range(5):
        y = T.augment(x, rng)
        assert y.shape == x.shape and y.dtype == x.dtype and np.isfinite(y).all()
        assert np.abs(y[:, :, 3:6]).max() <= T.LIM_DPS + 1e-3 and np.abs(y[:, :, 0:3]).max() <= T.LIM_G + 1e-3
        assert np.allclose(y[:, :, 6], np.linalg.norm(y[:, :, 3:6], axis=-1), atol=1e-3)
        assert np.allclose(y[:, :, 7], np.linalg.norm(y[:, :, 0:3], axis=-1), atol=1e-3)
        hit = (np.abs(y[:, :, 3:6]) >= T.LIM_DPS - 1e-3).any(-1)
        assert (y[:, :, 8][hit] == 1).all() and set(np.unique(y[:, :, 8:])) <= {0.0, 1.0}


def fit_eval(model, seed=0, classes=(1, 3, 4)):
    x, y, s = synth(seed=seed, classes=classes)
    xt, yt, _ = synth(seed=seed + 1, classes=classes)
    model.fit(x, y, s, 0)
    pc, ps = model.predict(xt)
    assert pc.shape == (len(xt), T.NC) and ps.shape == (len(xt), 2) and np.allclose(pc.sum(1), 1, atol=1e-4)
    return (pc.argmax(1) == yt).mean(), x, y, s


def test_gbm_learns_and_exports():
    m = T.GBM(max_iter=30)  # all 11 classes: the ONNX graph has one column per class seen in training
    acc, x, y, s = fit_eval(m, classes=range(T.NC))
    assert acc > 0.8, acc
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.onnx"
        T.export_gbm(m, p)
        pc, ps = m.predict(x)
        oc, os_ = T.ort_probs(p, F.full_features_batch(x))
        assert np.abs(oc - pc).max() <= 1e-4 and np.abs(os_ - ps).max() <= 1e-4


def test_cnn_learns_and_exports():
    m = T.CNN(epochs=150, patience=150, batch=16, device="cpu", widths=(8, 12, 16, 24))
    acc, x, y, s = fit_eval(m)
    assert acc > 0.7, acc
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.onnx"
        T.export_cnn(m, p)
        pc, ps = m.predict(x)
        oc, os_ = T.ort_probs(p, x)
        assert np.abs(oc - pc).max() <= 1e-4 and np.abs(os_ - ps).max() <= 1e-4


def test_folds_never_share_a_group():
    g = np.array(["a"] * 5 + ["b"] * 7 + ["c"] * 3)
    folds, names = T.group_folds(g)
    assert names == ["a", "b", "c"] and len(folds) == 3
    for tr, te in folds:
        assert not set(g[tr]) & set(g[te]) and len(tr) + len(te) == len(g)
    try:
        T.group_folds(np.array(["a", "a"]))
    except SystemExit:
        pass
    else:
        raise AssertionError("one group must be refused")


def test_block_folds_never_split_a_block():
    x, y, s = synth(30, classes=range(5))
    sess = np.array(["s"] * len(y))
    block = np.repeat(np.arange(15), 10)  # 15 blocks of 10 swings
    folds, names = T.block_folds(y, sess, block, k=5)
    assert len(folds) == 5 and len(names) == 5
    for tr, te in folds:
        assert not set(block[tr]) & set(block[te]) and not set(tr) & set(te)
    assert sorted(np.concatenate([te for _, te in folds])) == list(range(len(y)))
    f, n, mode = T.choose_folds(dict(session=sess, y=y, block=block, player=sess), "auto")
    assert mode == "block"
    two = dict(session=np.where(np.arange(len(y)) % 2, "s", "t"), y=y, block=block, player=sess)
    assert T.choose_folds(two, "auto")[2] == "loso"


def test_orientation_free_features_ignore_a_mount_rotation():
    x, _, _ = synth(6)
    th = np.radians(25)
    R = np.array([[np.cos(th), -np.sin(th), 0], [np.sin(th), np.cos(th), 0], [0, 0, 1]])
    y = x.copy()
    y[:, :, 0:3] = x[:, :, 0:3] @ R.T
    y[:, :, 3:6] = x[:, :, 3:6] @ R.T
    a, b = F.full_features_batch(x), F.full_features_batch(y)
    i = F.ORIENTATION_FREE_IDX
    assert len(i) > 15 and np.allclose(a[:, i], b[:, i], rtol=1e-3, atol=1e-3)
    assert not np.allclose(a, b, rtol=1e-3, atol=1e-3)  # the axis-wise features do move


def test_group_export_lists_groups_and_only_seen_ones():
    with tempfile.TemporaryDirectory() as d:
        c = [i for i in range(1, T.NC)]  # no not_a_hit, like the drill data
        x, y, s = synth(12, classes=c)
        data = dict(x=x, y=y, side=s, player=np.array(["P01"] * len(y)), session=np.array(["s"] * len(y)))
        m = T.GBM(max_iter=10, cols=F.ORIENTATION_FREE_IDX, nc=4).fit(x, T.GROUP_OF[y], s, 0)
        summ = dict(mean_macro_f1=0.5, mean_accuracy=0.5, mean_side_accuracy=0.5, mean_not_a_hit_precision=None)
        parity, meta = T.export_model(m, "a_of", data, dict(threshold=0.5, met=False, coverage=0.5, accuracy=0.5), summ, "0" * 64, d, 0, group=True)
        assert parity["max_abs_class"] <= 1e-4 and meta["output_class_ids"] == [1, 2, 3] and meta["groups_not_modelled"] == ["not_a_hit"]
        assert meta["class_to_group"]["smash"] == "power" and (Path(d) / "stroke_group_v0.onnx").exists()
        assert (Path(d) / "test_vectors_group_v0.npz").exists()


def test_group_map_covers_every_class():
    assert len(T.GROUP_OF) == T.NC and set(T.GROUP_OF) == {0, 1, 2, 3} and T.GROUP_OF[F.CLASSES.index("smash")] == 1


def test_threshold_and_metrics():
    rng = np.random.default_rng(0)
    y = rng.integers(0, T.NC, 400)
    pc = np.full((400, T.NC), 0.01)
    right = rng.random(400) < 0.7
    conf = np.where(right, rng.uniform(0.8, 1.0, 400), rng.uniform(0.2, 0.5, 400))
    pc[np.arange(400), np.where(right, y, (y + 1) % T.NC)] = conf
    pc /= pc.sum(1, keepdims=True)
    thr = E.choose_threshold(y, pc)
    assert thr["met"] and thr["accuracy"] >= 0.9 and 0.5 < thr["coverage"] < 0.9, thr
    m = E.metrics(y, np.zeros(400, int), pc, np.full((400, 2), 0.5))
    assert 0.5 < m["accuracy"] < 0.8 and m["absent_classes"] == []
    m2 = E.metrics(np.array([1, 1, 3]), np.array([0, 1, 0]), pc[:3], pc[:3, :2])
    assert len(m2["absent_classes"]) == T.NC - 2


def test_meta_validation():
    with tempfile.TemporaryDirectory() as d:
        x, y, s = synth(12, classes=range(T.NC))
        m = T.GBM(max_iter=10).fit(x, y, s, 0)
        data = dict(x=x, y=y, side=s, player=np.array(["P01"] * len(y)), session=np.array(["s"] * len(y)))
        summ = dict(mean_macro_f1=0.5, mean_accuracy=0.5, mean_side_accuracy=0.5, mean_not_a_hit_precision=None)
        thr = dict(threshold=0.5, met=True, coverage=0.8, accuracy=0.9)
        parity, meta = T.export_model(m, "a", data, thr, summ, "0" * 64, d, 0)
        assert parity["max_abs_class"] <= 1e-4 and (Path(d) / "test_vectors_v0.npz").stat().st_size < 1_000_000
        T.check_meta(meta)
        del meta["classes"]
        try:
            T.check_meta(meta)
        except AssertionError:
            return
        raise AssertionError("missing key accepted")


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
