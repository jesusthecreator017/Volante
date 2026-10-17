"""Match, log, animate, vendored body code and the CLI."""
import gzip
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from helpers import GOOD_BUILD, ROOT, random_actor, run
from oso_arena import animate, body, cli, export, log, match, sim

EVENT_KINDS = {"start", "hit", "cord", "land", "point", "game_over"}


def model(tmp, seed=0, name="M", build=GOOD_BUILD, scale=0.2):
    p = Path(tmp) / f"{name}{seed}.safetensors"
    export.export(random_actor(seed, scale), p, name, "", build)
    return str(p)


def test_play_is_deterministic_and_bytes_identical():
    with tempfile.TemporaryDirectory() as d:
        a, b = model(d, 1, "A"), model(d, 2, "B", {"power": 3, "speed": 8, "reach": 8, "accuracy": 5})
        l1, l2, l3 = match.play(a, b, 5, target=5), match.play(a, b, 5, target=5), match.play(a, b, 6, target=5)
        log.write(Path(d) / "1.gz", l1)
        log.write(Path(d) / "2.gz", l2)
        log.write(Path(d) / "3.gz", l3)
        assert (Path(d) / "1.gz").read_bytes() == (Path(d) / "2.gz").read_bytes()
        assert (Path(d) / "1.gz").read_bytes() != (Path(d) / "3.gz").read_bytes()
        # the gzip header time is 0 (bytes 4-8), so equal logs are equal bytes whenever they are written
        assert (Path(d) / "1.gz").read_bytes()[4:8] == b"\0\0\0\0"
        assert l1.a_name == "A" and l1.b_name == "B" and l1.builds == [[9, 6, 5, 4], [3, 8, 8, 5]]
        assert l1.a_sha256 == hashlib.sha256(Path(a).read_bytes()).hexdigest() and l1.b_sha256 != l1.a_sha256
        assert l1.contract == "oso-arena/1" and l1.seed == 5


def test_log_round_trip_and_fields():
    with tempfile.TemporaryDirectory() as d:
        l = match.play("bot", "bot", 3, target=5)
        assert len(l.games) in (2, 3) and sorted(l.score)[1] == 2
        log.write(Path(d) / "m.gz", l)
        r = log.read(Path(d) / "m.gz")
        assert r.__dict__.keys() == l.__dict__.keys()
        for k, v in l.__dict__.items():
            if k == "frames":
                assert v.keys() == r.frames.keys() and all(np.array_equal(v[n], r.frames[n]) and v[n].dtype == r.frames[n].dtype for n in v)
            else:
                assert getattr(r, k) == v, k
        assert l.frames["pos"].dtype == np.float32 and l.frames["vel"].dtype == np.float16 and l.frames["shuttle"].shape[1:] == (3,)
        n = len(l.frames["pos"])
        assert len(l.frames["vel"]) == len(l.frames["shuttle"]) == n and n == l.events[-1]["k"] // 4 + 1
        assert {e["kind"] for e in l.events} <= EVENT_KINDS and l.events[-1]["kind"] == "game_over"
        # fields the Pi reads
        assert l.winner in (0, 1) and not l.voided and l.reason is None and l.score[l.winner] == 2  # games won
        assert l.rallies == len(l.rallies_detail) and l.duration_s == l.events[-1]["t"]
        assert set(l.rallies_detail[0]) == {"game", "ends", "interval_before_s", "start_t", "end_t", "server", "winner", "reason", "shots", "score", "contact_q"}
        assert l.wall_duration_s == l.duration_s + sum(r["interval_before_s"] for r in l.rallies_detail)
        for i, g in enumerate(l.games):
            assert set(g) == {"score", "winner", "first_server", "rallies"} and g["rallies"] == sum(r["game"] == i + 1 for r in l.rallies_detail)
            last = [r for r in l.rallies_detail if r["game"] == i + 1][-1]
            assert last["score"] == g["score"] and g["winner"] == int(g["score"][1] > g["score"][0])
        # container: gzip of a versioned blob, no pickle; a garbage / foreign file is rejected
        raw = gzip.decompress((Path(d) / "m.gz").read_bytes())
        assert raw[:4] == b"OSML" and b"\x80\x04" not in raw[:8]
        (Path(d) / "bad.gz").write_bytes(gzip.compress(b"XXXX" + b"\0" * 20))
        try:
            log.read(Path(d) / "bad.gz")
        except ValueError:
            pass
        else:
            raise AssertionError
        bomb = Path(d) / "bomb.gz"
        bomb.write_bytes(gzip.compress(b"\0" * ((256 << 20) + 10), 1))
        try:
            log.read(bomb)
        except ValueError:
            pass
        else:
            raise AssertionError("decompression bomb accepted")


