"""Phase 18a Volante Twins: the style profile (validator shared with oso-rec through a test vector), build from profile, the tempo reward,
fidelity, export / check / train / eval with --style. Torch tests are skipped when torch is missing."""
import copy
import json
import tempfile
from collections import deque
from pathlib import Path

import numpy as np

from helpers import GOOD_BUILD, KIT, ROOT, random_actor, write_st
from oso_arena import builds, check, contract, events as E, export, rewards, style
from test_train import TINY, cli_run, skip_without_torch, tiny_train

DATA = KIT / "tests" / "data"
GOOD = {
    "schema": "volante-style/1", "created_at": "2026-10-17T18:00:00Z",
    "source": {"sessions": 3, "swings": 1240, "minutes": 52.4, "pod_fw": "0.1", "tool": "oso-rec 0.1.0"},
    "speed": {"p50_kmh": 58.0, "p90_kmh": 84.0, "cv": 0.27, "clipped_frac": 0.31},
    "tempo": {"median_gap_s": 1.9, "p25_gap_s": 1.4, "p75_gap_s": 2.6},
    "stroke_mix": None, "side_mix": None, "notes": "x"}


def prof(**sections):
    p = copy.deepcopy(GOOD)
    for k, v in sections.items():
        p[k] = {**p[k], **v} if isinstance(v, dict) and isinstance(p[k], dict) else v
    return p


# ---------------------------------------------------------------- numpy reference of `oso-rec profile` (STYLE_PROFILE.md section 2)
def reference_profile(path, perp):
    """Independent port of imu/profile.rs for a bare imu.bin: perp = the two sensor-frame gyro axes perpendicular to the shaft
    ((0, 2) identity mount, (0, 1) flat mount). Returns speeds (km/h per swing), clipped flags and the within-rally gaps."""
    b = Path(path).read_bytes()
    assert b[:4] == b"OSOI"
    a = np.frombuffer(b[16:], np.dtype([("seq", "<u2"), ("t", "<u4"), ("v", "<i2", (6,))]))
    seq, t = a["seq"].astype(np.int64), a["t"].astype(np.int64)
    ds = np.diff(seq) % 65536
    dt = np.diff(t) % 2**32
    S = np.concatenate([[seq[0]], seq[0] + np.cumsum(np.where(ds >= 32768, ds - 65536, ds))])
    T = np.concatenate([[t[0]], t[0] + np.cumsum(np.where(dt >= 2**31, dt - 2**32, dt))])
    o = np.argsort(S, kind="stable")
    S, T, v = S[o], T[o], a["v"][o]
    keep = np.concatenate([[True], np.diff(S) != 0])
    T, v = T[keep] / 1e6, v[keep]
    g = v[:, 3:].astype(float) / 16.4
    sat = ((v[:, 3:] == 32767) | (v[:, 3:] == -32768)).any(1)
    norm = np.linalg.norm(g, axis=1)
    win, sm, armed, last, onsets = deque(), 0.0, True, None, []
    for i, (ti, d) in enumerate(zip(T, norm)):
        win.append((ti, d))
        sm += d
        while ti - win[0][0] >= 0.020:
            sm -= win.popleft()[1]
        m = sm / len(win)
        if not armed:
            armed = m < 150 and (last is None or ti - last >= 0.8)
        elif m >= 300:
            armed, last = False, ti
            onsets.append(i)
    speeds, clipped = [], []
    for i in onsets:
        j = np.searchsorted(T, T[i] + 0.8, side="left")
        speeds.append(np.radians(np.hypot(g[i:j, perp[0]], g[i:j, perp[1]]).max()) * 0.53 * 3.6)
        clipped.append(bool(sat[i:j].any()))
    gaps = np.diff(T[onsets])
    return np.array(speeds), np.array(clipped), gaps[gaps <= 6.0], (T[-1] - T[0]) / 60


