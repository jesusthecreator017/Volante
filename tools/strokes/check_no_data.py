"""Fails if session data, video or participant files are tracked by git (the dataset lives in ~/Datasets/oso-strokes, never
in the repo; docs/strokes/consent.md). Run from anywhere: python tools/strokes/check_no_data.py [repo_root]"""
import re
import subprocess
import sys
from pathlib import Path

# Paths that may hold a bundled sample or a test fixture (synthetic or the owner's own demo data, reviewed in the PR)
ALLOWED = ("assets/", "app/public/sample/", "tests/", "fixtures/")
FORBIDDEN = re.compile(r"(\.mp4|\.mov|(^|/)imu\.bin|(^|/)session\.json|(^|/)participants\.csv)$", re.IGNORECASE)


def offenders(files):
    return [f for f in files if FORBIDDEN.search(f) and not any(f.startswith(a) or f"/{a}" in f for a in ALLOWED)]


def main(root):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    bad = offenders(out.splitlines())
    for f in bad:
        print(f"tracked session data or video: {f}")
    if bad:
        print("Remove with `git rm --cached` and keep it under ~/Datasets/oso-strokes/ (docs/strokes/consent.md).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]))
