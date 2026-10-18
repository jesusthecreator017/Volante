"""GET /api/me/dashboard aggregates (spec section 3): a pure function over a user's play_sessions rows.
A row's volante-report/1 is used when it has one; otherwise its flat numbers (duration as active time, swings, best_kmh, avg_bpm).
Racquet and wrist speeds are never mixed: sensor "watch" is the wrist; "pod" and "pod+watch" are racquet sessions."""
from datetime import date, timedelta

GROUPS = ("power", "mid", "touch", "not_a_hit", "unclassified")


def _week(d):
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _best(items, key):
    """The (first) session with the largest positive value -> {value, session_id, started_at}, or None."""
    best = None
    for x in items:
        v = x[key]
        if v is not None and v > 0 and (best is None or v > best["value"]):
            best = {"value": v, "session_id": x["id"], "started_at": x["started_at"]}
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


def _flat(x):
    """One row -> the numbers the dashboard uses, from the report when there is one."""
    r = x["report"]
    hr = r and r["heart_rate"] and r["heart_rate"]["avg_bpm"]
    return {"id": x["id"], "started_at": x["started_at"], "wrist": x["sensor"] == "watch",
            "active_s": r["session"]["active_s"] if r else x["duration_s"],
            "swings": r["swings"]["count"] if r else x["swings"],
            "rallies": r["rallies"]["count"] if r else 0,
            "longest": r["rallies"]["longest_shots"] if r else 0,
            "p90_kmh": r and r["speed"]["p90_kmh"],
            "max_kmh": r["speed"]["max_kmh"] if r else x["best_kmh"],
            "rate": r and r["swings"]["per_min_active"],
            "gap": r and r["tempo"]["median_gap_s"],
            "hr": hr if hr is not None else x["avg_bpm"],
            "groups": r["strokes"]["groups"] if r and r["strokes"] else None}


def aggregate(rows, style, twin_model_id, today):
    """rows: play_sessions rows as dicts, "report" already validated (or None). -> the Dashboard shape of GET /api/me/dashboard."""
    items = sorted((_flat(x) for x in rows), key=lambda t: (t["started_at"], t["id"]))
    pod = [t for t in items if not t["wrist"]]
    watch = [t for t in items if t["wrist"]]

    monday = today - timedelta(days=today.weekday())
    weeks = {_week(monday - timedelta(weeks=n)): [0, 0, 0.0] for n in range(11, -1, -1)}  # oldest first
    for t in items:
        w = weeks.get(_week(date.fromisoformat(t["started_at"][:10])))
        if w:
            w[0] += 1
            w[1] += t["swings"]
            w[2] += t["active_s"]

    stroked = [t["groups"] for t in items if t["groups"] is not None]
    recent, overall = _shares(stroked[-10:]), _shares(stroked)
    strokes = recent and {"groups": recent, "overall": overall, "sessions": len(stroked[-10:]), "experimental": True}

    return {
        "totals": {"sessions": len(items), "swings": sum(t["swings"] for t in items),
                   "active_min": round(sum(t["active_s"] for t in items) / 60, 1),
                   "rallies": sum(t["rallies"] for t in items)},
        "weekly": [{"week": k, "sessions": n, "swings": s, "active_min": round(a / 60, 1)} for k, (n, s, a) in weeks.items()],
        "trends": [{"id": t["id"], "started_at": t["started_at"], "p90_kmh": t["p90_kmh"], "swings_per_min": t["rate"],
                    "median_gap_s": t["gap"], "hr_avg": t["hr"]} for t in pod[-50:]],
        "wrist": {"sessions": len(watch), "swings": sum(t["swings"] for t in watch)},
        "bests": {"max_kmh": _best(pod, "max_kmh"),
                  "most_swings": _best(items, "swings"),
                  "longest_rally": _best(items, "longest"),
                  "best_rate": _best(items, "rate")},
        "strokes": strokes,
        "style": style,
        "twin_model_id": twin_model_id,
    }
