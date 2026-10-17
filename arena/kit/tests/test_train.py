"""Phase 14.5 K3/K4 and the coach skill's reward files: the trainer's flags, safetensors checkpoints, export taking the build from the run,
every style reward running one training iteration, and the per-rally shaping bound. Torch tests are skipped when torch is missing.
Training sizes here are tiny (a few games, a short horizon): they test the plumbing, not learning."""
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

from helpers import KIT, ROOT, run
from oso_arena import basemodel, builds, check, cli, export, policy, rewards, sim

try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False

STYLES = ROOT / "skills" / "oso-arena-coach" / "rewards"
STYLE_NAMES = ["smasher", "wall", "net", "controller", "tempo", "counter", "allrounder"]
TINY = ["--minutes", "1e-7", "--games", "16", "--horizon", "16", "--eval-games", "4", "--eval-steps", "150", "--guard-games", "8",
        "--guard-steps", "100", "--device", "cpu", "--threads", "2"]


def cli_run(argv):
    """cli.main(argv) -> (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def tiny_train(out, *extra):
    code, so, se = cli_run(["train", "--out", str(out), *TINY, *extra])
    assert code == 0, (so[-500:], se)
    return so


def skip_without_torch(name):
    if not HAVE_TORCH:
        print(f"skip {name}: torch is not installed")
    return not HAVE_TORCH


def test_style_reward_files_exist_and_load():
    assert sorted(p.stem for p in STYLES.glob("*.py")) == sorted(STYLE_NAMES)
    for name in STYLE_NAMES:
        fn = rewards.load_reward_file(STYLES / f"{name}.py")
        src = (STYLES / f"{name}.py").read_text()
        b = re.search(r'^BUILD = "([\d,]+)"', src, re.M).group(1)
        assert builds.parse(b)  # a valid build
    print("7 style reward files load")


def test_style_rewards_pay_per_rally_below_one_point():
    """T4: bot-vs-bot and random play, 64 games x 600 steps, each style's build: no rally pays any player a point."""
    for name in STYLE_NAMES:
        fn = rewards.load_reward_file(STYLES / f"{name}.py")
        b = builds.parse(re.search(r'^BUILD = "([\d,]+)"', (STYLES / f"{name}.py").read_text(), re.M).group(1))
        for pol in ("bot", "random"):
            g = rewards.shaping_guard(fn, b, None, policy=pol, n=64, steps=600, seed=3)
            assert g["rallies"] > 10, (name, pol, g["rallies"])
            assert g["max"] < rewards.SHAPING_LIMIT, (name, pol, g["max"])
            if pol == "bot":
                print(f"  {name:10s} {pol}: {g['rallies']} rallies, shaping per rally mean {g['mean']:.3f} max {g['max']:.3f}")
    d = rewards.shaping_guard(None, builds.ALL6, None, policy="bot", n=64, steps=600)  # and the default reward (its un-annealed 0.1 per crossing)
    assert d["mean"] < rewards.SHAPING_LIMIT and d["p95"] < rewards.SHAPING_LIMIT, (d["mean"], d["p95"])


def test_style_rewards_respond_to_the_style():
    """Each reward is not constant: some rollout pays it something, and the shapes differ between styles."""
    means = {}
    for name in STYLE_NAMES:
        fn = rewards.load_reward_file(STYLES / f"{name}.py")
        g = rewards.shaping_guard(fn, builds.ALL6, None, policy="bot", n=32, steps=400, seed=1)
        means[name] = g["mean"]
        assert g["max"] > 0, name
    assert len({round(v, 6) for v in means.values()}) > 3, means


def fake_hit(speed, angle, height, land, contact_x=-3.0, extended=False, disp=3.0, shot_no=4, player=0, z=0.0):
    """A StepEvents with one hit by `player` in game 0 (everything else empty), to ask a reward what it thinks of that one shot."""
    from oso_arena import events as E

    b = E.empty_buffers(1)
    b["hit"][0, 0], b["hit_player"][0, 0], b["hit_shot"][0, 0] = True, player, shot_no
    b["hit_speed"][0, 0], b["hit_angle"][0, 0], b["hit_disp"][0, 0], b["hit_ext"][0, 0] = speed, angle, disp, extended
    b["hit_contact"][0, 0] = [contact_x, height, z]
    b["hit_land"][0, 0] = land
    return E.freeze(b, np.array([[[-3.8, 0.0], [3.8, 0.0]]]), np.full((1, 2, 4), 6), np.array([0]), np.array([shot_no]), np.array([0]))


