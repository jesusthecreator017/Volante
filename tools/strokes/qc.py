"""Quality checks of the dataset build (spec 4.2 steps 3 and 6). Session checks reject a whole recording part; swing checks
send swings to the review queue (16.3) instead of letting them in with a possibly wrong label."""
import numpy as np

from imu import G, implausible_rest

# A part is rejected when its measured rate is off by more than this share, or more packets than this share were lost
MAX_RATE_ERR = 0.03
MAX_LOSS = 0.05
# Robust z-score (|x - median| / (1.4826 MAD)) above which a swing is an outlier of its class and side
OUTLIER_Z = 4.0
# Outliers are only judged in a (class, side) group with at least this many swings
OUTLIER_MIN_GROUP = 8
# Rest gravity: the median |a| of the stillest REST_SHARE of samples (lowest |w|) must be within this band, in g
REST_SHARE = 0.20
REST_G = (0.8, 1.2)


def rest_gravity(acc, gyro):
    """Median |a| (g) of the stillest samples. A racquet rests between reps, so this is ~1 g in any real recording; misaligned
    bytes scramble every sample, so it lands far from 1 g. Catches the noisy shifts that `implausible_rest` (Rust parity) misses:
    that one needs a near noise-free rest with gravity on one particular axis."""
    w = np.linalg.norm(gyro.astype(float), axis=1)
    a = np.linalg.norm(acc.astype(float), axis=1) / G
    k = max(1, int(len(w) * REST_SHARE))
    return float(np.median(a[np.argsort(w, kind="stable")[:k]]))


def session_qc(rec, nominal_hz=500):
    """-> dict(ok, reasons, rate_hz, loss_pct, clip_pct) for one recording part."""
    reasons = []
    st = rec.stats
    n = st["unique"]
    if n < 2:
        return dict(ok=False, reasons=["no samples"], rate_hz=0.0, loss_pct=0.0, clip_pct=0.0)
    span = float(rec.t[-1] - rec.t[0])
    rate = (n + st["lost"] - 1) / span if span > 0 else 0.0
    loss = st["lost"] / (n + st["lost"])
    clip = float((rec.sat_gyro | rec.sat_acc).mean())
    if rec.rate_hz != nominal_hz:
        reasons.append(f"header rate {rec.rate_hz} Hz, expected {nominal_hz}")
    if abs(rate - nominal_hz) > MAX_RATE_ERR * nominal_hz:
        reasons.append(f"measured rate {rate:.1f} Hz is more than {MAX_RATE_ERR:.0%} off {nominal_hz} Hz")
    if loss > MAX_LOSS:
        reasons.append(f"{loss:.1%} of packets lost (limit {MAX_LOSS:.0%})")
    if implausible_rest(rec.t, rec.acc, rec.gyro):
        reasons.append("misaligned IMU data (implausible_rest): physically impossible rest or steady spin; reset the pod and re-record")
    rest = rest_gravity(rec.acc, rec.gyro)
    if not (REST_G[0] <= rest <= REST_G[1]):
        reasons.append(f"misaligned IMU data: at rest the accelerometer reads {rest:.2f} g, not 1 g; reset the pod and re-record")
    return dict(ok=not reasons, reasons=reasons, rate_hz=round(rate, 3), loss_pct=round(100 * loss, 3), clip_pct=round(100 * clip, 3),
                rest_g=round(rest, 3))


# A block's count mismatch is only a review matter when it is large (owner decision 2026-10-17: trust the drill blocks). More than
# LARGE_OVER x max(target, live) detections suggests non-hits (resets, shadow moves) among them; fewer than LARGE_UNDER x
# min(target, live) suggests a failed block. A timed block (target 0, e.g. not_a_hit) has no rep count to compare, so never "large".
LARGE_OVER = 1.3
LARGE_UNDER = 0.5


def count_mismatch(block, detected):
    """-> (note, large). `note` describes a count disagreement (detected vs live, or live vs target), else None; `large` says it
    is big enough to send the block's swings to review rather than just annotate them."""
    name = f"block {block['index']} ({block['label']} {block.get('side') or ''})"
    target, live = block["target"], block["counted"]
    if detected != live:
        note = f"{name}: {detected} swings detected, {live} counted live"
    elif live != target:
        note = f"{name}: {live} of {target} reps" if target else f"{name}: {live} counted live"
    else:
        return None, False
    large = bool(target) and (detected > LARGE_OVER * max(target, live) or detected < LARGE_UNDER * min(target, live))
    return note, large


def outliers(feats, groups):
    """feats [n, k], groups [n] (hashable or None = not judged) -> bool [n]: far from the group's median on any feature."""
    feats = np.asarray(feats, float)
    out = np.zeros(len(feats), bool)
    keys = sorted({g for g in groups if g is not None})
    for key in keys:
        idx = np.array([i for i, g in enumerate(groups) if g == key])
        if len(idx) < OUTLIER_MIN_GROUP:
            continue
        x = feats[idx]
        med = np.median(x, axis=0)
        mad = 1.4826 * np.median(np.abs(x - med), axis=0)
        z = np.abs(x - med) / np.where(mad > 1e-9, mad, np.inf)
        out[idx] = (z > OUTLIER_Z).any(axis=1)
    return out
