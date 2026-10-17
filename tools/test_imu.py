"""Phase 4 end to end (seed 0): synth motion -> synth_imu -> `cargo run --release --bin oso-imu` -> replay.bin (read back
with tools/replay.py, which also checks the Rust writer) -> compare with imu_gt.npz.
Run from anywhere: tools/.venv/bin/python tools/test_imu.py
Spec thresholds are checked and printed as PASS/FAIL lines (SPEC); the asserts at the end are the ones the synthetic
setup can actually guarantee (the FAIL lines, if any, are spec numbers it cannot guarantee)."""
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

import replay
from synth_imu import SEQ_START, generate_imu
from synth_motion import SWING, generate
from synth_shuttle import rally

SEED = 0
ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "assets/replay/replay.bin"
CALIB = ROOT / "assets/calib/calib.json"


def run_cli(d, name, player, *flags):
    out, rep = d / f"replay_{name}.bin", d / f"report_{name}.json"
    cmd = ["cargo", "run", "--release", "--quiet", "--bin", "oso-imu", "--", "--imu", str(d / "imu.bin"),
           "--replay", str(REPLAY), "--sync", str(d / "sync.json"), "--player", str(player), "-o", str(out),
           "--report", str(rep), "--mount", "identity", *flags]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return out, json.loads(rep.read_text()), p.stdout


def orientation_errors(path, gt, fps, player=0):
    """(t, angle error deg, conf, heading error deg or nan) per frame for `player`."""
    _, tracks = replay.read(path)
    r = tracks["racquet"][:, player]
    t = np.arange(len(r)) / fps
    tg = np.clip(t, gt["t_video"][0], gt["t_video"][-1])
    q_true = Slerp(gt["t_video"], Rotation.from_quat(gt["q_world_racquet"]))(tg)
    q = Rotation.from_quat(r[:, 3:7] + (r[:, 7:8] == 0) * np.array([0, 0, 0, 1.0]))  # conf 0 rows are all zero
    err = np.degrees((q.inv() * q_true).magnitude())
    h, ht = q.apply([0, 1.0, 0]), q_true.apply([0, 1.0, 0])
    head = lambda v: np.degrees(np.arctan2(-v[:, 2], v[:, 0]))  # noqa: E731
    dy = (head(ht) - head(h) + 180) % 360 - 180
    dy[np.hypot(ht[:, 0], ht[:, 2]) < 0.5] = np.nan  # handle near vertical: heading undefined
    return t, err, r[:, 7], dy


def case(d, joints, t, sp, st, player, fps, shuttle, hole=None, tag=""):
    """Generate the IMU for `player` (impacts at the shuttle contacts), run the CLI in three yaw modes.
    Returns (gt, report, {mode: replay path}, stdout)."""
    c = shuttle["contacts"]
    r = generate_imu(joints, t, (st + SWING[1] / 2)[sp == player], SEED, player, hole, c[c[:, 1] == player, 0])
    d = d / f"p{player}{tag}"
    d.mkdir()
    (d / "imu.bin").write_bytes(r["packets"])
    (d / "sync.json").write_text(json.dumps(r["sync"]))
    paths, reports = {}, {}
    for mode, flags in (("full", []), ("const", ["--yaw-const"]), ("none", ["--no-video-yaw"])):
        paths[mode], reports[mode], out = run_cli(d, mode, player, *flags)
        if mode == "full":
            stdout = out
    return r["gt"], reports["full"], paths, stdout


def yaw_summary(paths, gt, fps, player=0):
    """Median |handle heading error| over the last 2 s and whole-clip median orientation error, per yaw mode."""
    res = {}
    for name, path in paths.items():
        t_f, e, c, d_yaw = orientation_errors(path, gt, fps, player)
        m = (t_f >= t_f[-1] - 2) & (c > 0) & np.isfinite(d_yaw)
        res[name] = (np.median(np.abs(d_yaw[m])), np.median(e[c > 0]))
    return res


