"""GET /api/me/dashboard aggregates (spec section 3): a pure function over a user's stored volante-report/1 reports.
Pod (racquet) and watch (wrist) speeds are never mixed: speed numbers come from pod sessions only."""
import logging
from datetime import date, timedelta

from . import reports

log = logging.getLogger(__name__)

GROUPS = ("power", "mid", "touch", "not_a_hit", "unclassified")


def _week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _best(items, value):
    """The (first) session with the largest positive value -> {value, session_id, started_at}, or None."""
    best = None
    for sid, r in items:
        v = value(r)
        if v is not None and v > 0 and (best is None or v > best["value"]):
            best = {"value": v, "session_id": sid, "started_at": r["session"]["started_at"]}
    return best


def _shares(groups):
    """Summed group counts -> shares rounded to 4 decimals that sum to 1 (the remainder goes to the largest group); None if no strokes."""
    sums = {g: sum(x.get(g, 0) for x in groups) for g in GROUPS}
    total = sum(sums.values())
    if not total:
        return None
    out = {g: round(v / total, 4) for g, v in sums.items()}
    top = max(GROUPS, key=lambda g: sums[g])
    out[top] = round(1 - sum(v for g, v in out.items() if g != top), 4)
    return out


def aggregate(rows, style, twin_model_id, today):
    """rows: [{"id", "report"}] (validated reports, any order). -> the Dashboard shape of GET /api/me/dashboard."""
    ok = []
    for x in rows:  # rows stored before the stricter validator: skip one bad row rather than 500 the whole dashboard
        try:
            ok.append((x["id"], reports.validate_report(x["report"])))
        except ValueError as e:
            log.warning("dashboard: skipping stored session %s: %s", x["id"], e)
    items = sorted(ok, key=lambda t: (t[1]["session"]["started_at"], t[0]))
    pod = [t for t in items if t[1]["session"]["source"] == "pod"]
    watch = [t for t in items if t[1]["session"]["source"] == "watch"]

    monday = today - timedelta(days=today.weekday())
    weeks = {_week(monday - timedelta(weeks=n)): [0, 0, 0.0] for n in range(11, -1, -1)}  # oldest first
    for _, r in items:
        w = weeks.get(_week(date.fromisoformat(r["session"]["started_at"][:10])))
        if w:
            w[0] += 1
            w[1] += r["swings"]["count"]
            w[2] += r["session"]["active_s"]

    stroked = [r["strokes"]["groups"] for _, r in items if r["strokes"] is not None]
    recent, overall = _shares(stroked[-10:]), _shares(stroked)
    strokes = recent and {"groups": recent, "overall": overall, "sessions": len(stroked[-10:]), "experimental": True}

    return {
        "totals": {"sessions": len(items), "swings": sum(r["swings"]["count"] for _, r in items),
                   "active_min": round(sum(r["session"]["active_s"] for _, r in items) / 60, 1),
                   "rallies": sum(r["rallies"]["count"] for _, r in items)},
        "weekly": [{"week": k, "sessions": n, "swings": s, "active_min": round(a / 60, 1)} for k, (n, s, a) in weeks.items()],
        "trends": [{"id": sid, "started_at": r["session"]["started_at"], "p90_kmh": r["speed"]["p90_kmh"],
                    "swings_per_min": r["swings"]["per_min_active"], "median_gap_s": r["tempo"]["median_gap_s"],
                    "hr_avg": r["heart_rate"]["avg_bpm"] if r["heart_rate"] else None} for sid, r in pod[-50:]],
        "wrist": {"sessions": len(watch), "swings": sum(r["swings"]["count"] for _, r in watch)},
        "bests": {"max_kmh": _best(pod, lambda r: r["speed"]["max_kmh"]),
                  "most_swings": _best(items, lambda r: r["swings"]["count"]),
                  "longest_rally": _best(items, lambda r: r["rallies"]["longest_shots"]),
                  "best_rate": _best(items, lambda r: r["swings"]["per_min_active"])},
        "strokes": strokes,
        "style": style,
        "twin_model_id": twin_model_id,
    }
