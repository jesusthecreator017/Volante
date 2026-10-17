"""Contract tests: docs match code, builds, export -> check round trip, every bad submission, the pickle canary, NumpyPolicy vs torch."""
import json
import os
import pickle
import re
import tempfile
from pathlib import Path

import numpy as np

from helpers import GOOD_BUILD, KIT, ROOT, meta_for, random_actor, raw_safetensors, run, write_st
from oso_arena import CONTRACT, KIT_VERSION, builds, check, contract, export, sim
from oso_arena.policy import NumpyPolicy

DOC = (ROOT / "arena/contract/CONTRACT.md").read_text()


def table_rows(heading):
    """Rows (list of cell strings) of the first markdown table after the line starting with `heading`."""
    block, started = [], False
    for l in DOC.split(heading, 1)[1].splitlines():
        if l.strip().startswith("|"):
            started = True
            block.append([c.strip() for c in l.strip().strip("|").split("|")])
        elif started:
            break
    return block[2:]  # without the header and separator rows


# ---------------------------------------------------------------- A: docs == code
def test_doc_mapping_table_matches_code():
    rows = table_rows("## 3. Stat builds")
    pt = builds.param_table()
    assert {r[0].strip("`") for r in rows} == set(builds.PARAM_KEYS)
    for r in rows:
        key = r[0].strip("`")
        assert r[1] == builds.stat_of(key), key
        for col, s in ((2, 1), (3, 6), (4, 10)):
            assert float(r[col]) == pt[key][s], (key, s)


def test_doc_obs_table_matches_code():
    rows = table_rows("## 4. Observation")
    assert [int(r[0]) for r in rows] == list(range(sim.OBS_DIM)) == list(range(37))
    assert all(r[1] and r[2] and r[3] for r in rows)
    # the heading and the doc's network section agree with the code
    assert f"Observation ({sim.OBS_DIM} floats)" in DOC and f"[256, {contract.OBS_DIM}]" in DOC
    assert contract.OBS_DIM == sim.OBS_DIM and contract.ACT_DIM == sim.ACT_DIM


def test_doc_tensor_table_and_versions():
    rows = table_rows("Tensors: exactly these six")
    got = {r[0].strip("`"): tuple(int(x) for x in r[1].strip("[]").split(",")) for r in rows[:6]}
    assert got == contract.TENSORS
    assert f"`{CONTRACT}`" in DOC and CONTRACT == "oso-arena/1"
    assert f"{contract.MAX_BYTES:,}" in DOC and str(contract.NAME_MAX) in DOC and str(contract.DESC_MAX) in DOC
    pyproject = (KIT / "pyproject.toml").read_text()
    assert re.search(rf'^version = "{re.escape(KIT_VERSION)}"', pyproject, re.M)
    schema = json.loads((ROOT / "arena/contract/header.schema.json").read_text())
    p = schema["properties"]
    assert p["contract"]["const"] == CONTRACT and p["name"]["maxLength"] == contract.NAME_MAX
    assert p["description"]["maxLength"] == contract.DESC_MAX
    b = schema["$defs"]["build"]
    assert set(b["required"]) == set(builds.STATS) and b["properties"]["power"]["maximum"] == builds.MAX


# ---------------------------------------------------------------- builds
def test_build_mapping_at_6_is_the_13_2_constants():
    p = builds.params(builds.ALL6)
    assert p == dict(launch_hi=90.0, launch_lo=25.0, v_max=5.0, a_max=10.0, reach=1.5, reach_lunge=2.0, noise_mult=1.0)
    assert sim.max_launch_speed(np.array([0.5, 1.0, 1.8, 2.6, 3.0])).tolist() == np.interp([0.5, 1.0, 1.8, 2.6, 3.0], [1, 2.6], [25, 90]).tolist()


