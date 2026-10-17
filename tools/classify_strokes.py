"""Label the contacts of a replay.bin with the BFMD-trained stroke classifier (Phase 10).

  python tools/classify_strokes.py --replay replay.bin --model ~/Datasets/BFMD-work/stroke_hgb.joblib [-o out.bin]

The 3D joints and shuttle are re-projected through the virtual broadcast camera (tools/bfmd.py) at 30 fps so the features mean what
they meant in training. P0 is the near ("bottom", -X half) player, P1 the far ("top") one. Hits = swings rows with a contact.
The model file is BFMD-derived research data: it lives in ~/Datasets/BFMD-work, not in the repo.
"""
import argparse
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bfmd  # noqa: E402
import replay  # noqa: E402
import stroke_features as sf  # noqa: E402

# Must equal STROKE_TYPES in src/replay.rs (existing indices unchanged, BFMD-only types appended).
STROKE_TYPES = ["unknown", "serve", "clear", "smash", "drop", "drive", "lift", "net", "swing_only",
                "block", "push", "press", "net_kill", "flick_serve"]
BFMD_TO_TYPE = {"net_shot": "net"}  # every other BFMD label is a STROKE_TYPES name
MIN_PROB = 0.35
RALLY_GAP_S = 3.0
SIDE = ["bottom", "top"]  # player slot -> side


def _bbox(kp):
    """BFMD-like detector box from projected keypoints [F, 17, 3]: tight x extent, y extent nose..ankles grown by BBOX_PAD."""
    x, yn = kp[..., 0], kp[:, 0, 1]
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")  # all-NaN frames give NaN boxes
        ya = np.nanmean(kp[:, 15:17, 1], 1)
        mid, h = (ya + yn) / 2, (ya - yn) * sf.BBOX_PAD
        return np.stack([np.nanmin(x, 1), mid - h / 2, np.nanmax(x, 1), mid + h / 2], 1).astype(np.float32)


def to_rallies(joints, shuttle, fps, hits):
    """joints [F,2,17,4], shuttle [F,8] at `fps`; hits [(t, player)] sorted. Returns [(Rally, [hit index])]."""
    n = int(np.floor((len(joints) - 1) / fps * sf.FPS)) + 1
    idx = np.minimum(np.round(np.arange(n) / sf.FPS * fps).astype(int), len(joints) - 1)
    kp, bb = {}, {}
    for p, side in enumerate(SIDE):
        j = joints[idx, p]
        k = np.concatenate([bfmd.project(j[..., :3]), j[..., 3:4]], -1).astype(np.float32)
        k[j[..., 3] <= 0, :2] = np.nan
        kp[side] = k
        bb[side] = _bbox(k)
    s = shuttle[idx]
    sh = bfmd.project(s[:, :3]).astype(np.float32)
    sh[s[:, 6] <= 0] = np.nan
    Hm = bfmd.homography(bfmd.project(bfmd.WORLD[:4]))
    groups, cur = [], []
    for i, (t, _) in enumerate(hits):
        if cur and t - hits[cur[-1]][0] > RALLY_GAP_S:
            groups.append(cur)
            cur = []
        cur.append(i)
    groups += [cur] if cur else []
    return [(sf.Rally(kp, bb, sh, [(int(round(hits[i][0] * sf.FPS)), SIDE[hits[i][1]]) for i in g], Hm), g) for g in groups]


def classify(path, model_path, out=None, quiet=False):
    import joblib
    bundle = joblib.load(model_path)
    assert bundle["features"] == sf.FEATURE_NAMES, "model was trained with different features"
    header, tr = replay.read(path)
    sw = tr["swings"].copy()
    rows = [i for i in np.argsort(sw[:, 3]) if np.isfinite(sw[i, 3])]
    hits = [(float(sw[i, 3]), int(sw[i, 4])) for i in rows]
    P = np.zeros((len(rows), len(bundle["classes"])))
    for r, g in to_rallies(tr["joints3d"], tr["shuttle"], header["fps"], hits):
        P[g] = bundle["model"].predict_proba(sf.hit_features(r))
    classes = [str(c) for c in bundle["classes"]]
    table = []
    for k, i in enumerate(rows):
        order = np.argsort(-P[k])
        name = "unknown" if P[k, order[0]] < MIN_PROB else BFMD_TO_TYPE.get(classes[order[0]], classes[order[0]])
        sw[i, 5] = STROKE_TYPES.index(name)
        top2 = ", ".join(f"{BFMD_TO_TYPE.get(classes[o], classes[o])} {P[k, o]:.2f}" for o in order[:2])
        table.append((hits[k][0], hits[k][1], name, P[k, order[0]], top2))
    if not quiet:
        print(f"{'t':>7s} {'player':>6s} {'label':12s} {'p':>5s}  top-2")
        for t, p, name, pr, top2 in table:
            print(f"{t:7.2f} {p:6d} {name:12s} {pr:5.2f}  {top2}")
        print("labels:", dict(Counter(n for _, _, n, _, _ in table).most_common()))
    metas = []
    for m in header["tracks"]:
        m = {k: v for k, v in m.items() if k not in ("dtype", "shape", "offset")}
        if m["name"] == "swings":
            m["stroke_types"] = STROKE_TYPES
        metas.append((m, sw if m["name"] == "swings" else tr[m["name"]]))
    replay.write(out or path, header["fps"], metas, header["start_time"], header["coordinate_system"], header["frame_count"])
    return table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--model", default=str(bfmd.WORK / "stroke_hgb.joblib"))
    ap.add_argument("-o", "--output")
    a = ap.parse_args()
    classify(a.replay, Path(a.model).expanduser(), a.output)


if __name__ == "__main__":
    main()
