"""volante-report/1 validator (docs/strokes/report-format.md, report.schema.json), hand-written, no dependencies."""
import math
import re
from datetime import datetime

MAX_BYTES = 16384
TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
_FW = re.compile(r"[A-Za-z0-9._ ,-]{1,64}")
_ID = re.compile(r"[a-z_]{1,40}")
# Upper bounds (stricter than report.schema.json) so summed/charted numbers stay sane: a day, a million, 1000 km/h, 300 bpm.
DAY_S, COUNT, KMH, BPM, DAY_MIN = 86400, 1_000_000, 1000, 300, 1440


def _bad(path, why):
    raise ValueError(f"{path}: {why}")


def _obj(v, path, keys):
    if not isinstance(v, dict):
        _bad(path, "must be an object")
    if set(v) != set(keys):
        _bad(path, f"keys must be exactly {sorted(keys)}")
    return v


def _num(v, path, lo=None, hi=None, null=False, integer=False):
    if v is None and null:
        return
    ok = isinstance(v, int) and not isinstance(v, bool) if integer else isinstance(v, (int, float)) and not isinstance(v, bool)
    if not ok or (isinstance(v, float) and not math.isfinite(v)):
        _bad(path, "must be an integer" if integer else "must be a finite number")
    if lo is not None and v < lo or hi is not None and v > hi:
        _bad(path, "out of range")


def _str(v, path, pat=None, maxlen=None):
    if not isinstance(v, str) or (pat and not pat.fullmatch(v)) or (maxlen and len(v) > maxlen):
        _bad(path, "invalid string")


def _count_map(m, path, depth):
    if not isinstance(m, dict):
        _bad(path, "must be an object")
    for k, v in m.items():
        _str(k, path, maxlen=40)
        _count_map(v, f"{path}.{k}", depth - 1) if depth > 1 else _num(v, f"{path}.{k}", 0, COUNT, integer=True)


def _strokes(s):
    keys = set(s) if isinstance(s, dict) else set()
    if not {"groups", "experimental"} <= keys or not keys <= {"groups", "counts", "speed_by_class", "unclassified", "experimental"}:
        _bad("strokes", "bad keys")
    _count_map(s["groups"], "strokes.groups", 1)
    if "counts" in s:
        _count_map(s["counts"], "strokes.counts", 2)
    sbc = s.get("speed_by_class", {})
    if not isinstance(sbc, dict):
        _bad("strokes.speed_by_class", "must be an object")
    for cls, d in sbc.items():
        _str(cls, "strokes.speed_by_class", maxlen=40)
        if not isinstance(d, dict):
            _bad("strokes.speed_by_class", "must be an object")
        for k, v in d.items():
            _str(k, "strokes.speed_by_class", maxlen=40)
            _num(v, "strokes.speed_by_class", hi=COUNT)
    if "unclassified" in s:
        _num(s["unclassified"], "strokes.unclassified", 0, COUNT, integer=True)
    if not isinstance(s["experimental"], bool):
        _bad("strokes.experimental", "must be a boolean")


def _insight(i, path):
    _obj(i, path, ("id", "severity", "title", "body", "evidence"))
    _str(i["id"], path + ".id", _ID)
    if i["severity"] not in ("info", "tip", "praise"):
        _bad(path + ".severity", "invalid")
    _str(i["title"], path + ".title", maxlen=80)
    _str(i["body"], path + ".body", maxlen=280)
    if not isinstance(i["evidence"], dict):
        _bad(path + ".evidence", "must be an object")
    for k, v in i["evidence"].items():
        _str(k, path + ".evidence", maxlen=40)
        _num(v, f"{path}.evidence.{k}")


