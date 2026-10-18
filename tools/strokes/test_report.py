"""volante-report/1: the reference implementation reproduces the committed parity vector, and the examples validate against the
schema (a small validator for the keywords the schema uses; the repo has no jsonschema dependency). Run: python tools/strokes/test_report.py"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/strokes"))
import report  # noqa: E402

SCHEMA = json.loads((ROOT / "docs/strokes/report.schema.json").read_text())


def check(v, s, path="$"):
    errs = []
    t = s.get("type")
    types = t if isinstance(t, list) else [t] if t else []
    ok_type = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    if types:
        good = any((x == "number" and isinstance(v, (int, float)) and not isinstance(v, bool)) or
                   (x == "integer" and isinstance(v, int) and not isinstance(v, bool)) or
                   (x in ok_type and isinstance(v, ok_type[x])) for x in types)
        if not good:
            return [f"{path}: {v!r} is not {types}"]
    if "const" in s and v != s["const"]:
        errs.append(f"{path}: must be {s['const']!r}")
    if "enum" in s and v not in s["enum"]:
        errs.append(f"{path}: {v!r} not in {s['enum']}")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in s and v < s["minimum"]:
            errs.append(f"{path}: below {s['minimum']}")
        if "maximum" in s and v > s["maximum"]:
            errs.append(f"{path}: above {s['maximum']}")
    if isinstance(v, str):
        if "pattern" in s and not re.search(s["pattern"], v):
            errs.append(f"{path}: {v!r} does not match {s['pattern']}")
        if "maxLength" in s and len(v) > s["maxLength"]:
            errs.append(f"{path}: too long")
    if isinstance(v, dict):
        props = s.get("properties", {})
        for k in s.get("required", []):
            if k not in v:
                errs.append(f"{path}: missing {k}")
        for k, x in v.items():
            if k in props:
                errs += check(x, props[k], f"{path}.{k}")
            elif s.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key {k}")
            elif isinstance(s.get("additionalProperties"), dict):
                errs += check(x, s["additionalProperties"], f"{path}.{k}")
    if isinstance(v, list):
        if "maxItems" in s and len(v) > s["maxItems"]:
            errs.append(f"{path}: too many items")
        for i, x in enumerate(v):
            errs += check(x, s.get("items", {}), f"{path}[{i}]")
    return errs


def test_vector():
    got = report.from_file(ROOT / "arena/kit/tests/data/twins_vector.imu.bin", "identity", started_at="2026-10-17T18:00:00Z")
    want = json.loads((ROOT / "tools/strokes/data/report_vector.json").read_text())
    assert got == want, (got, want)
    # agrees with the style profile of the same recording (same swing and speed definitions)
    prof = json.loads((ROOT / "arena/kit/tests/data/twins_vector.profile.json").read_text())
    assert got["swings"]["count"] == prof["source"]["swings"] and got["speed"]["p50_kmh"] == prof["speed"]["p50_kmh"]
    assert got["tempo"] == prof["tempo"] and got["speed"]["clipped_frac"] == prof["speed"]["clipped_frac"]
    assert not check(got, SCHEMA), check(got, SCHEMA)


def test_examples_and_rules():
    for name in ("report.example.json", "report.example-strokes.json"):
        d = json.loads((ROOT / "docs/strokes" / name).read_text())
        assert not check(d, SCHEMA), (name, check(d, SCHEMA))
        assert (d["strokes"] is None) == (d["model"] is None), name  # both or neither
    bad = json.loads((ROOT / "docs/strokes/report.example.json").read_text())
    bad["session"]["player"] = "Alvaro"  # no personal fields
    assert check(bad, SCHEMA)
    bad = json.loads((ROOT / "docs/strokes/report.example.json").read_text())
    bad["speed"]["estimated"] = False
    assert check(bad, SCHEMA)


def test_no_swings():
    import numpy as np
    t = np.arange(0, 10, 0.002)
    r = report.build([(t, np.zeros((len(t), 3), np.float32), np.zeros(len(t), bool))], "2026-10-17T18:00:00Z")
    assert r["swings"]["count"] == 0 and r["speed"]["p50_kmh"] is None and r["tempo"]["median_gap_s"] is None
    assert r["rallies"] == {"count": 0, "median_shots": None, "longest_shots": 0} and r["heart_rate"] is None
    assert not check(r, SCHEMA)


if __name__ == "__main__":
    for f in (test_vector, test_examples_and_rules, test_no_swings):
        f()
        print("ok", f.__name__)