def test_build_mapping_endpoints_and_monotonic():
    pt = builds.param_table()
    ends = {"launch_hi": (60, 110), "launch_lo": (20, 30), "v_max": (4, 6), "a_max": (8, 12), "reach": (1.2, 1.8),
            "reach_lunge": (1.7, 2.3), "noise_mult": (1.4, 0.6)}
    for k, (lo, hi) in ends.items():
        assert pt[k][1] == lo and pt[k][10] == hi and pt[k][6] == builds.params(builds.ALL6)[k]
        d = np.diff(pt[k][1:])
        assert (d > 0).all() if hi > lo else (d < 0).all(), k
    # piecewise linear: s = 8 is 2/4 of the way from 6 to 10; s = 3 is 2/5 of the way from 1 to 6
    assert abs(pt["launch_hi"][8] - 100.0) < 1e-12 and abs(pt["noise_mult"][3] - 1.24) < 1e-12


def test_build_validate_and_parse():
    assert builds.validate(GOOD_BUILD) == [] and builds.validate(builds.ALL6) == []
    bad = [{**GOOD_BUILD, "power": 10}, {**GOOD_BUILD, "power": 8}, {**GOOD_BUILD, "power": 0, "speed": 15},
           {**GOOD_BUILD, "power": 11, "speed": 4}, {**GOOD_BUILD, "power": 9.0}, {**GOOD_BUILD, "power": "9"},
           {**GOOD_BUILD, "power": True, "speed": 14}, {"power": 6, "speed": 6, "reach": 6}, {**builds.ALL6, "extra": 0}, [6, 6, 6, 6], None]
    for b in bad:
        assert builds.validate(b), b
    assert builds.parse("9,6,5,4") == GOOD_BUILD
    for s in ("9,6,5", "a,b,c,d", "9,6,5,5", "0,8,8,8", ""):
        try:
            builds.parse(s)
        except ValueError:
            continue
        raise AssertionError(s)


# ---------------------------------------------------------------- C: export / check
def exported(tmp, name="Round Trip", seed=0, build=GOOD_BUILD, fname="m.safetensors"):
    p = Path(tmp) / fname
    export.export(random_actor(seed), p, name, "desc", build)
    return p


