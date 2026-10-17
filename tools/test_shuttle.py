"""Phase 5 end to end (seed 0): synth motion -> pose3d -> synth_shuttle -> shuttle3d -> synth_imu -> `oso-imu` (cargo).
Run from anywhere: tools/.venv/bin/python tools/test_shuttle.py
Prints the numbers; plain asserts on the spec thresholds."""
import json
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

import replay
import shuttle_physics as sp
from pose3d import pose3d
from shuttle3d import shuttle3d
from synth import CAMERA_A, CAMERA_B
from synth_imu import generate_imu
from synth_motion import detect as detect_pose
from synth_motion import generate
from synth_shuttle import detect as detect_shuttle
from synth_shuttle import rally

SEED = 0
ROOT = Path(__file__).resolve().parent.parent
CALIB = ROOT / "assets/calib/calib.json"


def test_physics():
    p0, v0 = np.array([0.0, 2.0, 0.0]), np.array([12.0, 8.0, -3.0])
    ts = np.linspace(0, 2, 7)
    a, _ = sp.simulate(p0, v0, 0.0, ts)
    b, _ = sp.simulate(p0, v0, 0.0, ts, h=sp.H_FIT)
    assert np.abs(a - b).max() < 1e-6, "coarse step must match the 1 ms truth"
    # backward in time inverts forward
    p1, v1 = sp.simulate(p0, v0, 0.0, [1.5])
    pb, _ = sp.simulate(p1[0], v1[0], 1.5, [0.0])
    assert np.abs(pb[0] - p0).max() < 1e-6
    # falls at the terminal velocity
    _, v = sp.simulate(np.array([0, 100.0, 0]), np.zeros(3), 0.0, [8.0])
    assert abs(v[0][1] + sp.VT) < 0.02
    # batched == one by one
    pb2, _ = sp.simulate(np.stack([p0, p0 + 1]), np.stack([v0, v0]), 0.0, ts)
    assert np.abs(pb2[0] - a).max() < 1e-12
    # shooting reaches the target
    tgt = np.array([-4.0, 1.0, 1.0])
    v = sp.solve_shot(p0, 0.3, tgt, 1.1)
    assert np.abs(sp.simulate(p0, v, 0.3, [1.1])[0][0] - tgt).max() < 1e-4
    print("physics: ok")


def stats(x):
    return f"median {np.median(x) * 100:.2f} cm  p95 {np.percentile(x, 95) * 100:.2f} cm  max {x.max() * 100:.2f} cm  (n={len(x)})"


