"""replay.bin v1 writer/reader (format: docs/data-format.md)."""
import json
import struct
from pathlib import Path

import numpy as np

MAGIC = b"OSOR"


def _pad(n):
    return -n % 16


def write(path, fps, tracks, start_time=0.0, coordinate_system="court_m_yup_xlength", frame_count=None):
    """tracks: list of (meta dict, array). meta needs name (+ anything else to put in the header);
    dtype/shape/offset are filled in here. Arrays are written little-endian f32 only."""
    blob, headers = b"", []
    for meta, arr in tracks:
        arr = np.ascontiguousarray(arr, "<f4")
        headers.append({**meta, "dtype": "f32", "shape": list(arr.shape), "offset": len(blob)})
        blob += arr.tobytes()
        blob += b"\0" * _pad(len(blob))
    if frame_count is None:
        frame_count = next(h for h in headers if h.get("per_frame", True))["shape"][0]
    head = json.dumps({"version": 1, "fps": fps, "frame_count": frame_count, "start_time": start_time,
                       "coordinate_system": coordinate_system, "tracks": headers}).encode()
    head += b"\0" * _pad(8 + len(head))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(MAGIC + struct.pack("<I", len(head)) + head + blob)


def read(path):
    """Returns (header dict, {track name: array})."""
    b = Path(path).read_bytes()
    assert b[:4] == MAGIC, "bad magic"
    (n,) = struct.unpack("<I", b[4:8])
    header = json.loads(b[8:8 + n].rstrip(b"\0"))
    assert header["version"] == 1
    base = 8 + n
    assert base % 16 == 0
    out = {}
    for t in header["tracks"]:
        assert t["offset"] % 16 == 0 and t["dtype"] == "f32"
        count = int(np.prod(t["shape"]))
        out[t["name"]] = np.frombuffer(b, "<f4", count, base + t["offset"]).reshape(t["shape"])
    return header, out
