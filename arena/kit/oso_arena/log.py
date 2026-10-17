"""Match log: what `match.play` returns and `animate.to_replay` consumes. File = gzip (mtime 0, so equal logs are equal bytes) of
  b"OSML" | u32 json_len | JSON (scalar fields, rallies_detail, events, array table) | raw little-endian array blobs (8-byte aligned).
No pickle, no npz (zip stamps the time): arrays are described in the JSON and read with np.frombuffer."""
import gzip
import json
import struct
import zlib
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np

LOG_VERSION = 1
MAGIC = b"OSML"
MAX_RAW = 256 << 20  # decompression cap
_DT = {"<f4": np.dtype("<f4"), "<f2": np.dtype("<f2")}


@dataclass
class MatchLog:
    contract: str
    kit_version: str
    seed: int
    a_sha256: str
    b_sha256: str
    a_name: str
    b_name: str
    builds: list  # [[p, s, r, a] of A, [..] of B]
    score: list  # games won [a, b]
    winner: object  # 0 (a) / 1 (b) / None
    voided: bool
    reason: object  # str | None
    rallies: int
    duration_s: float  # simulated play time (intervals excluded)
    wall_duration_s: float  # including the intervals
    games: list = field(default_factory=list)  # {score [a, b], winner, first_server, rallies} per finished game
    rallies_detail: list = field(default_factory=list)  # {game 1-3, ends (world side of a: 0 = -X, 1 = +X), interval_before_s, start_t, end_t (sim play seconds), server, winner (None = let), reason, shots, score (the game's points)}
    frames: dict = field(default_factory=dict)  # 30 Hz: pos [F,2,2] f32 (court x,z), vel [F,2,2] f16, shuttle [F,3] f32 (x,y,z); frame f = sim step 4f
    events: list = field(default_factory=list)  # sim events: start (+ game, ends, interval_before_s), hit, land, point, game_over; k = sim step (1/120 s), t = sim seconds


_SCALARS = [f.name for f in fields(MatchLog) if f.name != "frames"]


def _encode(log):
    meta = {n: getattr(log, n) for n in _SCALARS}
    meta["log_version"] = LOG_VERSION
    table, blob = {}, b""
    for name in sorted(log.frames):
        a = np.ascontiguousarray(log.frames[name])
        dt = {"float32": "<f4", "float16": "<f2"}[a.dtype.name]
        a = a.astype(_DT[dt], copy=False)
        table[name] = dict(dtype=dt, shape=list(a.shape), offset=len(blob))
        blob += a.tobytes()
        blob += b"\0" * (-len(blob) % 8)
    meta["arrays"] = table
    head = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode()
    head += b" " * (-(8 + len(head)) % 8)
    return MAGIC + struct.pack("<I", len(head)) + head + blob


def write(path, log):
    Path(path).write_bytes(gzip.compress(_encode(log), compresslevel=6, mtime=0))


def _inflate(raw):
    d = zlib.decompressobj(31)
    out = d.decompress(raw, MAX_RAW)
    if d.unconsumed_tail:
        raise ValueError("match log is too large once decompressed")
    if not d.eof:
        raise ValueError("match log is truncated")
    return out


def read(path):
    try:
        return _read(path)
    except (zlib.error, KeyError, TypeError, RecursionError) as e:  # a damaged or foreign file is a ValueError, never a crash
        raise ValueError(f"not a match log ({type(e).__name__})") from None


def _read(path):
    data = _inflate(Path(path).read_bytes())
    if data[:4] != MAGIC or len(data) < 8:
        raise ValueError("not a match log")
    (n,) = struct.unpack("<I", data[4:8])
    meta = json.loads(data[8:8 + n])
    if meta.pop("log_version", None) != LOG_VERSION:
        raise ValueError("unsupported match log version")
    base = 8 + n
    frames = {}
    for name, t in meta.pop("arrays").items():
        dt, shape, off = _DT[t["dtype"]], tuple(t["shape"]), t["offset"]
        count = int(np.prod(shape))
        if off < 0 or base + off + count * dt.itemsize > len(data):
            raise ValueError("match log array out of range")
        frames[name] = np.frombuffer(data, dt, count, base + off).reshape(shape).copy()
    return MatchLog(**{k: meta[k] for k in _SCALARS}, frames=frames)
