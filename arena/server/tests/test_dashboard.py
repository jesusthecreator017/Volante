"""Dashboard aggregates (dashboard.aggregate, a pure function). Run:  python -m pytest -q tests/test_dashboard.py"""
import copy
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from osoarena import dashboard, reports  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
BASE = json.loads((ROOT / "docs/strokes/report.example-strokes.json").read_text())
TODAY = date(2026, 10, 18)  # a Sunday, ISO week 2026-W42


def rep(started_at, source="pod", swings=10, p90=60.0, mx=90.0, rallies=2, longest=5, active_s=600.0, hr=None, rate=30.0,
        gap=2.0, groups=None):
    """A valid volante-report/1 with the given numbers (strokes only when `groups` is given)."""
    r = copy.deepcopy(BASE)
    r["session"].update(started_at=started_at, source=source, active_s=active_s, duration_s=active_s + 60)
    r["swings"].update(count=swings, per_min_active=rate)
    r["speed"].update(p50_kmh=p90 and p90 / 2, p90_kmh=p90, max_kmh=mx)
    r["tempo"]["median_gap_s"] = gap
    r["rallies"].update(count=rallies, longest_shots=longest)
    r["heart_rate"] = None if hr is None else {"avg_bpm": hr, "max_bpm": hr + 30}
    if groups is None:
        r["strokes"] = r["model"] = None
    else:
        r["strokes"]["groups"] = groups
    return reports.validate_report(r)


def fixture():
    """5 pod + 2 watch sessions over three ISO weeks; the watch ones have the fastest 'speeds', which must never count."""
    return [
        {"id": "p1", "report": rep("2026-10-01T18:00:00Z", swings=100, p90=50.0, mx=80.0, rallies=10, longest=8, active_s=1200, rate=5.0)},
        {"id": "p2", "report": rep("2026-10-03T18:00:00Z", swings=200, p90=55.0, mx=95.5, rallies=20, longest=12, active_s=1800, rate=6.7,
                                   hr=140, groups={"power": 10, "mid": 20, "touch": 10})},
        {"id": "p3", "report": rep("2026-10-10T18:00:00Z", swings=50, p90=60.0, mx=88.0, rallies=5, longest=4, active_s=300, rate=10.0,
                                   groups={"power": 5, "mid": 5, "touch": 5, "not_a_hit": 3, "unclassified": 2})},
        {"id": "p4", "report": rep("2026-10-12T18:00:00Z", swings=0, p90=None, mx=None, rallies=0, longest=0, active_s=0, rate=None,
                                   gap=None)},
        {"id": "p5", "report": rep("2026-10-17T09:30:00Z", swings=150, p90=62.0, mx=91.0, rallies=15, longest=20, active_s=900, rate=10.0,
                                   hr=150)},
        {"id": "w1", "report": rep("2026-10-05T07:00:00Z", source="watch", swings=300, p90=150.0, mx=200.0, rallies=0, longest=0,
                                   active_s=600, rate=30.0)},
        {"id": "w2", "report": rep("2026-10-16T07:00:00Z", source="watch", swings=40, p90=140.0, mx=190.0, rallies=1, longest=3,
                                   active_s=120, rate=20.0)},
    ]


