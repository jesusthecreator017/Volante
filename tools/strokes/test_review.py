"""Phase 16.4a part 1 tests (synthetic sessions, no real data): the label policy (small count mismatch -> labelled with a note,
large mismatch / outlier -> review), the label CSV round trip and rebuild, and the local review server's validation.
Run from anywhere: tools/.venv/bin/python tools/strokes/test_review.py"""
import csv
import http.client
import json
import sys
import tempfile
import threading
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "review"))
import build_dataset as B  # noqa: E402
import qc  # noqa: E402
import server as S  # noqa: E402
from test_dataset import make_session, standard_root, synth, us  # noqa: E402

T0 = 1_000_000


def blk(index, label, side, target, counted, start, end):
    return dict(index=index, label=label, side=side, target=target, counted=counted, t_start_us=us(T0, start), t_end_us=us(T0, end))


def policy_root(tmp):
    """One session, four blocks (2.5 s per rep):
    0 clear FH   target 5 live 5, 6 detected  -> small mismatch: labelled + note
    1 drop BH    target 5 live 5, 8 detected  -> 8 > 1.5 x 5: review
    2 lift FH    target 10 live 10, 4 detected -> 4 < 0.5 x 10: review
    3 drive FH   target 10 live 10, 10 detected, the 6th one far stronger -> labelled, the outlier goes to review"""
    root = Path(tmp) / "ds"
    times = [3 + 2.5 * k for k in range(6)] + [22 + 2.5 * k for k in range(8)] + [45 + 2.5 * k for k in range(4)] + [60 + 2.5 * k for k in range(10)]
    amp = list(np.random.default_rng(3).uniform(850, 950, len(times)))  # real swings vary; identical ones make the MAD vanish
    amp[-5] = 2500.0  # the 6th drive
    pk = synth(times, 90, peak_dps=amp, seed=11)
    make_session(root, "S001", "P01", [
        blk(0, "clear", "FH", 5, 5, 2, 19), blk(1, "drop", "BH", 5, 5, 21, 43),
        blk(2, "lift", "FH", 10, 10, 44, 57), blk(3, "drive", "FH", 10, 10, 59, 86)], pk)
    return root


def status_by_label(d):
    out = {}
    for l, st in zip(d["label"].tolist(), d["status"].tolist()):
        out.setdefault(l, []).append(st)
    return out


def test_count_mismatch_policy_unit():
    b = lambda t, c: dict(index=1, label="clear", side="FH", target=t, counted=c)  # noqa: E731
    assert qc.count_mismatch(b(25, 25), 25) == (None, False)
    note, large = qc.count_mismatch(b(25, 25), 27)
    assert note and not large
    assert qc.count_mismatch(b(25, 37), 37)[1] is False and qc.count_mismatch(b(25, 38), 38)[1] is True  # 1.5 x 25 = 37.5, live agrees
    assert qc.count_mismatch(b(25, 69), 69) == ("block 1 (clear FH): 69 of 25 reps", True)
    assert qc.count_mismatch(b(25, 25), 12)[1] is True and qc.count_mismatch(b(25, 25), 13)[1] is False  # 0.5 x 25 = 12.5
    assert qc.count_mismatch(b(0, 0), 5) == ("block 1 (clear FH): 5 swings detected, 0 counted live", False)  # timed block: never large


def test_policy_small_note_large_review_outlier_review():
    with tempfile.TemporaryDirectory() as tmp:
        root = policy_root(tmp)
        m = B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    st = status_by_label(d)
    assert st["clear"] == ["labelled"] * 6
    note = d["notes"][d["label"] == "clear"].tolist()
    assert all("6 swings detected, 5 counted live" in n for n in note) and not any(d["reasons"][d["label"] == "clear"].tolist())
    assert st["drop"] == ["review"] * 8 and "8 swings detected" in d["reasons"][d["label"] == "drop"][0]
    assert st["lift"] == ["review"] * 4
    assert sorted(st["drive"]) == ["labelled"] * 9 + ["review"]
    out = d["reasons"][(d["label"] == "drive") & (d["status"] == "review")][0]
    assert "far from" in out
    assert m["counts"]["noted"] == 6 + 8 + 4 and m["counts"]["review"] == 8 + 4 + 1


def test_label_csv_round_trip_replace_discard_and_rebuild():
    with tempfile.TemporaryDirectory() as tmp:
        root = policy_root(tmp)
        B.build(root)
        s = S.Store(root, 1, "R1")
        ids = [s.d["swing_id"][i] for i in s.queue]
        a, b, c = ids[0], ids[1], ids[2]
        s.set_label(a, "smash", "FH")
        s.set_label(b, "discard", "")
        s.set_label(a, "clear", "BH")  # replaces a's row
        s.set_label(c, "not_a_hit", "")
        s.set_label(c, clear=True)
        p = root / "labels" / "S001.csv"
        rows = list(csv.DictReader(p.open(newline="")))
        assert [r["swing_id"] for r in rows] == sorted([a, b])
        assert {r["swing_id"]: (r["class"], r["side"], r["source"], r["reviewer"]) for r in rows} == {
            a: ("clear", "BH", "review", "R1"), b: ("discard", "", "review", "R1")}
        assert list(rows[0]) == B.LABEL_FIELDS
        assert B.load_labels(root, "S001").keys() == {a, b}
        B.build(root)
        d = B.load(root / "processed" / "swings_v1.npz")
    row = {sid: (l, sd, st, src) for sid, l, sd, st, src in zip(*(d[k].tolist() for k in ("swing_id", "label", "side", "status", "source")))}
    assert row[a] == ("clear", 1, "labelled", "review") and row[b][2] == "discarded" and row[c][2] == "review"


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        c.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)


