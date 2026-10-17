"""Swing windows and features, shared by the dataset build (16.2) and the model (16.4).

A window is -600..+400 ms around the swing's gyro peak, resampled on the nominal 2 ms grid (500 Hz), in the racquet frame
(+Y shaft toward the tip, +Z string-face normal, +X = Y x Z): channels CHANNELS, float32 [WINDOW_N, 10]."""
import numpy as np

from imu import G

# docs/strokes/README.md; the order is fixed (stroke_model_v1.json pins it in 16.4)
CLASSES = ["not_a_hit", "serve_short", "serve_long", "clear", "smash", "drop", "drive", "lift", "net_shot", "block_push", "net_kill"]
SIDES = ["FH", "BH"]
SIDELESS = {"not_a_hit"}
# Block labels that are not classes: their swings are kept but unlabelled (free rallies get video labels in 16.3)
UNLABELLED_BLOCKS = {"warmup", "free"}

# Our class -> the Replay STROKE_TYPES name (crates/oso-render/src/replay.rs), so IMU and video reports share names.
# Replay has one "serve" (plus "flick_serve", a video-only distinction) and separate "block"/"push"; we have one block_push.
TO_REPLAY = {
    "not_a_hit": "swing_only",
    "serve_short": "serve",
    "serve_long": "serve",
    "clear": "clear",
    "smash": "smash",
    "drop": "drop",
    "drive": "drive",
    "lift": "lift",
    "net_shot": "net",
    "block_push": "push",
    "net_kill": "net_kill",
}

RATE_HZ = 500
DT = 1.0 / RATE_HZ
PRE_S, POST_S = 0.600, 0.400
WINDOW_N = int(round((PRE_S + POST_S) * RATE_HZ))  # 500
CHANNELS = ["ax_g", "ay_g", "az_g", "wx_dps", "wy_dps", "wz_dps", "w_dps", "a_g", "sat_gyro", "sat_acc"]
# Holes inside a window longer than this (s) are interpolated across but send the swing to review
MAX_HOLE_S = 0.010
# After the counter's onset, the peak is the largest raw |w| within this time (the counter fires on the way up)
PEAK_SEARCH_S = 0.500
# Two peaks closer than this are one swing: keep the larger
DUPLICATE_S = 0.250


def peak_index(t, w_dps, onset):
    hi = int(np.searchsorted(t, t[onset] + PEAK_SEARCH_S, side="right"))
    return onset + int(np.argmax(w_dps[onset:hi]))


def dedupe(t, w_dps, peaks):
    """Peaks closer than DUPLICATE_S are one swing: keep the larger (ties: the earlier). A swing that straddles a block edge is
    seen by both regions' counters; this merges it. -> sorted unique indices."""
    kept = []
    for i in sorted(set(peaks)):
        if kept and t[i] - t[kept[-1]] < DUPLICATE_S:
            if w_dps[i] > w_dps[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)
    return kept


def window(t, acc, gyro, sat_gyro, sat_acc, t_peak):
    """-> (float32 [WINDOW_N, 10], flags) for a peak at t_peak; acc/gyro already in the racquet frame (SI units).
    flags: 'edge' (the window leaves the recording) and/or 'hole' (a gap > MAX_HOLE_S inside it)."""
    grid = t_peak - PRE_S + np.arange(WINDOW_N) * DT
    flags = []
    if grid[0] < t[0] or grid[-1] > t[-1]:
        flags.append("edge")
    lo = max(0, int(np.searchsorted(t, grid[0])) - 1)
    hi = min(len(t), int(np.searchsorted(t, grid[-1])) + 2)
    if hi - lo > 1 and np.diff(t[lo:hi]).max(initial=0) > MAX_HOLE_S:
        flags.append("hole")
    out = np.empty((WINDOW_N, len(CHANNELS)), np.float32)
    a_g = acc.astype(float) / G
    w_d = np.degrees(gyro.astype(float))
    for k in range(3):
        out[:, k] = np.interp(grid, t, a_g[:, k])
        out[:, 3 + k] = np.interp(grid, t, w_d[:, k])
    out[:, 6] = np.linalg.norm(out[:, 3:6], axis=1)
    out[:, 7] = np.linalg.norm(out[:, 0:3], axis=1)
    near = np.clip(np.searchsorted(t, grid), 0, len(t) - 1)
    prev = np.clip(near - 1, 0, len(t) - 1)
    near = np.where(np.abs(t[prev] - grid) < np.abs(t[near] - grid), prev, near)
    out[:, 8] = sat_gyro[near]
    out[:, 9] = sat_acc[near]
    return out, flags