def run_cell(calib, seed, vt, verbose=False):
    """Full pipeline for one (seed, generator vt); the fitter is not told vt. Returns a dict of metrics + artifacts."""
    fps = calib["cameras"][0]["fps"]
    joints, t, sp_, st = generate(seed, fps)
    ct = st + 0.06
    rng = np.random.default_rng(seed + 1000)
    p2d = [detect_pose(rng, cam, joints, fps, cid) for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B))]
    j3d = pose3d(calib, *p2d)
    gt = rally(joints, t, sp_, ct, seed, vt)
    rng = np.random.default_rng(seed + 4000)
    dets = []
    for cid, cam in (("a", CAMERA_A), ("b", CAMERA_B)):
        d = detect_shuttle(rng, cam, gt, joints, cid)
        dets.append({"uv": d["uv"].astype(np.float64), "score": d["score"], "t": d["t"], "camera_id": cid})
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / "base.bin"
        meta = {"name": "joints3d", "layout": "x,y,z,conf", "keypoint_format": "coco17", "players": ["p0", "p1"]}
        replay.write(base, fps, [(meta, j3d)])
        _, tracks = replay.read(base)
        t0 = time.perf_counter()
        shuttle, events, flights = shuttle3d(calib, dets, tracks)
        fit_s = time.perf_counter() - t0
    inf, conf = gt["in_flight"], shuttle[:, 6]
    err = np.linalg.norm(shuttle[:, :3] - gt["pos"], axis=1)
    e1, e5 = err[inf & (conf == 1)], err[inf & (conf == 0.5)]
    w, h = CAMERA_A.size
    hidden = inf.copy()
    for cam in (CAMERA_A, CAMERA_B):
        uv = np.full((len(t), 2), -1e4)
        uv[inf] = cam.project(gt["pos"][inf])
        hidden &= ~((uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h))
    ev = events[events[:, 1] == 1]
    gc = gt["contacts"]
    te, pe, matched, lines = [], [], set(), []
    for c in gc:
        k = int(np.argmin(np.abs(ev[:, 0] - c[0]))) if len(ev) else -1
        if k < 0 or abs(ev[k, 0] - c[0]) > 0.05 or k in matched or int(ev[k, 2]) != int(c[1]):
            lines.append(f"  t {c[0]:.4f} player {int(c[1])}: MISSED")
            continue
        matched.add(k)
        dt, dp = (ev[k, 0] - c[0]) * 1e3, np.linalg.norm(ev[k, 3:] - c[2:])
        te.append(abs(dt))
        pe.append(dp)
        lines.append(f"  GT t {c[0]:.4f} p{int(c[1])} ({c[2]:+.2f},{c[3]:.2f},{c[4]:+.2f})  fit t {ev[k, 0]:.4f} p{int(ev[k, 2])} "
                     f"({ev[k, 3]:+.2f},{ev[k, 4]:.2f},{ev[k, 5]:+.2f})  dt {dt:+.2f} ms  dpos {dp * 100:.1f} cm")
    land = events[events[:, 1] == 2]
    dl = abs(land[0, 0] - gt["landing"][0]) * 1e3 if len(land) else np.inf
    dpl = np.linalg.norm(land[0, [3, 5]] - gt["landing"][[1, 3]]) if len(land) else np.inf
    r = dict(seed=seed, vt=vt, apex=gt["max_apex"], e1=e1, e5=e5, cov=(inf & (conf > 0)).sum() / inf.sum(),
             hidden=int(hidden.sum()), hidden_filled=int((hidden & (conf > 0)).sum()),
             n_gt=len(gc), matched=len(matched), extra=len(ev) - len(matched), te=te, pe=pe, nland=len(land),
             dl=dl, dpl=dpl, clip_vt=flights[0].clip_vt if flights else np.nan, fit_s=fit_s, n_flights=len(flights),
             lines=lines, shuttle=shuttle, events=events, gt=gt, joints=joints, t=t, sp=sp_, ct=ct, j3d=j3d, meta=meta)
    return r


def failures(r):
    """Spec thresholds not met by one cell."""
    f = []
    q = lambda x, p: np.percentile(x, p) if len(x) else np.inf  # noqa: E731
    med = lambda x: np.median(x) if len(x) else np.inf  # noqa: E731
    if not r["apex"] > 6.5:
        f.append("apex")
    if not (med(r["e1"]) < 0.04 and q(r["e1"], 95) < 0.10):
        f.append("conf1 err")
    if len(r["e5"]) and not med(r["e5"]) < 0.25:
        f.append("conf0.5 err")
    if not r["cov"] >= 0.95:
        f.append("coverage")
    if len(r["e1"]) and not r["e1"].max() < 0.30:
        f.append("conf1 max")
    if r["hidden_filled"] != r["hidden"]:
        f.append("out-of-frame fill")
    if r["matched"] != r["n_gt"] or r["extra"]:
        f.append("contacts missed/extra")
    elif not (med(r["te"]) < 3 and max(r["te"]) < 8 and med(r["pe"]) < 0.08):
        f.append("contact err")
    if not (r["dl"] < 20 and r["dpl"] < 0.15 and r["nland"] == 1):
        f.append("landing")
    return f


MATRIX = [(s, v) for s in (0, 1, 2) for v in (6.2, 6.8, 7.4)]