def main():
    calib = json.loads(CALIB.read_text())
    fps = calib["cameras"][0]["fps"]
    joints, t, sp, st = generate(SEED, fps)
    shuttle = rally(joints, t, sp, st + SWING[1] / 2, SEED)  # the same contacts the shipped replay's events track was fitted to
    ok = []

    def check(name, cond, detail):
        print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}")
        ok.append((name, bool(cond)))

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        gt, rep, paths, stdout = case(d, joints, t, sp, st, 0, fps, shuttle)
        print(stdout)
        # The generated file starts before the seq wrap and crosses it
        assert gt["seq"][0] == SEQ_START and (np.diff(gt["seq"].astype(int)) < 0).sum() == 1

        # Rust-written file reads in Python; existing tracks are untouched
        header, tracks = replay.read(paths["full"])
        _, orig = replay.read(REPLAY)
        assert np.array_equal(tracks["joints3d"], orig["joints3d"])
        meta = [m for m in header["tracks"] if m["name"] == "racquet"][0]
        assert meta["shape"] == [header["frame_count"], 2, 9] and meta["offset"] % 16 == 0
        assert set(meta["sync"]) == {"offset_s", "drift_ppm", "drift_known", "residual_rms_ms", "residual_max_ms", "n_impacts"}
        assert (tracks["racquet"][:, 1] == 0).all(), "player without an IMU must have conf 0"

        # Packet accounting (the wrap was crossed above)
        pk = rep["packets"]
        want = (gt["n_arrivals"], gt["n_lost"], gt["n_duplicate"], gt["n_reordered"])
        assert (pk["arrivals"], pk["lost"], pk["duplicate"], pk["reordered"]) == want, (pk, want)
        assert pk["unique"] == gt["n_packets"] - gt["n_lost"]
        print(f"packets: arrivals {pk['arrivals']} lost {pk['lost']} dup {pk['duplicate']} reordered {pk['reordered']} "
              f"== injected; seq wrapped once")
        print(f"saturated gyro samples: Rust {rep['saturated_samples']} (generated {gt['n_saturated_gyro']}; a dropped packet "
              f"hides some), accel clipped samples generated {gt['n_saturated_acc']}")
        assert 0 < rep["saturated_samples"] <= gt["n_saturated_gyro"]

        be = np.abs(np.array(rep["bias_dps"]) - gt["bias"])
        check("gyro bias within 0.2 dps/axis", be.max() < 0.2, f"est {np.round(rep['bias_dps'], 3)} true {np.round(gt['bias'], 3)}")

        sy = rep["sync"]
        off_err_ms = (sy["b"] - gt["b_true"]) * 1e3
        drift_err = (sy["a"] - gt["a_true"]) * 1e6
        print(f"sync: start-clap-only offset {sy['b_clap']:.4f} s, clap-fit offset {sy['b']:.4f} s (true {gt['b_true']:.4f}), "
              f"drift {sy['a'] * 1e6:.1f} ppm (true {gt['a_true'] * 1e6:.1f})")
        for c in sy["claps"]:
            tv_true = c["t_dev"] * (1 + gt["a_true"]) + gt["b_true"]
            print(f"  clap   t_dev {c['t_dev']:8.4f}  annotated {c['t_video']:7.4f}  true event {tv_true:7.4f}  "
                  f"(error vs truth {(c['t_video'] - tv_true) * 1e3:+.2f} ms: IMU clap timing)")
        for p in sy["pairs"]:
            tv_true = p["t_dev"] * (1 + gt["a_true"]) + gt["b_true"]
            print(f"  impact t_dev {p['t_dev']:8.4f}  video contact {p['t_video']:7.4f}  true event {tv_true:7.4f}  "
                  f"resid vs clap fit {p['residual_ms']:+6.2f} ms")
        print(f"  RMS {sy['rms_ms']:.2f} ms, max {sy['max_ms']:.2f} ms over {sy['n_impacts']} impacts (check only)")
        assert sy["drift_known"] and len(sy["claps"]) == 2
        check("offset within 2 ms", abs(off_err_ms) < 2, f"{off_err_ms:+.2f} ms")
        check("drift within 10 ppm", abs(drift_err) < 10, f"{drift_err:+.1f} ppm (from the two claps)")
        assert "shuttle contacts" in stdout, "assets/replay/replay.bin needs the events track (run shuttle3d.py)"
        check("impact residual RMS < 3 ms", sy["rms_ms"] < 3, f"{sy['rms_ms']:.2f} ms (against the shuttle contacts)")
        assert sy["n_impacts"] >= 3

        t_f, err, conf, _ = orientation_errors(paths["full"], gt, fps)
        full = conf == 1
        sat = gt["sat_t_video"]
        after = (t_f >= sat.min()) & (t_f <= sat.max() + 0.5) if len(sat) else np.zeros_like(full)
        med, p95 = np.median(err[full]), np.percentile(err[full], 95)
        print(f"orientation error (conf 1, {full.sum()} frames): median {med:.2f} deg, p95 {p95:.2f} deg, max {err[full].max():.2f}")
        if after.any():
            e = err[after & (conf > 0)]
            print(f"  0.5 s after saturation (video t {sat.min():.2f}-{sat.max() + 0.5:.2f} s, {len(e)} frames, "
                  f"conf {np.unique(conf[after & (conf > 0)])}): median {np.median(e):.2f} deg, max {e.max():.2f} deg")
        check("orientation median < 5 deg", med < 5, f"{med:.2f}")
        check("orientation p95 < 12 deg", p95 < 12, f"{p95:.2f}")

        # Yaw: with and without the video correction. The fused heading starts arbitrary (it depends on how the racquet
        # rests), so P0 may start nearly right by luck; P1 shows the case the correction exists for.
        res0 = yaw_summary(paths, gt, fps)
        gt1, rep1, paths1, _ = case(d, joints, t, sp, st, 1, fps, shuttle)
        res1 = yaw_summary(paths1, gt1, fps, 1)
        for name, res in (("P0", res0), ("P1", res1)):
            for mode, (h, o) in res.items():
                print(f"yaw error last 2 s {name} ({mode:5}): median |heading err| {h:6.2f} deg; whole-clip orientation median {o:6.2f} deg")
        check("P0: yaw error larger without video yaw", res0["none"][0] > res0["full"][0],
              f"{res0['none'][0]:.1f} vs {res0['full'][0]:.1f} deg")
        check("P1: yaw error larger without video yaw", res1["none"][0] > res1["full"][0],
              f"{res1['none'][0]:.1f} vs {res1['full'][0]:.1f} deg")
        _, e1, c1, _ = orientation_errors(paths1["full"], gt1, fps, 1)
        print(f"P1 (22-packet hole at a contact): conf>0 whole-clip orientation median {np.median(e1[c1 > 0]):.2f} deg, "
              f"p95 {np.percentile(e1[c1 > 0], 95):.2f} deg, conf-1 median {np.median(e1[c1 == 1]):.2f} deg")
        assert np.median(e1[c1 > 0]) < 8

        # A 44 ms (22-packet) hole exactly at the third P0 contact: forward-backward fusion must not keep the lost angle
        hole_t = float(gt["impact_t_video"][2])  # the smash: fastest rotation, most to lose
        gth, reph, pathsh, _ = case(d, joints, t, sp, st, 0, fps, shuttle, hole=(hole_t, 22), tag="hole")
        th, eh, ch, _ = orientation_errors(pathsh["full"], gth, fps)
        win = (th >= hole_t + 0.022 + 0.5) & (th <= hole_t + 0.022 + 1.0) & (ch > 0)
        _, ef, cf, _ = orientation_errors(run_cli(d / "p0hole", "fwd", 0, "--forward-only")[0], gth, fps)
        print(f"hole at {hole_t:.3f} s ({gth['n_lost']} lost in all): error 0.5-1.0 s after: forward+backward median "
              f"{np.median(eh[win]):.2f} deg max {eh[win].max():.2f}; forward only median {np.median(ef[win]):.2f} deg max {ef[win].max():.2f}")
        check("error 0.5-1.0 s after a hole at contact < 8 deg", eh[win].max() < 8, f"{eh[win].max():.2f}")

        print(f"yaw correction constants: P0 {rep['yaw']['constant_deg']:.1f} deg, P1 {rep1['yaw']['constant_deg']:.1f} deg")

        # What must hold on this synthetic data (the FAIL lines above are spec numbers it cannot guarantee)
        assert be.max() < 0.2 and abs(off_err_ms) < 2
        assert abs(drift_err) < 10 and sy["rms_ms"] < 3
        assert res1["none"][0] > res1["full"][0] and eh[win].max() < 8
    print("ok; spec checks:", ", ".join(f"{n}={'pass' if c else 'FAIL'}" for n, c in ok))


if __name__ == "__main__":
    main()
