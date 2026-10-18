#!/usr/bin/env python3
"""Phase 16.2: build the stroke dataset from collected sessions (spec 4.2). Deterministic: the same inputs give byte-identical
outputs, so a rebuild (after a new session, a review label or a withdrawal) is safe to run any time.

    tools/.venv/bin/python tools/strokes/build_dataset.py [--root ~/Datasets/oso-strokes] [--version 1]

Reads   ROOT/raw/<session_id>/{session.json, imu.bin | imu.partN.bin}, ROOT/labels/<session_id>.csv (review/video labels),
        ROOT/meta/sessions.json (optional per-session overrides, e.g. {"<session_id>": {"context": "rally"}}; raw/ stays read-only)
Writes  ROOT/processed/swings_v<N>.npz, ROOT/processed/qc_v<N>.html, ROOT/manifest.json

The dataset never lives in the repo (the script refuses a root inside it). Only pseudonymous player IDs are read."""
import argparse
import csv
import hashlib
import html
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402
import qc  # noqa: E402
from imu import THRESHOLD_DPS, Mount, onsets, read_imu  # noqa: E402

PIPELINE = "oso-strokes-dataset/1"
REPO = Path(__file__).resolve().parents[2]
PROTOCOLS = Path(__file__).resolve().parent / "protocols"
DEFAULT_ROOT = Path(os.environ.get("OSO_STROKES_ROOT", "~/Datasets/oso-strokes")).expanduser()
SCHEMAS = {"oso-strokes-session/1", "oso-strokes-session/2"}
LABEL_FIELDS = ["swing_id", "t_peak_us", "class", "side", "source", "reviewer"]
DISCARD = "discard"


def swing_id(session, part, t_peak_us):
    return f"{session}:p{part}:{t_peak_us}"


# ---------------------------------------------------------------- inputs

def load_session(d):
    s = json.loads((d / "session.json").read_text(encoding="utf-8"))
    if s.get("schema") not in SCHEMAS:
        raise ValueError(f"{d.name}: unknown session schema {s.get('schema')!r}")
    pid = s["player"]["id"]
    if not (pid.startswith("P") and pid[1:].isdigit() and 2 <= len(pid) - 1 <= 3):
        raise ValueError(f"{d.name}: player id {pid!r} is not a pseudonymous ID")
    parts = s.get("parts") or [{"index": 1, "file": "imu.bin"}]  # schema /1: one imu.bin
    return s, [(int(p["index"]), d / p["file"]) for p in parts]


CONTEXTS = ("drill", "rally")


def load_overrides(root):
    p = root / "meta" / "sessions.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def context_of(session_id, session, overrides):
    """'drill' (clean sets) or 'rally' (play with a partner; block labels approximate). meta/sessions.json > session.json > drill."""
    c = overrides.get(session_id, {}).get("context") or session.get("context") or "drill"
    if c not in CONTEXTS:
        raise ValueError(f"{session_id}: context {c!r} is not one of {CONTEXTS}")
    return c


def thresholds(session):
    """(label, side) -> live counter threshold (dps) from the session's protocol file; None = protocol default."""
    p = PROTOCOLS / f"{session['protocol']['name']}.json"
    if not p.is_file():
        return {}, THRESHOLD_DPS
    proto = json.loads(p.read_text(encoding="utf-8"))
    default = float(proto.get("count_threshold_dps", THRESHOLD_DPS))
    out = {}
    for b in proto.get("warmup", []) + proto["blocks"] + proto.get("finale", []):
        out[(b["label"], b.get("side"))] = float(b.get("count_threshold_dps", default))
    return out, default


def load_labels(root, session_id):
    p = root / "labels" / f"{session_id}.csv"
    if not p.is_file():
        return {}
    with p.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        missing = [k for k in LABEL_FIELDS if k not in r]
        if missing:
            raise ValueError(f"{p}: missing columns {missing}")
        if r["class"] not in F.CLASSES and r["class"] != DISCARD:
            raise ValueError(f"{p}: unknown class {r['class']!r} for {r['swing_id']}")
    return {r["swing_id"]: r for r in rows}


# ---------------------------------------------------------------- one part

