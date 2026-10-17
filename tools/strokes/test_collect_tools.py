"""Phase 16.1 tests: the shipped collection protocols (same rules as the Rust parser), the shuffle's pinned vector (the
16.2 Python port must reproduce Rust's order), and tools/strokes/check_no_data.py.
Run from anywhere: tools/.venv/bin/python tools/strokes/test_collect_tools.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_no_data  # noqa: E402

PROTOCOLS = Path(__file__).parent / "protocols"
CLASSES = ["not_a_hit", "serve_short", "serve_long", "clear", "smash", "drop", "drive", "lift", "net_shot", "block_push", "net_kill"]
SIDELESS = {"not_a_hit", "warmup", "free"}
M = (1 << 64) - 1


def splitmix(st):
    st = (st + 0x9E3779B97F4A7C15) & M
    z = ((st ^ (st >> 30)) * 0xBF58476D1CE4E5B9) & M
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M
    return st, z ^ (z >> 31)


def shuffle(v, seed):
    """Same as oso_replay::collect::protocol::shuffle"""
    v, st = list(v), seed
    for i in range(len(v) - 1, 0, -1):
        st, r = splitmix(st)
        j = r % (i + 1)
        v[i], v[j] = v[j], v[i]
    return v


def check_protocol(p):
    blocks = p.get("warmup", []) + p["blocks"] + p.get("finale", [])
    assert p["blocks"], "no blocks"
    for b in blocks:
        assert b["label"] in CLASSES + ["warmup", "free"], b
        assert (b.get("side") is None) == (b["label"] in SIDELESS), b
        assert b.get("side") in (None, "FH", "BH"), b
        assert (b.get("target", 0) > 0) != (b.get("secs", 0) > 0), b
        assert b["instruction"].strip(), b
    return blocks


def test_protocols():
    drills = check_protocol(json.loads((PROTOCOLS / "drills-v1.json").read_text()))
    for c in CLASSES:
        assert any(b["label"] == c for b in drills), c
    assert sum(b["label"] == "smash" for b in drills) == 2
    assert [b["label"] for b in drills if b.get("secs")][-1] == "free"
    smoke = check_protocol(json.loads((PROTOCOLS / "smoke.json").read_text()))
    assert len(smoke) == 2 and all(b["target"] == 3 for b in smoke)
    print(f"protocols: drills-v1 {len(drills)} blocks, smoke {len(smoke)}")


def test_shuffle_matches_rust():
    assert shuffle(range(8), 1) == [4, 3, 2, 7, 5, 6, 0, 1]
    assert shuffle(range(8), 1) == shuffle(range(8), 1) != shuffle(range(8), 2)
    print("shuffle: pinned vector matches the Rust implementation")


def test_check_no_data():
    assert check_no_data.offenders(["raw/20261017-140322-P07/imu.bin", "a/b.mp4", "x/session.json", "participants.csv"]) == [
        "raw/20261017-140322-P07/imu.bin", "a/b.mp4", "x/session.json", "participants.csv"]
    assert check_no_data.offenders(["app/public/sample/imu.bin", "assets/replay/replay.bin", "crates/x/tests/fixtures/imu.bin", "src/main.rs"]) == []
    with tempfile.TemporaryDirectory() as d:
        git = lambda *a: subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
        git("init", "-q")
        Path(d, "ok.txt").write_text("x")
        git("add", ".")
        assert check_no_data.main(d) == 0
        Path(d, "clip.mov").write_bytes(b"x")
        git("add", "-f", ".")
        assert check_no_data.main(d) == 1
    assert check_no_data.main(Path(__file__).resolve().parents[2]) == 0, "this repo tracks session data or video"
    print("check_no_data: ok")


if __name__ == "__main__":
    test_protocols()
    test_shuffle_matches_rust()
    test_check_no_data()