def test_dashboard_aggregates():
    rows = fixture()
    d = dashboard.aggregate(list(reversed(rows)), {"schema": "volante-style/1"}, 7, TODAY)
    assert d["totals"] == {"sessions": 7, "swings": 840, "active_min": 82.0, "rallies": 51}
    assert len(d["weekly"]) == 12 and d["weekly"][0]["week"] == "2026-W31" and d["weekly"][-1]["week"] == "2026-W42"
    assert all(w == {"week": w["week"], "sessions": 0, "swings": 0, "active_min": 0} for w in d["weekly"][:9])
    assert d["weekly"][9:] == [
        {"week": "2026-W40", "sessions": 2, "swings": 300, "active_min": 50.0},   # p1, p2
        {"week": "2026-W41", "sessions": 2, "swings": 350, "active_min": 15.0},   # w1, p3
        {"week": "2026-W42", "sessions": 3, "swings": 190, "active_min": 17.0},   # p4, w2, p5
    ]
    assert [t["id"] for t in d["trends"]] == ["p1", "p2", "p3", "p4", "p5"]  # pod only, oldest first
    assert d["trends"][1] == {"id": "p2", "started_at": "2026-10-03T18:00:00Z", "p90_kmh": 55.0, "swings_per_min": 6.7,
                              "median_gap_s": 2.0, "hr_avg": 140}
    assert d["trends"][3]["p90_kmh"] is None and d["trends"][3]["swings_per_min"] is None
    assert d["wrist"] == {"sessions": 2, "swings": 340}
    b = d["bests"]
    assert b["max_kmh"] == {"value": 95.5, "session_id": "p2", "started_at": "2026-10-03T18:00:00Z"}  # not the watch's 200
    assert b["most_swings"] == {"value": 300, "session_id": "w1", "started_at": "2026-10-05T07:00:00Z"}
    assert b["longest_rally"] == {"value": 20, "session_id": "p5", "started_at": "2026-10-17T09:30:00Z"}
    assert b["best_rate"] == {"value": 30.0, "session_id": "w1", "started_at": "2026-10-05T07:00:00Z"}
    s = d["strokes"]
    assert s["sessions"] == 2 and s["experimental"] is True
    assert s["groups"] == {"power": 0.25, "mid": 0.4167, "touch": 0.25, "not_a_hit": 0.05, "unclassified": 0.0333}
    assert s["overall"] == s["groups"]  # every stroked session is within the last 10
    assert d["style"] == {"schema": "volante-style/1"} and d["twin_model_id"] == 7
    json.dumps(d, allow_nan=False)


def test_dashboard_strokes_last_10_only():
    rows = [{"id": f"s{i:02d}", "report": rep(f"2026-10-{i + 1:02d}T10:00:00Z", groups={"power": 1} if i < 2 else {"touch": 1})}
            for i in range(12)]
    s = dashboard.aggregate(rows, None, None, TODAY)["strokes"]
    assert s["sessions"] == 10 and s["groups"]["touch"] == 1.0 and s["groups"]["power"] == 0.0
    assert s["overall"] == {"power": 0.1667, "mid": 0.0, "touch": 0.8333, "not_a_hit": 0.0, "unclassified": 0.0}


def test_dashboard_stroke_shares_sum_to_one():
    rows = [{"id": "a", "report": rep("2026-10-01T10:00:00Z", groups={"power": 1, "mid": 1, "touch": 1})}]
    s = dashboard.aggregate(rows, None, None, TODAY)["strokes"]
    for mix in (s["groups"], s["overall"]):
        assert abs(sum(mix.values()) - 1) < 1e-9 and mix["power"] == 0.3334 and mix["mid"] == mix["touch"] == 0.3333
    zero = [{"id": "z", "report": rep("2026-10-01T10:00:00Z", groups={"power": 0})}]
    assert dashboard.aggregate(zero, None, None, TODAY)["strokes"] is None


def test_dashboard_watch_only():
    rows = [r for r in fixture() if r["id"].startswith("w")]
    d = dashboard.aggregate(rows, None, None, TODAY)
    assert d["totals"]["sessions"] == 2 and d["wrist"] == {"sessions": 2, "swings": 340}
    assert d["trends"] == [] and d["bests"]["max_kmh"] is None
    assert d["bests"]["most_swings"]["session_id"] == "w1"
    assert d["strokes"] is None and d["style"] is None and d["twin_model_id"] is None


def test_dashboard_empty():
    d = dashboard.aggregate([], None, None, TODAY)
    assert d["totals"] == {"sessions": 0, "swings": 0, "active_min": 0, "rallies": 0}
    assert len(d["weekly"]) == 12 and all(w["sessions"] == 0 for w in d["weekly"])
    assert d["trends"] == [] and d["wrist"] == {"sessions": 0, "swings": 0}
    assert d["bests"] == {"max_kmh": None, "most_swings": None, "longest_rally": None, "best_rate": None}
    assert d["strokes"] is None


def test_weeks_cross_the_year():
    d = dashboard.aggregate([{"id": "a", "report": rep("2025-12-29T10:00:00Z")}], None, None, date(2026, 1, 4))
    assert d["weekly"][-1] == {"week": "2026-W01", "sessions": 1, "swings": 10, "active_min": 10.0}
    assert d["weekly"][-2]["week"] == "2025-W52"