def part_swings(session_id, session, part, rec, labels):
    """-> list of swing dicts for one recording part (windows in the racquet frame)."""
    m = session["mount"]
    mount = Mount.from_axes(m["shaft"], m["normal"])
    acc, gyro = mount.apply(rec.acc), mount.apply(rec.gyro)
    w_dps = np.degrees(np.linalg.norm(gyro.astype(float), axis=1))
    by_block, default = thresholds(session)
    blocks = sorted((b for b in session["blocks"] if b.get("part", 1) == part and b.get("t_start_us") is not None
                     and b.get("t_end_us") is not None), key=lambda b: (b["t_start_us"], b["index"]))

    # Regions: each block with its own (fresh) counter, as `oso-rec collect` ran it; the gaps between with the default.
    regions, cursor = [], int(rec.t_us[0])
    for b in blocks:
        if b["t_start_us"] > cursor:
            regions.append((cursor, b["t_start_us"], None))
        regions.append((b["t_start_us"], b["t_end_us"], b))
        cursor = max(cursor, b["t_end_us"])
    if cursor < rec.t_us[-1]:
        regions.append((cursor, int(rec.t_us[-1]) + 1, None))

    cands, detected = [], {}
    for lo_us, hi_us, b in regions:
        lo, hi = int(np.searchsorted(rec.t_us, lo_us)), int(np.searchsorted(rec.t_us, hi_us))
        if hi - lo < 2:
            continue
        thr = default
        if b is not None:
            thr = by_block.get((b["label"], b.get("side")), default) or THRESHOLD_DPS  # 0 = live counter off
        ons = [lo + i for i in onsets(rec.t[lo:hi], gyro[lo:hi], thr)]
        if b is not None:
            detected[b["index"]] = len(ons)
        cands += [F.peak_index(rec.t, w_dps, i) for i in ons]

    kept = F.dedupe(rec.t, w_dps, cands)

    mismatch = {b["index"]: qc.count_mismatch(b, detected.get(b["index"], 0)) for b in blocks}
    out = []
    for i in kept:
        t_us = int(rec.t_us[i])
        win, flags = F.window(rec.t, acc, gyro, rec.sat_gyro, rec.sat_acc, float(rec.t[i]))
        b = next((b for b in reversed(blocks) if b["t_start_us"] <= t_us <= b["t_end_us"]), None)
        sid = swing_id(session_id, part, t_us)
        sw = dict(swing_id=sid, session=session_id, part=part, t_peak_us=t_us, x=win, feat=F.summary(win),
                  block=b["index"] if b else -1, label="", side="", status="unlabelled", reasons=[], notes=[], source="")
        if b is not None and b.get("status") == "discarded":
            sw.update(status="discarded", reasons=[f"in block {b['index']}, discarded during collection"])
        elif b is not None and b["label"] not in F.UNLABELLED_BLOCKS:
            sw.update(label=b["label"], side="" if b["label"] in F.SIDELESS else (b.get("side") or ""), status="labelled",
                      source="block")
            note, large = mismatch[b["index"]]
            if note:
                sw["notes"].append(note)
                if large:
                    sw["status"], sw["reasons"] = "review", [note]
        for f in flags:
            sw["reasons"].append({"edge": "window runs past the recording", "hole": "lost packets inside the window"}[f])
            if sw["status"] == "labelled":
                sw["status"] = "review"
        lab = labels.get(sid)
        if lab is not None:
            if lab["class"] == DISCARD:
                sw.update(status="discarded", reasons=[f"discarded by {lab['reviewer']} ({lab['source']})"], label="", side="")
            else:
                sw.update(label=lab["class"], side="" if lab["class"] in F.SIDELESS else lab["side"], status="labelled",
                          reasons=[], source=lab["source"])
        out.append(sw)
    return out


# ---------------------------------------------------------------- whole dataset

