"""Phase 22: export the orientation-free (a_of) gradient-boosted models as plain JSON trees for the iPhone app (pure Swift evaluator).

    tools/.venv/bin/python tools/strokes/export_trees.py            # train a_of (11-class + group) on the drill swings, write models/strokes/
    (train.py --final calls export_all() too)

Writes stroke_group_v0.trees.json (3 groups + FH/BH head), stroke_class_v0.trees.json (10 classes; not_a_hit has no drill data)
and test_vectors_trees_v0.json. Schema `volante-trees/1`: see docs/strokes/model-card.md. A tree is stored in preorder (left child
= next node): f feature index (-1 = leaf), v threshold (go left when x <= v, x float32) or leaf value, r right child, m missing-goes-left
bits. Raw score = baseline + sum of leaf values per output; softmax (or sigmoid for one output)."""
import argparse
import datetime
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402
import imu as IMU  # noqa: E402
import train as T  # noqa: E402

SCHEMA = "volante-trees/1"
MIN_COVERAGE, FALLBACK_COVERAGE = 0.05, 0.20
GROUP_MIN_ACC, CLASS_MIN_ACC = 0.80, 0.50  # display-threshold rule: lowest threshold whose accepted swings are this accurate (OOF)


def f32(x):
    """Shortest decimal that round-trips the float32 (a float, so json prints it as-is)."""
    return float(str(np.float32(x)))


def leaf(x):
    """Leaf value to 6 significant digits (relative error 5e-7; the JSON is checked against sklearn to 1e-6 below)."""
    return float("%.6g" % x)


def tree_json(pred):
    """sklearn TreePredictor -> dict(f, v, r, m) in preorder. Thresholds are rounded DOWN to float32 (the features are float32, so
    `x <= t64` and `x <= t32` agree for every float32 x), the same fix as the ONNX export."""
    nodes = pred.nodes
    f, v, r, m = [], [], [], []

    def walk(i):
        k = len(f)
        n = nodes[i]
        if n["is_leaf"]:
            f.append(-1), v.append(leaf(n["value"])), r.append(0), m.append("1")
            return
        t = np.float32(n["num_threshold"])
        if float(t) > float(n["num_threshold"]):
            t = np.nextafter(t, np.float32(-np.inf))
        f.append(int(n["feature_idx"])), v.append(f32(t)), r.append(0), m.append("1" if n["missing_go_to_left"] else "0")
        walk(int(n["left"]))
        r[k] = len(f)
        walk(int(n["right"]))

    walk(0)
    return dict(f=f, v=v, r=r, m="".join(m))


def head_json(clf, classes):
    """A fitted HistGradientBoostingClassifier -> dict(classes, link, baseline, per_iter, trees). Binary: one output, sigmoid."""
    k = len(clf._predictors[0])
    base = np.asarray(clf._baseline_prediction, float).ravel()
    assert len(base) == k
    trees = [tree_json(p) for it in clf._predictors for p in it]  # iteration-major: tree i belongs to output i % k
    return dict(classes=classes, link="sigmoid" if k == 1 else "softmax", baseline=[float(b) for b in base], per_iter=k, trees=trees)


def run_tree(t, x):
    i = 0
    while t["f"][i] >= 0:
        xi = x[t["f"][i]]
        # thresholds are float32 values printed as shortest decimals: compare as float32 (a double parse can sit a hair off)
        left = t["m"][i] == "1" if math.isnan(xi) else xi <= float(np.float32(t["v"][i]))
        i = i + 1 if left else t["r"][i]
    return t["v"][i]


def evaluate(head, x):
    """Pure-Python evaluator of a head on one float32 feature vector -> probabilities (list)."""
    x = [float(np.float32(v)) for v in x]
    k = head["per_iter"]
    raw = list(head["baseline"])
    for i, t in enumerate(head["trees"]):
        raw[i % k] += run_tree(t, x)
    if head["link"] == "sigmoid":
        p = 1 / (1 + math.exp(-raw[0]))
        return [1 - p, p]
    mx = max(raw)
    e = [math.exp(r - mx) for r in raw]
    return [v / sum(e) for v in e]


def display_threshold(y, pc, min_acc):
    """Lowest max-probability threshold whose accepted swings (max prob >= threshold) have accuracy >= min_acc, with its coverage.
    A threshold must keep >= MIN_COVERAGE of the swings to count. If none reaches min_acc: met False, and the fallback is the threshold with
    the best accuracy among those keeping >= FALLBACK_COVERAGE (E.choose_threshold's convention)."""
    conf, pred = pc.max(1), pc.argmax(1)
    rows = []
    for t in np.unique(np.floor(conf * 1e4) / 1e4):
        keep = conf >= t
        rows.append(dict(threshold=float(t), coverage=float(keep.mean()), accuracy=float((pred[keep] == y[keep]).mean())))
    ok = [r for r in rows if r["accuracy"] >= min_acc and r["coverage"] >= MIN_COVERAGE]
    if ok:
        return dict(met=True, **min(ok, key=lambda r: r["threshold"]))
    return dict(met=False, **max((r for r in rows if r["coverage"] >= FALLBACK_COVERAGE), key=lambda r: r["accuracy"]))


