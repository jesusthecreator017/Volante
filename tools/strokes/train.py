"""Phase 16.4a part 2: train, evaluate (leave-one-session-out) and export the v0 personal stroke model.

    tools/.venv/bin/python tools/strokes/train.py                          # all variants, auto eval, report only
    tools/.venv/bin/python tools/strokes/train.py --final                  # + train the winner on all drill swings and export
    ... --candidate a|a_of|b|b30|both  --eval auto|loso|block|lopo|none  --seed N  --root ~/Datasets/oso-strokes

Variants: a = GBM on all features, a_of = GBM on orientation-free features only, b = 1D-CNN (mount-rotation augmentation +-10 deg),
b30 = same with +-30 deg. Each is also evaluated as a 4-way "power group" model (not_a_hit / power / mid / touch).
Training uses `drill` sessions only (rally sessions have approximate block labels); rally swings with a review label are a separate
real-play test set. Eval auto: leave-one-session-out when >= 2 drill sessions, else grouped K-fold over drill blocks (within-session,
optimistic).
Retrain after reviewing labels: tools/strokes/build_dataset.py, then train.py --final (one command each; the build applies labels/*.csv).
Only status == labelled swings are used. Reports go to ROOT/reports/, the model to models/strokes/ (small, no raw data)."""
import argparse
import datetime
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as E  # noqa: E402
import features as F  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
NC = len(F.CLASSES)
PEAK = int(round(F.PRE_S * F.RATE_HZ))  # nominal |w| peak index in a window
MODEL_VERSION = "v0"
LIM_DPS, LIM_G = 2000.0, 16.0  # MPU-6500 full scale
# Coarse effort groups for the 4-way model (not_a_hit stays its own class)
GROUPS = {"not_a_hit": 0, "smash": 1, "clear": 1, "drive": 2, "drop": 2, "lift": 2, "net_kill": 2, "serve_long": 2,
          "net_shot": 3, "block_push": 3, "serve_short": 3}
GROUP_NAMES = ["not_a_hit", "power", "mid", "touch"]
GROUP_OF = np.array([GROUPS[c] for c in F.CLASSES])


# ---------------------------------------------------------------- data
def load_labelled(npz):
    d = np.load(npz, allow_pickle=False)
    m = d["status"] == "labelled"
    ctx = d["context"][m] if "context" in d.files else np.array(["drill"] * int(m.sum()))
    return dict(x=d["x"][m], y=d["class_id"][m].astype(int), side=d["side"][m].astype(int), session=d["session"][m],
                player=d["player"][m], swing_id=d["swing_id"][m], context=ctx, source=d["source"][m], block=d["block"][m].astype(int))


def subset(d, m):
    return {k: v[m] for k, v in d.items()}