# ---------------------------------------------------------------- profile
def test_cross_language_vector():
    """The tiny imu.bin fixture -> the committed profile JSON: oso-rec's output (crates/oso-replay/src/imu/profile.rs asserts the bytes),
    this module's validator and an independent numpy port of the algorithm all agree."""
    text = (DATA / "twins_vector.profile.json").read_text()
    p = style.parse_profile(text)  # the python validator accepts the Rust output
    assert len(style.compact(p).encode()) <= style.MAX_BYTES
    sp, cl, gaps, minutes = reference_profile(DATA / "twins_vector.imu.bin", (0, 2))
    assert p["source"]["swings"] == len(sp) == 8 and abs(p["source"]["minutes"] - minutes) < 0.001
    q = lambda x: float(f"{x:.3g}")  # noqa: E731
    assert p["speed"] == dict(p50_kmh=q(np.percentile(sp, 50)), p90_kmh=q(np.percentile(sp, 90)), cv=q(sp.std() / sp.mean()), clipped_frac=q(cl.mean()))
    assert p["tempo"] == dict(median_gap_s=q(np.percentile(gaps, 50)), p25_gap_s=q(np.percentile(gaps, 25)), p75_gap_s=q(np.percentile(gaps, 75)))
    assert p["speed"]["clipped_frac"] == 0.125 and len(gaps) == 6, "one saturated swing; the 7.9 s gap splits the rally"
    assert p["stroke_mix"] is None and p["side_mix"] is None and set(p) == set(style.TOP_KEYS)
    schema = json.loads((ROOT / "arena/contract/style.schema.json").read_text())
    assert set(schema["required"]) == set(style.REQUIRED) and set(schema["properties"]) == set(style.TOP_KEYS)
    assert schema["properties"]["source"]["properties"]["swings"]["maximum"] == style.MAX_COUNT
    assert schema["properties"]["tempo"]["properties"]["median_gap_s"]["minimum"] == style.GAP_MIN_S


def test_oso_rec_profile_cli_matches_the_vector():
    """`oso-rec profile` (the built binary, when there is one) on the fixture: the committed JSON apart from created_at."""
    import subprocess

    exe = next((ROOT / "target" / m / "oso-rec" for m in ("release", "debug") if (ROOT / "target" / m / "oso-rec").exists()), None)
    if exe is None:
        print("skip: oso-rec is not built")
        return
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "my_style.json"
        r = subprocess.run([str(exe), "profile", str(DATA / "twins_vector.imu.bin"), "--mount", "identity", "-o", str(out)], capture_output=True, text=True)
        assert r.returncode == 0 and "8 swings" in r.stdout, (r.stdout, r.stderr)
        got, want = style.load_profile(out), json.loads((DATA / "twins_vector.profile.json").read_text())
        got.pop("created_at"), want.pop("created_at")
        assert got == want
        for bad in ([], [str(DATA / "nope.bin")]):
            r = subprocess.run([str(exe), "profile", *bad, "-o", str(Path(d) / "z.json")], capture_output=True, text=True)
            assert r.returncode != 0 and not (Path(d) / "z.json").exists()


def test_real_recording_matches_the_numpy_reference():
    import os

    for d in (os.environ.get("OSO_HW_DIR"), ROOT / "out/hw"):
        f = Path(d or "/nonexistent") / "smashes_live1.bin"
        if f.exists():
            break
    else:
        print("skip: out/hw/smashes_live1.bin not found")
        return
    sp, cl, gaps, _ = reference_profile(f, (0, 1))  # flat mount
    top = np.sort(sp)[::-1]
    assert abs(top[0] - 94.6) < 0.05 * 94.6 and abs(top[:41].mean() - 68.9) < 0.05 * 68.9  # the 2026-10-17 analysis: max ~95, mean ~69 of 41
    # the numbers pinned in the Rust test (cross_language_vector_and_the_real_recording)
    assert abs(np.percentile(sp, 50) - 62.57) < 0.01 and abs(np.percentile(sp, 90) - 82.01) < 0.01 and abs(np.median(gaps) - 2.656) < 0.001