def pay(name, **kw):
    fn = rewards.load_reward_file(STYLES / f"{name}.py")
    return float(fn(fake_hit(**kw))[0, 0]) - float(fn(fake_hit(**{**kw, "land": [-3.0, 0.0]}))[0, 0])  # minus what a shot landing on the own side pays


def test_style_rewards_prefer_their_own_shots():
    smash = dict(speed=80.0, angle=-25.0, height=2.6, land=[4.5, 2.0], contact_x=-3.5)
    clear = dict(speed=28.0, angle=40.0, height=1.0, land=[6.4, 0.0], contact_x=-6.0)
    net_shot = dict(speed=10.0, angle=8.0, height=1.1, land=[0.5, 0.3], contact_x=-0.8)
    drive = dict(speed=38.0, angle=3.0, height=1.3, land=[5.0, 1.0], contact_x=-3.0)
    far = dict(speed=40.0, angle=10.0, height=1.5, land=[6.2, 2.4], contact_x=-3.0, disp=7.0)
    assert pay("smasher", **smash) > 4 * max(pay("smasher", **clear), pay("smasher", **net_shot))
    assert pay("wall", **clear) > 3 * pay("wall", **net_shot) and pay("wall", **clear) > pay("wall", **smash)
    assert pay("net", **net_shot) > 3 * max(pay("net", **clear), pay("net", **drive))
    assert pay("controller", **far) > 2 * pay("controller", **dict(drive, disp=0.5, land=[3.0, 0.0]))
    assert pay("tempo", **drive) > 3 * max(pay("tempo", **clear), pay("tempo", **smash))
    assert pay("counter", **clear) > 0.05 and pay("counter", **smash) > 0.05 and pay("counter", **net_shot) < 0.02
    assert pay("allrounder", **far) == 0.0  # the all-rounder pays at the crossing, not at the hit
    for name in STYLE_NAMES:  # never the serve, never a shot landing out or on the own side, never above W
        fn = rewards.load_reward_file(STYLES / f"{name}.py")
        assert fn(fake_hit(shot_no=1, **smash))[0, 0] <= 0.01
        assert fn(fake_hit(**{**smash, "land": [9.0, 0.0]}))[0, 0] <= 0.01 and fn(fake_hit(**{**smash, "land": [-4.0, 0.0]}))[0, 0] <= 0.01
        assert fn(fake_hit(**smash))[0, 0] <= 0.12 + 0.01


