"""Phase 16.2 core: read a pod recording, apply the mount, and find swings. Ports of the Rust code, constants included:
- read_imu: crates/oso-replay/src/imu/packet.rs `parse` (unwrap seq/t_us against the previous arrival, sort, dedupe, gaps)
- implausible_rest: packet.rs `implausible_rest` (the misaligned-FIFO detector)
- Mount: crates/oso-replay/src/imu/mount.rs `Mount::from_axes` (rows = racquet X, Y, Z in the sensor frame)
- SwingCounter: crates/oso-replay/src/imu/swingdet.rs (what `oso-rec collect` counted live)
"""
import math
import struct
from dataclasses import dataclass, field

import numpy as np

HEADER_LEN, PACKET_LEN = 16, 18
G = 9.80665
ACC_K = np.float32(G / 2048.0)  # m/s^2 per count (+-16 g)
GYRO_K = np.float32(math.pi / 180.0 / 16.4)  # rad/s per count (+-2000 dps)
RAIL = (32767, -32768)

# swingdet.rs
SMOOTH_S = 0.020
THRESHOLD_DPS = 400.0
REFRACTORY_S = 0.8
STILL_DPS = 150.0

# packet.rs
IMPLAUSIBLE_WINDOW_S = 2.0


@dataclass
class Recording:
    rate_hz: int
    mount_id: int
    t: np.ndarray  # [n] device time, s (unwrapped from the first packet's raw t_us), sorted, unique
    t_us: np.ndarray  # [n] int64, the same in microseconds (block and marker times in session.json are on this clock)
    acc: np.ndarray  # [n, 3] float32, m/s^2, sensor frame
    gyro: np.ndarray  # [n, 3] float32, rad/s, sensor frame
    sat_gyro: np.ndarray  # [n] bool: a gyro axis at the int16 rail
    sat_acc: np.ndarray  # [n] bool
    stats: dict = field(default_factory=dict)  # arrivals, unique, lost, duplicate, reordered


def read_imu(path):
    b = open(path, "rb").read()
    if len(b) < HEADER_LEN or b[:4] != b"OSOI":
        raise ValueError(f"{path}: not an imu.bin (magic)")
    version, rate, mount_id = struct.unpack_from("<HHI", b, 4)
    if version != 1:
        raise ValueError(f"{path}: unsupported imu.bin version {version}")
    body = b[HEADER_LEN:]
    if len(body) % PACKET_LEN:
        raise ValueError(f"{path}: body of {len(body)} bytes is not a whole number of packets")
    rec = np.frombuffer(body, dtype=np.dtype([("seq", "<u2"), ("t", "<u4"), ("v", "<i2", 6)]))
    n = len(rec)
    seq = np.empty(n, np.int64)
    tus = np.empty(n, np.int64)
    if n:
        # Unwrap against the previous arrival: the signed 16/32-bit step is the true step even across a wrap
        ds = ((rec["seq"][1:].astype(np.int64) - rec["seq"][:-1].astype(np.int64) + 32768) % 65536) - 32768
        dt = ((rec["t"][1:].astype(np.int64) - rec["t"][:-1].astype(np.int64) + (1 << 31)) % (1 << 32)) - (1 << 31)
        seq[0], tus[0] = int(rec["seq"][0]), int(rec["t"][0])
        seq[1:] = seq[0] + np.cumsum(ds)
        tus[1:] = tus[0] + np.cumsum(dt)
    # Accounting (packet.rs): duplicate = a seq seen before; reordered = first arrival below the highest seq so far
    dup = reord = 0
    seen, top = set(), None
    for s in seq.tolist():
        if s in seen:
            dup += 1
        else:
            seen.add(s)
            if top is not None and s < top:
                reord += 1
        top = s if top is None else max(top, s)
    order = np.lexsort((np.arange(n), seq))  # stable: the first arrival of a seq wins
    seq_s, keep = seq[order], np.ones(n, bool)
    keep[1:] = seq_s[1:] != seq_s[:-1]
    idx = order[keep]
    lost = int((np.diff(seq[idx]) - 1).clip(min=0).sum()) if len(idx) > 1 else 0
    v = rec["v"][idx]
    return Recording(
        rate_hz=rate, mount_id=mount_id, t=tus[idx] * 1e-6, t_us=tus[idx],
        acc=(v[:, :3].astype(np.float32) * ACC_K), gyro=(v[:, 3:].astype(np.float32) * GYRO_K),
        sat_gyro=np.isin(v[:, 3:], RAIL).any(axis=1), sat_acc=np.isin(v[:, :3], RAIL).any(axis=1),
        stats=dict(arrivals=n, unique=int(len(idx)), lost=lost, duplicate=dup, reordered=reord),
    )