def test_validate_edge_cases():
    assert style.validate_profile(GOOD) == []
    assert style.validate_profile({**GOOD, "side_mix": {"fh": 0.7, "bh": 0.3}, "stroke_mix": {"clear": 0.5, "smash": 0.5}}) == []
    bad = [
        {**GOOD, "schema": "volante-style/2"}, {**GOOD, "name": "Jesus"}, {**GOOD, "created_at": "2026-10-17 18:00"},
        prof(source={"sessions": 0}), prof(source={"swings": 10**7 + 1}), prof(source={"swings": 5.0}), prof(source={"swings": True}),
        prof(source={"minutes": 0}), prof(source={"pod_fw": "a/b"}), prof(source={"tool": ""}),
        prof(speed={"p90_kmh": 401}), prof(speed={"p50_kmh": 90}), prof(speed={"cv": 2.5}), prof(speed={"clipped_frac": 1.2}),
        prof(speed={"cv": float("nan")}), prof(speed={"cv": "0.3"}),
        prof(tempo={"median_gap_s": 0.1}), prof(tempo={"p75_gap_s": 10.5}), prof(tempo={"p25_gap_s": 2.0}),
        {**GOOD, "side_mix": {"fh": 0.7, "bh": 0.2}}, {**GOOD, "side_mix": {"fh": 1.0}}, {**GOOD, "stroke_mix": {"clear": -0.1, "smash": 1.1}},
        {**GOOD, "speed": {"p50_kmh": 1}}, {k: v for k, v in GOOD.items() if k != "tempo"}, {**GOOD, "notes": "x" * 281}, [],
    ]
    for p in bad:
        assert style.validate_profile(p), p
    # text level: size, syntax, NaN
    for text in ("x" * (style.MAX_BYTES + 1), "{", json.dumps(GOOD).replace("0.27", "NaN"), json.dumps({**GOOD, "notes": "x" * 279}) + " " * 9000):
        try:
            style.parse_profile(text)
            raise AssertionError(text[:40])
        except ValueError:
            pass
    assert style.parse_profile(json.dumps(GOOD)) == GOOD and style.compact(GOOD) == json.dumps(GOOD, separators=(",", ":"))
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.json"
        f.write_text(json.dumps(GOOD))
        assert style.load_profile(f) == GOOD
        f.write_text(json.dumps({**GOOD, "pilot": "x"}))
        try:
            style.load_profile(f)
            raise AssertionError("unknown key accepted")
        except ValueError:
            pass


# ---------------------------------------------------------------- build from profile
def test_build_mapping_vectors_validity_and_monotonicity():
    t = [  # (p90, cv, median gap) -> (power, speed, reach, accuracy); the test_twins vector table, also in STYLE_PROFILE.md
        ((84, 0.27, 1.9), (6, 6, 5, 7)), ((80, 0.35, 2.0), (6, 6, 6, 6)),
        ((40, 0.6, 3.0), (5, 5, 10, 4)),  # all minimums: reach takes 21 -> 10, the surplus of 11 is spread
        ((120, 0.1, 1.0), (7, 8, 1, 8)),  # all maximums: reach 1, 6 points too many are spread
        ((60, 0.5, 2.5), (5, 4, 10, 5)), ((100, 0.2, 1.2), (8, 7, 1, 8)), ((120, 0.6, 3.0), (10, 2, 10, 2)), ((40, 0.1, 1.0), (1, 10, 3, 10)),
        ((60, 0.3, 1.5), (3, 8, 7, 6)), ((110, 0.45, 2.8), (9, 2, 9, 4)),
    ]
    for (p90, cv, gap), want in t:
        got = style.build_from_profile(prof(speed={"p90_kmh": p90, "p50_kmh": 0, "cv": cv}, tempo={"median_gap_s": gap, "p25_gap_s": 0.2, "p75_gap_s": 10}))
        assert tuple(got[k] for k in ("power", "speed", "reach", "accuracy")) == want, (p90, cv, gap, got)
        assert builds.validate(got) == []
    # fixed rows are exact where the remainder is in range
    assert style.build_from_profile(GOOD) == dict(power=6, speed=6, reach=5, accuracy=7)
    # always valid over a grid that includes every clamp and rebalance path
    n = 0
    for p90 in (0, 30, 40, 55, 80, 100, 120, 200, 400):
        for cv in (0, 0.1, 0.3, 0.6, 1, 2):
            for gap in (0.2, 1.0, 1.5, 2.0, 3.0, 6.0, 10.0):
                p = prof(speed={"p90_kmh": p90, "p50_kmh": 0, "cv": cv}, tempo={"median_gap_s": gap, "p25_gap_s": 0.2, "p75_gap_s": 10})
                b = style.build_from_profile(p)
                assert builds.validate(b) == [], (p90, cv, gap, b)
                assert style.build_from_profile(p) == b  # deterministic
                n += 1
    # monotone in each input (all others at the middle; the sweeps stay where the remainder is in 1-10 or are rebalanced evenly)
    mid = prof(speed={"p90_kmh": 80, "p50_kmh": 30, "cv": 0.35}, tempo={"median_gap_s": 2.0, "p25_gap_s": 0.2, "p75_gap_s": 10})
    for key, sect, field, stat, vals in (("p90", "speed", "p90_kmh", "power", np.linspace(40, 120, 41)), ("cv", "speed", "cv", "accuracy", np.linspace(0.6, 0.1, 51)),
                                         ("gap", "tempo", "median_gap_s", "speed", np.linspace(3.0, 1.0, 41))):
        def at(v):
            q = copy.deepcopy(mid)
            q[sect][field] = float(v)
            return style.build_from_profile(q)[stat]

        seen = [at(v) for v in vals]
        assert seen == sorted(seen) and seen[-1] - seen[0] >= 6, (key, seen)
    try:
        style.build_from_profile({**GOOD, "schema": "x"})
        raise AssertionError
    except ValueError:
        pass
    print(f"  build mapping valid on {n} grid points")


