"""Phase 14.5 packaging: pyproject metadata, the CONTRACT.md copy, the built wheel / sdist, and (T6) a clean venv that installs the wheel
without the train extra and runs check / match / animate on a model exported by a training run. The build uses --no-isolation
(the build backend must already be installed, as in the repo venv); the clean venv gets numpy / safetensors from PyPI when the network
is there, else from the repo venv through a .pth file that shows only those two packages (never torch)."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

from helpers import KIT, ROOT, run
from oso_arena import CONTRACT, KIT_VERSION

try:
    import tomllib
except ImportError:  # python 3.10
    tomllib = None

try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


def test_contract_copy_is_in_sync():
    assert (KIT / "oso_arena" / "CONTRACT.md").read_text() == (ROOT / "arena/contract/CONTRACT.md").read_text(), \
        "arena/kit/oso_arena/CONTRACT.md is a copy of arena/contract/CONTRACT.md: cp arena/contract/CONTRACT.md arena/kit/oso_arena/CONTRACT.md"


def test_pyproject_metadata():
    if tomllib is None:
        print("skip: tomllib needs python 3.11")
        return
    p = tomllib.loads((KIT / "pyproject.toml").read_text())
    m = p["project"]
    assert m["name"] == "oso-arena" and m["version"] == KIT_VERSION and m["requires-python"] == ">=3.10"
    assert m["license"] == "MIT" and m["readme"] == "README.md" and m["description"]
    assert any(c.startswith("Programming Language :: Python :: 3.10") for c in m["classifiers"])
    assert [d.split(">")[0] for d in m["dependencies"]] == ["numpy", "safetensors"]
    assert [d.split(">")[0] for d in m["optional-dependencies"]["train"]] == ["torch"]
    assert m["scripts"]["oso-arena"] == "oso_arena.cli:main"
    assert set(p["tool"]["setuptools"]["package-data"]["oso_arena"]) == {"data/*.safetensors", "CONTRACT.md"}
    lic = (KIT / "LICENSE").read_text()
    assert lic.startswith("MIT License") and "Copyright (c) 2026 oso contributors" in lic


def test_docs_exist_and_do_not_promise_pypi():
    for f in ("README.md", "TRAINING.md", "LICENSE"):
        assert (KIT / f).is_file(), f
    for f in ("README.md", "TRAINING.md"):
        text = (KIT / f).read_text()
        assert "pip install oso-arena" not in text.replace("pip install oso-arena[", "").replace("oso-arena-", ""), f  # the name is not on PyPI
    readme = (KIT / "README.md").read_text()
    for cmd in ("oso-arena train", "oso-arena export", "oso-arena check", "oso-arena eval", "oso-arena match", "oso-arena animate"):
        assert cmd in readme, cmd


def _build(src_copy, outdir):
    r = subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(outdir), str(src_copy)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    wheels, sdists = list(Path(outdir).glob("*.whl")), list(Path(outdir).glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1, (wheels, sdists)
    return wheels[0], sdists[0]


def test_wheel_and_sdist_contents_and_clean_venv_t6():
    """P1 + T6."""
    try:
        import build  # noqa: F401
    except ImportError:
        print("skip: the `build` package is not installed")
        return
    tmp = Path(tempfile.mkdtemp()).resolve()
    src = tmp / "kit"
    shutil.copytree(KIT, src, ignore=shutil.ignore_patterns("__pycache__", "dist", "build", "*.egg-info", "runs"))
    wheel, sdist = _build(src, tmp / "out")
    assert re.fullmatch(rf"oso_arena-{re.escape(KIT_VERSION)}-py3-none-any\.whl", wheel.name), wheel.name
    names = zipfile.ZipFile(wheel).namelist()
    for need in ("oso_arena/cli.py", "oso_arena/rewards.py", "oso_arena/events.py", "oso_arena/train.py", "oso_arena/CONTRACT.md",
                 f"oso_arena-{KIT_VERSION}.dist-info/METADATA", f"oso_arena-{KIT_VERSION}.dist-info/entry_points.txt"):
        assert need in names, need
    assert any(n.endswith("licenses/LICENSE") or n.endswith("/LICENSE") for n in names)
    assert not [n for n in names if n.endswith(".pt") or "runs/" in n]
    meta = zipfile.ZipFile(wheel).read(f"oso_arena-{KIT_VERSION}.dist-info/METADATA").decode()
    for line in ("Name: oso-arena", f"Version: {KIT_VERSION}", "Requires-Python: >=3.10", "License-Expression: MIT", "Provides-Extra: train",
                 'Requires-Dist: torch>=2.2; extra == "train"'):
        assert line in meta, line
    assert re.search(r"^Requires-Dist: numpy", meta, re.M) and re.search(r"^Requires-Dist: safetensors", meta, re.M)
    assert "oso-arena" in meta and "quickstart" in meta.lower()  # the README is the long description
    assert "oso-arena = oso_arena.cli:main" in zipfile.ZipFile(wheel).read(f"oso_arena-{KIT_VERSION}.dist-info/entry_points.txt").decode()
    import tarfile

    sd = tarfile.open(sdist).getnames()
    for need in ("README.md", "LICENSE", "pyproject.toml", "oso_arena/CONTRACT.md", "oso_arena/rewards.py", "tests/data/main_rewards.npz", "tests/test_rewards.py"):
        assert any(n.endswith("/" + need) for n in sd), need

    # --- T6: a clean venv, the wheel without the train extra
    env_dir = tmp / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    py = env_dir / "bin" / "python"
    r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--retries", "0", "--timeout", "8", str(wheel)], capture_output=True, text=True)
    if r.returncode != 0:  # offline: install the wheel alone and show only numpy and safetensors from the repo venv
        r = subprocess.run([str(py), "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-1500:]
        import numpy
        import safetensors

        shown = tmp / "shown"
        shown.mkdir()
        for mod in (numpy, safetensors):
            (shown / Path(mod.__file__).parent.name).symlink_to(Path(mod.__file__).parent)
        site = next((env_dir / "lib").glob("python3*/site-packages"))
        (site / "shown.pth").write_text(str(shown) + "\n")
        print("  (offline: numpy / safetensors shown from the repo venv)")
    else:
        print("  (numpy / safetensors installed from PyPI)")
    work = tmp / "work"
    work.mkdir()
    cenv = dict(os.environ, OSO_ARENA_CACHE=str(tmp / "cache"), PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1")
    cenv.pop("PYTHONHOME", None)

    def cmd(*args, ok=True):
        r = subprocess.run([str(env_dir / "bin" / "oso-arena"), *args], capture_output=True, text=True, cwd=work, env=cenv)
        assert (r.returncode == 0) == ok, (args, r.returncode, r.stdout[-800:], r.stderr[-800:])
        return r

    assert subprocess.run([str(py), "-c", "import torch"], capture_output=True, cwd=work, env=cenv).returncode != 0, "the clean venv must not have torch"
    assert subprocess.run([str(py), "-c", "import oso_arena, sys; print(oso_arena.__file__)"], capture_output=True, text=True, cwd=work, env=cenv
                          ).stdout.strip().startswith(str(env_dir))  # imported from the venv, not from the repo
    r = cmd("train", "--minutes", "1", ok=False)  # the trainer needs torch: a clear message, not a traceback
    assert "PyTorch" in r.stderr and "Traceback" not in r.stderr, r.stderr
    if (KIT / "oso_arena" / "data" / "base_40min.safetensors").exists():
        cmd("check", "base")
    else:  # a skip until the base model is bundled: it says so and fails cleanly
        r = cmd("check", "base", ok=False)
        assert "the base model is not bundled in this version of the kit" in r.stderr, r.stderr
        print("  check base: skipped (the base model is not bundled)")
    if not HAVE_TORCH:
        print("skip the exported-model part: torch is not installed in the repo venv")
        return
    from test_train import TINY, tiny_train

    run_dir = tmp / "run"
    tiny_train(run_dir, "--build", "4,6,5,9")
    shutil.copy(run_dir / "best.safetensors", work / "best.safetensors")
    m = work / "m.safetensors"
    cmd("export", "best.safetensors", "-o", str(m), "--name", "Wheel Test")  # np-only export of a training state: no torch needed
    r = cmd("check", str(m))
    assert "ok: 'Wheel Test'" in r.stdout and '"power":4' in r.stdout, r.stdout
    cmd("match", str(m), "bot", "-o", "match.log.gz", "--seed", "1")
    cmd("animate", "match.log.gz", "-o", "match.bin")
    assert (work / "match.log.gz").stat().st_size > 1000 and (work / "match.bin").stat().st_size > 1000
    r = cmd("eval", str(m), "--games", "2")
    assert "win rate" in r.stdout
    print(f"clean venv: wheel {wheel.name} ({wheel.stat().st_size // 1024} KiB) installed without torch; export, check, match, animate, eval ok")


if __name__ == "__main__":
    run(globals())
