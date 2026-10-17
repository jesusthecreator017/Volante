"""Volante Twins (Phase 18a): the style profile `volante-style/1` (arena/contract/STYLE_PROFILE.md), the build it maps to, the tempo
reward and the fidelity check. Pure numpy / stdlib, so the Pi can import it next to `contract`.

The validator here and `validate` in crates/oso-replay/src/imu/profile.rs are the same rules (a shared test vector pins both)."""
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from oso_arena import builds

SCHEMA = "volante-style/1"
MAX_BYTES = 8192
MAX_COUNT = 10**7
TOP_KEYS = ("schema", "created_at", "source", "speed", "tempo", "stroke_mix", "side_mix", "notes")
REQUIRED = ("schema", "created_at", "source", "speed", "tempo")
SOURCE_KEYS = ("sessions", "swings", "minutes", "pod_fw", "tool")
SPEED_KEYS = ("p50_kmh", "p90_kmh", "cv", "clipped_frac")
TEMPO_KEYS = ("median_gap_s", "p25_gap_s", "p75_gap_s")
SPEED_MAX_KMH, GAP_MIN_S, GAP_MAX_S, CV_MAX = 400.0, 0.2, 10.0, 2.0
NOTES_MAX = 280
_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ID = re.compile(r"[A-Za-z0-9._ -]{1,64}")
_CLASS = re.compile(r"[a-z_]{1,24}")

# fidelity (spec 5): model median gap / profile median gap
TEMPO_RATIO_LO, TEMPO_RATIO_HI = 0.75, 1.33

# tempo reward
TEMPO_WEIGHT = 0.05  # default per paid shot
TEMPO_MAX_WEIGHT = 0.1  # 5 paying shots per player in a rally => at most 0.5 per rally and player, even at the cap
TEMPO_MAX_SHOTS = 10  # first 10 shots of a rally (5 per player), like the default reward's cap
TEMPO_SIGMA_MIN_S = 0.3
SIM_DT = 1 / 120  # the simulator step; a hit's time is (decision step * 4 + sub-step) * SIM_DT


def _num(v):
    return (type(v) in (int, float)) and math.isfinite(v)


def _in(v, lo, hi):
    return _num(v) and lo <= v <= hi


def _obj(p, key, keys, errs):
    o = p.get(key)
    if not isinstance(o, dict):
        errs.append(f"{key} must be an object")
        return None
    if set(o) != set(keys):
        errs.append(f"{key} keys must be exactly {list(keys)}, got {sorted(map(str, o))}")
        return None
    return o


def _mix(p, key, keys, errs):
    m = p.get(key)
    if m is None:
        return
    ok = isinstance(m, dict) and bool(m) and len(m) <= 16 and all(isinstance(k, str) and _CLASS.fullmatch(k) for k in m)
    if keys is not None:
        ok = ok and set(m) == set(keys)
    if not ok or not all(_in(v, 0, 1) for v in m.values()):
        errs.append(f"{key} must be null or an object of class -> share in [0, 1]" + (f" with the keys {list(keys)}" if keys else ""))
    elif abs(sum(m.values()) - 1) > 1e-3:
        errs.append(f"{key} must sum to 1 (+-1e-3), got {sum(m.values()):.4f}")