def with_server(fn):
    with tempfile.TemporaryDirectory() as tmp:
        root = policy_root(tmp)
        B.build(root)
        srv = S.serve(root, 1, "R7", port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            fn(root, Client(srv.server_port))
        finally:
            srv.shutdown()
            srv.server_close()


def test_server_reads_and_writes_labels():
    def run(root, c):
        code, q = c.call("GET", "/api/queue")
        assert code == 200 and len(q["items"]) == 13 and q["items"][0]["override"] is None, q
        sid = q["items"][0]["id"]
        code, sw = c.call("GET", "/api/swing/" + q["items"][-1]["id"])  # the drive outlier: labelled drives to compare with
        assert code == 200 and len(sw["t_ms"]) == len(sw["w"]) == len(sw["a"]) == 250 and len(sw["axes"]) == 6 and len(sw["sat"]) == 250
        assert sw["compare"]["n"] > 0 and len(sw["mean_w"]) == 250
        code, sw = c.call("GET", "/api/swing/" + sid + "?class=smash&side=BH")  # no labelled smash in the fixture
        assert code == 200 and sw["compare"]["n"] == 0 and sw["mean_w"] is None
        code, r = c.call("POST", "/api/label", dict(swing_id=sid, **{"class": "smash"}, side="FH"))
        assert code == 200 and r["override"] == {"cls": "smash", "side": "FH"}
        c.call("POST", "/api/label", dict(swing_id=sid, **{"class": "discard"}))
        rows = list(csv.DictReader((root / "labels" / "S001.csv").open(newline="")))
        assert len(rows) == 1 and rows[0]["class"] == "discard" and rows[0]["reviewer"] == "R7" and rows[0]["source"] == "review"
        assert c.call("GET", "/api/queue")[1]["items"][0]["override"]["cls"] == "discard"
        assert c.call("POST", "/api/label", dict(swing_id=sid, clear=True))[0] == 200
        assert len(list(csv.DictReader((root / "labels" / "S001.csv").open(newline="")))) == 0
        code, page = c.call("GET", "/")
        assert code == 200 and b"review.js" in page
    with_server(run)


def test_server_rejects_bad_requests_and_stays_inside_labels():
    def run(root, c):
        before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
        sid = c.call("GET", "/api/queue")[1]["items"][0]["id"]
        ok = {"swing_id": sid, "class": "clear", "side": "FH"}
        bad = [
            ("invalid class", c.call("POST", "/api/label", {**ok, "class": "flick"}), 400),
            ("sided class without side", c.call("POST", "/api/label", {"swing_id": sid, "class": "clear"}), 400),
            ("bad side", c.call("POST", "/api/label", {**ok, "side": "LEFT"}), 400),
            ("side on not_a_hit", c.call("POST", "/api/label", {**ok, "class": "not_a_hit"}), 400),
            ("unknown swing", c.call("POST", "/api/label", {**ok, "swing_id": "S001:p1:1"}), 404),
            ("path in swing id", c.call("POST", "/api/label", {**ok, "swing_id": "../../evil:p1:1"}), 404),
            ("not json", c.call("POST", "/api/label", raw="nope"), 400),
            ("not an object", c.call("POST", "/api/label", raw="[1]"), 400),
            ("oversize", c.call("POST", "/api/label", {**ok, "pad": "x" * 5000}), 413),
            ("bad Host", c.call("POST", "/api/label", ok, headers={"Host": "evil.example"}), 403),
            ("bad Origin", c.call("POST", "/api/label", ok, headers={"Origin": "http://evil.example"}), 403),
            ("bad Host on GET", c.call("GET", "/api/queue", headers={"Host": "rebind.example:8765"}), 403),
            ("GET on /api/label", c.call("GET", "/api/label"), 404),
            ("PUT", c.call("PUT", "/api/label", ok), 405),
            ("DELETE", c.call("DELETE", "/api/label"), 405),
            ("POST elsewhere", c.call("POST", "/api/queue", ok), 404),
            ("no static traversal", c.call("GET", "/../server.py"), 404),
        ]
        for name, (code, _), want in bad:
            assert code == want, (name, code)
        assert c.call("POST", "/api/label", ok, headers={"Origin": f"http://localhost:{c.port}"})[0] == 200
        after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
        assert [p for p in after if p not in before] == ["labels", "labels/S001.csv"], after
        assert not list((root / "labels").glob(".tmp-*"))
    with_server(run)


def test_server_refuses_remote_binding_and_real_names():
    with tempfile.TemporaryDirectory() as tmp:
        root = policy_root(tmp)
        B.build(root)
        for kw in (dict(reviewer="R1", host="0.0.0.0"), dict(reviewer="Alice"), dict(reviewer="R1234"), dict(reviewer="r1")):
            try:
                S.serve(root, 1, port=0, **kw)
            except SystemExit:
                continue
            raise AssertionError(kw)


def test_rebuild_is_still_deterministic_with_notes():
    with tempfile.TemporaryDirectory() as tmp:
        root = standard_root(tmp)
        B.build(root)
        a = (root / "processed" / "swings_v1.npz").read_bytes()
        B.build(root)
        assert a == (root / "processed" / "swings_v1.npz").read_bytes()


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