# ---------------------------------------------------------------- tempo reward
def hits_ev(n, steps_hits):
    """One StepEvents: steps_hits = [(game, sub-step k, player, shot_no)]; no other events."""
    b = E.empty_buffers(n)
    for g, k, p, s in steps_hits:
        b["hit"][g, k], b["hit_player"][g, k], b["hit_shot"][g, k] = True, p, s
    return E.freeze(b, np.zeros((n, 2, 2)), np.full((n, 2, 4), 6), np.zeros(n, int), np.ones(n, int), np.zeros(n, int))


def test_tempo_reward_is_a_bounded_gaussian_on_the_own_gap():
    p = prof(tempo={"median_gap_s": 1.2, "p25_gap_s": 0.9, "p75_gap_s": 1.5})  # sigma = 0.6/1.349 = 0.445 s
    r = style.TempoReward(p, 0.05)
    # player 0 serves (step 0), hits again 1.2 s later (36 decision steps): on the median -> the full weight; the first hit pays nothing
    assert r(hits_ev(2, [(0, 0, 0, 1)])).sum() == 0
    for _ in range(35):
        assert r(hits_ev(2, [])).sum() == 0
    got = r(hits_ev(2, [(0, 0, 0, 3)]))
    assert abs(got[0, 0] - 0.05) < 1e-12 and got[0, 1] == 0 and got[1].sum() == 0
    # 2 s later: far from 1.2 s -> a small, positive payment
    for _ in range(59):
        r(hits_ev(2, []))
    far = r(hits_ev(2, [(0, 0, 0, 5)]))[0, 0]
    assert 0 < far < 0.05 * np.exp(-0.5 * (0.8 / 0.445) ** 2) * 1.01
    # the opponent's first hit (shot 2) pays nothing: it has no previous hit of its own; the shot cap: shot 11 pays nothing
    assert r(hits_ev(2, [(0, 0, 1, 2)])).sum() == 0
    for _ in range(36):
        r(hits_ev(2, []))
    assert r(hits_ev(2, [(0, 0, 0, 11)])).sum() == 0
    # a rally end forgets the hits; a serve starts over
    ev = hits_ev(2, [])
    r2 = style.TempoReward(p, 0.05)
    r2(hits_ev(2, [(0, 0, 0, 1)]))
    b = E.empty_buffers(2)
    b["end"][0] = True
    r2(E.freeze(b, np.zeros((2, 2, 2)), np.full((2, 2, 4), 6), np.zeros(2, int), np.ones(2, int), np.zeros(2, int)))
    assert r2(hits_ev(2, [(0, 0, 0, 3)])).sum() == 0 and ev.n_games == 2
    # weights: 0 pays nothing; above 0.1 or negative is refused
    z = style.TempoReward(p, 0)
    z(hits_ev(1, [(0, 0, 0, 1)]))
    assert z(hits_ev(1, [(0, 1, 0, 3)])).sum() == 0
    for w in (-0.01, 0.11, float("nan"), None):
        try:
            style.TempoReward(p, w)
            raise AssertionError(w)
        except ValueError:
            pass


