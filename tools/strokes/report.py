"""Session report `volante-report/1` (docs/strokes/report-format.md): the reference implementation.

    python tools/strokes/report.py <imu.bin> [--mount identity|flat] [--started-at 2026-10-17T18:00:00Z] [-o report.json]

Swing detection and head speed are the style profile's (crates/oso-replay/src/imu/profile.rs), so a report and a profile of the
same recording agree. Insights and strokes are left to the apps (empty / null here).
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from imu import SwingCounter, read_imu  # noqa: E402

SCHEMA = "volante-report/1"
THRESHOLD_DPS = 300.0
WINDOW_S = 0.8
RALLY_SPLIT_S = 6.0
HEAD_CENTER_M = 0.53
FLAT = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], np.float64)  # racquet X, Y (shaft), Z rows in the sensor frame


def sig3(x):
    """3 significant digits, half away from zero (profile.rs `sig3`)."""
    if x is None or x == 0 or not math.isfinite(x):
        return x
    p = 2 - math.floor(math.log10(abs(x)))
    k = 10.0 ** abs(p)
    r = lambda v: math.floor(v + 0.5) if v >= 0 else -math.floor(-v + 0.5)  # noqa: E731
    return r(x * k) / k if p >= 0 else r(x / k) * k


def pct(sorted_vals, q):
    pos = q * (len(sorted_vals) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def swings(t, gyro, sat):
    """(onset t, km/h, clipped) per swing; gyro in the racquet frame (float32 rad/s)."""
    c = SwingCounter(THRESHOLD_DPS)
    dps = (np.sqrt((gyro.astype(np.float64) ** 2).sum(axis=1)) * (180.0 / math.pi)).tolist()
    perp = np.hypot(gyro[:, 0].astype(np.float64), gyro[:, 2].astype(np.float64))
    out, tl = [], t.tolist()
    for i, (ti, di) in enumerate(zip(tl, dps)):
        if c.push(ti, di):
            j = int(np.searchsorted(t, ti + WINDOW_S, side="left"))
            out.append((ti, float(perp[i:j].max()) * HEAD_CENTER_M * 3.6, bool(sat[i:j].any())))
    return out


def build(parts, started_at, source="pod", pod_fw="unknown", hr=None):
    """parts: [(t, gyro_racquet, sat)] per recording part. hr: optional [(t_on_part0_clock, bpm)]."""
    sw, gaps, rallies, duration, active = [], [], [], 0.0, 0.0
    for t, g, sat in parts:
        if len(t):
            duration += float(t[-1] - t[0])
        s = swings(t, g, sat)
        run = [s[0]] if s else []
        for a, b in zip(s, s[1:]):
            gap = b[0] - a[0]
            if gap <= RALLY_SPLIT_S:
                gaps.append(gap)
                run.append(b)
            else:
                rallies.append(run)
                run = [b]
        if run:
            rallies.append(run)
        sw += s
    for run in rallies:
        active += (run[-1][0] - run[0][0]) + WINDOW_S
    real = [len(r) for r in rallies if len(r) >= 2]
    sp = sorted(x[1] for x in sw)
    gs = sorted(gaps)
    t0 = parts[0][0][0] if parts and len(parts[0][0]) else 0.0
    timeline = []
    if parts and len(parts[0][0]):
        n_min = int(math.floor((parts[0][0][-1] - t0) / 60.0)) + 1
        for m in range(n_min):
            inside = sorted(x[1] for x in sw if t0 + 60 * m <= x[0] < t0 + 60 * (m + 1))
            hrs = [b for (ht, b) in (hr or []) if t0 + 60 * m <= ht < t0 + 60 * (m + 1)]
            timeline.append({"t_min": m, "swings": len(inside), "p90_kmh": sig3(pct(inside, 0.9)) if len(inside) >= 3 else None,
                             "hr_bpm": sig3(sum(hrs) / len(hrs)) if hrs else None})
    return {
        "schema": SCHEMA,
        "session": {"started_at": started_at, "duration_s": sig3(duration), "active_s": sig3(active), "source": source, "pod_fw": pod_fw},
        "swings": {"count": len(sw), "per_min_active": sig3(len(sw) / (active / 60.0)) if active > 0 else None},
        "speed": {"p50_kmh": sig3(pct(sp, 0.5)) if sp else None, "p90_kmh": sig3(pct(sp, 0.9)) if sp else None,
                  "max_kmh": sig3(sp[-1]) if sp else None, "clipped_frac": sig3(sum(x[2] for x in sw) / len(sw)) if sw else 0,
                  "estimated": True},
        "tempo": {"median_gap_s": sig3(pct(gs, 0.5)) if gs else None, "p25_gap_s": sig3(pct(gs, 0.25)) if gs else None,
                  "p75_gap_s": sig3(pct(gs, 0.75)) if gs else None},
        "rallies": {"count": len(real), "median_shots": sig3(float(np.median(real))) if real else None,
                    "longest_shots": max(real) if real else 0},
        "timeline": timeline,
        "heart_rate": ({"avg_bpm": sig3(sum(b for _, b in hr) / len(hr)), "max_bpm": sig3(max(b for _, b in hr))} if hr else None),
        "strokes": None,
        "insights": [],
        "model": None,
    }


def from_file(path, mount="flat", **kw):
    r = read_imu(path)
    g = r.gyro if mount == "identity" else (r.gyro.astype(np.float64) @ FLAT.T).astype(np.float32)
    return build([(r.t, g, r.sat_gyro)], **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("imu")
    ap.add_argument("--mount", choices=["identity", "flat"], default="flat")
    ap.add_argument("--started-at", default="2026-10-17T18:00:00Z")
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    text = json.dumps(from_file(a.imu, a.mount, started_at=a.started_at), indent=2) + "\n"
    if a.out:
        Path(a.out).write_text(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
