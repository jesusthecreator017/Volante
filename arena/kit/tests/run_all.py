"""Runs every test module in this folder in one process: tools/.venv/bin/python arena/kit/tests/run_all.py
Keeps going after a failure and prints a summary; the exit code is 1 when any test failed."""
import importlib
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402,F401

total, failed = 0, []
for f in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    print(f"== {f.name}")
    mod = importlib.import_module(f.stem)
    for k, t in sorted(vars(mod).items()):
        if not (k.startswith("test_") and callable(t)):
            continue
        total += 1
        t0 = time.time()
        try:
            t()
            print(f"ok  {k} ({time.time() - t0:.1f}s)")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            failed.append(f"{f.stem}.{k}")
            print(f"FAIL {k}")
print(f"\n{total} tests, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}")
sys.exit(1 if failed else 0)