def test_best_of_three_rules():
    l = match.play("bot", "bot", 1, target=9)
    rd = l.rallies_detail
    for g in l.games:
        hi, lo = max(g["score"]), min(g["score"])
        assert hi >= 9 and (hi - lo >= 2 or hi == 21), g
    assert l.score == [sum(g["winner"] == 0 for g in l.games), sum(g["winner"] == 1 for g in l.games)] and max(l.score) == 2
    assert l.games[0]["first_server"] == l.events[0]["s"]
    for prev, g in zip(l.games, l.games[1:]):  # the winner of a game serves first in the next
        assert g["first_server"] == prev["winner"]
    for prev, r in zip(rd, rd[1:]):  # within a game the winner of a rally serves next (a let keeps the server)
        if r["game"] == prev["game"]:
            assert r["server"] == (prev["winner"] if prev["winner"] is not None else prev["server"])
    # intervals: 120 s before the first rally of games 2 and 3, 60 s once per game when the leading score first reaches 8
    for n in (1, 2, 3)[:len(l.games)]:
        gr = [r for r in rd if r["game"] == n]
        assert (gr[0]["interval_before_s"] == 120.0) == (n > 1)
        mids = [r for r in gr if r["interval_before_s"] == 60.0]
        k = gr.index(mids[0]) if len(mids) == 1 else 0
        assert len(mids) == 1 and k > 0 and max(gr[k - 1]["score"]) >= 8 and all(max(r["score"]) < 8 for r in gr[:k - 1])
    # ends: game 1 a on -X (0), game 2 +X (1), game 3 -X until the leader reaches 8, then +X
    for r in rd:
        flip = r["game"] == 3 and any(q["game"] == 3 and q["interval_before_s"] == 60.0 and q["start_t"] <= r["start_t"] for q in rd)
        assert r["ends"] == ((r["game"] - 1) % 2 ^ flip), r
    for fs in (0, 1):
        assert match.play("bot", "bot", 4, first_server=fs, target=5).events[0]["s"] == fs
    assert match.play("bot", "bot", 4, target=5).events[0]["s"] == int(np.random.default_rng(4).integers(0, 2, 1)[0])  # random from the seed
    one = match.play("bot", "bot", 1, target=5, best_of=1)
    assert len(one.games) == 1 and sorted(one.score) == [0, 1] and one.duration_s == one.events[-1]["t"]
    v = match.play("bot", "bot", 1, max_sim_s=20.0)
    assert v.voided and v.winner is None and v.reason and v.duration_s > 20.0 and v.wall_duration_s >= v.duration_s
    assert len(match.play("bot", "bot", 1, max_sim_s=20.0).rallies_detail) == v.rallies