def test_tempo_reward_stays_inside_the_shaping_budget():
    """Real rollouts (the scripted bot, random play): the default reward plus the tempo term pays < 1 point per rally and player (mean and p95), at the
    default weight and at the cap, and the tempo term alone never pays more than 5 W."""
    p = prof(tempo={"median_gap_s": 1.0, "p25_gap_s": 0.8, "p75_gap_s": 1.3})
    for w in (style.TEMPO_WEIGHT, style.TEMPO_MAX_WEIGHT):
        for pol in ("bot", "random"):
            t = style.TempoReward(p, w)
            alone = rewards.shaping_guard(t.__call__, builds.ALL6, None, policy=pol, n=48, steps=500, seed=5)
            assert alone["max"] <= 5 * w + 1e-9, (w, pol, alone["max"])
            both = rewards.shaping_guard(None, builds.ALL6, None, policy=pol, n=48, steps=500, seed=5, extra=style.TempoReward(p, w))
            base = rewards.shaping_guard(None, builds.ALL6, None, policy=pol, n=48, steps=500, seed=5)
            assert both["rallies"] > 5 and both["mean"] < rewards.SHAPING_LIMIT and both["p95"] < rewards.SHAPING_LIMIT, (w, pol, both["mean"], both["p95"])
            d = both["per_rally"] - base["per_rally"]  # same rollout, so rally by rally: the tempo term adds between 0 and 5 W
            assert d.min() >= -1e-9 and d.max() <= 5 * w + 1e-9, (w, pol, d.min(), d.max())
            if pol == "bot":
                print(f"  weight {w}: default {base['mean']:.3f} -> with tempo {both['mean']:.3f} (max {both['max']:.3f}); tempo alone max {alone['max']:.3f}")
    # the default reward is untouched when no extra is given
    ev = hits_ev(2, [])
    assert np.array_equal(rewards.total_reward(np.zeros((2, 2)), ev), rewards.default_reward(ev))


# ---------------------------------------------------------------- fidelity
class FakeLog:
    def __init__(self, hits_by_rally):
        self.events = []
        for rally in hits_by_rally:
            self.events.append(dict(kind="start", t=rally[0][1] - 0.5))
            self.events += [dict(kind="hit", p=p, t=t) for p, t in rally]


def test_fidelity_ratio_and_build():
    p = prof(tempo={"median_gap_s": 1.6, "p25_gap_s": 1.2, "p75_gap_s": 2.0})
    want = style.build_from_profile(p)
    # A hits every 2.0 s in two rallies, B every 1 s; the rally boundary adds no gap
    rally1 = [(0, 0.0), (1, 1.0), (0, 2.0), (1, 2.0 + 1.0), (0, 4.0), (1, 4.0 + 1.0)]
    rally2 = [(0, 100.0), (1, 101.0), (0, 102.0)]
    log = FakeLog([rally1, rally2])
    assert style.hit_gaps(log, 0) == [2.0, 2.0, 2.0] and style.hit_gaps(log, 1) == [2.0, 2.0]
    r = style.fidelity(p, [log], want)
    assert r.build_ok and abs(r.tempo_ratio - 2.0 / 1.6) < 1e-12 and r.tempo_ok and r.fidelity_ok and r.gaps == 3 and r.mix_js is None
    assert "OK" in r.text() and r.to_dict()["fidelity_ok"] is True
    # B's side: 2.0 s too (its gaps are 2.0): ratio the same; playing as (log, 1)
    assert abs(style.fidelity(p, [(log, 1)], want).tempo_ratio - 1.25) < 1e-12
    # too slow (ratio 1.5), too fast (0.5), edges 0.75 / 1.33 pass
    slow = FakeLog([[(0, 0.0), (0, 2.4), (0, 4.8)]])
    assert not style.fidelity(p, [slow], want).tempo_ok and abs(style.fidelity(p, [slow], want).tempo_ratio - 1.5) < 1e-12
    assert not style.fidelity(p, [FakeLog([[(0, 0.0), (0, 0.8), (0, 1.6)]])], want).tempo_ok
    assert style.fidelity(p, [FakeLog([[(0, 0.0), (0, 1.21), (0, 2.42)]])], want).tempo_ok  # 0.756
    assert style.fidelity(p, [FakeLog([[(0, 0.0), (0, 2.1), (0, 4.2)]])], want).tempo_ok  # 1.31
    # a mismatched build fails, with a reason
    other = dict(want, power=want["power"] % 10 + 1, reach=want["reach"] - (want["power"] % 10 + 1 - want["power"]))
    r = style.fidelity(p, [log], other)
    if builds.validate(other) == []:
        assert not r.build_ok and not r.fidelity_ok and r.tempo_ok and any("build" in x for x in r.reasons)
    r = style.fidelity(p, [log], dict(builds.ALL6))
    assert (want == builds.ALL6) or (not r.build_ok and not r.fidelity_ok)
    # no hits: no ratio, not ok
    r = style.fidelity(p, [FakeLog([[(1, 0.0)]])], want)
    assert r.tempo_ratio is None and not r.tempo_ok and not r.fidelity_ok


