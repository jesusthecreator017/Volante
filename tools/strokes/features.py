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