def summary(win):
    """A few interpretable numbers per window (outlier QC here, baseline features in 16.4): float64 [8]."""
    c = 1 + int(round(PRE_S * RATE_HZ))  # index just after the peak sample
    w = win[:, 6].astype(float)
    peak = int(np.argmax(w))
    roll = win[:, 4].astype(float)  # rate about the shaft (+Y): pronation / supination
    face = win[:, 5].astype(float)  # rate about the face normal (+Z)
    above = np.nonzero(w > 0.5 * w[peak])[0]
    width = (above[-1] - above[0] + 1) * DT if len(above) else 0.0
    return np.array([
        w[peak],                                 # peak |w|, dps
        win[:, 7].max(),                         # peak |a|, g
        roll[np.argmax(np.abs(roll))],           # signed peak shaft roll, dps
        face[np.argmax(np.abs(face))],           # signed peak face-normal rate, dps
        width,                                   # time above half the peak, s
        win[:c, 6].mean(),                       # mean |w| before the peak (build-up), dps
        win[c:, 6].mean(),                       # mean |w| after (follow-through), dps
        win[:, 8].mean(),                        # share of gyro-saturated samples
    ])


# ---------------------------------------------------------------------------------------------------------------------------
# Phase 16.4 candidate A: the full feature set. Part of the model contract (stroke_model_v0.json "features_version"): a change
# here changes the classifier input, so bump FEATURES_VERSION and retrain. Deterministic, pure numpy, window -> float32 [N_FEATURES].
FEATURES_VERSION = 2  # v2: + orientation-free timing/clipping/shape features (appended at the end)
BANDS_HZ = [(0.5, 3), (3, 6), (6, 12), (12, 25), (25, 60), (60, 125)]  # |w| spectrum bands; log share of the window's energy
_C = int(round(PRE_S * RATE_HZ))  # nominal peak index
FEATURE_NAMES = (
    [f"{n}_peak" for n in ("ax_g", "ay_g", "az_g", "wx_dps", "wy_dps", "wz_dps")]  # signed value of the largest |.| on the axis
    + [f"{n}_at_peak" for n in ("ax_g", "ay_g", "az_g", "wx_dps", "wy_dps", "wz_dps")]  # signed value at the |w| peak sample
    + [f"{n}_sign_pm50ms" for n in ("wx", "wy", "wz")]  # sign (+1/-1/0) of the mean rate within +-50 ms of the |w| peak
    + ["w_peak_dps", "a_peak_g", "buildup_s", "burst_s", "burst_lo_s", "follow_decay", "follow_to20_s",
       "shaft_roll_share", "pre_elev_deg", "pre_ax_g", "pre_ay_g", "pre_az_g", "clip_gyro_n", "clip_acc_n",
       "t_apeak_minus_wpeak_s", "w_prebuild_mean_dps"]
    + [f"band{i}_log_share" for i in range(len(BANDS_HZ))]
    + ["a_burst_s", "clip_gyro_run_s", "clip_acc_run_s", "w_rise_dps2", "w_fall_dps2", "w_energy_pre_share", "a_jerk_peak_gps"]
)
# Features that do not depend on how the pod sits in the racquet (norms, timing, clipping, spectra): the axis-wise peaks/signs,
# at-peak values, shaft-roll share and pre-swing gravity direction are excluded. Candidate "a_of" trains on these only.
ORIENTATION_FREE_NAMES = (
    {"w_peak_dps", "a_peak_g", "buildup_s", "burst_s", "burst_lo_s", "follow_decay", "follow_to20_s", "clip_gyro_n", "clip_acc_n",
     "t_apeak_minus_wpeak_s", "w_prebuild_mean_dps", "a_burst_s", "clip_gyro_run_s", "clip_acc_run_s", "w_rise_dps2", "w_fall_dps2",
     "w_energy_pre_share", "a_jerk_peak_gps"} | {n for n in FEATURE_NAMES if n.startswith("band")})
ORIENTATION_FREE_IDX = [i for i, n in enumerate(FEATURE_NAMES) if n in ORIENTATION_FREE_NAMES]
N_FEATURES = len(FEATURE_NAMES)


def _run(mask, i):
    """Contiguous True run of `mask` containing index i -> (first, last) indices."""
    lo = hi = i
    while lo > 0 and mask[lo - 1]:
        lo -= 1
    while hi < len(mask) - 1 and mask[hi + 1]:
        hi += 1
    return lo, hi