def main():
    test_physics()
    calib = json.loads(CALIB.read_text())
    fps = calib["cameras"][0]["fps"]
    cells = {}
    print("robustness matrix (generator vt differs from the fitter's 6.8 default; the fitter is not told):")
    print(f"{'seed':>4} {'vt':>4} {'apex':>5} | {'c1 med':>7} {'p95':>6} {'max':>6} | {'c.5 med':>7} {'n':>3} | {'cov%':>5} {'oof':>7} | "
          f"{'match':>5} {'extra':>5} {'t med':>6} {'t max':>6} {'p med':>6} | {'land ms':>7} {'cm':>5} | {'fit vt':>6} {'s':>5}  result")
    for seed, vt in MATRIX:
        r = run_cell(calib, seed, vt)
        cells[(seed, vt)] = r
        m = lambda x, k=1.0: np.median(x) * k if len(x) else float("nan")  # noqa: E731
        f = failures(r)
        print(f"{seed:>4} {vt:>4} {r['apex']:5.2f} | {m(r['e1'], 100):7.2f} {np.percentile(r['e1'], 95) * 100:6.2f} "
              f"{r['e1'].max() * 100:6.2f} | {m(r['e5'], 100):7.2f} {len(r['e5']):>3} | {r['cov'] * 100:5.1f} "
              f"{r['hidden_filled']:>3}/{r['hidden']:<3} | {r['matched']:>2}/{r['n_gt']:<2} {r['extra']:>5} {m(r['te']):6.2f} "
              f"{max(r['te'], default=float('nan')):6.2f} {m(r['pe'], 100):6.1f} | {r['dl']:7.1f} {r['dpl'] * 100:5.1f} | "
              f"{r['clip_vt']:6.2f} {r['fit_s']:5.1f}  {'PASS' if not f else 'FAIL ' + ', '.join(f)}", flush=True)
    bad = {k: failures(r) for k, r in cells.items() if failures(r)}
    assert not bad, f"cells failing the spec thresholds: {bad}"

    # The default cell (seed 0, vt 6.8): detail, round trip, and the Rust sync check against the events track
    r = cells[(0, 6.8)]
    print("seed 0 vt 6.8 detail:")
    print("conf 1.0 position error:", stats(r["e1"]))
    print("conf 0.5 position error:", stats(r["e5"]))
    print("contacts (GT time/pos vs fitted):")
    print("\n".join(r["lines"]))
    shuttle, events, joints, t, sp_, ct, j3d, meta, gt = (r[k] for k in
                                                         ("shuttle", "events", "joints", "t", "sp", "ct", "j3d", "meta", "gt"))
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        out = d / "out.bin"
        replay.write(out, fps, [({"name": "events", "per_frame": False}, events), (meta, j3d), ({"name": "shuttle"}, shuttle)])
        header, back = replay.read(out)
        assert header["frame_count"] == len(t) and back["events"].shape == events.shape
        assert np.array_equal(back["joints3d"], j3d.astype("<f4")) and np.array_equal(back["shuttle"], shuttle)

        gc = gt["contacts"]
        impacts = gc[gc[:, 1] == 0, 0]
        im = generate_imu(joints, t, ct[sp_ == 0], 0, 0, None, impacts)
        (d / "imu.bin").write_bytes(im["packets"])
        (d / "sync.json").write_text(json.dumps(im["sync"]))
        base = d / "base.bin"
        replay.write(base, fps, [(meta, j3d), ({"name": "shuttle", "layout": "x,y,z,vx,vy,vz,conf,flight_id"}, shuttle),
                                 ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, events)])
        rep = d / "report.json"
        cmd = ["cargo", "run", "--release", "--quiet", "--bin", "oso-imu", "--", "--imu", str(d / "imu.bin"),
               "--replay", str(base), "--sync", str(d / "sync.json"), "--player", "0", "-o", str(d / "racq.bin"),
               "--report", str(rep)]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr
        assert "shuttle contacts" in p.stdout
        sy = json.loads(rep.read_text())["sync"]
        print(f"oso-imu with shuttle contacts: residual RMS {sy['rms_ms']:.2f} ms, max {sy['max_ms']:.2f} ms over {sy['n_impacts']} impacts")
        assert sy["n_impacts"] == len(impacts) and sy["rms_ms"] < 3
    print("ok")


if __name__ == "__main__":
    main()