def test_fidelity_from_a_real_match():
    from oso_arena import match

    p = prof()
    lg = match.play("bot", "bot", 3, best_of=1, target=11)
    gaps = style.hit_gaps(lg, 0) + style.hit_gaps(lg, 1)
    assert len(gaps) > 10 and min(gaps) >= 0.35 - 1e-6, (len(gaps), min(gaps))  # the laws: no hit within 0.35 s of the player's last
    r = style.fidelity(p, [lg], builds.ALL6)
    assert r.gaps == len(style.hit_gaps(lg, 0)) and r.tempo_ratio is not None
    print(f"  bot tempo: median gap {r.model_median_gap_s:.2f} s")


# ---------------------------------------------------------------- export / check
def style_file(d, p=GOOD, name="style.json"):
    f = Path(d) / name
    f.write_text(json.dumps(p))
    return f


def test_export_embeds_the_profile_forces_the_build_and_check_validates_it():
    from safetensors import safe_open

    want = style.build_from_profile(GOOD)
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "twin.safetensors"
        export.export(random_actor(), out, "Twin", "", None, style_profile=GOOD)
        r = check.check_file(out)
        assert r.ok, r.reasons
        assert r.meta["build"] == want and json.loads(r.meta["style_profile"]) == GOOD
        with safe_open(str(out), "numpy") as f:
            m = f.metadata()
        assert m["style_profile"] == style.compact(GOOD) and json.loads(m["build"]) == want and len(m["style_profile"]) < style.MAX_BYTES
        export.export(random_actor(), Path(d) / "same.safetensors", "Twin", "", want, style_profile=GOOD)  # the profile's own build is fine
        # a conflicting build is refused; an invalid profile is refused
        for kw in (dict(build=GOOD_BUILD, style_profile=GOOD), dict(build=None, style_profile=prof(speed={"cv": 9}))):
            try:
                export.export(random_actor(), Path(d) / "x.safetensors", "Twin", "", **kw)
                raise AssertionError(kw)
            except ValueError:
                pass
        assert not (Path(d) / "x.safetensors").exists()
        # check rejects a file whose style_profile is invalid or oversize, and ignores none at all
        base = {"contract": contract.CONTRACT, "name": "T", "description": "", "build": json.dumps(want, separators=(",", ":"))}
        ok = write_st(Path(d) / "a.safetensors", random_actor(), {**base, "style_profile": style.compact(GOOD)})
        assert check.check_file(ok).ok
        for label, sp in (("bad json", "{"), ("bad rule", json.dumps({**GOOD, "schema": "volante-style/9"})), ("oversize", json.dumps({**GOOD, "notes": "x" * 9000})),
                          ("personal key", json.dumps({**GOOD, "player": "P07"}))):
            f = write_st(Path(d) / "b.safetensors", random_actor(), {**base, "style_profile": sp})
            res = check.check_file(f)
            assert not res.ok and any(x.startswith("header style_profile:") for x in res.reasons), (label, res.reasons)
        assert check.check_file(write_st(Path(d) / "c.safetensors", random_actor(), base)).ok