def build(root, version=1, out_dir=None):
    root = Path(root).expanduser().resolve()
    if REPO == root or REPO in root.parents:
        raise SystemExit(f"refusing to build a dataset inside the repo ({root}): the data lives outside it (spec 5)")
    raw = root / "raw"
    sessions_meta, swings, players = [], [], {}
    overrides = load_overrides(root)
    for d in sorted(p for p in raw.iterdir() if p.is_dir()) if raw.is_dir() else []:
        session, parts = load_session(d)
        labels = load_labels(root, d.name)
        player = session["player"]
        players[player["id"]] = player.get("handed") or ""
        context = context_of(d.name, session, overrides)
        meta = dict(id=d.name, context=context, player=player["id"], handed=player.get("handed"), level=player.get("level"),
                    protocol=f"{session['protocol']['name']}/{session['protocol']['version']}", parts=[])
        for index, path in parts:
            rec = read_imu(path)
            q = qc.session_qc(rec)
            meta["parts"].append(dict(index=index, file=path.name, packets=rec.stats["arrivals"], **q))
            if not q["ok"]:
                continue
            for sw in part_swings(d.name, session, index, rec, labels):
                sw["player"], sw["handed"], sw["context"] = player["id"], player.get("handed") or "", context
                swings.append(sw)
        sessions_meta.append(meta)

    # Outliers among confidently labelled swings, per class and side
    groups = [(s["label"], s["side"]) if s["status"] == "labelled" and s["source"] == "block" else None for s in swings]
    if swings:
        for s, bad in zip(swings, qc.outliers(np.stack([s["feat"] for s in swings]), groups)):
            if bad:
                s["status"], s["reasons"] = "review", s["reasons"] + ["far from the other swings of its class and side"]

    swings.sort(key=lambda s: (s["session"], s["part"], s["t_peak_us"]))
    for m in sessions_meta:
        mine = [s for s in swings if s["session"] == m["id"]]
        m["swings"] = {k: sum(s["status"] == k for s in mine) for k in ("labelled", "review", "unlabelled", "discarded")}
        m["notes"] = sorted({n for s in mine for n in s["notes"]})  # informational, not review (qc.count_mismatch)
        m["ok"] = all(p["ok"] for p in m["parts"])

    out = Path(out_dir) if out_dir else root / "processed"
    out.mkdir(parents=True, exist_ok=True)
    arrays = arrays_of(swings)
    npz = out / f"swings_v{version}.npz"
    write_npz(npz, arrays)
    manifest = dict(
        pipeline=PIPELINE, version=version, classes=F.CLASSES, sides=F.SIDES, to_replay=F.TO_REPLAY, channels=F.CHANNELS,
        window=dict(pre_s=F.PRE_S, post_s=F.POST_S, rate_hz=F.RATE_HZ, n=F.WINDOW_N),
        sessions=sessions_meta, players=[{"id": k, "handed": v} for k, v in sorted(players.items())],
        counts=dict(swings=len(swings), noted=sum(bool(s["notes"]) for s in swings), **{k: sum(s["status"] == k for s in swings) for k in ("labelled", "review", "unlabelled", "discarded")}),
        processed={npz.name: hashlib.sha256(npz.read_bytes()).hexdigest()},
    )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / f"qc_v{version}.html").write_text(report(manifest, swings), encoding="utf-8")
    return manifest


def arrays_of(swings):
    n = len(swings)
    cid = {c: i for i, c in enumerate(F.CLASSES)}
    sid = {s: i for i, s in enumerate(F.SIDES)}
    u = lambda key: np.array([s[key] for s in swings], dtype=str) if n else np.zeros(0, "<U1")  # noqa: E731
    return {
        "x": np.stack([s["x"] for s in swings]).astype(np.float32) if n else np.zeros((0, F.WINDOW_N, len(F.CHANNELS)), np.float32),
        "feat": np.stack([s["feat"] for s in swings]).astype(np.float32) if n else np.zeros((0, 8), np.float32),
        "swing_id": u("swing_id"), "session": u("session"), "player": u("player"), "handed": u("handed"), "context": u("context"),
        "part": np.array([s["part"] for s in swings], np.int16), "t_peak_us": np.array([s["t_peak_us"] for s in swings], np.int64),
        "block": np.array([s["block"] for s in swings], np.int16),
        "label": u("label"), "class_id": np.array([cid.get(s["label"], -1) for s in swings], np.int8),
        "side": np.array([sid.get(s["side"], -1) for s in swings], np.int8),
        "status": u("status"), "source": u("source"),
        "reasons": np.array(["; ".join(s["reasons"]) for s in swings], dtype=str) if n else np.zeros(0, "<U1"),
        "notes": np.array(["; ".join(s["notes"]) for s in swings], dtype=str) if n else np.zeros(0, "<U1"),
    }