def encode_imu(rate_hz, packets, mount_id=0):
    """packets: iterable of (seq16, t32, (ax, ay, az, gx, gy, gz)) in arrival order -> imu.bin bytes (tests, fixtures)."""
    out = bytearray(b"OSOI" + struct.pack("<HHII", 1, rate_hz, mount_id, 0))
    for s, t, v in packets:
        out += struct.pack("<HI6h", s & 0xFFFF, t & 0xFFFFFFFF, *v)
    return bytes(out)


class Mount:
    """Rows = the racquet's X, Y (shaft), Z (face normal) axes in the sensor frame. `apply(v)` -> racquet-frame coordinates."""

    def __init__(self, rows):
        self.rows = np.asarray(rows, float)

    @classmethod
    def from_axes(cls, shaft, normal):
        y = np.asarray(shaft, float)
        if np.linalg.norm(y) <= 1e-9:
            raise ValueError("shaft axis has no length")
        y = y / np.linalg.norm(y)
        z = np.asarray(normal, float) - np.dot(normal, y) * y
        if np.linalg.norm(z) <= 1e-6:
            raise ValueError("face normal is parallel to the shaft")
        z = z / np.linalg.norm(z)
        return cls([np.cross(y, z), y, z])

    def apply(self, v):
        """[..., 3] -> [..., 3] (float32 like the Rust Sample)"""
        return (np.asarray(v, float) @ self.rows.T).astype(np.float32)


def implausible_rest(t, acc, gyro):
    """packet.rs `implausible_rest`: misaligned FIFO bytes seen as physically impossible data (frame-independent norms).
    - still but wrong gravity for >= 0.5 s: |w| < 20 dps while |a| is outside 0.7-1.3 g;
    - in any back-to-back 2 s window: a steady spin (mean |w| > 200 dps, CV < 0.15), or a steady wrong gravity (median |a|
      outside 0.5-2.0 g, CV < 0.1). A trailing partial window is not judged."""
    a = np.linalg.norm(acc.astype(float), axis=1) / G
    w = np.degrees(np.linalg.norm(gyro.astype(float), axis=1))
    since = None
    for ti, ai, wi in zip(t.tolist(), a.tolist(), w.tolist()):
        if wi < 20.0 and not (0.7 <= ai <= 1.3):
            since = ti if since is None else since
            if ti - since >= 0.5:
                return True
        else:
            since = None
    i, n = 0, len(t)
    while i < n:
        j = int(np.searchsorted(t, t[i] + IMPLAUSIBLE_WINDOW_S, side="left"))
        if j >= n:
            break
        ww, aa = w[i:j], a[i:j]
        wm = ww.mean()
        wcv = ww.std() / max(abs(wm), 1e-9)
        am = aa.mean()
        acv = aa.std() / max(abs(am), 1e-9)
        med = np.sort(aa)[len(aa) // 2]
        if (wm > 200.0 and wcv < 0.15) or (not (0.5 <= med <= 2.0) and acv < 0.1):
            return True
        i = j
    return False


class SwingCounter:
    """swingdet.rs `SwingCounter`, sample for sample: |w| (dps) averaged over the trailing SMOOTH_S; armed -> rising through
    `threshold` counts one swing and disarms; re-armed after REFRACTORY_S since the last count AND smoothed |w| < STILL_DPS."""

    def __init__(self, threshold=THRESHOLD_DPS, refractory=REFRACTORY_S):
        self.threshold, self.refractory = threshold, refractory
        self.win, self.sum, self.armed, self.last, self.count = [], 0.0, True, None, 0
        self._head = 0

    def push(self, t, dps):
        self.win.append((t, dps))
        self.sum += dps
        while t - self.win[self._head][0] >= SMOOTH_S:
            self.sum -= self.win[self._head][1]
            self._head += 1
        v = self.sum / (len(self.win) - self._head)
        if self._head > 4096:  # keep the list short
            self.win, self._head = self.win[self._head:], 0
        if not self.armed:
            self.armed = v < STILL_DPS and (self.last is None or t - self.last >= self.refractory)
        elif v >= self.threshold:
            self.armed, self.last = False, t
            self.count += 1
            return True
        return False


def onsets(t, gyro, threshold=THRESHOLD_DPS):
    """Indices where the live counter would have counted a swing (fresh counter over this span)."""
    c = SwingCounter(threshold)
    dps = np.degrees(np.linalg.norm(gyro.astype(float), axis=1))
    return [i for i, (ti, di) in enumerate(zip(t.tolist(), dps.tolist())) if c.push(ti, di)]