def validate_profile(p):
    """Error strings for a decoded profile (empty = valid): the rules of STYLE_PROFILE.md, the same everywhere (kit, Pi, site, oso-rec).
    Does not check the 8 KB file size (see `compact` / `load_profile`)."""
    if not isinstance(p, dict):
        return ["profile must be a JSON object"]
    errs = []
    extra = sorted(set(map(str, p)) - set(TOP_KEYS))
    if extra:
        errs.append(f"unknown keys {extra} (a profile holds no personal data: only {list(TOP_KEYS)})")
    errs += [f"missing key {k!r}" for k in REQUIRED if k not in p]
    if "schema" in p and p["schema"] != SCHEMA:
        errs.append(f"schema must be {SCHEMA!r}, got {p['schema']!r}")
    if "created_at" in p and not (isinstance(p["created_at"], str) and _ISO.fullmatch(p["created_at"])):
        errs.append("created_at must look like 2026-10-17T18:00:00Z")
    if "source" in p and (s := _obj(p, "source", SOURCE_KEYS, errs)):
        for k in ("sessions", "swings"):
            if type(s[k]) is not int or not 1 <= s[k] <= MAX_COUNT:
                errs.append(f"source.{k} must be an integer 1-{MAX_COUNT}, got {s[k]!r}")
        if not _in(s["minutes"], 0, MAX_COUNT) or s["minutes"] <= 0:
            errs.append(f"source.minutes must be a number above 0 and at most {MAX_COUNT}, got {s['minutes']!r}")
        for k in ("pod_fw", "tool"):
            if not (isinstance(s[k], str) and _ID.fullmatch(s[k])):
                errs.append(f"source.{k} must be 1-64 characters of letters, digits, space . _ -, got {s[k]!r}")
    if "speed" in p and (s := _obj(p, "speed", SPEED_KEYS, errs)):
        for k, hi in (("p50_kmh", SPEED_MAX_KMH), ("p90_kmh", SPEED_MAX_KMH), ("cv", CV_MAX), ("clipped_frac", 1)):
            if not _in(s[k], 0, hi):
                errs.append(f"speed.{k} must be a number 0-{hi:g}, got {s[k]!r}")
        if not errs and s["p50_kmh"] > s["p90_kmh"]:
            errs.append("speed.p50_kmh must not exceed speed.p90_kmh")
    if "tempo" in p and (t := _obj(p, "tempo", TEMPO_KEYS, errs)):
        for k in TEMPO_KEYS:
            if not _in(t[k], GAP_MIN_S, GAP_MAX_S):
                errs.append(f"tempo.{k} must be a number {GAP_MIN_S:g}-{GAP_MAX_S:g} s, got {t[k]!r}")
        if not errs and not t["p25_gap_s"] <= t["median_gap_s"] <= t["p75_gap_s"]:
            errs.append("tempo must satisfy p25_gap_s <= median_gap_s <= p75_gap_s")
    _mix(p, "stroke_mix", None, errs)
    _mix(p, "side_mix", ("fh", "bh"), errs)
    n = p.get("notes")
    if n is not None and not (isinstance(n, str) and len(n) <= NOTES_MAX and all(" " <= c <= "~" for c in n)):
        errs.append(f"notes must be null or a printable ASCII string of at most {NOTES_MAX} characters")
    return errs


def compact(profile):
    """The profile as compact JSON text (what `export --style` embeds). ValueError when invalid or over 8 KB."""
    errs = validate_profile(profile)
    if errs:
        raise ValueError("invalid style profile: " + "; ".join(errs))
    text = json.dumps(profile, separators=(",", ":"), allow_nan=False)
    if len(text.encode()) > MAX_BYTES:
        raise ValueError(f"style profile is {len(text.encode())} bytes, over the {MAX_BYTES}-byte limit")
    return text


def parse_profile(text):
    """JSON text -> validated profile dict. ValueError with the reasons (also for text over 8 KB, bad JSON, NaN)."""
    if len(text.encode()) > MAX_BYTES:
        raise ValueError(f"style profile is over the {MAX_BYTES}-byte limit")

    def _bad(c):
        raise ValueError(f"not valid JSON ({c})")

    try:
        p = json.loads(text, parse_constant=_bad)
    except RecursionError:
        raise ValueError("style profile is not valid JSON") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"style profile is not valid JSON ({e.msg})") from None
    errs = validate_profile(p)
    if errs:
        raise ValueError("invalid style profile: " + "; ".join(errs))
    return p


def load_profile(path):
    """Reads and validates a profile file (`oso-rec profile` output). ValueError / OSError."""
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError(f"{p} is over the {MAX_BYTES}-byte limit for a style profile")
    return parse_profile(p.read_text())


# --- build from profile (STYLE_PROFILE.md "Build mapping"; v1 heuristic)

def _lin(x, x1, v1, x2, v2):
    return v1 + (x - x1) * (v2 - v1) / (x2 - x1)


