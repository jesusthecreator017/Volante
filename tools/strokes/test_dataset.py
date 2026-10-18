"""Phase 16.2 tests on synthetic sessions (no real data in the repo): the imu.bin reader, the counter port, session QC
(a byte-shifted recording is rejected), block/count mismatch -> note (policy in test_review.py), the duplicate-peak rule, labels from review CSVs,
withdrawal, and byte-identical rebuilds.
Run from anywhere: tools/.venv/bin/python tools/strokes/test_dataset.py"""
import csv
import json
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_dataset as B  # noqa: E402
import features as F  # noqa: E402
import qc  # noqa: E402
import withdraw as W  # noqa: E402
from imu import GYRO_K, ACC_K, Mount, encode_imu, onsets, read_imu  # noqa: E402

RATE = 500
FLAT = {"shaft": [0.0, 0.0, 1.0], "normal": [1.0, 0.0, 0.0]}  # mount.rs FLAT: sensor +Z = shaft, sensor +X = face normal


def synth(swing_times, seconds, peak_dps=900.0, seed=0, t0_us=1_000_000, seq0=0, roll_axis=2, quiet=False):
    """A recording at rest (gravity on sensor +Y, light noise) with one swing per time: a 120 ms smooth |w| bump about
    `roll_axis` (sensor +Z = the shaft under the flat mount) plus a 2 g accel bump. `quiet`: about one count of noise, like a
    pod lying still (the rest fixture of packet.rs). -> list of packets (seq16, t32, v)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    gyro = rng.normal(0, 0.06 if quiet else 0.5, (n, 3))  # dps
    acc = np.tile([0.0, 1.0, 0.0], (n, 1)) + rng.normal(0, 0.0005 if quiet else 0.005, (n, 3))  # g
    for k, ts in enumerate(swing_times):
        pk = peak_dps[k] if isinstance(peak_dps, (list, tuple)) else peak_dps  # one amplitude, or one per swing
        bump = np.exp(-0.5 * ((t - ts) / 0.04) ** 2)
        gyro[:, roll_axis] += -pk * bump
        gyro[:, 0] += 0.3 * pk * bump
        acc[:, 1] += 2.0 * bump
    gc = np.clip(np.round(gyro / math.degrees(GYRO_K)), -32768, 32767).astype(int)
    ac = np.clip(np.round(acc * 9.80665 / ACC_K), -32768, 32767).astype(int)
    return [((seq0 + i) & 0xFFFF, (t0_us + i * 2000) & 0xFFFFFFFF, (*ac[i], *gc[i])) for i in range(n)]


def make_session(root, sid, player, blocks, packets, handed="R"):
    d = root / "raw" / sid
    d.mkdir(parents=True)
    (d / "imu.part1.bin").write_bytes(encode_imu(RATE, packets))
    t_first = packets[0][1]
    session = {
        "schema": "oso-strokes-session/2",
        "player": {"id": player, "handed": handed, "level": "club", "height_bin": "175-180"},
        "protocol": {"name": "drills-v1", "version": 1, "seed": 7},
        "pod": {"mac": "AA:BB:CC:DD:EE:FF", "fw": "0.1"},
        "mount": FLAT,
        "started_at": "2026-10-17T14:03:22Z",
        "parts": [{"index": 1, "file": "imu.part1.bin", "packets": len(packets), "t_first_us": t_first, "t_last_us": packets[-1][1]}],
        "open_part": None,
        "blocks": [dict(part=1, note=None, status="done", **b) for b in blocks],
        "markers": [],
        "operator_notes": "",
    }
    (d / "session.json").write_text(json.dumps(session), encoding="utf-8")
    return d


def us(t0_us, s):
    return t0_us + int(s * 1e6)


def standard_root(tmp):
    """Two players. P01: a 6-rep FH clear block and a 4-rep BH drop block (one rep short of the target). P02 (left-handed): a
    5-rep FH smash block in which an extra waggle got counted live (counted 6)."""
    root = Path(tmp) / "ds"
    t1 = [3 + 2.5 * k for k in range(6)] + [20 + 2.5 * k for k in range(4)]
    make_session(root, "S001", "P01", [
        dict(index=0, label="clear", side="FH", target=6, counted=6, t_start_us=us(1_000_000, 2), t_end_us=us(1_000_000, 18)),
        dict(index=1, label="drop", side="BH", target=5, counted=4, t_start_us=us(1_000_000, 19), t_end_us=us(1_000_000, 30)),
    ], synth(t1, 32, seed=1))
    t2 = [3 + 2.5 * k for k in range(5)]
    make_session(root, "S002", "P02", [
        dict(index=0, label="smash", side="FH", target=5, counted=6, t_start_us=us(1_000_000, 2), t_end_us=us(1_000_000, 16)),
    ], synth(t2, 18, peak_dps=1500, seed=2, quiet=True), handed="L")
    (root / "private").mkdir()
    (root / "private" / "participants.csv").write_text("id,name,consent\nP01,Alex Example,2026-10-17\nP02,Sam Example,2026-10-17\n")
    return root


# ---------------------------------------------------------------- tests

def test_reader_unwraps_seq_and_time_and_counts_faults():
    p = synth([], 2, seq0=65_500, t0_us=(1 << 32) - 300_000)  # seq and t_us both wrap inside the recording
    arrivals = p[:100] + [p[101], p[100]] + p[102:500] + [p[499]] + p[510:]  # one reorder, one dup, 10 lost
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "imu.bin"
        f.write_bytes(encode_imu(RATE, arrivals))
        r = read_imu(f)
    assert r.stats == dict(arrivals=len(arrivals), unique=len(p) - 10, lost=10, duplicate=1, reordered=1), r.stats
    assert np.all(np.diff(r.t) > 0) and abs((r.t[-1] - r.t[0]) - (len(p) - 1) / RATE) < 1e-9


def test_counter_counts_each_swing_once_and_respects_the_threshold():
    pk = synth([1.5 + 2.5 * k for k in range(5)], 15)
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "imu.bin"
        f.write_bytes(encode_imu(RATE, pk))
        r = read_imu(f)
    assert len(onsets(r.t, r.gyro, 400)) == 5
    assert len(onsets(r.t, r.gyro, 2000)) == 0  # 900 dps swings stay under a 2000 dps threshold


def test_mount_and_window_layout():
    m = Mount.from_axes(FLAT["shaft"], FLAT["normal"])
    assert np.allclose(m.rows, [[0, 1, 0], [0, 0, 1], [1, 0, 0]])  # mount.rs FLAT
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    assert d["x"].shape[1:] == (F.WINDOW_N, len(F.CHANNELS)) == (500, 10)
    # The swing is about sensor +Z = the shaft = racquet +Y: the shaft-roll channel (wy) carries it, and the peak sits at -600 ms
    first = d["x"][0]
    assert np.argmax(first[:, 6]) in (299, 300, 301)
    assert abs(first[:, 4]).max() > 3 * abs(first[:, 5]).max()


def test_python_detector_matches_rust_on_its_fixture():
    """packet.rs implausible_rest_flags_a_stream_shifted_by_seven_bytes, through the Python port."""
    fifo = b"".join(np.array([0, 0, 2048, -3, 2 + (i % 2), -1], ">i2").tobytes() for i in range(500))

    def cap(f):
        vals = np.frombuffer(f[: len(f) // 12 * 12], ">i2").reshape(-1, 6)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.bin"
            p.write_bytes(encode_imu(RATE, [(i, i * 2000, tuple(int(x) for x in v)) for i, v in enumerate(vals)]))
            return read_imu(p)
    from imu import implausible_rest
    r, s = cap(fifo), cap(fifo[7:])
    assert not implausible_rest(r.t, r.acc, r.gyro) and implausible_rest(s.t, s.acc, s.gyro)


def test_byte_shifted_recording_is_rejected():
    clean = synth([7 + 2.5 * k for k in range(3)], 14, seed=3, quiet=True)  # 6 s at rest first, as a session starts
    # The pod's bug: the FIFO stream shifted by 7 bytes, so every value is assembled from the wrong bytes
    fifo = b"".join(np.array(v, ">i2").tobytes() for _, _, v in clean)[7:]
    vals = np.frombuffer(fifo[: len(fifo) // 12 * 12], ">i2").reshape(-1, 6)
    shifted = [(s, t, tuple(int(x) for x in v)) for (s, t, _), v in zip(clean, vals)]
    with tempfile.TemporaryDirectory() as tmp:
        f, g = Path(tmp) / "clean.bin", Path(tmp) / "shifted.bin"
        f.write_bytes(encode_imu(RATE, clean))
        g.write_bytes(encode_imu(RATE, shifted))
        ok, bad = qc.session_qc(read_imu(f)), qc.session_qc(read_imu(g))
    assert ok["ok"], ok
    assert not bad["ok"] and any("misaligned" in r for r in bad["reasons"]), bad


def test_rejected_session_is_in_the_manifest_not_the_swings():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        f = root / "raw" / "S002" / "imu.part1.bin"
        b = bytearray(f.read_bytes())
        body = bytes(b[16:])
        pk = np.frombuffer(body, np.dtype([("seq", "<u2"), ("t", "<u4"), ("v", "<i2", 6)])).copy()
        fifo = pk["v"].astype(">i2").tobytes()[7:]
        n = len(fifo) // 12
        pk = pk[:n]
        pk["v"] = np.frombuffer(fifo[: n * 12], ">i2").reshape(-1, 6)
        f.write_bytes(bytes(b[:16]) + pk.tobytes())
        m = B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    s2 = next(s for s in m["sessions"] if s["id"] == "S002")
    assert not s2["ok"] and "misaligned" in s2["parts"][0]["reasons"][-1]
    assert "S002" not in set(d["session"].tolist())


def test_small_count_mismatch_keeps_the_block_label_with_a_note_and_left_handed_sides_stay():
    # Policy 2026-10-17 (trust the drill blocks): 4 of 5 reps and 5 detected vs 6 counted are small mismatches, so the swings stay
    # labelled (source=block) and carry the mismatch as a note. Large ones go to review (test_review.py).
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        m = B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    st = {(s, l): [] for s, l in zip(d["session"].tolist(), d["label"].tolist())}
    for s, l, status, why, note in zip(d["session"].tolist(), d["label"].tolist(), d["status"].tolist(), d["reasons"].tolist(), d["notes"].tolist()):
        st[(s, l)].append((status, why, note))
    assert [x[0] for x in st[("S001", "clear")]] == ["labelled"] * 6 and not any(x[2] for x in st[("S001", "clear")])
    assert len(st[("S001", "drop")]) == 4 and all(x == ("labelled", "", x[2]) and "4 of 5 reps" in x[2] for x in st[("S001", "drop")])
    smash = st[("S002", "smash")]
    assert len(smash) == 5 and all(x[0] == "labelled" and not x[1] and "5 swings detected, 6 counted live" in x[2] for x in smash)
    # P02 is left-handed: the FH label is kept as recorded (side is relative to the racquet face, never mirrored)
    lefty = d["side"][d["session"] == "S002"]
    assert set(lefty.tolist()) == {F.SIDES.index("FH")}
    assert m["counts"] == dict(swings=15, noted=9, labelled=15, review=0, unlabelled=0, discarded=0), m["counts"]


def test_duplicate_peaks_merge():
    t = np.arange(1000) / RATE
    w = np.zeros(1000)
    w[[100, 160, 400, 650]] = [800, 900, 700, 600]  # 100 and 160 are 120 ms apart; 650 is 500 ms after 400
    assert F.dedupe(t, w, [400, 160, 100, 650, 100]) == [160, 400, 650]


def test_swing_across_a_block_edge_is_one_swing():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "ds"
        # The block ends 10 ms after the swing's peak: the block's counter and the next region's fresh counter both see it
        make_session(root, "S010", "P05", [
            dict(index=0, label="drive", side="FH", target=1, counted=1, t_start_us=us(1_000_000, 1), t_end_us=us(1_000_000, 3.01)),
        ], synth([3.0], 6, seed=5))
        m = B.build(root)
    assert m["counts"]["swings"] == 1, m["counts"]


def test_review_labels_override_and_discard():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
        drops = [sid for sid, l in zip(d["swing_id"].tolist(), d["label"].tolist()) if l == "drop"]
        (root / "labels").mkdir()
        with (root / "labels" / "S001.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(B.LABEL_FIELDS)
            w.writerow([drops[0], drops[0].rsplit(":", 1)[1], "net_shot", "BH", "review", "op1"])
            w.writerow([drops[1], drops[1].rsplit(":", 1)[1], "discard", "", "review", "op1"])
        B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    row = {s: (l, st, src) for s, l, st, src in zip(d["swing_id"].tolist(), d["label"].tolist(), d["status"].tolist(), d["source"].tolist())}
    assert row[drops[0]] == ("net_shot", "labelled", "review")
    assert row[drops[1]][1] == "discarded"
    assert row[drops[2]][1] == "labelled"  # the block label stands for the swings nobody touched


def test_withdrawal_removes_every_trace():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        B.build(root, version=1)
        B.build(root, version=2)
        (root / "labels").mkdir()
        (root / "labels" / "S002.csv").write_text(",".join(B.LABEL_FIELDS) + "\n")
        W.withdraw(root, "P02", yes=True)
        assert not (root / "raw" / "S002").exists() and not (root / "labels" / "S002.csv").exists()
        assert sorted(p.name for p in (root / "processed").iterdir()) == ["qc_v2.html", "swings_v2.npz"]
        d = B.load(root / "processed" / "swings_v2.npz")
        assert "P02" not in set(d["player"].tolist()) and len(d["player"]) == 10
        for p in root.rglob("*"):
            if p.is_file():
                data = p.read_bytes()
                assert b"P02" not in data and b"Sam Example" not in data, f"{p} still mentions P02"


def test_rebuild_is_byte_identical():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        B.build(root)
        first = {p.name: p.read_bytes() for p in (root / "processed").iterdir()} | {"manifest": (root / "manifest.json").read_bytes()}
        B.build(root)
        again = {p.name: p.read_bytes() for p in (root / "processed").iterdir()} | {"manifest": (root / "manifest.json").read_bytes()}
    assert first == again


def test_session_context_default_session_json_and_meta_override():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        m = B.build(root)
        assert {s["context"] for s in m["sessions"]} == {"drill"}
        sj = root / "raw" / "S001" / "session.json"
        j = json.loads(sj.read_text())
        j["context"] = "rally"
        sj.write_text(json.dumps(j))
        assert {s["id"]: s["context"] for s in B.build(root)["sessions"]}["S001"] == "rally"
        (root / "meta").mkdir()
        (root / "meta" / "sessions.json").write_text(json.dumps({"S001": {"context": "drill"}, "S002": {"context": "rally"}}))
        B.build(root)
        z = B.load(root / "processed" / "swings_v1.npz")
        assert {(s, c) for s, c in zip(z["session"], z["context"])} == {("S001", "drill"), ("S002", "rally")}


def test_refuses_a_root_inside_the_repo():
    try:
        B.build(B.REPO / "out" / "never")
    except SystemExit as e:
        assert "inside the repo" in str(e)
    else:
        raise AssertionError("built inside the repo")


def test_replay_names_cover_every_class():
    assert sorted(F.TO_REPLAY) == sorted(F.CLASSES)
    replay = (B.REPO / "crates" / "oso-render" / "src" / "replay.rs").read_text()
    names = replay[replay.index("pub const STROKE_TYPES"):].split("];")[0]
    for v in F.TO_REPLAY.values():
        assert f'"{v}"' in names, v


def main():
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failed.append(name)
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests)} tests, {len(failed)} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