def write_npz(path, arrays):
    """np.savez with fixed member order, timestamps and compression, so equal data gives equal bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in sorted(arrays):
            b = io.BytesIO()
            np.lib.format.write_array(b, np.ascontiguousarray(arrays[k]), allow_pickle=False)
            info = zipfile.ZipInfo(f"{k}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type, info.external_attr = zipfile.ZIP_DEFLATED, 0o644 << 16
            z.writestr(info, b.getvalue(), compresslevel=6)
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(buf.getvalue())
    tmp.replace(path)


def load(path):
    """The processed dataset as a dict of arrays (no pickle)."""
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


# ---------------------------------------------------------------- QC report

def report(manifest, swings):
    e = html.escape
    rows = []
    for m in manifest["sessions"]:
        for p in m["parts"]:
            rows.append(f"<tr><td>{e(m['id'])}</td><td>{e(m['player'])}</td><td>{p['index']}</td><td>{'ok' if p['ok'] else 'REJECTED'}</td>"
                        f"<td>{p['rate_hz']:.1f}</td><td>{p['loss_pct']:.2f}</td><td>{p['clip_pct']:.2f}</td><td>{e('; '.join(p['reasons']))}</td></tr>")
    players = sorted({s["player"] for s in swings})
    keys = [(c, s) for c in F.CLASSES for s in ([""] if c in F.SIDELESS else F.SIDES)]
    grid = ["<tr><th>class</th><th>side</th>" + "".join(f"<th>{e(p)}</th>" for p in players) + "<th>total</th></tr>"]
    for c, sd in keys:
        cnt = [sum(1 for s in swings if s["player"] == p and s["label"] == c and s["side"] == sd and s["status"] == "labelled") for p in players]
        grid.append(f"<tr><td>{e(c)}</td><td>{e(sd)}</td>" + "".join(f"<td>{n}</td>" for n in cnt) + f"<td>{sum(cnt)}</td></tr>")
    review = [f"<tr><td>{e(s['swing_id'])}</td><td>{e(s['label'])}</td><td>{e(s['side'])}</td><td>{e('; '.join(s['reasons']))}</td></tr>"
              for s in swings if s["status"] == "review"]
    notes = {}
    for s in swings:
        for n in s["notes"]:
            notes[(s["session"], n)] = notes.get((s["session"], n), 0) + 1
    note_rows = [f"<tr><td>{e(k[0])}</td><td>{e(k[1])}</td><td>{v}</td></tr>" for k, v in sorted(notes.items())]
    clip = [s["feat"][7] for s in swings if s["status"] == "labelled"]
    c = manifest["counts"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>oso strokes QC v{manifest['version']}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#111}}table{{border-collapse:collapse;margin:8px 0 24px}}
td,th{{border:1px solid #ccc;padding:3px 8px;text-align:left}}th{{background:#f3f3f3}}</style></head><body>
<h1>Stroke dataset v{manifest['version']} ({e(manifest['pipeline'])})</h1>
<p>{c['swings']} swings: {c['labelled']} labelled, {c['review']} to review, {c['unlabelled']} unlabelled, {c['discarded']} discarded.
{c['noted']} swings carry a count note (kept labelled).
Labelled swings with gyro clipping: {sum(1 for x in clip if x > 0)} of {len(clip)}.</p>
<h2>Sessions</h2><table><tr><th>session</th><th>player</th><th>part</th><th>QC</th><th>rate Hz</th><th>loss %</th><th>clip %</th><th>reasons</th></tr>
{''.join(rows)}</table>
<h2>Labelled swings per player, class and side</h2><table>{''.join(grid)}</table>
<h2>Review queue ({len(review)})</h2><table><tr><th>swing</th><th>block label</th><th>side</th><th>why</th></tr>{''.join(review)}</table>
<h2>Count notes, informational ({len(note_rows)} blocks; these swings stay labelled)</h2><table><tr><th>session</th><th>note</th><th>swings</th></tr>{''.join(note_rows)}</table>
</body></html>
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--version", type=int, default=1)
    a = ap.parse_args(argv)
    m = build(a.root, a.version)
    c = m["counts"]
    rejected = [f"{s['id']} part {p['index']}: {'; '.join(p['reasons'])}" for s in m["sessions"] for p in s["parts"] if not p["ok"]]
    print(f"{len(m['sessions'])} sessions, {c['swings']} swings: {c['labelled']} labelled, {c['review']} review, "
          f"{c['unlabelled']} unlabelled, {c['discarded']} discarded")
    for r in rejected:
        print("REJECTED", r)
    print(f"{c['noted']} swings carry a count note (kept labelled)")
    print(f"wrote {a.root.expanduser() / 'processed' / f'swings_v{a.version}.npz'}, qc_v{a.version}.html, manifest.json")


if __name__ == "__main__":
    main()
