#!/usr/bin/env python3
"""A participant withdraws (spec 5): delete every trace of them and rebuild without them.

    tools/.venv/bin/python tools/strokes/withdraw.py P07 [--root ~/Datasets/oso-strokes] [--yes]

Deletes ROOT/raw/<session>/ and ROOT/labels/<session>.csv of every session whose player is P07, the P07 row of
ROOT/private/participants.csv, and every ROOT/processed/swings_v*.npz / qc_v*.html (older versions also hold their swings), then
rebuilds the newest version. Models trained on the old data are retrained at the next model version (16.4)."""
import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_dataset  # noqa: E402


def sessions_of(root, player):
    out = []
    for d in sorted((root / "raw").glob("*/session.json")):
        if json.loads(d.read_text(encoding="utf-8"))["player"]["id"] == player:
            out.append(d.parent)
    return out


def withdraw(root, player, yes=False):
    root = Path(root).expanduser().resolve()
    if not (player.startswith("P") and player[1:].isdigit()):
        raise SystemExit(f"{player!r} is not a participant ID (P01...)")
    sessions = sessions_of(root, player)
    processed = sorted((root / "processed").glob("swings_v*.npz")) if (root / "processed").is_dir() else []
    versions = sorted(int(p.stem.split("_v")[1]) for p in processed)
    print(f"{player}: {len(sessions)} sessions: {', '.join(s.name for s in sessions) or 'none'}")
    if not yes:
        if input(f"Delete them, their labels and the {player} participant row, and rebuild? [y/N] ").strip().lower() != "y":
            raise SystemExit("nothing deleted")
    for s in sessions:
        shutil.rmtree(s)
        (root / "labels" / f"{s.name}.csv").unlink(missing_ok=True)
    pc = root / "private" / "participants.csv"
    if pc.is_file():
        with pc.open(newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        head, body = (rows[0], rows[1:]) if rows and rows[0] and rows[0][0].lower() in ("id", "player", "player_id") else ([], rows)
        keep = [r for r in body if not (r and r[0].strip() == player)]
        with pc.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(([head] if head else []) + keep)
    for p in sorted((root / "processed").glob("*_v*.*")) if (root / "processed").is_dir() else []:
        p.unlink()
    m = build_dataset.build(root, versions[-1] if versions else 1)
    print(f"rebuilt v{m['version']}: {m['counts']['swings']} swings; {player} is in no file under {root}")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("player")
    ap.add_argument("--root", type=Path, default=build_dataset.DEFAULT_ROOT)
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)
    withdraw(a.root, a.player, a.yes)


if __name__ == "__main__":
    main()
