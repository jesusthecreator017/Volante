"""volante-report/1 validator + app session tables. Run:  python -m pytest -q tests/test_reports.py"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

os.environ["OSO_ARENA_DATA_DIR"] = tempfile.mkdtemp(prefix="arena-reports-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from osoarena import db, reports  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
FILES = [ROOT / "docs/strokes/report.example.json", ROOT / "docs/strokes/report.example-strokes.json",
         ROOT / "tools/strokes/data/report_vector.json"]


def load(p):
    return json.loads(p.read_text())


def test_examples_validate():
    for p in FILES:
        r = reports.validate_report(load(p))
        s = reports.summary_of(r)
        assert s["swings"] == r["swings"]["count"] and s["has_strokes"] == (r["strokes"] is not None)
    assert len(json.dumps(load(FILES[1]))) < reports.MAX_BYTES


def test_validator_rejects_malformed():
    base = load(FILES[1])

    def mut(f):
        r = copy.deepcopy(base)
        f(r)
        return r

    bad = [
        mut(lambda r: r["speed"].update(p90_kmh=float("nan"))),
        mut(lambda r: r["speed"].update(max_kmh=float("inf"))),
        mut(lambda r: r["swings"].update(count=-1)),
        mut(lambda r: r.update(schema="volante-report/2")),
        mut(lambda r: r.update(extra=1)),
        mut(lambda r: r.update(model=None)),
        mut(lambda r: r["insights"].append({"id": "x", "severity": "info", "title": "t", "body": "b" * 100_000, "evidence": {}})),
        mut(lambda r: r["session"].update(source="phone")),
        mut(lambda r: r["timeline"].extend([r["timeline"][0]] * 600)),
        mut(lambda r: r["session"].update(started_at="2026-13-45T25:61:61Z")),
        mut(lambda r: r["session"].update(duration_s=1e308)),
        mut(lambda r: r["session"].update(active_s=86401)),
        mut(lambda r: r["swings"].update(count=10**7)),
        mut(lambda r: r["speed"].update(max_kmh=1e308)),
        mut(lambda r: r.update(heart_rate={"avg_bpm": 120, "max_bpm": 301})),
        mut(lambda r: r["timeline"][0].update(t_min=1441)),
        [], "x", None,
    ]
    for r in bad:
        with pytest.raises(ValueError):
            reports.validate_report(r)


def test_migrate_adds_tables_and_column():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    db.migrate(con)
    db.migrate(con)  # no-op
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"app_sessions", "app_style"} <= names
    assert "app_linked_at" in {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    assert con.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='app_sessions_time'").fetchone()


def test_validator_rejects_type_and_format_edge_cases():
    base = load(FILES[1])

    def mut(f):
        r = copy.deepcopy(base)
        f(r)
        return r

    bad = [
        mut(lambda r: r["swings"].update(count=True)),                     # bool where an integer is expected
        mut(lambda r: r["speed"].update(p90_kmh=False)),                   # bool where a number is expected
        mut(lambda r: r["session"].update(duration_s=True)),
        mut(lambda r: r["session"].update(started_at="2026-10-17T18:00:00Z\n")),  # trailing newline
        mut(lambda r: r["session"].update(pod_fw="unknown\n")),
        mut(lambda r: r["insights"].append({"id": "x\n", "severity": "info", "title": "t", "body": "b", "evidence": {}})),
        mut(lambda r: r["speed"].update(estimated=1)),
        mut(lambda r: r["speed"].update(estimated=False)),
        mut(lambda r: r.update(heart_rate={"avg_bpm": 120})),
        mut(lambda r: r.update(heart_rate={})),
    ]
    for r in bad:
        with pytest.raises(ValueError):
            reports.validate_report(r)