def data_hash(root, npz):
    h = hashlib.sha256(Path(npz).read_bytes())
    for p in sorted((Path(root) / "labels").glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def group_folds(groups):
    """Leave-one-group-out: [(train_idx, test_idx)], a group (session or player) never on both sides."""
    groups = np.asarray(groups)
    u = sorted(set(groups.tolist()))
    if len(u) < 2:
        raise SystemExit(f"need >= 2 groups to cross-validate, got {u}")
    return [(np.nonzero(groups != g)[0], np.nonzero(groups == g)[0]) for g in u], u


def block_folds(y, session, block, k=5, seed=0):
    """Grouped K-fold over (session, block): a block is never split between train and test (StratifiedGroupKFold keeps the class
    mix similar across folds). Within-session, so optimistic. -> ([(train_idx, test_idx)], names)"""
    from sklearn.model_selection import StratifiedGroupKFold
    groups = np.array([f"{a}/{b}" for a, b in zip(session, block)])
    folds = list(StratifiedGroupKFold(k, shuffle=True, random_state=seed).split(np.zeros(len(y)), y, groups))
    return folds, [f"blocks-{i}" for i in range(len(folds))]


def stratified_val(y, rng, frac=0.15):
    """-> (train_idx, val_idx); classes with < 4 swings stay in train."""
    val = []
    for c in np.unique(y):
        idx = np.nonzero(y == c)[0]
        if len(idx) >= 4:
            val += list(rng.choice(idx, max(1, int(round(frac * len(idx)))), replace=False))
    val = np.array(sorted(val), int)
    return np.setdiff1d(np.arange(len(y)), val), val


def balanced_weights(y, n=NC):
    cnt = np.bincount(y, minlength=n).astype(float)
    k = (cnt > 0).sum()
    return np.where(cnt > 0, len(y) / (k * np.maximum(cnt, 1)), 0.0)


# ---------------------------------------------------------------- candidate A
class GBM:
    name = "a"

    def __init__(self, max_iter=200, cols=None, nc=NC, **_):
        self.max_iter, self.nc = max_iter, nc
        self.cols = list(range(F.N_FEATURES)) if cols is None else list(cols)

    def _f(self, x):
        return F.full_features_batch(x)[:, self.cols]

    def _hgb(self, seed):
        from sklearn.ensemble import HistGradientBoostingClassifier
        return HistGradientBoostingClassifier(max_iter=self.max_iter, learning_rate=0.08, max_leaf_nodes=15, l2_regularization=1.0,
                                              class_weight="balanced", early_stopping=False, random_state=seed)

    def fit(self, x, y, side, seed=0):
        f = self._f(x)
        self.clf = self._hgb(seed).fit(f, y)
        s = side >= 0
        self.side_clf = self._hgb(seed).fit(f[s], side[s]) if len(np.unique(side[s])) == 2 else None
        return self

    def predict(self, x):
        f = self._f(x)
        pc = np.zeros((len(x), self.nc))
        pc[:, self.clf.classes_] = self.clf.predict_proba(f)
        ps = self.side_clf.predict_proba(f) if self.side_clf else np.full((len(x), 2), 0.5)
        return pc, ps


# ---------------------------------------------------------------- candidate B
def augment(x, rng, rot_deg=10.0):
    """x float32 [B, WINDOW_N, 10] (raw units) -> augmented copy: time warp +-10 % about the peak, gain +-15 % (acc and gyro
    separately; 30 % of swings get an extra up-to-1.4x gyro gain so clipping is seen), a random mount rotation within +-rot_deg applied to
    the acc and gyro axes, then the sensor limits (+-2000 dps, +-16 g) with the saturation flags set where they bind; |w| and |a| are
    recomputed."""
    n = x.shape[1]
    out = np.empty_like(x)
    ar = np.arange(n)
    for i in range(len(x)):
        s = rng.uniform(0.9, 1.1)
        src = np.clip(PEAK + (ar - PEAK) * s, 0, n - 1)
        i0 = np.minimum(src.astype(int), n - 2)
        fr = (src - i0)[:, None]
        out[i, :, :8] = x[i, i0, :8] * (1 - fr) + x[i, i0 + 1, :8] * fr
        out[i, :, 8:] = x[i, np.rint(src).astype(int), 8:]
        ga, gw = rng.uniform(0.85, 1.15, 2)
        if rng.random() < 0.3:
            gw *= rng.uniform(1.0, 1.4)
        out[i, :, 0:3] *= ga
        out[i, :, 3:6] *= gw
        ax = rng.normal(size=3)
        ax /= np.linalg.norm(ax)
        th = np.radians(rng.uniform(-rot_deg, rot_deg))
        K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
        R = np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K
        out[i, :, 0:3] = out[i, :, 0:3] @ R.T
        out[i, :, 3:6] = out[i, :, 3:6] @ R.T
    np.clip(out[:, :, 3:6], -LIM_DPS, LIM_DPS, out=out[:, :, 3:6])
    np.clip(out[:, :, 0:3], -LIM_G, LIM_G, out=out[:, :, 0:3])
    out[:, :, 8] = np.maximum(out[:, :, 8], (np.abs(out[:, :, 3:6]) >= LIM_DPS - 1e-3).any(-1))
    out[:, :, 9] = np.maximum(out[:, :, 9], (np.abs(out[:, :, 0:3]) >= LIM_G - 1e-3).any(-1))
    out[:, :, 6] = np.linalg.norm(out[:, :, 3:6], axis=-1)
    out[:, :, 7] = np.linalg.norm(out[:, :, 0:3], axis=-1)
    return out


def make_net(mean, std, widths=(32, 48, 64, 96), nc=NC):
    import torch
    from torch import nn

    class Net(nn.Module):
        """raw window [B, 500, 10] -> (class logits [B, 11], side logits [B, 2]); normalisation is part of the graph."""
        def __init__(self):
            super().__init__()
            self.register_buffer("mean", torch.tensor(mean, dtype=torch.float32).view(1, 1, -1))
            self.register_buffer("std", torch.tensor(std, dtype=torch.float32).view(1, 1, -1))
            layers, c = [], len(F.CHANNELS)
            for i, w in enumerate(widths):
                layers += [nn.Conv1d(c, w, 7 if i == 0 else 5 if i < 3 else 3, stride=2, padding=3 if i == 0 else 2 if i < 3 else 1),
                           nn.BatchNorm1d(w), nn.ReLU()]
                c = w
            self.conv = nn.Sequential(*layers)
            self.drop = nn.Dropout(0.3)
            self.cls = nn.Linear(2 * c, nc)
            self.side = nn.Linear(2 * c, 2)

        def forward(self, x):
            h = self.conv(((x - self.mean) / self.std).transpose(1, 2))
            h = self.drop(torch.cat([h.mean(-1), h.amax(-1)], 1))
            return self.cls(h), self.side(h)

    return Net()


def norm_stats(x):
    mean = np.zeros(x.shape[2], np.float32)
    std = np.ones(x.shape[2], np.float32)
    mean[:8] = x[:, :, :8].reshape(-1, 8).mean(0)
    std[:8] = x[:, :, :8].reshape(-1, 8).std(0) + 1e-6  # saturation flags (8, 9) stay raw
    return mean, std


class CNN:
    name = "b"

    def __init__(self, epochs=80, patience=12, device=None, widths=(32, 48, 64, 96), batch=32, rot_deg=10.0, nc=NC, **_):
        self.epochs, self.patience, self.widths, self.batch, self.device = epochs, patience, widths, batch, device
        self.rot_deg, self.nc = rot_deg, nc

    def fit(self, x, y, side, seed=0):
        import torch
        from torch import nn
        dev = self.device or ("mps" if torch.backends.mps.is_available() else "cpu")
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        tr, va = stratified_val(y, rng)
        if len(va) == 0:
            tr, va = np.arange(len(y)), np.arange(len(y))
        self.mean, self.std = norm_stats(x[tr])
        net = make_net(self.mean, self.std, self.widths, self.nc).to(dev)
        opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-3)
        wcls = torch.tensor(balanced_weights(y[tr], self.nc), dtype=torch.float32, device=dev)
        ce_c = nn.CrossEntropyLoss(weight=wcls)
        ce_s = nn.CrossEntropyLoss(ignore_index=-1)

        def loss(xb, yb, sb, train):
            xb = torch.from_numpy(np.ascontiguousarray(xb)).to(dev)
            lc, ls = net(xb)
            yb_t, sb_t = torch.from_numpy(yb).to(dev), torch.from_numpy(sb).to(dev)
            lo = ce_c(lc, yb_t)
            if (sb_t >= 0).any():
                lo = lo + 0.5 * ce_s(ls, sb_t)
            return lo

        best, best_state, bad = np.inf, None, 0
        for ep in range(self.epochs):
            net.train()
            order = rng.permutation(tr)
            for i in range(0, len(order), self.batch):
                b = order[i:i + self.batch]
                if len(b) < 2:
                    continue
                opt.zero_grad()
                loss(augment(x[b], rng, self.rot_deg), y[b], side[b], True).backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                v = float(loss(x[va], y[va], side[va], False))
            if v < best - 1e-4:
                best, bad, self.best_epoch = v, 0, ep
                best_state = {k: t.detach().clone() for k, t in net.state_dict().items()}
            else:
                bad += 1
                if bad >= self.patience:
                    break
        net.load_state_dict(best_state)
        self.net = net.to("cpu").eval()
        self.dev = dev
        return self

    def predict(self, x):
        import torch
        with torch.no_grad():
            lc, ls = self.net(torch.from_numpy(np.ascontiguousarray(x, np.float32)))
            return torch.softmax(lc, 1).numpy().astype(np.float64), torch.softmax(ls, 1).numpy().astype(np.float64)


