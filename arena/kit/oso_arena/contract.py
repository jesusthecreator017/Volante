"""The oso-arena/1 submission file: layout, header and value rules, and the one reader every consumer uses (NumpyPolicy.load,
check.check_file, the Pi upload check). SECURITY: an upload is untrusted. The file is parsed by hand (8-byte length + capped JSON header)
and then by `safetensors` only; nothing here (or anywhere in the kit's submission path) uses pickle, torch.load or allow_pickle."""
import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass, field

import numpy as np

from oso_arena import CONTRACT, builds

MAX_BYTES = 1_048_576  # 1 MiB
MAX_HEADER = 65_536  # sanity cap on the declared JSON header size
MAX_ABS = 1000.0
OBS_DIM, ACT_DIM, HIDDEN = 37, 6, 256
TENSORS = {  # name -> shape; all float32
    "actor.0.weight": (HIDDEN, OBS_DIM), "actor.0.bias": (HIDDEN,),
    "actor.2.weight": (HIDDEN, HIDDEN), "actor.2.bias": (HIDDEN,),
    "actor.4.weight": (ACT_DIM, HIDDEN), "actor.4.bias": (ACT_DIM,),
}
NAME_MAX, DESC_MAX = 40, 280
_DTYPES = {"float32": "F32", "float64": "F64", "float16": "F16", "int64": "I64", "int32": "I32", "int8": "I8", "uint8": "U8"}


class NotSafetensors(ValueError):
    pass


@dataclass
class Submission:
    reasons: list = field(default_factory=list)
    fatal: bool = False  # the file could not be read as a submission at all
    arrays: dict = None
    meta: dict = field(default_factory=lambda: dict(name=None, description="", build=None, sha256=None))


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def parse_header(data):
    """(entries, metadata, data_start) of a safetensors byte string, parsed by hand with a size cap. Raises NotSafetensors."""
    if len(data) < 8:
        raise NotSafetensors("file is shorter than the 8-byte safetensors length prefix")
    (n,) = struct.unpack("<Q", data[:8])
    if n > MAX_HEADER:
        raise NotSafetensors(f"declared header length {n} exceeds the {MAX_HEADER}-byte cap")
    if 8 + n > len(data):
        raise NotSafetensors(f"truncated: the header declares {n} bytes but only {len(data) - 8} follow the length prefix")
    try:
        header = json.loads(data[8:8 + n].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise NotSafetensors("the header is not valid UTF-8 JSON") from None
    if not isinstance(header, dict):
        raise NotSafetensors("the header is not a JSON object")
    meta, entries = {}, {}
    for k, v in header.items():
        if k == "__metadata__":
            if not isinstance(v, dict) or not all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()):
                raise NotSafetensors("__metadata__ must map strings to strings")
            meta = v
            continue
        ok = (isinstance(v, dict) and isinstance(v.get("dtype"), str) and isinstance(v.get("shape"), list)
              and isinstance(v.get("data_offsets"), list) and len(v["data_offsets"]) == 2)
        ok = ok and all(type(d) is int and d >= 0 for d in v["shape"] + v["data_offsets"])
        if not ok:
            raise NotSafetensors(f"tensor entry {k!r} is malformed")
        entries[k] = v
    return entries, meta, 8 + n


def check_layout(layout):
    """layout {name: (dtype str like 'F32', shape)} -> reasons: exactly the 6 tensors, F32, the right shapes."""
    out = []
    for name in sorted(set(layout) - set(TENSORS)):
        out.append(f"unexpected tensor {name!r}")
    for name, shape in TENSORS.items():
        if name not in layout:
            out.append(f"missing tensor {name!r}")
            continue
        dt, sh = layout[name]
        if dt != "F32":
            out.append(f"{name} has dtype {dt}, expected F32")
        if tuple(sh) != shape:
            out.append(f"{name} has shape {list(sh)}, expected {list(shape)}")
    return out


