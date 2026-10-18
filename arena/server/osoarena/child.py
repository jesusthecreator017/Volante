"""The sandboxed child: `python -I -m osoarena.child <job dir>`. Runs as oso-arena-sandbox with no network, no database and no
secrets (systemd unit oso-arena-child@.service). Reads <job dir>/job.json, opens user files ONLY through the pinned kit, writes
<job dir>/result.json (atomically) and, for match/replay jobs, the log / replay into the job dir. Imports nothing from the server
except this file: it must not be able to reach the DB even by accident."""
import gzip
import json
import os
import resource
import shutil
import sys
import traceback

LIMITS = {"check": (300, 600), "match": (900, 1800), "replay": (600, 1200)}  # same as config.LIMITS (cpu, wall)
RLIMIT_AS = 2 << 30
RLIMIT_FSIZE = 256 << 20
RLIMIT_NOFILE = 64


def _limits(kind):
    cpu = LIMITS[kind][0]
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS, RLIMIT_AS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE, RLIMIT_FSIZE))
    resource.setrlimit(resource.RLIMIT_NOFILE, (RLIMIT_NOFILE, RLIMIT_NOFILE))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _write_result(job_dir, obj):
    tmp = os.path.join(job_dir, "result.json.tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, os.path.join(job_dir, "result.json"))


def _player(p):
    return "bot" if p == "bot" else p


def do_check(job, job_dir):
    from oso_arena.check import check_file
    r = check_file(job["path"])
    return {"ok": bool(r.ok), "reasons": [str(x) for x in r.reasons], "meta": r.meta}


def rally_list(log):
    """MatchDetail.rallies: game/server/winner/reason/shots/score from rallies_detail (indexed by model, lets left out), t_start/t_end
    in replay seconds from animate.build's sheet (one entry per non-let rally, same order)."""
    from oso_arena import animate
    sheet = animate.build(log)["sheet"]
    real = [r for r in log.rallies_detail if r["winner"] is not None]
    if len(sheet) != len(real):
        raise RuntimeError(f"rally sheet has {len(sheet)} entries for {len(real)} rallies")
    side = "ab"
    return [dict(game=int(r["game"]), t_start=round(float(s["start_t"]), 3), t_end=round(float(s["end_t"]), 3),
                 server=side[int(r["server"])], winner=side[int(r["winner"])], reason=str(r["reason"]), shots=int(r["shots"]),
                 score=[int(r["score"][0]), int(r["score"][1])]) for r, s in zip(real, sheet)]


def do_fidelity(job, job_dir):
    """Volante Twins: does the model play like its profile? Reads its match logs (written by earlier sandboxed matches)."""
    from oso_arena import log as L, style
    profile = style.parse_profile(job["profile"])
    logs = [(L.read(p), int(side)) for p, side in job["logs"] if os.path.isfile(p)]
    rep = style.fidelity(profile, logs, job["build"]).to_dict()
    return {"ok": True, "report": rep, "logs": len(logs)}


def do_match(job, job_dir):
    from oso_arena import log as L, match
    m = match.play(_player(job["a"]), _player(job["b"]), int(job["seed"]))
    L.write(os.path.join(job_dir, "match.osml.gz"), m)
    return {"ok": True, "score": [int(x) for x in m.score], "winner": m.winner, "voided": bool(m.voided), "reason": m.reason,
            "games": [[int(g["score"][0]), int(g["score"][1])] for g in m.games], "duration_s": float(m.duration_s),
            "rallies": [] if m.voided else rally_list(m)}


def do_replay(job, job_dir):
    from oso_arena import animate, log as L
    m = L.read(job["log"])
    raw = os.path.join(job_dir, "replay.bin")
    animate.to_replay(m, raw)
    gz = os.path.join(job_dir, "replay.bin.gz.tmp")
    with open(raw, "rb") as fi, open(gz, "wb") as fz, gzip.GzipFile(fileobj=fz, mode="wb", compresslevel=6, mtime=0) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.replace(gz, os.path.join(job_dir, "replay.bin.gz"))
    raw_size = os.path.getsize(raw)
    os.unlink(raw)
    return {"ok": True, "raw_size": raw_size, "size": os.path.getsize(os.path.join(job_dir, "replay.bin.gz"))}


def main(argv):
    job_dir = argv[1]
    os.umask(0o007)
    with open(os.path.join(job_dir, "job.json")) as f:
        job = json.load(f)
    kind = job["kind"]
    _limits(kind)
    try:
        out = {"check": do_check, "match": do_match, "replay": do_replay, "fidelity": do_fidelity}[kind](job, job_dir)
    except MemoryError:
        out = {"ok": False, "error": "MemoryError"}
    except Exception as e:  # noqa: BLE001 -- reported to the supervisor as a failed job
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500], "trace": traceback.format_exc()[-2000:]}
    ru = resource.getrusage(resource.RUSAGE_SELF)
    out["cpu_s"] = round(ru.ru_utime + ru.ru_stime, 2)
    out["maxrss_kb"] = ru.ru_maxrss
    _write_result(job_dir, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