def n_params(net):
    return sum(p.numel() for p in net.parameters())


# ---------------------------------------------------------------- export
def export_cnn(model, path):
    import torch
    from torch import nn

    class Probs(nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, x):
            lc, ls = self.net(x)
            return torch.softmax(lc, 1), torch.softmax(ls, 1)

    wrap = Probs(model.net).eval()
    torch.onnx.export(wrap, (torch.zeros(1, F.WINDOW_N, len(F.CHANNELS)),), str(path), input_names=["window"],
                      output_names=["probabilities", "side_probabilities"], opset_version=17, dynamo=False,
                      dynamic_axes={"window": {0: "batch"}, "probabilities": {0: "batch"}, "side_probabilities": {0: "batch"}})


def export_gbm(model, path):
    """One ONNX graph, input `features` [N, len(model.cols)] (features.full_features(window)[cols]), both heads. The class output has
    one column per class seen in training (model.clf.classes_)."""
    import onnx
    from onnx import compose
    from skl2onnx import convert_sklearn
    from skl2onnx.common import tree_ensemble as te
    from skl2onnx.common.data_types import FloatTensorType

    if not getattr(te.add_node, "_oso_fix", False):
        orig = te.add_node

        def add_node(*a, **k):
            # skl2onnx 1.20 passes a numpy bool where onnx 1.23 wants an int
            if "nodes_missing_value_tracks_true" in k:
                k["nodes_missing_value_tracks_true"] = int(k["nodes_missing_value_tracks_true"])
            # sklearn splits `x <= t` with t in float64; the graph compares float32. Round t DOWN to float32 so the two agree
            # for every float32 x (plain rounding flips x just above t when t's float32 neighbour equals x).
            if a[6] != "LEAF":
                t32 = np.float32(a[7])
                a = (*a[:7], t32 if float(t32) <= float(a[7]) else np.nextafter(t32, np.float32(-np.inf)), *a[8:])
            return orig(*a, **k)

        add_node._oso_fix = True
        te.add_node = add_node

    def conv(clf):
        return convert_sklearn(clf, initial_types=[("features", FloatTensorType([None, len(model.cols)]))],
                               options={id(clf): {"zipmap": False}}, target_opset={"": 17, "ai.onnx.ml": 3})

    m1, m2 = conv(model.clf), compose.add_prefix(conv(model.side_clf), "side_")
    g = compose.merge_models(m1, m2, io_map=[])
    for n in g.graph.node:
        n.input[:] = ["features" if i == "side_features" else i for i in n.input]
    keep = [i for i in g.graph.input if i.name != "side_features"]
    del g.graph.input[:]
    g.graph.input.extend(keep)
    keep = [o for o in g.graph.output if o.name in ("probabilities", "side_probabilities")]
    del g.graph.output[:]
    g.graph.output.extend(keep)
    onnx.checker.check_model(g)
    onnx.save(g, str(path))