def test_cli_export_style_and_eval_style():
    if skip_without_torch("cli export/eval --style"):
        return
    with tempfile.TemporaryDirectory() as d:
        sf = style_file(d)
        want = style.build_from_profile(GOOD)
        run = Path(d) / "run"
        # train --style: sets the build from the profile; a conflicting --build is refused; a bad weight is refused
        code, so, se = cli_run(["train", "--out", str(Path(d) / "r0"), *TINY, "--style", str(sf), "--build", "9,6,5,4"])
        assert code == 1 and "conflicts with --style" in se, (code, se)
        code, so, se = cli_run(["train", "--out", str(Path(d) / "r1"), *TINY, "--style", str(sf), "--style-weight", "0.5"])
        assert code == 1 and "--style-weight" in se, se
        code, so, se = cli_run(["train", "--out", str(Path(d) / "r2"), *TINY, "--style-weight", "0.05"])
        assert code == 1 and "needs --style" in se, se
        so = tiny_train(run, "--style", str(sf), "--style-weight", "0.05")
        assert json.dumps(want, separators=(",", ":")) in so and "shaping guard (bot)" in so
        meta = json.loads((run / "run.json").read_text())
        assert meta["build"] == want and meta["style"] == str(sf) and meta["style_weight"] == 0.05
        # the guard line shows a bounded per-rally payment
        import re

        for m in re.finditer(r"max ([\d.]+) \(limit", so):
            assert float(m.group(1)) < rewards.SHAPING_LIMIT
        # export --style embeds the profile and keeps the build; --build conflicting is refused
        out = Path(d) / "twin.safetensors"
        code, so, se = cli_run(["export", str(run / "latest"), "-o", str(out), "--name", "Twin", "--style", str(sf)])
        assert code == 0 and check.check_file(out).meta["build"] == want, (so, se)
        code, so, se = cli_run(["export", str(run / "latest"), "-o", str(Path(d) / "y.safetensors"), "--name", "Twin", "--style", str(sf), "--build", "9,6,5,4"])
        assert code == 1 and "conflicts" in se
        # eval --style on the exported twin (and on a random model) prints a report; the model vs the bot
        code, so, se = cli_run(["eval", str(out), "--games", "4", "--style"])
        assert code == 0 and "== Twins fidelity ==" in so and "tempo:" in so and "fidelity:" in so, (so[-600:], se)
        rnd = Path(d) / "rnd.safetensors"
        export.export(random_actor(3), rnd, "Rnd", "", None, style_profile=GOOD)
        code, so, se = cli_run(["eval", str(rnd), "--games", "4", "--style"])
        assert code == 0 and "build:  ok" in so and "tempo:" in so, so[-400:]
        # a file without a profile: a clear error
        plain = Path(d) / "plain.safetensors"
        export.export(random_actor(4), plain, "Plain", "", GOOD_BUILD)
        code, so, se = cli_run(["eval", str(plain), "--games", "2", "--style"])
        assert code == 1 and "no style_profile" in se
        print(so.split("== Twins fidelity ==")[-1].strip().splitlines()[0])


def test_training_with_style_keeps_the_reward_bounded():
    """One real iteration with the tempo term on: finite, and the per-step reward the trainer feeds PPO is within win/loss + shaping + 0.1."""
    if skip_without_torch("train --style reward bound"):
        return
    p = prof(tempo={"median_gap_s": 1.0, "p25_gap_s": 0.8, "p75_gap_s": 1.3})
    from oso_arena import sim

    t = style.TempoReward(p, style.TEMPO_WEIGHT)
    s = sim.BadSim(32, 7)
    rng = np.random.default_rng(7)
    obs = s._obs()
    worst = 0.0
    for _ in range(300):
        act = rng.uniform(-1, 1, (32, 2, sim.ACT_DIM))
        obs, rew, done, info = s.step(act)
        ev = s.events()
        x = t(ev)
        assert x.shape == (32, 2) and np.isfinite(x).all() and (x >= 0).all() and x.max() <= style.TEMPO_WEIGHT * 2 + 1e-12  # two hits can share a step
        worst = max(worst, float(x.max()))
        total = rewards.total_reward(rew, ev, None, t, shaping=0.1, base_scale=1.0)
        assert np.isfinite(total).all()
    print(f"  largest single-step tempo payment {worst:.4f}")