def oof(d, y, nc, folds, seed):
    """Out-of-fold probabilities of the a_of GBM over the same folds train.py uses."""
    pc = np.zeros((len(y), nc))
    for k, (tr, te) in enumerate(folds):
        m = T.GBM(cols=F.ORIENTATION_FREE_IDX, nc=nc).fit(d["x"][tr], y[tr], d["side"][tr], seed + k)
        pc[te] = m.predict(d["x"][te])[0]
    return pc


def model_json(name, head, side_head, display, digest, eval_note, n_swings, extra):
    names = [F.FEATURE_NAMES[i] for i in F.ORIENTATION_FREE_IDX]
    out = dict(schema=SCHEMA, name=name, version=T.MODEL_VERSION, created=datetime.date.today().isoformat(),
               status="experimental: one player's drill session (little data); see docs/strokes/model-card.md",
               features_version=F.FEATURES_VERSION, feature_names=names, feature_dtype="float32",
               window=dict(pre_s=F.PRE_S, post_s=F.POST_S, rate_hz=F.RATE_HZ, n=F.WINDOW_N),
               **extra, **head)
    if side_head:
        out["side"] = dict(classes=F.SIDES, **{k: v for k, v in side_head.items() if k != "classes"})
    out["display_threshold"] = dict(
        **display, rule="lowest max-probability threshold whose accepted swings have out-of-fold accuracy >= min_accuracy; "
                        "below it the app says Unclassified", eval=eval_note)
    out["training_data_sha256"], out["n_swings"] = digest, n_swings
    return out


def dump(obj, path):
    Path(path).write_text(json.dumps(obj, separators=(",", ":")) + "\n")


def window_fixture():
    """A short synthetic racquet recording as raw imu.bin packets (jittered times, a 6 ms hole, a saturated stretch), the peak time
    and the window the Python pipeline builds from it: the Swift StrokeWindow must reproduce it."""
    rng = np.random.default_rng(7)
    n = 650
    tus = (np.arange(n) * 2000 + rng.integers(-150, 150, n)).astype(np.int64)
    keep = np.ones(n, bool)
    keep[400:406] = False  # 6 packets lost: a ~14 ms hole inside the window (> MAX_HOLE_S)
    t = tus * 1e-6
    t_pk = 0.760
    ax = 2048 * (0.2 + 3 * np.exp(-0.5 * ((t - t_pk - 0.004) / 0.012) ** 2))
    gx = 16.4 * (1800 * np.exp(-0.5 * ((t - t_pk) / 0.035) ** 2) + 250 * np.exp(-0.5 * ((t - 0.5) / 0.08) ** 2))
    pk = []
    for i in range(n):
        v = [ax[i], 2048 + rng.normal(0, 8), rng.normal(0, 8), gx[i] + rng.normal(0, 20), 0.3 * gx[i] + rng.normal(0, 20), rng.normal(0, 20)]
        v = [int(np.clip(round(c), -32768, 32767)) for c in v]
        v[3] = min(v[3], 32767)
        if keep[i]:
            pk.append((i, int(tus[i]), tuple(v)))
    # a saturated gyro stretch near the peak (rail value) so the flag channel is exercised
    pk = [(s, tu, (*v[:3], 32767 if 375 <= s < 381 else v[3], *v[4:])) for s, tu, v in pk]
    path = Path(__file__).parent / "_fixture.tmp"
    path.write_bytes(IMU.encode_imu(500, pk))
    rec = IMU.read_imu(path)
    path.unlink()
    i = int(np.argmax(np.linalg.norm(rec.gyro.astype(float), axis=1)))
    win, flags = F.window(rec.t, rec.acc, rec.gyro, rec.sat_gyro, rec.sat_acc, rec.t[i])
    return dict(packets=[[s, tu, *v] for s, tu, v in pk], t_peak_us=int(rec.t_us[i]), flags=flags,
                w=[f32(v) for v in win[:, 6]], a=[f32(v) for v in win[:, 7]],
                sat_gyro=[int(j) for j in np.nonzero(win[:, 8])[0]], sat_acc=[int(j) for j in np.nonzero(win[:, 9])[0]])


