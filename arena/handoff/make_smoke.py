"""Generates the Arena handoff smoke set deterministically: 3 valid and 7 invalid submission files plus expected.json.

    tools/.venv/bin/python arena/handoff/make_smoke.py OUTDIR            # write the files and expected.json
    tools/.venv/bin/python arena/handoff/make_smoke.py OUTDIR --verify   # ... and run `oso-arena check` on each (exit 1 on any difference)

expected.json: {"<file>": {"valid": bool, "reasons": [...]}}. `reasons` are the exact lines `oso-arena check` prints for a rejected
file (the Pi upload check returns the same list); for a valid file it is empty. The valid files go through the kit's own
`oso_arena.export.export`; the invalid ones are built by hand from a valid one. Same inputs, same bytes: no clock, no randomness."""
import json
import os
import pickle
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

KIT = Path(__file__).resolve().parents[1] / "kit"
if str(KIT) not in sys.path:
    sys.path.insert(0, str(KIT))

from safetensors.numpy import save_file  # noqa: E402

from oso_arena import contract, export  # noqa: E402

VALID = [  # file, name, description, build, weight seed
    ("valid_smasher.safetensors", "Smoke Smasher", "Power build. Smoke-test file, random weights.", {"power": 9, "speed": 6, "reach": 5, "accuracy": 4}, 11),
    ("valid_runner.safetensors", "Smoke Runner", "", {"power": 3, "speed": 8, "reach": 8, "accuracy": 5}, 12),
    ("valid_sniper.safetensors", "Smoke Sniper 狙", "Accuracy build with a non-ASCII name.", {"power": 4, "speed": 6, "reach": 5, "accuracy": 9}, 13),
]
BUILD_OK = '{"power":6,"speed":6,"reach":6,"accuracy":6}'


def _canon(path):
    """Rewrites the safetensors header with sorted keys and compact JSON (padded to 8 bytes). The safetensors writer keeps the metadata in
    a hash map whose order changes from run to run, which would make the files differ byte for byte; the data section is untouched."""
    data = Path(path).read_bytes()
    (n,) = struct.unpack("<Q", data[:8])
    head = json.dumps(json.loads(data[8:8 + n]), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    head += b" " * (-len(head) % 8)
    Path(path).write_bytes(struct.pack("<Q", len(head)) + head + data[8 + n:])


def _actor(seed, scale=0.2):
    rng = np.random.default_rng(seed)
    return {k: (rng.normal(size=sh) * scale).astype(np.float32) for k, sh in contract.TENSORS.items()}


def _meta(**over):
    m = {"contract": "oso-arena/1", "name": "Smoke Bad", "description": "", "build": BUILD_OK}
    m.update(over)
    return m


def generate(out):
    """Writes the smoke files into `out` (created) and returns the expected dict, which it also saves as expected.json."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    expected = {}
    for fname, name, desc, build, seed in VALID:  # the kit's own export path
        export.export(_actor(seed), out / fname, name, desc, build)
        _canon(out / fname)
        expected[fname] = {"valid": True, "reasons": []}

    def bad(fname, tensors, meta, reasons):
        save_file(tensors, str(out / fname), metadata=meta)
        _canon(out / fname)
        expected[fname] = {"valid": False, "reasons": reasons}

    base = _actor(21)

    t = dict(base)
    t["actor.6.weight"] = np.zeros((6, 256), np.float32)
    bad("invalid_extra_tensor.safetensors", t, _meta(), ["unexpected tensor 'actor.6.weight'"])

    t = dict(base)
    t["actor.0.weight"] = np.zeros((256, 36), np.float32)  # 36 inputs: the pre-14.2 observation size
    bad("invalid_wrong_shape.safetensors", t, _meta(), ["actor.0.weight has shape [256, 36], expected [256, 37]"])

    t = dict(base)
    t["actor.2.bias"] = t["actor.2.bias"].copy()
    t["actor.2.bias"][7] = np.nan
    bad("invalid_nan_weight.safetensors", t, _meta(), ["actor.2.bias contains NaN or inf"])

    bad("invalid_bad_build_sum.safetensors", base, _meta(build='{"power":9,"speed":6,"reach":5,"accuracy":5}'),
        ["header build stats must sum to 24, got 25"])

    bad("invalid_wrong_contract.safetensors", base, _meta(contract="oso-arena/2"),
        ["header contract is 'oso-arena/2', expected 'oso-arena/1'"])

    # oversize: a valid file with zero bytes appended, one byte over 1 MiB (the size check is the first one and is fatal)
    over = out / "invalid_oversize.safetensors"
    export.export(_actor(22), over, "Smoke Oversize", "", {"power": 6, "speed": 6, "reach": 6, "accuracy": 6})
    _canon(over)
    n = over.stat().st_size
    with open(over, "ab") as f:
        f.write(b"\0" * (contract.MAX_BYTES + 1 - n))
    size = over.stat().st_size
    expected[over.name] = {"valid": False, "reasons": [f"file is {size} bytes, over the {contract.MAX_BYTES}-byte (1 MiB) limit"]}

    # hostile: a pickle named .safetensors. Harmless content (a plain dict); the point is that the check must refuse it without ever
    # unpickling it. Its first 8 bytes read as a little-endian "header length" far above the 64 KiB cap.
    blob = pickle.dumps({"smoke": "this is a pickle, not a safetensors file"}, protocol=2)
    (out / "invalid_pickle.safetensors").write_bytes(blob)
    (n8,) = struct.unpack("<Q", blob[:8])
    expected["invalid_pickle.safetensors"] = {
        "valid": False,
        "reasons": [f"not a safetensors file: declared header length {n8} exceeds the {contract.MAX_HEADER}-byte cap"],
    }

    (out / "expected.json").write_text(json.dumps(dict(sorted(expected.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in out.iterdir():
        os.chmod(f, 0o644)  # the same mode everywhere, so the bundle zip does not depend on the umask
    return expected


def run_check_cli(path):
    """(exit code, printed lines) of `python -m oso_arena check PATH` run from the kit source tree."""
    env = dict(os.environ, PYTHONPATH=str(KIT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = subprocess.run([sys.executable, "-m", "oso_arena", "check", str(path)], capture_output=True, text=True, env=env)
    return r.returncode, [ln for ln in r.stdout.splitlines() if ln.strip()]


def verify(out, use_cli=True):
    """Differences between expected.json in `out` and what `check` says for each file (empty list = all as expected)."""
    from oso_arena import check

    out = Path(out)
    expected = json.loads((out / "expected.json").read_text(encoding="utf-8"))
    problems = []
    for fname, exp in expected.items():
        if use_cli:
            code, lines = run_check_cli(out / fname)
            ok, reasons = code == 0, ([] if code == 0 else lines)
            if code not in (0, 1):
                problems.append(f"{fname}: oso-arena check exited {code}")
        else:
            r = check.check_file(out / fname)
            ok, reasons = r.ok, r.reasons
        if ok != exp["valid"] or (not ok and reasons != exp["reasons"]):
            problems.append(f"{fname}: expected valid={exp['valid']} {exp['reasons']}, check says valid={ok} {reasons}")
    return problems


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 1
    out = Path(argv[0])
    exp = generate(out)
    print(f"wrote {len(exp)} smoke files + expected.json to {out}")
    if "--verify" in argv[1:]:
        problems = verify(out)
        for p in problems:
            print("MISMATCH " + p)
        print("smoke check: " + ("all files give the expected result" if not problems else f"{len(problems)} mismatch(es)"))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