def full_features(win):
    """float32 [WINDOW_N, 10] -> float32 [N_FEATURES], in FEATURE_NAMES order.
    - *_peak: signed value of each acc/gyro axis at its largest magnitude; *_at_peak: value at the |w| peak sample (axis signs
      and shaft roll direction); *_sign_pm50ms: sign of the mean gyro axis within +-50 ms of that sample.
    - w_peak_dps / a_peak_g: peak |w|, peak |a|.
    - buildup_s: length of the run ending at the |w| peak where |w| > 20 % of the peak; burst_s: the run around the peak with
      |w| > 50 % (burst_lo_s: same at 25 %, for the shoulders).
    - follow_decay: mean |w| over +100..+300 ms after the peak / peak; follow_to20_s: time after the peak until |w| < 20 % (cap 0.4 s).
    - shaft_roll_share: |wy| / |w| at the peak (pronation/supination share, racquet +Y = shaft).
    - pre_elev_deg: shaft elevation from gravity, asin(mean a_y / |mean a|) over -600..-300 ms (near rest a ~ gravity), and the
      normalised mean a vector (pre_a*_g are the means in g).
    - clip_gyro_n / clip_acc_n: saturated sample counts in the window.
    - t_apeak_minus_wpeak_s: time of the |a| peak minus the |w| peak. w_prebuild_mean_dps: mean |w| over -600..-300 ms.
    - v2: a_burst_s (run around the peak of ||a| - 1 g| above 50 % of it), clip_gyro_run_s / clip_acc_run_s (longest run of saturated samples),
      w_rise_dps2 / w_fall_dps2 (steepest rise before / fall after the peak of |w| smoothed over 10 ms), w_energy_pre_share (share
      of sum |w|^2 before the peak), a_jerk_peak_gps (max |d|a|/dt|, g/s).
    - bandK_log_share: log10(share of the Hann-windowed |w| spectrum energy, DC removed, in BANDS_HZ[K] + 1e-6)."""
    x = np.asarray(win, np.float64)
    w = x[:, 6]
    p = int(np.argmax(w))
    wp = max(w[p], 1e-9)
    f = []
    for k in range(6):
        c = x[:, k]
        f.append(c[np.argmax(np.abs(c))])
    f += list(x[p, :6])
    r = max(1, int(0.05 * RATE_HZ))
    f += [float(np.sign(np.round(x[max(0, p - r):p + r + 1, 3 + k].mean(), 3))) for k in range(3)]
    f += [w[p], x[:, 7].max()]
    above20 = w > 0.2 * wp
    lo, _ = _run(above20, p)
    f.append((p - lo) * DT)
    lo, hi = _run(w > 0.5 * wp, p)
    f.append((hi - lo + 1) * DT)
    lo, hi = _run(w > 0.25 * wp, p)
    f.append((hi - lo + 1) * DT)
    a, b = min(len(w), p + int(0.1 * RATE_HZ)), min(len(w), p + int(0.3 * RATE_HZ))
    f.append(w[a:b].mean() / wp if b > a else 0.0)
    after = np.nonzero(w[p:] < 0.2 * wp)[0]
    f.append(min(0.4, after[0] * DT) if len(after) else 0.4)
    f.append(abs(x[p, 4]) / wp)
    pre = x[:int(0.3 * RATE_HZ), :3].mean(axis=0)
    f.append(np.degrees(np.arcsin(np.clip(pre[1] / max(np.linalg.norm(pre), 1e-9), -1, 1))))
    f += list(pre)
    f += [x[:, 8].sum(), x[:, 9].sum(), (np.argmax(x[:, 7]) - p) * DT, w[:int(0.3 * RATE_HZ)].mean()]
    spec = np.abs(np.fft.rfft((w - w.mean()) * np.hanning(len(w)))) ** 2
    freq = np.fft.rfftfreq(len(w), DT)
    tot = max(spec.sum(), 1e-9)
    for lo_hz, hi_hz in BANDS_HZ:
        f.append(np.log10(spec[(freq >= lo_hz) & (freq < hi_hz)].sum() / tot + 1e-6))
    dev = np.abs(x[:, 7] - 1.0)  # |a| minus gravity
    ap = int(np.argmax(dev))
    lo, hi = _run(dev > 0.5 * max(dev[ap], 1e-9), ap)
    f.append((hi - lo + 1) * DT)
    for k in (8, 9):
        runs = np.diff(np.flatnonzero(np.diff(np.concatenate([[0], (x[:, k] > 0.5).astype(int), [0]]))).reshape(-1, 2), axis=1)
        f.append(float(runs.max()) * DT if len(runs) else 0.0)
    ws = np.convolve(w, np.ones(5) / 5, mode="same")
    dw = np.diff(ws) / DT
    f += [dw[:p].max(initial=0.0), dw[p:].min(initial=0.0)]
    f.append((w[:p] ** 2).sum() / max((w ** 2).sum(), 1e-9))
    f.append(np.abs(np.diff(x[:, 7]) / DT).max())
    out = np.asarray(f, np.float32)
    assert out.shape == (N_FEATURES,), out.shape
    return out


def full_features_batch(x):
    return np.stack([full_features(w) for w in x]) if len(x) else np.zeros((0, N_FEATURES), np.float32)