def test_shaping_guard_flags_a_greedy_reward_and_the_trainer_warns():
    def greedy(ev):  # pays 0.4 for every hit of the rally: a cooperative long rally pays far more than a point
        r = np.zeros((ev.n_games, 2))
        h = ev.hits
        for k in range(h.mask.shape[1]):
            idx = np.nonzero(h.mask[:, k])[0]
            r[idx, h.player[idx, k]] += 0.4
        return r
    g = rewards.shaping_guard(greedy, builds.ALL6, None, policy="bot", n=32, steps=500, seed=1)
    assert g["max"] > 1.0, g["max"]
    if HAVE_TORCH:
        from oso_arena import train

        args = train.parse_args(["--guard-games", "32", "--guard-steps", "500", "--seed", "1"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            train.run_guard(greedy, builds.ALL6, None, args)
        assert "WARNING" in buf.getvalue() and "points per rally" in buf.getvalue()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            train.run_guard(None, builds.ALL6, None, args)
        assert "WARNING" not in buf.getvalue()  # the default reward is fine


def test_reward_file_loading_and_output_checks():
    d = Path(tempfile.mkdtemp())
    (d / "noreward.py").write_text("x = 1\n")
    (d / "broken.py").write_text("raise RuntimeError('boom')\n")
    (d / "wrongshape.py").write_text("import numpy as np\ndef reward(ev):\n    return np.zeros(3)\n")
    (d / "nan.py").write_text("import numpy as np\ndef reward(ev):\n    return np.full((ev.n_games, 2), np.nan)\n")
    for name, text in (("noreward", "reward(ev)"), ("broken", "boom"), ("missing", "not found")):
        try:
            rewards.load_reward_file(d / f"{name}.py")
            raise AssertionError(name)
        except ValueError as e:
            assert text in str(e), (name, str(e))
    ev = sim.BadSim(3, 0).events()
    for name, text in (("wrongshape", "shape"), ("nan", "NaN")):
        try:
            rewards.total_reward(np.zeros((3, 2)), ev, rewards.load_reward_file(d / f"{name}.py"))
            raise AssertionError(name)
        except ValueError as e:
            assert text in str(e), (name, str(e))
    assert rewards.total_reward(np.ones((3, 2)), ev, lambda e: np.full((3, 2), 0.5)).tolist() == [[1.5, 1.5]] * 3
    assert not ev.hits.mask.flags.writeable


def test_random_builds_are_valid_and_cover_the_space():
    rng = np.random.default_rng(0)
    b = builds.random_builds(rng, 4000)
    assert b.shape == (4000, 4) and (b.sum(1) == 24).all() and b.min() >= 1 and b.max() <= 10
    v = builds.all_valid()
    assert len(v) == len({tuple(x) for x in v}) and len({tuple(x) for x in b}) > 0.5 * len(v)
    for row in b[:50]:
        assert not builds.validate(dict(zip(builds.STATS, map(int, row))))


def test_random_opponent_builds_are_redrawn_at_reset_and_the_learner_keeps_its_build():
    if skip_without_torch("redraw"):
        return
    from oso_arena import train

    n = 32
    rng = np.random.default_rng(0)
    L = builds.to_array({"power": 9, "speed": 6, "reach": 5, "accuracy": 4})
    lslot = rng.integers(0, 2, n)
    opp = builds.random_builds(rng, n)
    game = sim.BadSim(n, 0)
    game.set_builds(slice(None), train._pair_builds(n, lslot, L, opp))
    obs = game._obs()
    before = game.builds.copy()
    idx = np.array([1, 5, 6, 20])
    train.redraw_opponents(game, idx, lslot, L, opp, rng, obs)
    for g in range(n):
        assert (game.builds[g, lslot[g]] == L).all()  # the learner keeps its build everywhere
        assert (game.builds[g, 1 - lslot[g]] == opp[g]).all()
    changed = [g for g in range(n) if not (game.builds[g] == before[g]).all()]
    assert set(changed) <= set(idx.tolist()) and len(changed) >= 3  # only the restarted games got a new opponent
    assert (game.builds.sum(-1) == 24).all()
    assert np.array_equal(obs[..., sim.OBS_BASE:], game._build_obs)  # the observation shows the new builds


def test_resume_keeps_the_reward_and_opponent_builds_of_the_run():
    if skip_without_torch("resume reward"):
        return
    d = Path(tempfile.mkdtemp())
    out = d / "run"
    rf = d / "my_reward.py"
    rf.write_text("import numpy as np\ndef reward(ev):\n    return np.zeros((ev.n_games, 2))\n")
    tiny_train(out, "--build", "6,6,6,6", "--reward", str(rf), "--opp-builds", "3,8,8,5")
    so = tiny_train(out, "--resume", "--minutes", "0.02")  # neither flag given
    assert f"reward: {rf}" in so and "vs build" in so and '"power":3' in so, so[:400]
    run = json.loads((out / "run.json").read_text())
    assert run["reward"] == str(rf) and run["opp_builds"] == "3,8,8,5"
    so = tiny_train(out, "--resume", "--minutes", "0.03", "--opp-builds", "random")  # explicit and different: warns, uses it
    assert "warning: --opp-builds random differs" in so and json.loads((out / "run.json").read_text())["opp_builds"] == "random"
    assert json.loads((out / "run.json").read_text())["reward"] == str(rf)


def test_existing_run_is_not_overwritten_without_resume():
    if skip_without_torch("overwrite"):
        return
    out = Path(tempfile.mkdtemp()) / "run"
    tiny_train(out)
    before = (out / "latest.safetensors").read_bytes()
    code, _, err = cli_run(["train", "--out", str(out), *TINY])
    assert code == 1 and "--resume" in err and "new --out" in err
    assert (out / "latest.safetensors").read_bytes() == before


def test_logs_are_strict_json_without_nan():
    if skip_without_torch("strict json"):
        return
    from oso_arena import train

    assert json.loads(train.jdump({"v0": float("nan"), "a": [float("inf"), 1.5]})) == {"v0": None, "a": [None, 1.5]}
    out = Path(tempfile.mkdtemp()) / "run"
    tiny_train(out)
    for f in ("train_log.jsonl", "ckpt_eval.jsonl"):
        for line in (out / f).read_text().splitlines():
            json.loads(line, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(f"{f} has {c}")))


def test_train_help_works_without_torch():
    code = ("import sys, importlib.abc\n"
            "class Block(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'torch' or name.startswith('torch.'): raise ImportError('blocked')\n"
            "sys.meta_path.insert(0, Block())\n"
            "from oso_arena import cli\n"
            "sys.exit(cli.main(['train', '--help']))\n")
    import subprocess

    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(KIT)})
    assert r.returncode == 0 and "--build" in r.stdout and "PyTorch" not in r.stderr, (r.stdout[-200:], r.stderr[-400:])


