"""QA additions: hostile headers / files must be rejected cleanly, log.read on damaged files, the exact game-end rule."""
import copy
import gzip
import json
import os
import struct
import tempfile
from pathlib import Path

import numpy as np

from helpers import meta_for, random_actor, run, write_st
from oso_arena import check, contract, log, match, sim


def _good(d):
    p = write_st(Path(d) / "g.safetensors", random_actor(), meta_for())
    raw = p.read_bytes()
    n = struct.unpack("<Q", raw[:8])[0]
    return json.loads(raw[8:8 + n]), raw[8 + n:]


def _put(d, header, body, rawh=None):
    h = rawh if rawh is not None else json.dumps(header).encode()
    p = Path(d) / "x.safetensors"
    p.write_bytes(struct.pack("<Q", len(h)) + h + body)
    return str(p)


def test_hostile_files_are_rejected_cleanly():
    with tempfile.TemporaryDirectory() as d:
        H, body = _good(d)
        raw = (Path(d) / "g.safetensors").read_bytes()
        bad = {}
        for n in (2**64 - 1, 2**63, 2**64 - 8, 65_537):
            bad[f"len {n}"] = struct.pack("<Q", n) + raw[8:]
        h = copy.deepcopy(H); h["actor.0.bias"]["data_offsets"] = [10**9, 10**9 + 1024]; bad["past EOF"] = None, h
        h = copy.deepcopy(H); h["actor.0.bias"]["data_offsets"] = list(h["actor.0.weight"]["data_offsets"]); bad["overlap"] = None, h
        for dt in ("F32 ", "f32", "F32\x00", "float32"):
            h = copy.deepcopy(H); h["actor.0.bias"]["dtype"] = dt; bad[f"dtype {dt!r}"] = None, h
        for v in ([], "x", None, {"contract": 1}, {"a": {"b": "c"}}):
            h = copy.deepcopy(H); h["__metadata__"] = v; bad[f"__metadata__ {v!r}"] = None, h
        for b in ('{"power":{"a":1},"speed":6,"reach":6,"accuracy":6}', "[6,6,6,6]", "null", '{"power":NaN,"speed":6,"reach":6,"accuracy":6}',
                  "[" * 190 + "]" * 10, '{"power":6,"power":9,"speed":6,"reach":3,"accuracy":3}'):
            h = copy.deepcopy(H); h["__metadata__"]["build"] = b; bad[f"build {b[:20]}"] = None, h
        for nm in ("   ", "\t", " ", "​", "‮abc", ""):
            h = copy.deepcopy(H); h["__metadata__"]["name"] = nm; bad[f"name {nm!r}"] = None, h
        # duplicate keys: last-wins in Python would hide an early bad entry from the layout check
        s = json.dumps(H)
        bad["dup tensor key"] = None, None, ('{"actor.0.bias": {"dtype":"F64","shape":[1],"data_offsets":[0,8]}, ' + s[1:]).encode()
        bad["dup __metadata__"] = None, None, (s[:-1] + ', "__metadata__": {"name":"x"}}').encode()
        bad["json bomb"] = None, None, b"[" * 60000
        bad["trailing bytes"] = raw + b"zz"
        bad["1MiB+1"] = b"\0" * (contract.MAX_BYTES + 1)
        for name, v in bad.items():
            if isinstance(v, bytes):
                p = Path(d) / "y.safetensors"; p.write_bytes(v); path = str(p)
            else:
                path = _put(d, v[1], body, v[2] if len(v) > 2 else None)
            r = check.check_file(path)
            assert not r.ok and r.reasons, name
        for p in (d, "/dev/null", "/dev/zero", str(Path(d) / "missing")):
            assert not check.check_file(p).ok
        fifo = Path(d) / "fifo"
        os.mkfifo(fifo)
        assert check.check_file(str(fifo)).reasons == ["cannot read the file: not a regular file"]  # must not block
        # sanity: the unmodified file passes
        assert check.check_file(str(Path(d) / "g.safetensors")).ok


def test_log_read_rejects_damaged_files_with_valueerror():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "m.gz"
        log.write(p, match.play("bot", "bot", 1, target=3, best_of=1))
        good = p.read_bytes()
        for name, raw in (("random", os.urandom(64)), ("empty", b""), ("truncated", good[:-1]), ("half", good[:len(good) // 2]),
                          ("bomb", gzip.compress(b"\0" * ((256 << 20) + 10), 1)),
                          ("deep json", gzip.compress(b"OSML" + struct.pack("<I", 200000) + b"[" * 200000))):
            p.write_bytes(raw)
            try:
                log.read(p)
            except ValueError:
                pass
            else:
                raise AssertionError(name)
        p.write_bytes(good)
        assert log.read(p).seed == 1


def test_game_end_rule_win_by_2_cap_21():
    for a in range(0, 22):  # is_over(scorer, other) is evaluated right after the scorer's point; scores reach 21-19 at most via 20-20
        for b in range(0, 22):
            if a == 21 and b == 21:
                continue
            assert bool(sim.is_over(a, b)) == (a >= 21 or (a >= 15 and a - b >= 2)), (a, b)
    assert not sim.is_over(14, 0) and not sim.is_over(15, 14) and sim.is_over(15, 13) and not sim.is_over(20, 20) and sim.is_over(21, 20)
    assert match.VOID_S == 150 * 60.0 and (match.INTERVAL_MID_S, match.INTERVAL_GAME_S, match.INTERVAL_AT) == (60.0, 120.0, 8)
    assert check.SMOKE_TARGET == 5 and check.SMOKE_SEEDS == (1, 2)


if __name__ == "__main__":
    run(globals())
