"""Phase 6 end to end (seed 0): synth motion + shuttle -> synth_imu -> `oso-imu` -> the `swings` track (read back with
tools/replay.py) vs ground truth. Prints the stroke table (labels are rule outputs on synthetic flights: no accuracy assert).
Run from anywhere: tools/.venv/bin/python tools/test_swings.py"""
import json
import tempfile
from pathlib import Path

import numpy as np

import replay
from synth_imu import generate_imu
from synth_motion import SWING, generate
from synth_shuttle import rally
from test_imu import CALIB, SEED, run_cli

COLS = ["t_start", "t_peak", "t_end", "t_contact", "player", "stroke_type", "peak_omega_dps", "head_speed_peak",
        "head_speed_rot", "impact_g", "timing_ms", "contact_h", "launch_speed", "launch_angle_deg", "saturated", "confidence"]
PEAK_TOL_S = 0.02
PLAYER = 0


def main():
    fps = json.loads(CALIB.read_text())["cameras"][0]["fps"]
    joints, t, sp, st = generate(SEED, fps)
    shuttle = rally(joints, t, sp, st + SWING[1] / 2, SEED)
    c = shuttle["contacts"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        r = generate_imu(joints, t, (st + SWING[1] / 2)[sp == PLAYER], SEED, PLAYER, None, c[c[:, 1] == PLAYER, 0])
        (d / "imu.bin").write_bytes(r["packets"])
        (d / "sync.json").write_text(json.dumps(r["sync"]))
        out, rep, stdout = run_cli(d, "full", PLAYER)
        print(stdout[stdout.index("strokes:"):])
        header, tracks = replay.read(out)
    meta = [m for m in header["tracks"] if m["name"] == "swings"][0]
    sw = tracks["swings"]
    assert meta["per_frame"] is False and meta["shape"] == [len(sw), 16] and meta["layout"].split(",") == COLS
    types = meta["stroke_types"]
    assert types[0] == "unknown" and types[8] == "swing_only" and types[-1] == "flick_serve" and len(types) == 14
    assert np.all(np.diff(sw[:, 1]) >= 0), "sorted by time"
    gt = r["gt"]
    tw, w = gt["t_video"], np.degrees(np.linalg.norm(gt["w"], axis=1))

    # 1. every GT forward swing of the IMU player is detected; the peak lands on the true forward-swing peak
    imu = sw[sw[:, 4] == PLAYER]
    imu = imu[~np.isnan(imu[:, 6])]  # rows with IMU data
    t0s = st[sp == PLAYER]
    print(f"\nGT forward swings {len(t0s)}, IMU swings detected {len(imu)}")
    for t0 in t0s:
        m = (tw >= t0) & (tw <= t0 + SWING[1] + 0.05)
        gt_peak = tw[m][np.argmax(w[m])]
        row = imu[(imu[:, 0] <= t0 + SWING[1] / 2) & (imu[:, 2] >= t0 + SWING[1] / 2)]
        assert len(row) == 1, f"swing at {t0:.3f} s: {len(row)} detected windows"
        err = (row[0, 1] - gt_peak) * 1e3
        print(f"  GT forward swing {t0:.3f} s: GT peak {gt_peak:.3f} s ({w[m].max():.0f} dps), detected {row[0, 1]:.3f} s "
              f"({row[0, 6]:.0f} dps, saturated {int(row[0, 14])}): error {err:+.1f} ms")
        assert abs(err) < PEAK_TOL_S * 1e3

    # 2. every shuttle contact is a stroke with the right player and time
    got = sw[~np.isnan(sw[:, 3])]
    assert len(got) == len(c), (len(got), len(c))
    for (tc, pl, *_), row in zip(c, got):
        assert int(row[4]) == int(pl) and abs(row[3] - tc) < 0.005, (tc, pl, row)

    # 3. IMU-player strokes with a swing carry impact and timing. A contact with no swing near it (the clear insertion
    # in synth_shuttle moves one contact away from its body swing) stays contact-only: flight fields, no IMU columns.
    near_swing = [row for row in got if row[4] == PLAYER and np.any(np.abs(row[3] - (t0s + SWING[1] / 2)) < 0.1)]
    assert len(near_swing) >= 3
    for row in near_swing:
        assert not np.isnan(row[6]) and not np.isnan(row[9]) and not np.isnan(row[10]), row
        assert row[9] > 5 and abs(row[10]) < 100
    for row in got:
        assert not np.isnan(row[11]) and not np.isnan(row[12]), "contact strokes carry their flight fields"
        if row[4] != PLAYER:
            assert np.isnan(row[6]) and np.isnan(row[9]), "players without an IMU have no swing metrics"
    air = sw[np.isnan(sw[:, 3])]
    assert np.all(air[:, 5] == types.index("swing_only")) and np.all(np.isnan(air[:, 11]))
    print(f"contacts {len(got)}, with IMU swing {len(near_swing)}, air swings {len(air)}")

    # 4. no duplicates
    for p in (0, 1):
        tp = np.sort(sw[sw[:, 4] == p][:, 1])
        assert np.all(np.diff(tp) > 0.2), f"player {p}: strokes closer than 0.2 s {tp}"
    print("ok")


if __name__ == "__main__":
    main()