def raw_stats(profile):
    """The unrounded, unclamped power / accuracy / speed the profile suggests (reach is not measurable)."""
    return {
        "power": _lin(profile["speed"]["p90_kmh"], 40, 1, 120, 10),
        "accuracy": _lin(profile["speed"]["cv"], 0.6, 1, 0.1, 10),
        "speed": _lin(profile["tempo"]["median_gap_s"], 3.0, 1, 1.0, 10),
    }


def build_from_profile(profile):
    """Deterministic and always valid (1-10 each, sum 24). power from speed.p90_kmh (40 -> 1, 120 -> 10), accuracy from cv (0.6 -> 1,
    0.1 -> 10), speed from tempo.median_gap_s (3.0 s -> 1, 1.0 s -> 10), each rounded half up and clamped; reach takes the remainder.
    When the remainder is outside 1-10, reach is clamped and the difference is taken from the largest raw stat (too many points) or
    given to the smallest (too few), never leaving 1-10; ties go in the order power, speed, accuracy. Monotone in each input while the
    remainder lands in 1-10 (power up with p90, accuracy up as cv falls, speed up as the median gap falls)."""
    errs = validate_profile(profile)
    if errs:
        raise ValueError("invalid style profile: " + "; ".join(errs))
    raw = raw_stats(profile)
    b = {k: min(max(math.floor(raw[k] + 0.5), builds.MIN), builds.MAX) for k in ("power", "speed", "accuracy")}
    b["reach"] = min(max(builds.BUDGET - sum(b.values()), builds.MIN), builds.MAX)
    order = ("power", "speed", "accuracy")
    while (diff := builds.BUDGET - sum(b.values())) != 0:
        if diff < 0:  # too many points: take from the largest raw stat that can drop
            k = max((k for k in order if b[k] > builds.MIN), key=lambda k: (b[k], raw[k], -order.index(k)))
            b[k] -= 1
        else:
            k = min((k for k in order if b[k] < builds.MAX), key=lambda k: (b[k], raw[k], order.index(k)))
            b[k] += 1
    return {k: b[k] for k in builds.STATS}


# --- tempo from match logs

def hit_gaps(log, side):
    """Seconds between consecutive hits of player `side` (0 = A, 1 = B) inside a rally (no gap spans a rally start) in a MatchLog."""
    gaps, last = [], {}
    for e in log.events:
        if e["kind"] == "start":
            last = {}
        elif e["kind"] == "hit":
            p = e["p"]
            if p in last and p == side:
                gaps.append(e["t"] - last[p])
            last[p] = e["t"]
    return gaps


def games_gaps(result):
    """The same gaps for model A from an `evaluate.play_games` result (its shot log: decision steps of 1/30 s, a serve starts a rally)."""
    last, gaps = {}, []
    for row in result["shots"]:
        g, p, first, step = row[0], row[1], row[2], row[9]
        if first:
            last = {k: v for k, v in last.items() if k[0] != g}
        if p == result["a_slot"][g]:
            if (g, p) in last:
                gaps.append((step - last[g, p]) / 30)
            last[g, p] = step
    return gaps


def _items(logs):
    for x in logs:
        yield x if isinstance(x, tuple) else (x, 0)


@dataclass
class FidelityReport:
    build_ok: bool
    tempo_ratio: object  # model median gap / profile median gap, None without any gap
    tempo_ok: bool
    model_median_gap_s: object
    profile_median_gap_s: float
    gaps: int  # how many gaps the model median is from
    mix_js: object = None  # 18b
    fidelity_ok: bool = False
    reasons: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def text(self):
        r = self.tempo_ratio
        lines = [
            f"build:  {'ok' if self.build_ok else 'FAIL (differs from build_from_profile)'}",
            f"tempo:  model median gap {'n/a' if r is None else f'{self.model_median_gap_s:.2f} s'} / profile {self.profile_median_gap_s:.2f} s = "
            f"{'n/a' if r is None else f'{r:.2f}'} (pass {TEMPO_RATIO_LO}-{TEMPO_RATIO_HI}, {self.gaps} gaps) {'ok' if self.tempo_ok else 'FAIL'}",
            f"fidelity: {'OK' if self.fidelity_ok else 'FAIL'}",
        ]
        return "\n".join(lines + [f"  - {x}" for x in self.reasons])


