"""Phase 16.4 evaluation: per-fold and mean metrics, the uncertainty threshold, and the report (JSON + HTML/PNG).
Inputs are plain arrays: y (class ids), side (0/1/-1), p_class [N,11], p_side [N,2]."""
import html
from pathlib import Path

import numpy as np

from features import CLASSES

TARGET_PRECISION = 0.90
MIN_COVERAGE = 0.05  # a threshold that keeps < 5 % of swings is not a usable "met"


def _nah(classes):
    return classes.index("not_a_hit") if "not_a_hit" in classes else -1


def _prf(y, pred, labels):
    out = {}
    for c in labels:
        tp = int(((pred == c) & (y == c)).sum())
        fp = int(((pred == c) & (y != c)).sum())
        fn = int(((pred != c) & (y == c)).sum())
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        out[c] = dict(precision=p, recall=r, f1=2 * p * r / (p + r) if p + r else 0.0, support=int((y == c).sum()))
    return out


def metrics(y, side, pc, ps, classes=CLASSES):
    """One fold (or pooled) -> dict. macro_f1 averages classes with support > 0 in `y` ('present'); absent ones are listed."""
    NAH, nc = _nah(classes), len(classes)
    pred = pc.argmax(1)
    prf = _prf(y, pred, range(nc))
    present = [c for c in range(nc) if prf[c]["support"] > 0]
    has_side = (side >= 0) & (y != NAH)
    n_pred_nah = int((pred == NAH).sum())
    conf = np.zeros((nc, nc), int)
    np.add.at(conf, (y, pred), 1)
    return dict(
        n=int(len(y)),
        macro_f1=float(np.mean([prf[c]["f1"] for c in present])),
        accuracy=float((pred == y).mean()),
        classes={classes[c]: prf[c] for c in range(nc)},
        absent_classes=[classes[c] for c in range(nc) if c not in present],
        side_accuracy=float((ps.argmax(1)[has_side] == side[has_side]).mean()) if has_side.any() else None,
        side_n=int(has_side.sum()),
        not_a_hit_precision=float(((pred == NAH) & (y == NAH)).sum() / n_pred_nah) if n_pred_nah else None,
        not_a_hit_predicted=n_pred_nah,
        confusion=conf.tolist(),
    )


def coverage_curve(y, pc, thresholds, classes=CLASSES):
    NAH = _nah(classes)
    conf = pc.max(1)
    pred = pc.argmax(1)
    rows = []
    for t in thresholds:
        keep = conf >= t
        n = int(keep.sum())
        nah = keep & (pred == NAH)
        rows.append(dict(threshold=float(t), coverage=float(keep.mean()), accuracy=float((pred[keep] == y[keep]).mean()) if n else None,
                         not_a_hit_precision=float((y[nah] == NAH).mean()) if nah.any() else None))
    return rows


def choose_threshold(y, pc, target=TARGET_PRECISION, classes=CLASSES):
    """Smallest max-probability threshold (largest coverage) at which the accepted swings have accuracy >= target AND
    not_a_hit precision >= target (vacuous when nothing accepted is predicted not_a_hit), keeping >= MIN_COVERAGE. 'met' False -> falls back to the
    threshold with the best accuracy among those keeping >= 20 % coverage."""
    cand = np.unique(np.round(pc.max(1), 4))
    rows = coverage_curve(y, pc, cand, classes)
    ok = [r for r in rows if r["coverage"] >= MIN_COVERAGE and r["accuracy"] is not None and r["accuracy"] >= target
          and (r["not_a_hit_precision"] is None or r["not_a_hit_precision"] >= target)]
    if ok:
        return dict(met=True, **min(ok, key=lambda r: r["threshold"]))
    big = [r for r in rows if r["coverage"] >= 0.2 and r["accuracy"] is not None] or [r for r in rows if r["accuracy"] is not None]
    return dict(met=False, **max(big, key=lambda r: r["accuracy"]))


