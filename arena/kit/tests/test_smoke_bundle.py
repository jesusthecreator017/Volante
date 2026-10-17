"""Phase 14.6: the Pi handoff smoke set. arena/handoff/make_smoke.py regenerates the 3 valid + 7 invalid submission files into a temp dir;
`check` must give exactly the results in expected.json, and the generation is byte-for-byte deterministic. Also keeps the handoff files in
step with the repo: make_bundle.sh parses, PROMPT.md names every endpoint of the site's API client and every placeholder the build fills."""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from helpers import ROOT, run

HANDOFF = ROOT / "arena" / "handoff"
sys.path.insert(0, str(HANDOFF))
import make_smoke  # noqa: E402


def _digest(d):
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(d).iterdir())}


def test_smoke_set_matches_check():
    with tempfile.TemporaryDirectory() as t:
        exp = make_smoke.generate(t)
        assert sum(v["valid"] for v in exp.values()) == 3 and sum(not v["valid"] for v in exp.values()) == 7, exp
        assert json.loads((Path(t) / "expected.json").read_text(encoding="utf-8")) == exp
        assert {f.name for f in Path(t).iterdir()} == set(exp) | {"expected.json"}
        assert (Path(t) / "invalid_oversize.safetensors").stat().st_size > 1 << 20
        assert all(v["reasons"] for v in exp.values() if not v["valid"])
        problems = make_smoke.verify(t, use_cli=False)
        assert not problems, problems
        # and through the real command line for two files (valid and rejected), as make_bundle.sh does for all of them
        for f in ("valid_runner.safetensors", "invalid_wrong_shape.safetensors"):
            code, lines = make_smoke.run_check_cli(Path(t) / f)
            assert (code == 0) == exp[f]["valid"] and (code == 0 or lines == exp[f]["reasons"]), (f, code, lines)


def test_smoke_generation_is_deterministic():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        make_smoke.generate(a)
        make_smoke.generate(b)
        assert _digest(a) == _digest(b)


def test_make_bundle_script_parses():
    r = subprocess.run(["bash", "-n", str(HANDOFF / "make_bundle.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_prompt_covers_the_site_api_and_placeholders():
    prompt = (HANDOFF / "PROMPT.md").read_text(encoding="utf-8")
    api = (ROOT / "site" / "src" / "api.ts").read_text(encoding="utf-8")
    paths = set(re.findall(r'"(/(?:api|auth)/[A-Za-z0-9_/.{}?=$-]*)', api)) | set(re.findall(r"`(/(?:api|auth)/[A-Za-z0-9_/.?=-]*)", api))
    assert len(paths) >= 8, paths
    for p in sorted(paths):
        assert p.split("$")[0].rstrip("/") in prompt or p.rstrip("/") in prompt, f"PROMPT.md does not mention {p}"
    for needed in ("GET /api/models?owner=me", "/api/matches/<id>/replay.bin", "Retry-After", "x-csrf-token", '{"error"', "oso-arena check"):
        assert needed in prompt, needed
    for ph in ("{{KIT_VERSION}}", "{{CONTRACT}}", "{{GIT_COMMIT}}", "{{BASE_MODEL}}"):
        assert ph in prompt, ph
    # every TypeScript interface of types.ts is described, by name (field by field: test_handoff_hostile.py)
    types = (ROOT / "site" / "src" / "types.ts").read_text(encoding="utf-8")
    for name in re.findall(r"export interface (\w+)", types):
        assert name in prompt, f"PROMPT.md does not describe {name}"


if __name__ == "__main__":
    run(globals())