def fidelity(profile, logs, build):
    """Does the model play like its profile? `logs`: the model's matches, each a MatchLog (the model is player A) or a (MatchLog, side)
    pair (side 0 = A, 1 = B). `build`: the submission's build. 18a checks: build_ok (build == build_from_profile) and tempo_ratio in
    0.75-1.33. fidelity_ok = all applicable checks pass."""
    return fidelity_from_gaps(profile, [g for log, side in _items(logs) for g in hit_gaps(log, side)], build)


def fidelity_from_gaps(profile, gaps, build):
    """`fidelity` from the model's own-hit gaps in seconds (hit_gaps / games_gaps)."""
    want = build_from_profile(profile)
    build_ok = build == want
    med = profile["tempo"]["median_gap_s"]
    mm = float(np.median(gaps)) if gaps else None
    ratio = None if mm is None else mm / med
    tempo_ok = ratio is not None and TEMPO_RATIO_LO <= ratio <= TEMPO_RATIO_HI
    reasons = []
    if not build_ok:
        reasons.append(f"build {json.dumps(build, separators=(',', ':'))} is not the profile's {json.dumps(want, separators=(',', ':'))}")
    if ratio is None:
        reasons.append("no consecutive hits of the model in the logs: tempo cannot be measured")
    elif not tempo_ok:
        reasons.append(f"tempo ratio {ratio:.2f} is outside {TEMPO_RATIO_LO}-{TEMPO_RATIO_HI}")
    return FidelityReport(build_ok, ratio, tempo_ok, mm, med, len(gaps), None, build_ok and tempo_ok, reasons)


# --- tempo reward

class TempoReward:
    """A small shaping term (`rewards.total_reward(..., extra=...)`): each hit that follows the same player's previous hit of the
    rally earns weight * exp(-(gap - median)^2 / (2 sigma^2)), sigma = IQR / 1.349 of the profile (at least 0.3 s). Only the first 10
    shots of a rally pay (5 per player), so a rally pays at most 5 * weight per player (0.25 at the default 0.05; weight <= 0.1).
    Stateful (remembers each game's last hits): one instance per training run; `reset()` between independent rollouts."""

    def __init__(self, profile, weight=TEMPO_WEIGHT):
        errs = validate_profile(profile)
        if errs:
            raise ValueError("invalid style profile: " + "; ".join(errs))
        if not (_num(weight) and 0 <= weight <= TEMPO_MAX_WEIGHT):
            raise ValueError(f"--style-weight must be 0-{TEMPO_MAX_WEIGHT:g}, got {weight!r}")
        t = profile["tempo"]
        self.weight, self.median = float(weight), t["median_gap_s"]
        self.sigma = max((t["p75_gap_s"] - t["p25_gap_s"]) / 1.349, TEMPO_SIGMA_MIN_S)
        self.reset()

    def reset(self):
        self.last = None  # [n, 2] time of each player's previous hit in the rally (nan = none)
        self.step = 0

    def __call__(self, ev):
        from oso_arena.events import SUBSTEPS

        n = ev.n_games
        if self.last is None or len(self.last) != n:
            self.last = np.full((n, 2), np.nan)
        r = np.zeros((n, 2))
        h = ev.hits
        for k in range(h.mask.shape[1]):
            idx = np.nonzero(h.mask[:, k])[0]
            if len(idx) == 0:
                continue
            p, t = h.player[idx, k], (self.step * SUBSTEPS + k) * SIM_DT
            prev = np.where(h.shot_no[idx, k] <= 1, np.nan, self.last[idx, p])  # a serve starts the rally
            pay = (h.shot_no[idx, k] <= TEMPO_MAX_SHOTS) & np.isfinite(prev)
            gap = np.where(pay, t - prev, self.median)
            r[idx, p] += self.weight * np.exp(-((gap - self.median) ** 2) / (2 * self.sigma**2)) * pay
            self.last[idx, p] = t
        self.last[ev.rally_end.mask] = np.nan
        self.step += 1
        return r