def check_values(arrays):
    out = []
    for name, a in arrays.items():
        if not np.isfinite(a).all():
            out.append(f"{name} contains NaN or inf")
        elif a.size and float(np.abs(a).max()) > MAX_ABS:
            out.append(f"{name} has a value above {MAX_ABS:g} in magnitude ({float(np.abs(a).max()):.6g})")
    return out


def check_tensors(arrays):
    """Reasons (empty = ok) for a dict of arrays: layout and values. Used by NumpyPolicy.from_arrays and export."""
    layout = {}
    for k, a in arrays.items():
        a = np.asarray(a)
        layout[k] = (_DTYPES.get(a.dtype.name, a.dtype.name.upper()), a.shape)
    out = check_layout(layout)
    return out if out else check_values({k: np.asarray(v) for k, v in arrays.items()})


def _printable(s, allow_newline=False):
    return all(c.isprintable() or (allow_newline and c in "\n\t") for c in s)


def check_meta(meta):
    """(reasons, name, description, build) for the safetensors string->string metadata."""
    out = []
    c = meta.get("contract")
    if c != CONTRACT:
        out.append(f"header contract is {c!r}, expected {CONTRACT!r}")
    name, desc, build = meta.get("name"), meta.get("description", ""), None
    if not isinstance(name, str) or not 1 <= len(name) <= NAME_MAX:
        out.append(f"header name must be 1-{NAME_MAX} characters")
        name = None
    elif not _printable(name):
        out.append("header name has non-printable or control characters")
        name = None
    if len(desc) > DESC_MAX:
        out.append(f"header description is {len(desc)} characters, max {DESC_MAX}")
    elif not _printable(desc, allow_newline=True):
        out.append("header description has control characters")
    raw = meta.get("build")
    if raw is None:
        out.append("header is missing 'build'")
    elif len(raw) > 200:
        out.append("header build is too long")
    else:
        try:
            b = json.loads(raw)
        except (ValueError, RecursionError):
            out.append("header build is not valid JSON")
        else:
            errs = builds.validate(b)
            out += ["header " + e for e in errs]
            build = None if errs else b
    return out, name, desc, build


def read_submission(path):
    """Everything check_file does except the smoke test. Reasons are collected up to the first fatal one (size, not safetensors)."""
    sub = Submission()
    data = b""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(0, "not a regular file")
        size = st.st_size
        if size <= MAX_BYTES:
            with open(path, "rb") as f:
                data = f.read(MAX_BYTES + 1)
    except OSError as e:
        sub.reasons, sub.fatal = [f"cannot read the file: {e.strerror}"], True
        return sub
    if size > MAX_BYTES or len(data) > MAX_BYTES:
        sub.reasons, sub.fatal = [f"file is {max(size, len(data))} bytes, over the {MAX_BYTES}-byte (1 MiB) limit"], True
        return sub
    sub.meta["sha256"] = sha256_of(data)
    try:
        entries, meta, start = parse_header(data)
    except NotSafetensors as e:
        sub.reasons, sub.fatal = [f"not a safetensors file: {e}"], True
        return sub
    sub.reasons += check_layout({k: (v["dtype"], v["shape"]) for k, v in entries.items()})
    mreasons, name, desc, build = check_meta(meta)
    sub.reasons += mreasons
    sub.meta.update(name=name, description=desc, build=build)
    if not sub.reasons or all(r.startswith("header ") for r in sub.reasons):  # tensors are structurally right: load and check values
        arrays = _load(data)
        if isinstance(arrays, str):
            sub.reasons.append(arrays)
            sub.fatal = True
            return sub
        sub.reasons += check_values(arrays)
        sub.arrays = arrays
    return sub


def _load(data):
    """safetensors.numpy parses the (already hand-checked) bytes: returns the arrays or an error string."""
    from safetensors.numpy import load

    try:
        return load(data)
    except Exception as e:  # the loader rejects overlapping / gapped / out-of-range offsets
        return f"not a safetensors file: {e}"
