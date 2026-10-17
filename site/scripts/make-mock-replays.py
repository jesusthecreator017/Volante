#!/usr/bin/env python3
"""Plays a few short real matches with the Arena kit for `npm run dev:mock`.

Writes site/.mock-replays/<id>.bin (served at /api/matches/<id>/replay.bin) and matches.json (real names, builds, games, rallies
and score for mock.ts). Both are gitignored. Needs numpy + safetensors (the kit's own dependencies); run via make-mock-replays.sh.
Models: OSO_MODEL_A / OSO_MODEL_B (a .safetensors path, "bot" or "base"); default: the packaged house base model (else $OSO_LOCAL_BASE, else the bot) vs the bot.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "arena" / "kit"))
from oso_arena import animate, basemodel, match  # noqa: E402
from oso_arena.cli import _who  # noqa: E402

OUT = HERE.parent / ".mock-replays"
# (mock match id, seed, points per game, best of): the newest finished mock matches, so the landing teaser and model pages find them
PLAN = [(500, 3, 7, 3), (499, 5, 7, 1), (498, 8, 9, 1), (497, 13, 7, 3)]
STATS = ("power", "speed", "reach", "accuracy")


def default_a():
    """The packaged house base model, else a local checkpoint, else the bot."""
    try:
        basemodel.path()
        return "base"
    except ValueError:
        local = Path(os.environ.get("OSO_LOCAL_BASE", "/Users/jesusgonzalez07/Desktop/MacToUSB/Rust/oso/out/p143/base087.safetensors"))
        return str(local) if local.exists() else "bot"


def main():
    a, b = _who(os.environ.get("OSO_MODEL_A") or default_a()), _who(os.environ.get("OSO_MODEL_B", "bot"))
    OUT.mkdir(exist_ok=True)
    meta = {}
    for mid, seed, target, best_of in PLAN:
        lg = match.play(a, b, seed, target=target, best_of=best_of)
        if lg.voided:
            print(f"match {mid} voided, skipped")
            continue
        tl = animate.to_replay(lg, OUT / f"{mid}.bin")
        spans = animate._rally_spans(lg.events)  # same order as tl["sheet"]
        rallies = [
            dict(game=sp["start"]["game"], t_start=round(sh["start_t"], 2), t_end=round(sh["end_t"], 2), server="ab"[int(sp["start"]["s"])],
                 winner="ab"[int(sp["point"]["winner"])], reason=sh["reason"], shots=sh["shots"], score=sh["score"])
            for sp, sh in zip(spans, tl["sheet"])
        ]
        meta[str(mid)] = dict(
            a=dict(name=lg.a_name, build=dict(zip(STATS, map(int, lg.builds[0])))),
            b=dict(name=lg.b_name, build=dict(zip(STATS, map(int, lg.builds[1])))),
            games_won=lg.score, winner="ab"[lg.winner], games=[g["score"] for g in lg.games], seed=seed, rallies=rallies)
        print(f"{mid}: {lg.a_name} {lg.score[0]}-{lg.score[1]} {lg.b_name}, {len(rallies)} rallies, {(OUT / f'{mid}.bin').stat().st_size >> 10} KiB")
    (OUT / "matches.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
