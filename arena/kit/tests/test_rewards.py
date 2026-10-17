"""Phase 14.5 K1/K2: the simulator records events and no longer pays shaping; `rewards.default_reward` reproduces main's per-step
rewards exactly, and the simulator's match logs are byte-identical to main's. tests/data/ holds values recorded from main (before the
shaping moved out of sim.py) with the scripts in this file's recording section."""
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np

from helpers import KIT, contract, run
from oso_arena import bot, export, log, match, rewards, sim

DATA = KIT / "tests" / "data"
POOL = np.array([[9, 6, 5, 4], [3, 8, 8, 5], [3, 6, 7, 8], [4, 6, 5, 9], [4, 9, 5, 6], [6, 7, 7, 4], [6, 6, 6, 6], [2, 10, 10, 2]])


def rollout(reward_of, n=8, steps=600):
    """The fixed rollout main's rewards were recorded on: mixed builds, bot in even games' slot 0, random actions elsewhere, and a mid-run
    schedule change (shaping 0.1 -> 0.05, base reward x1 -> x0.5). reward_of(sim, terminal_reward, shaping, base_scale) -> [n, 2]."""
    rng = np.random.default_rng(21)
    builds = POOL[rng.integers(0, 8, (n, 2))]
    s = sim.BadSim(n, 7, builds=builds)
    arng = np.random.default_rng(5)
    obs = s._obs()
    out, sched = [], (0.1, 1.0)
    for t in range(steps):
        if t == 300:
            sched = (0.05, 0.5)
        a = arng.uniform(-1, 1, (n, 2, 6))
        a[:, 0] = np.where((np.arange(n) % 2 == 0)[:, None], bot.bot_actions(obs[:, 0]), a[:, 0])
        a[:, 1, 2] = np.where(t % 3 == 0, 1.0, a[:, 1, 2])
        obs, rew, done, info = s.step(a)
        out.append(reward_of(s, rew, *sched))
    return np.array(out), s


def test_default_reward_equals_mains_per_step_rewards():
    """T1: terminal reward (sim) + default_reward(events) == the rewards main's sim.step returned, bit for bit."""
    main = np.load(DATA / "main_rewards.npz")["rewards"]
    got, s = rollout(lambda s, rew, sh, bs: rewards.total_reward(rew, s.events(), None, shaping=sh, base_scale=bs))
    assert got.shape == main.shape == (600, 8, 2)
    assert got.tobytes() == main.tobytes(), f"max abs difference {np.abs(got - main).max():.3g} at {np.argwhere(got != main)[:3].tolist()}"
    assert (main != 0).sum() > 4000 and s.stats["rallies"] == 109  # the rollout covers shaping, base and rally ends
    print(f"default reward == main's rewards on {main.size} values")


# test_sim_builds.fingerprint() (900 steps, obs, rewards, positions, shuttle; rewards = sim + default reward) as main's code at the start of
# phase 14.5 computed it (the value test_sim_builds.GOLDEN_14_2B now holds).
MAIN_FINGERPRINT = "7ab9390dbd54536996bbdcc14dd9375daaede8b9da8bb76d9ef220186533cbb1"


def test_golden_rollout_fingerprint_equals_mains():
    import test_sim_builds

    assert test_sim_builds.fingerprint()[0] == MAIN_FINGERPRINT


def test_sim_pays_only_win_loss_and_serve_fault():
    got, s = rollout(lambda s, rew, sh, bs: rew.copy())
    assert set(np.unique(got)) <= {-1.5, -1.0, 0.0, 1.0}, set(np.unique(got))
    assert sum(1 for x in got.ravel() if x == 1.0) == s.stats["rallies"] - s.stats["faults"][sim.LET]


def test_events_describe_the_step():
    n = 8
    s = sim.BadSim(n, 3, builds=POOL[np.random.default_rng(1).integers(0, 8, (n, 2))])
    rng = np.random.default_rng(2)
    obs = s._obs()
    hits = ends = lands = crosses = 0
    for t in range(500):
        a = rng.uniform(-1, 1, (n, 2, 6))
        a[..., 2] = np.where(rng.random((n, 2)) < 0.4, 1.0, -1.0)
        obs, rew, done, info = s.step(a)
        ev = s.events()
        assert (ev.rally_end.mask == info["rally_end"]).all()
        assert (ev.rally_end.shots[ev.rally_end.mask] == info["shots"][ev.rally_end.mask]).all()
        assert ev.hits.mask.shape == (n, sim.DECISION) and ev.hits.contact.shape == (n, sim.DECISION, 3)
        assert (ev.hits.player[ev.hits.mask] >= 0).all() and (ev.hits.player[~ev.hits.mask] == -1).all()
        assert (ev.hits.speed[ev.hits.mask] > 0).all() and (ev.hits.quality[ev.hits.mask] <= 1).all()
        assert (ev.landings.mask <= ev.rally_end.mask).all() and (ev.crossings.player[ev.crossings.mask] >= 0).all()
        assert ev.pos.shape == (n, 2, 2) and ev.builds.shape == (n, 2, 4) and (ev.builds == s.builds).all()
        for arr in (ev.pos, ev.hits.mask, ev.rally_end.reason, ev.landings.xz):
            assert not arr.flags.writeable
        hits += int(ev.hits.mask.sum()); ends += int(ev.rally_end.mask.sum()); lands += int(ev.landings.mask.sum())
        crosses += int(ev.crossings.mask.sum())
    assert hits == s.stats["hits"] and ends == s.stats["rallies"]  # no step in this rollout held two events of one kind per game
    assert lands > 0 and crosses > 0
    print(f"events: {hits} hits, {crosses} crossings, {lands} landings, {ends} rally ends")


def test_match_logs_are_byte_identical_to_main():
    """T2: a fixed seed gives exactly the bytes main's simulator gave (bot vs bot, and a fixed random model with a build vs bot)."""
    want = json.loads((DATA / "main_matchlogs.json").read_text())
    d = Path(tempfile.mkdtemp())
    rng = np.random.default_rng(3)
    arr = {k: (rng.normal(size=sh) * 0.2).astype(np.float32) for k, sh in contract.TENSORS.items()}
    export.export(arr, d / "m.safetensors", "Fixture", "", {"power": 9, "speed": 6, "reach": 5, "accuracy": 4})
    for name, (a, b, seed) in dict(botbot=("bot", "bot", 3), modelbot=(str(d / "m.safetensors"), "bot", 4)).items():
        lg = match.play(a, b, seed)
        lg.a_sha256 = lg.b_sha256 = "-"  # the model file's bytes (and so its hash) differ between safetensors writes
        assert lg.kit_version != "0.3.0"  # phase 14.5 bumped the kit version; main's logs say 0.3.0, the only intended difference
        lg.kit_version = "0.3.0"
        log.write(d / f"{name}.gz", lg)
        assert hashlib.sha256((d / f"{name}.gz").read_bytes()).hexdigest() == want[name], name
    print("match logs byte-identical to main")


if __name__ == "__main__":
    run(globals())
