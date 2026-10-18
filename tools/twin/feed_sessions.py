"""Feed new iPhone pod sessions into the player's Volante twin: pull, profile, keep training.

    python tools/twin/feed_sessions.py [--minutes 30] [--dry-run]          # one pass (what the launchd job runs)
    python tools/twin/feed_sessions.py --install [--every-min 20]           # run it every N minutes (macOS launchd, this user)
    python tools/twin/feed_sessions.py --uninstall

One pass:
1. Copies the Volante app's Documents/Sessions off the paired iPhone (`xcrun devicectl`; nothing happens when it is not reachable).
2. Adds sessions not seen before to ~/Datasets/volante-phone/all/<session>/ (racquet-pod sessions only: no sample, no watch).
3. Recomputes the style profile from every session (`oso-rec profile --mount flat`) -> ~/Datasets/volante-twin/my_style.json.
4. Starts PPO training (`oso-arena train --style`) from the latest twin checkpoint when the build is unchanged, else from House Base,
   unless a run is still going (then the next pass picks the new sessions up).

Raw sessions never go into the repository (tools/strokes/check_no_data.py); everything lives under ~/Datasets.
"""
import argparse
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PHONE = os.environ.get("VOLANTE_PHONE_UDID", "00008140-000C41AC1862201C")
BUNDLE = os.environ.get("VOLANTE_BUNDLE_ID", "com.alvarocm.oso.phone")
SESSIONS = Path.home() / "Datasets/volante-phone/all"
TWIN = Path.home() / "Datasets/volante-twin"
STATE = TWIN / "state.json"
PY = ROOT / "tools/.venv/bin/python"
OSO_REC = ROOT / "target/release/oso-rec"
LABEL = "com.volante.twin-feed"
PLIST = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"sessions": [], "runs": []}


def save_state(s):
    TWIN.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2))
    tmp.replace(STATE)


def pull():
    """Copy the app's sessions off the phone into a fresh temp dir. None when the phone is not reachable."""
    d = Path(tempfile.mkdtemp(prefix="volante-pull-"))
    r = subprocess.run(["xcrun", "devicectl", "device", "copy", "from", "--device", PHONE, "--domain-type", "appDataContainer",
                        "--domain-identifier", BUNDLE, "--source", "Documents/Sessions", "--destination", str(d)],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        log("phone not reachable, nothing to do:", (r.stderr or r.stdout).strip().splitlines()[-1:] or "")
        shutil.rmtree(d, ignore_errors=True)
        return None
    return d


def merge(pulled):
    """New racquet-pod sessions -> SESSIONS. Returns their folder names."""
    SESSIONS.mkdir(parents=True, exist_ok=True)
    new = []
    for meta in sorted(pulled.rglob("meta.json")):
        src = meta.parent
        try:
            m = json.loads(meta.read_text())
        except ValueError:
            continue
        if m.get("source") not in ("pod-stream", "pod-upload") or not (src / "imu.bin").is_file():
            continue
        dst = SESSIONS / src.name
        if dst.exists():
            continue
        tmp = SESSIONS / f".{src.name}.part"
        shutil.copytree(src, tmp, dirs_exist_ok=True)
        tmp.rename(dst)
        new.append(src.name)
    return new


def profile():
    imus = sorted(str(p) for p in SESSIONS.glob("*/imu.bin"))
    out = TWIN / "my_style.json"
    r = subprocess.run([str(OSO_REC), "profile", *imus, "--mount", "flat", "-o", str(out)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"oso-rec profile failed: {r.stderr.strip() or r.stdout.strip()}")
    log(r.stdout.strip())
    shutil.copy(out, TWIN / f"my_style.{time.strftime('%Y%m%d-%H%M%S')}.json")
    return out


def build_of(profile_path):
    r = subprocess.run([str(PY), "-c", "import json,sys; from oso_arena import style; "
                        "print(json.dumps(style.build_from_profile(style.load_profile(sys.argv[1]))))", str(profile_path)],
                       cwd=ROOT / "arena/kit", capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def training_running(state):
    pid = state.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # the pid is ours only if it is still an oso_arena trainer
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return False
    return " -m oso_arena train" in cmd and not cmd.startswith(("/bin/zsh", "zsh", "/bin/bash", "bash"))


def start_training(state, prof, minutes):
    build = build_of(prof)
    runs = sorted(TWIN.glob("run*/"), key=lambda p: p.stat().st_mtime)
    init = "base"
    for r in reversed(runs):  # the latest twin with the same build
        best = r / "best.safetensors"
        try:
            same = json.loads((r / "run.json").read_text()).get("build") == build
        except (OSError, ValueError):
            same = False
        if same and best.is_file():
            init = str(best)
            break
    n = 1 + max([int(p.name[3:]) for p in runs if p.name[3:].isdigit()] or [0])
    out = TWIN / f"run{n}"
    logf = open(TWIN / f"run{n}.log", "a")
    p = subprocess.Popen([str(PY), "-m", "oso_arena", "train", "--init", init, "--style", str(prof), "--minutes", str(minutes),
                          "--seed", str(n), "--out", str(out)], cwd=ROOT / "arena/kit", stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True)
    state["pid"] = p.pid
    state["runs"].append({"run": out.name, "init": init, "build": build, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "sessions": len(state["sessions"]), "minutes": minutes})
    log(f"training {out.name} for {minutes} min from {Path(init).parent.name if init != 'base' else 'House Base'} (pid {p.pid}), build {build}")


def once(minutes, dry):
    state = load_state()
    pulled = pull()
    if pulled is None:
        return
    try:
        new = merge(pulled)
    finally:
        shutil.rmtree(pulled, ignore_errors=True)
    state["sessions"] = sorted({*state["sessions"], *new, *(p.name for p in SESSIONS.iterdir() if p.is_dir() and not p.name.startswith("."))})
    pending = state.get("pending", False) or bool(new)
    log(f"{len(new)} new session(s): {', '.join(new) or '-'}; {len(state['sessions'])} in total")
    if not pending:
        save_state(state)
        return
    if training_running(state):
        state["pending"] = True
        save_state(state)
        log("a training run is still going: the new sessions go into the next one")
        return
    prof = profile()
    if dry:
        log("dry run: not training")
    else:
        start_training(state, prof, minutes)
    state["pending"] = False
    save_state(state)


def install(every_min):
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    TWIN.mkdir(parents=True, exist_ok=True)
    plistlib.dump({"Label": LABEL, "ProgramArguments": [sys.executable, str(Path(__file__).resolve())],
                   "StartInterval": every_min * 60, "RunAtLoad": True, "WorkingDirectory": str(ROOT),
                   "StandardOutPath": str(TWIN / "feed.log"), "StandardErrorPath": str(TWIN / "feed.log")}, open(PLIST, "wb"))
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    log(f"installed {PLIST}: every {every_min} min; log {TWIN / 'feed.log'}")


def uninstall():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    log("uninstalled")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minutes", type=int, default=30, help="training budget per run (default 30)")
    ap.add_argument("--dry-run", action="store_true", help="pull and profile, but do not train")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--every-min", type=int, default=20)
    ap.add_argument("--uninstall", action="store_true")
    a = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if a.install:
        return install(a.every_min)
    if a.uninstall:
        return uninstall()
    once(a.minutes, a.dry_run)


if __name__ == "__main__":
    main()