def test_export_check_round_trip():
    with tempfile.TemporaryDirectory() as d:
        p = exported(d)
        r = check.check_file(p)
        assert r.ok and r.reasons == [], r.reasons
        assert r.meta["name"] == "Round Trip" and r.meta["description"] == "desc" and r.meta["build"] == GOOD_BUILD
        assert len(r.meta["sha256"]) == 64
        import hashlib
        assert r.meta["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
        # exactly the 6 tensors, float32, and the header keys
        from safetensors import safe_open
        with safe_open(str(p), "numpy") as f:
            assert sorted(f.keys()) == sorted(contract.TENSORS) and f.metadata() == {
                "contract": CONTRACT, "name": "Round Trip", "description": "desc", "build": '{"power":9,"speed":6,"reach":5,"accuracy":4}'}
            assert all(f.get_tensor(k).dtype == np.float32 for k in f.keys())
        assert p.stat().st_size < contract.MAX_BYTES
        # export drops the critic / log_std of a full training state
        full = {**random_actor(1), "critic.0.weight": np.zeros((3, 3), np.float32), "log_std": np.zeros(6, np.float32)}
        export.export(full, Path(d) / "f.safetensors", "Full", "", GOOD_BUILD)
        assert check.check_file(Path(d) / "f.safetensors").ok


def test_export_refuses_invalid_input():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "x.safetensors"
        cases = [
            (dict(random_actor(), **{"actor.0.weight": np.zeros((256, 29), np.float32)}), "N", "", GOOD_BUILD),
            (dict(random_actor(), **{"actor.4.bias": np.full(6, np.nan, np.float32)}), "N", "", GOOD_BUILD),
            (dict(random_actor(), **{"actor.4.bias": np.full(6, 1e6, np.float32)}), "N", "", GOOD_BUILD),
            ({k: v for k, v in random_actor().items() if k != "actor.4.bias"}, "N", "", GOOD_BUILD),
            (random_actor(), "", "", GOOD_BUILD), (random_actor(), "x" * 41, "", GOOD_BUILD), (random_actor(), "a\nb", "", GOOD_BUILD),
            (random_actor(), "N", "d" * 281, GOOD_BUILD), (random_actor(), "N", "", {**GOOD_BUILD, "power": 8}),
            (random_actor(), "N", "", {"power": 6.0, "speed": 6, "reach": 6, "accuracy": 6}), (random_actor(), "N", "", None),
        ]
        for st, name, desc, b in cases:
            try:
                export.export(st, out, name, desc, b)
            except ValueError:
                assert not out.exists()
                continue
            raise AssertionError((name, desc, b))


def mutate(tmp, tag, tensors=None, meta=None):
    t = {**random_actor(), **(tensors or {})}
    m = meta_for() if meta is None else meta
    return write_st(Path(tmp) / f"{tag}.safetensors", t, m)


def test_check_rejects_every_bad_file():
    calls = []
    real = check.match.play
    check.match.play = lambda *a, **k: calls.append(1)  # the smoke test must never run on a broken file
    try:
        with tempfile.TemporaryDirectory() as d:
            D = Path(d)
            good = exported(d)
            raw = good.read_bytes()
            cases = {}  # tag -> (path, expected reason substring)
            cases["truncated"] = ((D / "t.safetensors").write_bytes(raw[:300]) and D / "t.safetensors", "not a safetensors file")
            (D / "e.safetensors").write_bytes(b"")
            cases["empty"] = (D / "e.safetensors", "not a safetensors file")
            (D / "h.safetensors").write_bytes((1 << 60).to_bytes(8, "little") + b"{}")
            cases["huge header length"] = (D / "h.safetensors", "exceeds")
            (D / "h2.safetensors").write_bytes((60000).to_bytes(8, "little") + b"{}")
            cases["header longer than file"] = (D / "h2.safetensors", "truncated")
            (D / "j.safetensors").write_bytes((5).to_bytes(8, "little") + b"nojsn")
            cases["not json"] = (D / "j.safetensors", "not valid")
            cases["extra tensor"] = (mutate(d, "x", {"actor.6.weight": np.zeros(3, np.float32)}), "unexpected tensor 'actor.6.weight'")
            cases["wrong shape"] = (mutate(d, "s", {"actor.0.weight": np.zeros((256, 29), np.float32)}), "actor.0.weight has shape [256, 29]")
            cases["float64"] = (mutate(d, "f", {"actor.2.bias": np.zeros(256, np.float64)}), "actor.2.bias has dtype F64")
            nan = random_actor()["actor.2.weight"].copy()
            nan[3, 3] = np.nan
            cases["nan"] = (mutate(d, "n", {"actor.2.weight": nan}), "NaN or inf")
            inf = nan.copy()
            inf[3, 3] = np.inf
            cases["inf"] = (mutate(d, "i", {"actor.2.weight": inf}), "NaN or inf")
            cases["1e6"] = (mutate(d, "b", {"actor.4.bias": np.full(6, 1e6, np.float32)}), "above 1000")
            cases["contract /2"] = (mutate(d, "c", meta=meta_for(contract_id="oso-arena/2")), "expected 'oso-arena/1'")
            cases["newline in name"] = (mutate(d, "nl", meta=meta_for(name="evil\nname")), "non-printable")
            cases["escape in name"] = (mutate(d, "esc", meta=meta_for(name="a\x1b[31mred")), "non-printable")
            cases["rtl override"] = (mutate(d, "rtl", meta=meta_for(name="ab‮cd")), "non-printable")
            cases["name too long"] = (mutate(d, "l", meta=meta_for(name="x" * 41)), "name must be 1-40")
            cases["empty name"] = (mutate(d, "en", meta=meta_for(name="")), "name must be 1-40")
            cases["description too long"] = (mutate(d, "dl", meta=meta_for(description="d" * 281)), "281 characters")
            cases["description control"] = (mutate(d, "dc", meta=meta_for(description="a\x00b")), "control characters")
            for tag, b in (("sum 23", {"power": 9, "speed": 6, "reach": 5, "accuracy": 3}), ("sum 25", {"power": 9, "speed": 6, "reach": 5, "accuracy": 5}),
                           ("stat 0", {"power": 0, "speed": 10, "reach": 10, "accuracy": 4}), ("stat 11", {"power": 11, "speed": 5, "reach": 4, "accuracy": 4}),
                           ("float stat", {"power": 9.0, "speed": 6, "reach": 5, "accuracy": 4}), ("string stat", {"power": "9", "speed": 6, "reach": 5, "accuracy": 4}),
                           ("bool stat", {"power": True, "speed": 14, "reach": 5, "accuracy": 4}), ("missing key", {"power": 9, "speed": 6, "reach": 9})):
                cases["build " + tag] = (mutate(d, tag.replace(" ", "_"), meta=meta_for(build=b)), "header build")
            cases["build string 6.0"] = (mutate(d, "b60", meta=meta_for(build='{"power":6.0,"speed":6,"reach":6,"accuracy":6}')), "header build")
            cases["build not json"] = (mutate(d, "bnj", meta=meta_for(build="9,6,5,4")), "not valid JSON")
            cases["missing build"] = (mutate(d, "mb", meta=meta_for(build=None)), "missing 'build'")
            big = D / "big.safetensors"
            big.write_bytes(raw + b"\0" * (contract.MAX_BYTES + 1 - len(raw)))
            cases["over 1 MiB"] = (big, "limit")
            cases["missing file"] = (D / "nope.safetensors", "cannot read")
            cases["a directory"] = (D, "cannot read")
            # structurally lying headers
            cases["bad offsets"] = (raw_safetensors(D / "bo.safetensors", {"actor.0.weight": {"dtype": "F32", "shape": [256, 37], "data_offsets": [0, 4]}}, b"\0" * 4), "")
            cases["entry not object"] = (raw_safetensors(D / "eno.safetensors", {"actor.0.weight": 5}), "malformed")
            cases["negative shape"] = (raw_safetensors(D / "ns.safetensors", {"actor.0.weight": {"dtype": "F32", "shape": [-1], "data_offsets": [0, 0]}}), "malformed")
            for tag, (path, want) in cases.items():
                r = check.check_file(path)
                assert not r.ok and r.reasons, tag
                assert any(want in x for x in r.reasons), (tag, want, r.reasons)
            # several problems at once are all reported (name + build + contract)
            r = check.check_file(mutate(d, "multi", meta=meta_for(name="", build={"power": 6, "speed": 6, "reach": 6, "accuracy": 5}, contract_id="x")))
            assert len(r.reasons) == 3, r.reasons
            # the polite file still passes, and the smoke test did not run for any bad file above
            assert not calls
    finally:
        check.match.play = real


def test_pickle_renamed_to_safetensors_is_rejected_and_never_executed():
    with tempfile.TemporaryDirectory() as d:
        canary = Path(d) / "canary"

        class Evil:
            def __reduce__(self):
                return (open, (str(canary), "w"))  # unpickling would create the file

        for proto in (0, 2, 4, 5):
            p = Path(d) / f"evil{proto}.safetensors"
            p.write_bytes(pickle.dumps(Evil(), protocol=proto))
            r = check.check_file(p)
            assert not r.ok and any("not a safetensors file" in x for x in r.reasons), (proto, r.reasons)
            try:
                NumpyPolicy.load(p)
            except ValueError as e:
                assert "not a safetensors file" in str(e)
            else:
                raise AssertionError("loaded a pickle")
            assert not canary.exists(), "the pickle was executed"
        # a pickle hidden behind a believable safetensors prefix: the loader rejects it, still nothing runs
        body = pickle.dumps(Evil())
        hdr = json.dumps({"actor.0.weight": {"dtype": "F32", "shape": [256, 37], "data_offsets": [0, len(body)]}}).encode()
        sneaky = Path(d) / "sneaky.safetensors"
        sneaky.write_bytes(len(hdr).to_bytes(8, "little") + hdr + body)
        assert not check.check_file(sneaky).ok and not canary.exists()
        # and a torch zip checkpoint renamed
        import torch
        torch.save({"w": torch.zeros(2)}, Path(d) / "t.safetensors")
        assert not check.check_file(Path(d) / "t.safetensors").ok and not canary.exists()


def test_submission_path_has_no_unsafe_loader():
    """Static check (AST, so docstrings may mention the words): no pickle-family import, no torch.load / eval / exec, no allow_pickle=True."""
    import ast

    bad_mods = {"pickle", "cloudpickle", "dill", "joblib", "marshal", "shelve"}
    for mod in ("contract", "policy", "check", "export", "log", "match", "animate", "body", "builds", "bot", "cli", "sim", "physics", "court"):
        tree = ast.parse((KIT / "oso_arena" / f"{mod}.py").read_text())
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
                assert not {x.split(".")[0] for x in names} & bad_mods, (mod, names)
            if isinstance(n, ast.Call):
                f = ast.unparse(n.func)
                assert f not in ("eval", "exec", "torch.load") or (mod == "cli" and f == "torch.load"), (mod, f)
                for kw in n.keywords:
                    if kw.arg == "allow_pickle":
                        assert isinstance(kw.value, ast.Constant) and kw.value.value is False, (mod, "allow_pickle")
                    if kw.arg == "weights_only" and mod == "cli":
                        assert isinstance(kw.value, ast.Constant) and kw.value.value is True  # the CLI loads our .pt weights-only


def test_check_file_runs_the_smoke_test_both_ways():
    seen = []
    real = check.match.play
    check.match.play = lambda a, b, seed, **k: seen.append((a == "bot", b == "bot", seed, k.get("target")))
    try:
        with tempfile.TemporaryDirectory() as d:
            assert check.check_file(exported(d)).ok
    finally:
        check.match.play = real
    assert seen == [(False, True, 1, 5), (True, False, 2, 5)], seen


def test_numpy_policy_matches_torch_actor():
    import torch
    from oso_arena import train

    net = train.ActorCritic().eval()
    with torch.no_grad():
        for p in net.actor.parameters():
            p.add_(torch.randn_like(p) * 0.05)
    state = {k: v for k, v in net.state_dict().items()}
    with tempfile.TemporaryDirectory() as d:
        export.export(state, Path(d) / "m.safetensors", "Torch", "", GOOD_BUILD)
        pol = NumpyPolicy.load(Path(d) / "m.safetensors")
    obs = np.random.default_rng(0).uniform(-2, 2, (500, 37)).astype(np.float32)
    with torch.no_grad():
        ref = net.act(torch.as_tensor(obs), True)[0].clamp(-1, 1).numpy()
    got = pol.act(obs)
    assert got.shape == (500, 6) and np.abs(got - ref).max() < 1e-5, np.abs(got - ref).max()
    assert (np.abs(got) <= 1).all() and pol.meta["name"] == "Torch" and pol.meta["build"] == GOOD_BUILD
    assert NumpyPolicy.from_arrays(random_actor(), dict(name="x")).act(obs[:3]).shape == (3, 6)
    try:
        NumpyPolicy.from_arrays({**random_actor(), "actor.0.weight": np.zeros((256, 29), np.float32)}, {})
    except ValueError:
        pass
    else:
        raise AssertionError


if __name__ == "__main__":
    run(globals())