def test_trainer_rejects_bad_flags():
    for argv, text in ((["--build", "5,5,5"], "4 values"), (["--build", "10,10,10,10"], "sum to 24"), (["--build", "0,8,8,8"], "1-10"),
                       (["--opp-builds", "banana"], "four comma-separated"), (["--reward", "/nonexistent/r.py"], "not found")):
        if not HAVE_TORCH:
            continue
        code, _, err = cli_run(["train", "--out", str(Path(tempfile.mkdtemp()) / "r"), *TINY, *argv])
        assert code == 1 and text in err, (argv, code, err)


def test_init_base_without_the_data_file_says_so():
    """T7."""
    if not HAVE_TORCH:
        print("skip: torch is not installed")
        return
    out = Path(tempfile.mkdtemp()) / "run"
    if (basemodel.DATA / "base_40min_full.safetensors").exists():
        print("skip: the base model is bundled")
        return
    code, so, err = cli_run(["train", "--init", "base", "--out", str(out), *TINY])
    assert code == 1 and "the base model is not bundled in this version of the kit" in err, (code, err)
    assert not out.exists()  # it failed before writing anything
    code, _, err = cli_run(["eval", "x.safetensors", "--vs", "base"])
    assert code == 1


def test_train_one_iteration_checkpoints_and_export_check_round_trip():
    """K3 + K4 + T5: a fresh 1-iteration run (no base model) -> safetensors checkpoints -> export without --build -> check."""
    if skip_without_torch("round trip"):
        return
    d = Path(tempfile.mkdtemp())
    out = d / "run"
    so = tiny_train(out, "--build", "9,6,5,4", "--opp-builds", "random")
    for f in ("best.safetensors", "latest.safetensors", "latest.opt.pt", "final.safetensors", "run.json", "train_log.jsonl", "ckpt_eval.jsonl"):
        assert (out / f).is_file(), f
    from safetensors import safe_open

    with safe_open(str(out / "best.safetensors"), framework="np") as f:
        head, keys = f.metadata(), set(f.keys())
    assert json.loads(head["build"]) == {"power": 9, "speed": 6, "reach": 5, "accuracy": 4} and head["format"] == "oso-arena-train/1"
    assert "elapsed" in head and json.loads(head["schedule"])["lr"] > 0
    assert {"log_std", "actor.0.weight", "actor.4.bias", "critic.0.weight", "critic.4.bias"} <= keys
    m = d / "m.safetensors"
    code, so, se = cli_run(["export", str(out / "best"), "-o", str(m), "--name", "Round Trip"])  # no --build: from the run
    assert code == 0, (so, se)
    r = check.check_file(m)
    assert r.ok, r.reasons
    assert r.meta["build"] == {"power": 9, "speed": 6, "reach": 5, "accuracy": 4} and r.meta["name"] == "Round Trip"
    code, so, se = cli_run(["check", str(m)])
    assert code == 0 and "ok:" in so
    assert (np.abs(policy.NumpyPolicy.load(m).act(np.zeros((2, 37)))) <= 1).all()
    # --build overrides the run's build; a bad one is rejected
    m2 = d / "m2.safetensors"
    assert cli_run(["export", str(out / "best.safetensors"), "-o", str(m2), "--name", "B", "--build", "6,6,6,6"])[0] == 0
    assert check.check_file(m2).meta["build"] == builds.ALL6
    assert cli_run(["export", str(out / "best"), "-o", str(d / "m3.safetensors"), "--name", "B", "--build", "9,9,9,9"])[0] == 1


