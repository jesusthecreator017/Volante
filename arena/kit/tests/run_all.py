"""Runs every test module in this folder in one process: tools/.venv/bin/python arena/kit/tests/run_all.py"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

for f in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
    print(f"== {f.name}")
    helpers.run(vars(importlib.import_module(f.stem)))