def export_all(d, out, seed, digest, eval_note=None):
    """d: drill swings (train.load_labelled + subset). Trains the a_of models on all of them, evaluates out-of-fold for the display
    thresholds, writes the three files. Returns a summary dict (parity numbers, thresholds)."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    folds, _, mode = T.choose_folds(d, "auto")
    eval_note = eval_note or T.EVAL_NOTE[mode]
    ycls, ygrp = d["y"], T.GROUP_OF[d["y"]]
    disp_c = display_threshold(ycls, oof(d, ycls, T.NC, folds, seed), CLASS_MIN_ACC) | dict(min_accuracy=CLASS_MIN_ACC)
    disp_g = display_threshold(ygrp, oof(d, ygrp, len(T.GROUP_NAMES), folds, seed), GROUP_MIN_ACC) | dict(min_accuracy=GROUP_MIN_ACC)
    mc = T.GBM(cols=F.ORIENTATION_FREE_IDX, nc=T.NC).fit(d["x"], ycls, d["side"], seed)
    mg = T.GBM(cols=F.ORIENTATION_FREE_IDX, nc=len(T.GROUP_NAMES)).fit(d["x"], ygrp, d["side"], seed)
    cls_names = [F.CLASSES[i] for i in mc.clf.classes_]
    grp_names = [T.GROUP_NAMES[i] for i in mg.clf.classes_]
    n = len(ycls)
    group = model_json("stroke_group_v0", head_json(mg.clf, grp_names), head_json(mg.side_clf, F.SIDES), disp_g, digest, eval_note, n,
                       dict(kind="stroke intensity group", class_to_group={c: T.GROUP_NAMES[g] for c, g in T.GROUPS.items()}))
    # The class model's side labels, features and seed equal the group model's, so its side head is the same trees: not repeated.
    klass = model_json("stroke_class_v0", head_json(mc.clf, cls_names), None, disp_c, digest, eval_note, n,
                       dict(kind="stroke type (10 classes, experimental guess)", side_from="stroke_group_v0.json"))
    dump(group, out / "stroke_group_v0.trees.json")
    dump(klass, out / "stroke_class_v0.trees.json")

    # parity of the pure-Python evaluator against sklearn on every drill swing
    feats = F.full_features_batch(d["x"])[:, F.ORIENTATION_FREE_IDX]
    pc_s, ps_s = mc.predict(d["x"])
    pg_s, _ = mg.predict(d["x"])
    ids_c, ids_g = list(mc.clf.classes_), list(mg.clf.classes_)
    err = dict(
        class_=max(np.abs(np.array(evaluate(klass, f)) - pc_s[i, ids_c]).max() for i, f in enumerate(feats)),
        group=max(np.abs(np.array(evaluate(group, f)) - pg_s[i, ids_g]).max() for i, f in enumerate(feats)),
        side=max(np.abs(np.array(evaluate(group["side"], f)) - ps_s[i]).max() for i, f in enumerate(feats)))
    assert max(err.values()) <= 1e-6, err

    # test vectors: 20 swings (the same ones as test_vectors_v0.npz), the 4 channels the features read, expected features/probs
    pick = np.sort(np.random.default_rng(seed).choice(len(d["x"]), 20, replace=False))
    vec = []
    for i in pick:
        w = d["x"][i].copy()
        w[:, 6], w[:, 7] = np.round(w[:, 6], 2), np.round(w[:, 7], 4)  # quantised to keep the file small; features come from these values
        fq = F.full_features(w)[F.ORIENTATION_FREE_IDX]
        vec.append(dict(w=[f32(v) for v in w[:, 6]], a=[f32(v) for v in w[:, 7]],
                        sat_gyro=[int(j) for j in np.nonzero(w[:, 8])[0]], sat_acc=[int(j) for j in np.nonzero(w[:, 9])[0]],
                        features=[f32(v) for v in fq],
                        group=evaluate(group, fq), cls=evaluate(klass, fq), side=evaluate(group["side"], fq)))
    dump(dict(schema="volante-trees-vectors/1", features_version=F.FEATURES_VERSION, group_classes=grp_names, class_classes=cls_names,
              note="w, a: |w| (dps) and |a| (g) on the 2 ms grid, 500 samples, peak at index 300; sat_*: indices of saturated samples",
              vectors=vec, window_fixture=window_fixture()), out / "test_vectors_trees_v0.json")
    return dict(parity=err, display_class=disp_c, display_group=disp_g, mode=mode,
                sizes={p.name: p.stat().st_size for p in sorted(out.glob("*trees*.json"))})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "Datasets" / "oso-strokes")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=T.REPO / "models" / "strokes")
    a = ap.parse_args(argv)
    npz = a.root / "processed" / "swings_v1.npz"
    allv = T.load_labelled(npz)
    d = T.subset(allv, allv["context"] == "drill")
    s = export_all(d, a.out, a.seed, T.data_hash(a.root, npz))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