def ort_probs(path, inp):
    import onnxruntime as ort
    s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    pc, ps = s.run(["probabilities", "side_probabilities"], {s.get_inputs()[0].name: inp.astype(np.float32)})
    return pc, ps


REQUIRED_META = ["version", "created", "candidate", "classes", "sides", "window", "channels", "input", "outputs", "preprocessing",
                 "mount_convention", "uncertainty_threshold", "training_data_sha256", "metrics", "n_swings"]


def check_meta(meta):
    missing = [k for k in REQUIRED_META if k not in meta]
    assert not missing, f"model json lacks {missing}"
    assert meta["classes"] == (GROUP_NAMES if "class_to_group" in meta else F.CLASSES) and meta["channels"] == F.CHANNELS
    assert 0.0 <= meta["uncertainty_threshold"] <= 1.0


def export_model(model, cand, d, thr, summary, digest, outdir, seed, eval_note="", group=False):
    """Train-on-all model -> ONNX + json + test vectors, parity-checked on every training swing. Returns the parity numbers."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"stroke_{'group' if group else 'model'}_{MODEL_VERSION}"
    nc = len(GROUP_NAMES) if group else NC
    y_true = GROUP_OF[d["y"]] if group else d["y"]
    onnx_path = outdir / f"{stem}.onnx"
    cnn = is_cnn(cand)
    (export_cnn if cnn else export_gbm)(model, onnx_path)
    ids = list(range(nc)) if cnn else [int(c) for c in model.clf.classes_]
    inp = d["x"] if cnn else F.full_features_batch(d["x"])[:, model.cols]
    pc, ps = model.predict(d["x"])
    oc, os_ = ort_probs(onnx_path, inp)
    parity = dict(max_abs_class=float(np.abs(oc - pc[:, ids]).max()), max_abs_side=float(np.abs(os_ - ps).max()), n=int(len(inp)))
    rng = np.random.default_rng(seed)
    pick = np.sort(rng.choice(len(d["x"]), 20, replace=False))
    vec = dict(window=d["x"][pick].astype(np.float32)) if not group else dict(window_same_as="test_vectors_v0.npz (same 20 swings: same seed and data)")
    np.savez_compressed(outdir / (f"test_vectors_group_{MODEL_VERSION}.npz" if group else f"test_vectors_{MODEL_VERSION}.npz"), **vec,
                        inputs=inp[pick].astype(np.float32), probabilities=oc[pick].astype(np.float32),
                        side_probabilities=os_[pick].astype(np.float32), class_id=y_true[pick], side=d["side"][pick])
    if cnn:
        pre = dict(normalisation="applied inside the ONNX graph: (x - mean) / std per channel", mean=model.mean.tolist(),
                   std=model.std.tolist(), note="saturation channels have mean 0, std 1 (raw)")
        inp_spec = dict(name="window", shape=["batch", F.WINDOW_N, len(F.CHANNELS)], dtype="float32",
                        note="raw window from features.window(): units g, dps, flags")
    else:
        names = [F.FEATURE_NAMES[i] for i in model.cols]
        pre = dict(features_version=F.FEATURES_VERSION, feature_names=names, bands_hz=F.BANDS_HZ,
                   note="features.full_features(window)[feature_names order]; features.py is part of the model contract; no scaling")
        inp_spec = dict(name="features", shape=["batch", len(names)], dtype="float32", note="the listed features of the window")
    meta = dict(
        version=MODEL_VERSION, created=datetime.date.today().isoformat(), candidate=cand,
        architecture=f"1D-CNN, 4 strided conv blocks, mean+max pool, class and side heads, mount-rotation augmentation +-{model.rot_deg:g} deg"
        if cnn else "HistGradientBoosting (class head + side head) on " + ("orientation-free features" if cand == "a_of" else "all features"),
        n_params=n_params(model.net) if cnn else None,
        classes=GROUP_NAMES if group else F.CLASSES, sides=F.SIDES, sideless_classes=sorted(F.SIDELESS),
        output_class_ids=ids, output_class_note="probabilities has one column per id in output_class_ids (classes absent from training are not modelled)",
        **(dict(kind="stroke intensity group", class_to_group={c: GROUP_NAMES[g] for c, g in GROUPS.items()},
                groups_not_modelled=[GROUP_NAMES[g] for g in range(nc) if g not in ids],
                note="not_a_hit has no drill training data, so it is not modelled: this model never outputs it")
           if group else dict(status="experimental: do not use in reports (one drill session, 1-2 blocks per class)")),
        window=dict(pre_s=F.PRE_S, post_s=F.POST_S, rate_hz=F.RATE_HZ, n=F.WINDOW_N, centre="gyro |w| peak at index %d" % PEAK),
        channels=F.CHANNELS, input=inp_spec, outputs=dict(probabilities=f"[batch, {len(ids)}] softmax over output_class_ids",
                                                            side_probabilities="[batch, 2] FH, BH (ignore for not_a_hit)"),
        preprocessing=pre,
        mount_convention="racquet frame: +Y shaft toward the tip, +Z string-face normal, +X = Y x Z; acc in g, gyro in dps",
        uncertainty_threshold=float(thr["threshold"]), uncertainty_rule="max class probability < threshold -> report 'unclassified'",
        threshold_met_targets=bool(thr["met"]), threshold_coverage=thr["coverage"], threshold_accuracy=thr["accuracy"],
        training_data_sha256=digest, training_context="drill", n_swings=int(len(d["y"])), players=sorted(set(d["player"].tolist())),
        sessions=sorted(set(d["session"].tolist())), seed=seed,
        metrics=dict(eval=eval_note, mean_macro_f1=summary["mean_macro_f1"], mean_accuracy=summary["mean_accuracy"],
                     mean_side_accuracy=summary["mean_side_accuracy"], mean_not_a_hit_precision=summary["mean_not_a_hit_precision"]),
        parity=parity,
    )
    check_meta(meta)
    (outdir / f"{stem}.json").write_text(json.dumps(meta, indent=2) + "\n")
    return parity, meta


# ---------------------------------------------------------------- driver
VARIANTS = {
    "a": lambda nc: GBM(nc=nc),
    "a_of": lambda nc: GBM(cols=F.ORIENTATION_FREE_IDX, nc=nc),
    "b": lambda nc: CNN(rot_deg=10.0, nc=nc),
    "b30": lambda nc: CNN(rot_deg=30.0, nc=nc),
}


def is_cnn(v):
    return v.startswith("b")


def run_eval(make, d, folds_idx, names, seed, task="11"):
    """task '11': the 11 classes; '4': not_a_hit + 3 effort groups (GROUP_NAMES)."""
    y_all = d["y"] if task == "11" else GROUP_OF[d["y"]]
    classes = F.CLASSES if task == "11" else GROUP_NAMES
    folds, t0 = [], time.time()
    for k, ((tr, te), g) in enumerate(zip(folds_idx, names)):
        assert not set(tr) & set(te)
        m = make(len(classes)).fit(d["x"][tr], y_all[tr], d["side"][tr], seed + k)
        pc, ps = m.predict(d["x"][te])
        folds.append(dict(fold=str(g), y=y_all[te], side=d["side"][te], pc=pc, ps=ps))
        print(f"  [{task}] fold {g}: n={len(te)} acc={(pc.argmax(1) == y_all[te]).mean():.3f}", flush=True)
    cat = lambda k: np.concatenate([f[k] for f in folds])  # noqa: E731
    out = E.summarise(folds, cat("y"), cat("side"), cat("pc"), cat("ps"), classes)
    out["train_seconds"] = time.time() - t0
    return out


def choose_folds(d, mode):
    """-> (folds, names, mode actually used). auto: leave-one-session-out with >= 2 drill sessions, else grouped K-fold over blocks."""
    n_sessions = len(set(d["session"].tolist()))
    if mode == "auto":
        mode = "loso" if n_sessions >= 2 else "block"
    if mode == "block":
        folds, names = block_folds(d["y"], d["session"], d["block"])
    else:
        folds, names = group_folds(d["session"] if mode == "loso" else d["player"])
    return folds, names, mode


EVAL_NOTE = {"loso": "leave-one-session-out over drill sessions",
             "lopo": "leave-one-player-out over drill sessions",
             "block": "WITHIN-SESSION grouped 5-fold over drill blocks (a block never split): optimistic, not a generalisation estimate"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=Path.home() / "Datasets" / "oso-strokes")
    ap.add_argument("--candidate", choices=[*VARIANTS, "both"], default="both")
    ap.add_argument("--eval", choices=["auto", "loso", "block", "lopo", "none"], default="auto")
    ap.add_argument("--final", action="store_true", help="train the winner on all drill swings and export to models/strokes/")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=REPO / "models" / "strokes")
    a = ap.parse_args(argv)
    npz = a.root / "processed" / "swings_v1.npz"
    if not npz.exists():
        raise SystemExit(f"{npz} missing: run tools/strokes/build_dataset.py first")
    allv = load_labelled(npz)
    d = subset(allv, allv["context"] == "drill")
    real = subset(allv, (allv["context"] == "rally") & (allv["source"] == "review"))
    n_rally = int((allv["context"] == "rally").sum())
    print(f"{len(allv['y'])} labelled swings: {len(d['y'])} drill (training), {n_rally} rally (block labels approximate: not used), "
          f"{len(real['y'])} rally swings with a review label (real-play test set)")
    if len(real["y"]) == 0:
        print("  hint: review rally swings in the review tool (docs/strokes/review.md) to build the real-play test set")
    if len(d["y"]) == 0:
        raise SystemExit("no labelled drill swings")
    print(f"  drill sessions {sorted(set(d['session'].tolist()))}, classes present {sorted({F.CLASSES[i] for i in d['y']})}")
    cands = list(VARIANTS) if a.candidate == "both" else [a.candidate]
    rep, repo_json = a.root / "reports", REPO / "out" / "strokes" / "eval_v0.json"
    results, mode = {}, None
    if a.eval != "none":
        folds, names, mode = choose_folds(d, a.eval)
        print(f"eval: {mode}: {EVAL_NOTE[mode]}")
        for c in cands:
            print(f"variant {c}")
            results[c] = run_eval(VARIANTS[c], d, folds, names, a.seed)
            results[c]["group4"] = run_eval(VARIANTS[c], d, folds, names, a.seed, "4")
            if len(real["y"]):
                m = VARIANTS[c](NC).fit(d["x"], d["y"], d["side"], a.seed)
                pc, ps = m.predict(real["x"])
                results[c]["real_play"] = E.metrics(real["y"], real["side"], pc, ps)
            r = results[c]
            print(f"  11-class: mean macro-F1 {r['mean_macro_f1']:.3f} acc {r['mean_accuracy']:.3f} side {r['mean_side_accuracy']:.3f} "
                  f"| 4-group: macro-F1 {r['group4']['mean_macro_f1']:.3f} acc {r['group4']['mean_accuracy']:.3f} "
                  f"| train {r['train_seconds']:.0f}s")
        digest = data_hash(a.root, npz)
        payload = dict(created=datetime.datetime.now().isoformat(timespec="seconds"), eval=mode, eval_note=EVAL_NOTE[mode], seed=a.seed,
                       training_data_sha256=digest, n_drill=len(d["y"]), n_rally=n_rally, n_real_play=len(real["y"]),
                       winner=max(results, key=lambda c: results[c]["mean_macro_f1"]), results=results)
        for p in (rep / "eval_v0.json", repo_json):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=1))
        E.write_report(results, rep, EVAL_NOTE[mode])
        print(f"winner by mean macro-F1 (11 classes): {payload['winner']}; report in {rep}")
    elif a.final:
        saved = json.loads((rep / "eval_v0.json").read_text())
        results, mode = {c: v for c, v in saved["results"].items() if c in cands}, saved["eval"]
    if a.final:
        win = max(results, key=lambda c: results[c]["mean_macro_f1"])
        print(f"final: training variant {win} on all {len(d['y'])} drill swings")
        t0 = time.time()
        model = VARIANTS[win](NC).fit(d["x"], d["y"], d["side"], a.seed)
        print(f"  trained in {time.time() - t0:.1f}s")
        parity, meta = export_model(model, win, d, results[win]["threshold"], results[win], data_hash(a.root, npz), a.out, a.seed,
                                    EVAL_NOTE[mode])
        print(f"  parity (onnxruntime vs python) max abs diff: class {parity['max_abs_class']:.2e}, side {parity['max_abs_side']:.2e}")
        gres = results.get("a_of")
        if gres is not None:
            gm = VARIANTS["a_of"](len(GROUP_NAMES)).fit(d["x"], GROUP_OF[d["y"]], d["side"], a.seed)
            gp, gmeta = export_model(gm, "a_of", d, gres["group4"]["threshold"], gres["group4"], data_hash(a.root, npz), a.out, a.seed,
                                     EVAL_NOTE[mode], group=True)
            print(f"  group model (a_of): parity class {gp['max_abs_class']:.2e}, side {gp['max_abs_side']:.2e}, groups {gmeta['output_class_ids']}")
        for f in sorted(a.out.glob("*v0*")):
            print(f"  {f.relative_to(REPO) if f.is_relative_to(REPO) else f}: {f.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