def test_resume_continues_the_run_and_keeps_its_build():
    if skip_without_torch("resume"):
        return
    out = Path(tempfile.mkdtemp()) / "run"
    tiny_train(out, "--build", "3,8,8,5", "--opp-builds", "6,6,6,6", "--seed", "4")
    n1 = len((out / "train_log.jsonl").read_text().splitlines())
    so = tiny_train(out, "--resume", "--minutes", "0.02", "--opp-builds", "6,6,6,6")  # no --build: the run's
    assert "resumed at" in so and '"speed":8' in so
    assert len((out / "train_log.jsonl").read_text().splitlines()) > n1
    code, _, err = cli_run(["train", "--out", str(out), *TINY, "--resume", "--build", "9,6,5,4"])
    assert code == 1 and "differs" in err
    # a fixed opponent build equal to the learner's: both slots learn; just make sure it runs with a snapshot-free pool
    from oso_arena import train
    assert train.read_state(out / "latest.safetensors")[1]["format"] == train.STATE_FORMAT


def test_init_from_a_file_and_a_submission():
    if skip_without_torch("init"):
        return
    d = Path(tempfile.mkdtemp())
    tiny_train(d / "a", "--build", "6,6,6,6")
    tiny_train(d / "b", "--init", str(d / "a" / "best.safetensors"), "--init-elapsed", "0.5")  # a full training state
    from oso_arena import train

    assert cli_run(["export", str(d / "a" / "best"), "-o", str(d / "sub.safetensors"), "--name", "S"])[0] == 0
    so = tiny_train(d / "c", "--init", str(d / "sub.safetensors"))  # a submission file: the actor only
    assert "initialised the actor" in so
    sd, _ = train.read_state(d / "b" / "best.safetensors")
    a, _ = train.read_state(d / "a" / "best.safetensors")
    assert set(sd) == set(a)
    code, _, err = cli_run(["train", "--init", str(d / "nothere.safetensors"), "--out", str(d / "e"), *TINY])
    assert code == 1 and "not found" in err
    code, _, err = cli_run(["train", "--init", str(d / "x.pt"), "--out", str(d / "e"), *TINY])
    assert code == 1


def test_every_style_reward_runs_one_training_iteration():
    """T3: each file in skills/oso-arena-coach/rewards trains one tiny iteration with its build, with the guard on."""
    if skip_without_torch("style rewards"):
        return
    d = Path(tempfile.mkdtemp())
    for name in STYLE_NAMES:
        src = (STYLES / f"{name}.py").read_text()
        b = re.search(r'^BUILD = "([\d,]+)"', src, re.M).group(1)
        so = tiny_train(d / name, "--reward", str(STYLES / f"{name}.py"), "--build", b)
        assert "shaping guard (bot)" in so and "WARNING" not in so, (name, so[:600])
        assert (d / name / "best.safetensors").is_file()
        rows = [json.loads(line) for line in (d / name / "train_log.jsonl").read_text().splitlines()]
        assert rows and rows[-1]["it"] >= 1
        assert json.loads((d / name / "run.json").read_text())["reward"].endswith(f"{name}.py")
    print("7 styles trained one iteration each")


def test_a_custom_reward_changes_what_is_trained():
    """The reward the trainer feeds PPO is terminal + the plug-in: with a constant bonus the logged value targets change (smoke via total_reward)."""
    s = sim.BadSim(4, 0)
    obs, rew, done, info = s.step(np.zeros((4, 2, 6)))
    ev = s.events()
    assert np.array_equal(rewards.total_reward(rew, ev, lambda e: np.ones((4, 2))), rew + 1)
    assert np.array_equal(rewards.total_reward(rew, ev, None, shaping=0.1, base_scale=1.0), rew + rewards.default_reward(ev))


def test_no_unsafe_loading_in_the_kit_sources():
    """K3: no weights_only=False and no pickle anywhere in the kit; every torch.load is weights_only=True."""
    for f in (KIT / "oso_arena").glob("*.py"):
        src = f.read_text()
        if f.name == "test_badsim.py":
            continue
        assert "weights_only=False" not in src, f.name
        assert "import pickle" not in src and "allow_pickle=True" not in src, f.name
        for m in re.finditer(r"torch\.load\(([^)]*)\)", src):
            assert "weights_only=True" in m.group(1), (f.name, m.group(0))


if __name__ == "__main__":
    run(globals())