def test_animate_replay_is_consistent_and_readable():
    import replay as tools_replay  # tools/replay.py (the Replay reader)

    with tempfile.TemporaryDirectory() as d:
        l = match.play("bot", "bot", 2, target=9)
        out = Path(d) / "m.bin"
        tl = animate.to_replay(l, out)
        hd, tr = tools_replay.read(out)
        assert hd["fps"] == 60.0 and tr["joints3d"].shape == (hd["frame_count"], 2, 17, 4) and tr["shuttle"].shape == (hd["frame_count"], 8)
        ev = tr["events"]
        pts = ev[ev[:, 1] == 3]
        assert len(pts) == sum(sum(g["score"]) for g in l.games)  # one point event per point
        assert (np.diff(ev[:, 0]) >= 0).all() and (np.diff(pts[:, 0]) > 3.0).all()
        assert np.diff(pts[:, 0]).max() > 8.0  # an interval shows as a pause (capped at 8 s)
        assert any(r["ends"] for r in l.rallies_detail)  # game 2 is played on the other ends
        n_contacts = (ev[:, 1] == 1).sum()
        assert n_contacts == sum(r["shots"] for r in l.rallies_detail if r["winner"] is not None) and (ev[:, 1] == 2).sum() == len(pts)
        sh = tr["shuttle"]
        assert (sh[sh[:, 6] > 0, 1] >= -1e-6).all() and np.abs(tr["joints3d"][..., 1]).max() < 3.7
        c = ev[ev[:, 1] == 1]
        assert (np.sign(c[:, 3]) == np.where(c[:, 2] == 0, -1, 1)).mean() > 0.97  # contacts happen on the hitter's own side
        # the rebuilt 120 Hz arrays follow the logged 30 Hz frames (players exactly, the re-integrated shuttle to < 1 mm)
        spans = animate._rally_spans(l.events)
        pos, shut = animate.expand(l, spans)
        worst_s = worst_p = 0.0
        for r in spans:
            for f in range(r["start"]["k"] // 4 + 1, r["land"]["k"] // 4):
                worst_s = max(worst_s, np.abs(shut[4 * f, :3] - l.frames["shuttle"][f]).max())
                worst_p = max(worst_p, np.abs(pos[4 * f] - l.frames["pos"][f]).max())
        assert worst_s < 1e-3 and worst_p < 1e-6, (worst_s, worst_p)
        if shutil.which("cargo"):  # the viewer's own parser accepts it
            r = subprocess.run(["cargo", "run", "--release", "-q", "-p", "oso-arena", "--", "--check-replay", str(out)], cwd=ROOT,
                               capture_output=True, text=True)
            assert r.returncode == 0 and ": ok," in r.stdout and "shuttle true" in r.stdout, (r.stdout, r.stderr)


def test_animate_refuses_a_log_without_a_finished_rally():
    l = match.play("bot", "bot", 1, max_sim_s=0.2)
    try:
        animate.to_replay(l, "/nonexistent/x.bin")
    except ValueError:
        return
    raise AssertionError


def test_vendored_body_code_equals_tools_synth_motion():
    import synth_motion as sm  # tools/synth_motion.py

    assert body.SWING == sm.SWING and body.JUMP_S == sm.JUMP_S and (body.THIGH, body.SHIN, body.UPPER_ARM, body.FOREARM) == (
        sm.THIGH, sm.SHIN, sm.UPPER_ARM, sm.FOREARM) and (body.HIP_Y, body.ANKLE_Y, body.TORSO, body.HIP_HALF, body.SHOULDER_HALF) == (
        sm.HIP_Y, sm.ANKLE_Y, sm.TORSO, sm.HIP_HALF, sm.SHOULDER_HALF)
    fps = 60.0
    t = np.arange(int(4 * fps)) / fps
    rng = np.random.default_rng(1)
    for sign in (-1, 1):
        root = np.cumsum(rng.normal(size=(len(t), 2)) * 0.02, 0) + [sign * 3.0, 0.0]
        swings = [(0.5, 100.0), (1.5, 95.0, (0.3, 0.12, 0.2, 0.2), 10.0), (2.5, 105.0, (0.4, 0.12, 0.3, 0.3), -20.0)]
        facing = np.tile([-sign, 0.0, 0.0], (len(t), 1)) + rng.normal(size=(len(t), 3)) * [0.1, 0, 0.1]
        facing /= np.linalg.norm(facing, axis=1, keepdims=True)
        for kw in (dict(), dict(facing=facing)):
            a = body.player_pose(root, sign, t, fps, swings, [1.0, 3.0], [2.0], **kw)
            b = sm.player_pose(root, sign, t, fps, swings, [1.0, 3.0], [2.0], **kw)
            assert np.array_equal(a, b)
    for u in (-1.0, 0.0, 0.3, 1.0, 2.0):
        assert body.mj(u) == sm.mj(u)
    # and the vendored replay writer writes the same bytes as tools/replay.py
    import replay as tr

    with tempfile.TemporaryDirectory() as d:
        tracks = [({"name": "joints3d", "layout": "x,y,z,conf"}, rng.normal(size=(7, 2, 17, 4))),
                  ({"name": "events", "layout": "t,type,player,x,y,z", "per_frame": False}, rng.normal(size=(5, 6)))]
        tr.write(Path(d) / "a.bin", 60.0, tracks)
        animate.write_replay(Path(d) / "b.bin", 60.0, tracks)
        assert (Path(d) / "a.bin").read_bytes() == (Path(d) / "b.bin").read_bytes()


def test_cli_every_subcommand():
    import torch
    from oso_arena import train

    with tempfile.TemporaryDirectory() as d:
        D = Path(d)
        net = train.ActorCritic()
        torch.save({"model": net.state_dict(), "elapsed": 0.0}, D / "ck.pt")
        out = D / "m.safetensors"
        base = ["export", str(D / "ck.pt"), "-o", str(out), "--name", "Cli Model", "--description", "d", "--build", "9,6,5,4"]
        assert cli.main(base) == 0 and out.exists()
        assert cli.main(["check", str(out)]) == 0
        # the safetensors training state (actor + critic + log_std) exports too
        from safetensors.numpy import save_file

        save_file({k: v.numpy() for k, v in net.state_dict().items()}, str(D / "full.safetensors"))
        assert cli.main(["export", str(D / "full.safetensors"), "-o", str(D / "f2.safetensors"), "--name", "F", "--build", "6,6,6,6"]) == 0
        bad = D / "bad.safetensors"
        bad.write_bytes(b"\x80\x04not safetensors")
        assert cli.main(["check", str(bad)]) == 1
        assert cli.main(["export", str(D / "ck.pt"), "-o", str(D / "x.safetensors"), "--name", "x", "--build", "9,6,5,5"]) == 1  # sum 25
        assert cli.main(["eval", str(out), "--vs", "bot", "--games", "2"]) == 0
        assert cli.main(["eval", str(out), "--vs", str(D / "f2.safetensors"), "--games", "2"]) == 0
        assert cli.main(["eval", str(out), "--vs", "base", "--games", "2"]) == 1  # the base model arrives with phase 14.3
        assert cli.main(["match", str(out), "bot", "-o", str(D / "m.log.gz"), "--seed", "3"]) == 0
        log.write(D / "small.log.gz", match.play("bot", "bot", 1, target=3, best_of=1))  # animate is slow (~1.5 ms/frame): keep it small
        assert cli.main(["animate", str(D / "small.log.gz"), "-o", str(D / "m.bin")]) == 0 and (D / "m.bin").stat().st_size > 1000
        r = subprocess.run([str(Path(ROOT / "tools/.venv/bin/python")), "-m", "oso_arena", "check", str(out)], cwd=ROOT / "arena/kit",
                           capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout.startswith("ok:"), (r.stdout, r.stderr)
        r = subprocess.run([str(Path(ROOT / "tools/.venv/bin/python")), "-m", "oso_arena", "nope"], cwd=ROOT / "arena/kit", capture_output=True, text=True)
        assert r.returncode == 1  # usage errors exit 1


if __name__ == "__main__":
    run(globals())