def validate_report(r):
    _obj(r, "report", ("schema", "session", "swings", "speed", "tempo", "rallies", "timeline", "heart_rate", "strokes", "insights", "model"))
    if r["schema"] != "volante-report/1":
        _bad("schema", "must be volante-report/1")
    s = _obj(r["session"], "session", ("started_at", "duration_s", "active_s", "source", "pod_fw"))
    _str(s["started_at"], "session.started_at", TS)
    try:
        datetime.strptime(s["started_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _bad("session.started_at", "not a real date")
    _num(s["duration_s"], "session.duration_s", 0, DAY_S)
    _num(s["active_s"], "session.active_s", 0, DAY_S)
    if s["source"] not in ("pod", "watch"):
        _bad("session.source", "must be pod or watch")
    _str(s["pod_fw"], "session.pod_fw", _FW)
    w = _obj(r["swings"], "swings", ("count", "per_min_active"))
    _num(w["count"], "swings.count", 0, COUNT, integer=True)
    _num(w["per_min_active"], "swings.per_min_active", 0, KMH, null=True)
    sp = _obj(r["speed"], "speed", ("p50_kmh", "p90_kmh", "max_kmh", "clipped_frac", "estimated"))
    for k in ("p50_kmh", "p90_kmh", "max_kmh"):
        _num(sp[k], "speed." + k, hi=KMH, null=True)
    _num(sp["clipped_frac"], "speed.clipped_frac", 0, 1)
    if sp["estimated"] is not True:
        _bad("speed.estimated", "must be true")
    t = _obj(r["tempo"], "tempo", ("median_gap_s", "p25_gap_s", "p75_gap_s"))
    for k, v in t.items():
        _num(v, "tempo." + k, hi=DAY_S, null=True)
    ra = _obj(r["rallies"], "rallies", ("count", "median_shots", "longest_shots"))
    _num(ra["count"], "rallies.count", 0, COUNT, integer=True)
    _num(ra["median_shots"], "rallies.median_shots", hi=COUNT, null=True)
    _num(ra["longest_shots"], "rallies.longest_shots", 0, COUNT, integer=True)
    if not isinstance(r["timeline"], list) or len(r["timeline"]) > 600:
        _bad("timeline", "must be a list of at most 600 entries")
    for n, e in enumerate(r["timeline"]):
        p = f"timeline[{n}]"
        _obj(e, p, ("t_min", "swings", "p90_kmh", "hr_bpm"))
        _num(e["t_min"], p + ".t_min", 0, DAY_MIN, integer=True)
        _num(e["swings"], p + ".swings", 0, COUNT, integer=True)
        _num(e["p90_kmh"], p + ".p90_kmh", hi=KMH, null=True)
        _num(e["hr_bpm"], p + ".hr_bpm", hi=BPM, null=True)
    if r["heart_rate"] is not None:
        h = _obj(r["heart_rate"], "heart_rate", ("avg_bpm", "max_bpm"))
        _num(h["avg_bpm"], "heart_rate.avg_bpm", hi=BPM)
        _num(h["max_bpm"], "heart_rate.max_bpm", hi=BPM)
    if (r["strokes"] is None) != (r["model"] is None):
        _bad("strokes", "strokes and model must both be null or both present")
    if r["strokes"] is not None:
        _strokes(r["strokes"])
        m = _obj(r["model"], "model", ("name", "version"))
        _str(m["name"], "model.name", maxlen=80)
        _str(m["version"], "model.version", maxlen=80)
    if not isinstance(r["insights"], list) or len(r["insights"]) > 4:
        _bad("insights", "must be a list of at most 4")
    for n, i in enumerate(r["insights"]):
        _insight(i, f"insights[{n}]")
    return r


def summary_of(r):
    s, hr = r["session"], r["heart_rate"]
    return {"started_at": s["started_at"], "source": s["source"], "duration_s": s["duration_s"], "active_s": s["active_s"],
            "swings": r["swings"]["count"], "p90_kmh": r["speed"]["p90_kmh"], "max_kmh": r["speed"]["max_kmh"],
            "rallies": r["rallies"]["count"], "longest_rally": r["rallies"]["longest_shots"],
            "hr_avg": hr["avg_bpm"] if hr else None, "has_strokes": r["strokes"] is not None}
