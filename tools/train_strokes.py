"""Train and evaluate the BFMD stroke classifier (Phase 10).

  python -I tools/train_strokes.py [--rebuild]

Features are cached in ~/Datasets/BFMD-work/features.npz, the model in stroke_hgb.joblib, the report in report.md (all outside the
repo: BFMD is non-commercial research data and nothing derived from it is redistributed). Evaluation is leave-matches-out (4-fold
GroupKFold by match). Players repeat across matches, so a player-disjoint split is not possible.
"""
import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bfmd  # noqa: E402
import stroke_features as sf  # noqa: E402

CLASSES = ["net_shot", "lift", "smash", "block", "drop", "drive", "serve", "clear", "push", "press", "net_kill", "flick_serve"]
CACHE = bfmd.WORK / "features.npz"
MODEL = bfmd.WORK / "stroke_hgb.joblib"
REPORT = bfmd.WORK / "report.md"


def build_rallies(m):
    """Yield (Rally, [labels]) per pose rally of match m, plus bbox / keypoint extent ratios."""
    hits = bfmd.load_hits(m)
    shuttle = bfmd.load_shuttle(m)
    Hm = bfmd.homography(bfmd.load_court(m))
    out, ratios = [], []
    rallies = bfmd.load_pose(m)
    for a, b, frames in rallies:
        rh = [h for h in hits if a <= h[0] <= b]
        if not rh or not frames:
            continue
        F = b - a + 1
        kp = {s: np.full((F, 17, 3), np.nan, np.float32) for s in ("top", "bottom")}
        bb = {s: np.full((F, 4), np.nan, np.float32) for s in ("top", "bottom")}
        for f, ents in frames.items():
            if not a <= f <= b:
                continue
            if len(ents) == 2:
                ents = sorted(ents, key=lambda e: e["bbox"][3])
                sides = ["top", "bottom"]
            else:
                x, _ = bfmd.to_court(Hm, [(ents[0]["bbox"][0] + ents[0]["bbox"][2]) / 2, ents[0]["bbox"][3]])
                sides = ["top" if x > 0 else "bottom"]
            for s, e in zip(sides, ents):
                k = np.array(e["keypoints"], np.float32)
                k[:, :2] *= [bfmd.W, bfmd.H]
                kp[s][f - a], bb[s][f - a] = k, e["bbox"]
                if (k[[15, 16], 2] > 0.5).all() and k[0, 2] > 0.5:
                    ratios.append((e["bbox"][3] - e["bbox"][1]) / (k[[15, 16], 1].mean() - k[0, 1]))
        sh = np.full((F, 2), np.nan, np.float32)
        for f in range(a, b + 1):
            if f in shuttle:
                sh[f - a] = shuttle[f]
        out.append((sf.Rally(kp, bb, sh, [(h[0] - a, h[2]) for h in rh], Hm), [h[1] for h in rh]))
    return out, ratios


def build_all():
    X, y, g, ratios = [], [], [], []
    for gi, m in enumerate(bfmd.matches()):
        rs, rt = build_rallies(m)
        ratios += rt
        for r, labels in rs:
            X.append(sf.hit_features(r))
            y += labels
            g += [gi] * len(labels)
        print(f"  {m[:60]}: {sum(len(l) for _, l in rs)} hits", flush=True)
    return np.concatenate(X), np.array(y), np.array(g), float(np.median(ratios))


def load_data(rebuild=False):
    bfmd.WORK.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not rebuild:
        d = np.load(CACHE)
        if list(d["names"]) == sf.FEATURE_NAMES:
            return d["X"], d["y"], d["g"], float(d["pad"])
    print("building features")
    X, y, g, pad = build_all()
    np.savez_compressed(CACHE, X=X, y=y, g=g, pad=pad, names=np.array(sf.FEATURE_NAMES))
    return X, y, g, pad


def make_model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(max_iter=250, learning_rate=0.08, max_leaf_nodes=20, l2_regularization=1.0,
                                          class_weight="balanced", early_stopping=True, random_state=0)


def evaluate(X, y, g):
    """Out-of-fold predictions for: full model, context-only model, majority class."""
    from sklearn.model_selection import GroupKFold
    pred = {k: np.empty(len(y), object) for k in ("full", "context", "majority")}
    for tr, te in GroupKFold(4).split(X, y, g):
        pred["full"][te] = make_model().fit(X[tr], y[tr]).predict(X[te])
        pred["context"][te] = make_model().fit(X[tr][:, sf.CONTEXT], y[tr]).predict(X[te][:, sf.CONTEXT])
        vals, cnt = np.unique(y[tr], return_counts=True)
        pred["majority"][te] = vals[cnt.argmax()]
    return pred


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    import joblib

    X, y, g, pad = load_data(args.rebuild)
    keep = np.isin(y, CLASSES)
    X, y, g = X[keep], y[keep], g[keep]
    print(f"{len(y)} hits, {len(set(g))} matches, bbox/keypoint-extent ratio {pad:.3f} (stroke_features.BBOX_PAD = {sf.BBOX_PAD})")
    pred = evaluate(X, y, g)
    lines = [f"# BFMD stroke classifier\n", f"{len(y)} hits from {len(set(g))} singles matches; 4-fold leave-matches-out "
             "(GroupKFold by match; players repeat across matches, no player-disjoint split). Median bbox / keypoint-extent "
             f"ratio {pad:.3f}.\n", "| model | accuracy | macro-F1 |", "|---|---|---|"]
    res = {}
    for k, p in pred.items():
        res[k] = (accuracy_score(y, p), f1_score(y, p, average="macro", labels=CLASSES, zero_division=0))
        lines.append(f"| {k} | {res[k][0]:.3f} | {res[k][1]:.3f} |")
    lines += ["", "## Per class (full model)", "```", classification_report(y, pred["full"], labels=CLASSES, zero_division=0, digits=3),
              "```", "## Per class (context-only)", "```", classification_report(y, pred["context"], labels=CLASSES, zero_division=0,
                                                                                   digits=3), "```",
              "## Confusion matrix (full; rows true, cols predicted)", "```", "      " + " ".join(f"{c[:6]:>6s}" for c in CLASSES)]
    cm = confusion_matrix(y, pred["full"], labels=CLASSES)
    lines += [f"{c[:6]:>6s}" + " ".join(f"{v:6d}" for v in row) for c, row in zip(CLASSES, cm)] + ["```"]
    model = make_model().fit(X, y)
    joblib.dump({"model": model, "features": sf.FEATURE_NAMES, "classes": list(model.classes_), "virtual_cam": bfmd.VIRTUAL_CAM,
                 "bbox_pad": sf.BBOX_PAD}, MODEL)
    REPORT.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nmodel -> {MODEL}\nreport -> {REPORT}")
    print(f"GATE macro-F1 full {res['full'][1]:.3f} > majority {res['majority'][1]:.3f}, context {res['context'][1]:.3f}: "
          f"{'PASS' if res['full'][1] > max(res['majority'][1], res['context'][1]) else 'FAIL'}; accuracy {res['full'][0]:.3f} (target 0.60)")
    return res


if __name__ == "__main__":
    main()