def summarise(folds, pooled_y, pooled_side, pooled_pc, pooled_ps, classes=CLASSES):
    """folds: list of dict(fold, y, side, pc, ps) -> the JSON-able result for one candidate."""
    per = []
    for f in folds:
        m = metrics(f["y"], f["side"], f["pc"], f["ps"], classes)
        m["fold"] = f["fold"]
        per.append(m)
    mean = lambda k: float(np.mean([m[k] for m in per]))  # noqa: E731
    sides = [m["side_accuracy"] for m in per if m["side_accuracy"] is not None]
    nah = [m["not_a_hit_precision"] for m in per if m["not_a_hit_precision"] is not None]
    pooled = metrics(pooled_y, pooled_side, pooled_pc, pooled_ps, classes)
    thr = choose_threshold(pooled_y, pooled_pc, classes=classes)
    return dict(
        folds=per,
        mean_macro_f1=mean("macro_f1"), mean_accuracy=mean("accuracy"),
        mean_side_accuracy=float(np.mean(sides)) if sides else None,
        mean_not_a_hit_precision=float(np.mean(nah)) if nah else None,
        pooled=pooled, threshold=thr,
        coverage_curve=coverage_curve(pooled_y, pooled_pc, [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], classes),
    )


def write_report(results, outdir, title="leave-one-session-out"):
    """results: {candidate: summarise(...)} -> confusion matrices + per-class bars (PNG) and a small HTML page in outdir."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    names = list(results)
    fig, ax = plt.subplots(1, len(names), figsize=(6.5 * len(names), 6), squeeze=False)
    for a, n in zip(ax[0], names):
        cm = np.array(results[n]["pooled"]["confusion"], float)
        a.imshow(cm / np.maximum(cm.sum(1, keepdims=True), 1), cmap="Blues", vmin=0, vmax=1)
        for i in range(len(CLASSES)):
            for j in range(len(CLASSES)):
                if cm[i, j]:
                    a.text(j, i, int(cm[i, j]), ha="center", va="center", fontsize=7)
        a.set_xticks(range(len(CLASSES)), CLASSES, rotation=90)
        a.set_yticks(range(len(CLASSES)), CLASSES)
        a.set_xlabel("predicted")
        a.set_ylabel("true")
        a.set_title(f"candidate {n}: pooled LOSO confusion (row-normalised)")
    fig.tight_layout()
    fig.savefig(outdir / "confusion_v0.png", dpi=110)
    plt.close(fig)
    fig, a = plt.subplots(figsize=(10, 4))
    w = 0.8 / len(names)
    for k, n in enumerate(names):
        a.bar(np.arange(len(CLASSES)) + k * w, [results[n]["pooled"]["classes"][c]["f1"] for c in CLASSES], w, label=f"candidate {n}")
    a.set_xticks(np.arange(len(CLASSES)) + 0.4 - w / 2, CLASSES, rotation=60)
    a.set_ylabel("F1 (pooled LOSO)")
    a.legend()
    fig.tight_layout()
    fig.savefig(outdir / "per_class_f1_v0.png", dpi=110)
    plt.close(fig)
    f3 = lambda v: "-" if v is None else format(v, ".3f")  # noqa: E731
    allr = [(n, r) for n, r in results.items()] + [(f"{n} (4 groups)", r["group4"]) for n, r in results.items() if "group4" in r]
    rows = "".join(
        f"<tr><td>{n}</td><td>{f3(r['mean_macro_f1'])}</td><td>{f3(r['mean_accuracy'])}</td><td>{f3(r['mean_side_accuracy'])}</td>"
        f"<td>{f3(r['mean_not_a_hit_precision'])}</td>"
        f"<td>{r['threshold']['threshold']:.3f} ({'met' if r['threshold']['met'] else 'NOT met'}): coverage {r['threshold']['coverage']:.2f}, "
        f"accuracy {r['threshold']['accuracy']:.3f}</td></tr>" for n, r in allr)
    folds = "".join(
        f"<tr><td>{n}</td><td>{html.escape(m['fold'])}</td><td>{m['n']}</td><td>{m['macro_f1']:.3f}</td><td>{m['accuracy']:.3f}</td>"
        f"<td>{html.escape(', '.join(m['absent_classes']) or '-')}</td></tr>" for n, r in allr for m in r["folds"])
    (outdir / "eval_v0.html").write_text(
        "<!doctype html><meta charset=utf-8><title>oso stroke model v0</title><style>body{font:14px sans-serif;margin:2em}"
        "td,th{border:1px solid #ccc;padding:3px 8px}table{border-collapse:collapse;margin-bottom:1em}</style>"
        f"<h1>Stroke model v0, {html.escape(title)}</h1><table><tr><th>cand</th><th>mean macro-F1</th><th>mean acc</th><th>side acc</th>"
        f"<th>not_a_hit precision</th><th>threshold</th></tr>{rows}</table>"
        f"<table><tr><th>cand</th><th>test fold</th><th>n</th><th>macro-F1</th><th>acc</th><th>classes absent from the fold</th></tr>{folds}</table>"
        '<img src="confusion_v0.png"><br><img src="per_class_f1_v0.png">')
