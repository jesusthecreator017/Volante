"""Stat builds (contract oso-arena/1, spec 3.2): four integer stats 1-10 that sum to 24 map piecewise-linearly (1 -> 6 -> 10) to
the simulator's per-player limits. A 6 in every stat is exactly the Phase 13.2 constant set."""
import numpy as np

STATS = ("power", "speed", "reach", "accuracy")
BUDGET = 24
MIN, MAX = 1, 10
ALL6 = {"power": 6, "speed": 6, "reach": 6, "accuracy": 6}

# parameter -> (stat, value at stat 1, at 6, at 10)
_MAP = {
    "launch_hi": ("power", 60.0, 90.0, 110.0),     # max launch speed at contact >= 2.6 m (m/s)
    "launch_lo": ("power", 20.0, 25.0, 30.0),      # max launch speed at contact < 1.0 m (m/s)
    "v_max": ("speed", 4.0, 5.0, 6.0),             # m/s
    "a_max": ("speed", 8.0, 10.0, 12.0),           # m/s^2
    "reach": ("reach", 1.2, 1.5, 1.8),             # m
    "reach_lunge": ("reach", 1.7, 2.0, 2.3),       # m
    "noise_mult": ("accuracy", 1.4, 1.0, 0.6),     # multiplies the whole aim-noise sigma
}
PARAM_KEYS = tuple(_MAP)


def validate(build):
    """Error strings for a build dict (empty = valid): exactly the 4 stats, python ints (no bool / float / str), 1-10, sum 24."""
    if not isinstance(build, dict):
        return ["build must be an object with the keys " + ", ".join(STATS)]
    errs = []
    if set(build) != set(STATS):
        errs.append(f"build keys must be exactly {list(STATS)}, got {sorted(map(str, build))}")
    for k in STATS:
        v = build.get(k)
        if k in build and (type(v) is not int or not MIN <= v <= MAX):
            errs.append(f"build.{k} must be an integer {MIN}-{MAX}, got {v!r}")
    if not errs and sum(build.values()) != BUDGET:
        errs.append(f"build stats must sum to {BUDGET}, got {sum(build.values())}")
    return errs


def params(build):
    """{launch_hi, launch_lo, v_max, a_max, reach, reach_lunge, noise_mult} for a valid build."""
    errs = validate(build)
    if errs:
        raise ValueError("; ".join(errs))
    return {k: float(np.interp(build[stat], [1, 6, 10], [a, b, c])) for k, (stat, a, b, c) in _MAP.items()}


def param_table():
    """{param: float64 array [11]} indexed by the stat value 1-10 (index 0 unused): the simulator's per-player lookup."""
    return {k: np.array([np.nan] + [np.interp(s, [1, 6, 10], [a, b, c]) for s in range(1, 11)]) for k, (_, a, b, c) in _MAP.items()}


def stat_of(param):
    return _MAP[param][0]


def parse(s):
    """'9,6,5,4' -> build dict (power,speed,reach,accuracy); ValueError when not 4 valid integers."""
    try:
        vals = [int(x) for x in s.split(",")]
    except ValueError:
        raise ValueError(f"build must be four comma-separated integers like 9,6,5,4, got {s!r}") from None
    if len(vals) != 4:
        raise ValueError(f"build must have 4 values (power,speed,reach,accuracy), got {len(vals)}")
    b = dict(zip(STATS, vals))
    errs = validate(b)
    if errs:
        raise ValueError("; ".join(errs))
    return b


def to_array(build):
    return np.array([build[k] for k in STATS], int)
